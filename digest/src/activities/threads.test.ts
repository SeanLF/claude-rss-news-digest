import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { MockActivityEnvironment } from "@temporalio/testing";
import { runActivities } from "./run.js";
import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb, type Db } from "../store/db.js";
import { freshDb } from "../store/test-db.js";
import type { UsageRow } from "../store/usage.js";
import { THREAD_CONTEXT } from "./index.js";
import { WORKFLOW_RUN_TIMEOUT } from "../workflow/digest.workflow.js";
import { plansFrom, RESUME_HORIZON_HOURS, retractAbandoned, THREAD_ASSIGNMENTS, THREAD_HEALTH, THREAD_INSTALLMENTS, THREAD_LINKS, threadsActivities, threadsConfigFrom, type ThreadsConfig } from "./threads.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const RUN = 300;

type Stage = "link" | "synthesis" | "audit";
const stageOf = (system: string): Stage => (system.includes("You track ongoing news stories") ? "link" : system.includes("EVOLVING daily digest thread") ? "synthesis" : "audit");
interface Call { stage: Stage; prompt: string; options: Options }
// Answers by stage, in order; an Error answer is thrown by the call.
function fakeQuery(answers: Partial<Record<Stage, unknown[]>>, calls: Call[]): SdkQuery {
  return (({ prompt, options }: { prompt: string; options: Options }) => {
    const stage = stageOf(typeof options.systemPrompt === "string" ? options.systemPrompt : "");
    calls.push({ stage, prompt, options });
    const next = answers[stage]?.shift();
    return (async function* () {
      await Promise.resolve();
      if (next instanceof Error) throw next;
      if (next === undefined) throw new Error(`no scripted answer for ${stage}`);
      yield { type: "result", subtype: "success", result: JSON.stringify(next), structured_output: next, total_cost_usd: 0.01, usage: { input_tokens: 10, output_tokens: 5 }, duration_ms: 5, is_error: false, num_turns: 1, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
}

const CSV = [
  "article_id,title,summary,source_id",
  'A1,Iran talks resume in Geneva,"Negotiators met again. Full story at https://example.com/iran",reuters',
  "A2,Geneva round two for Iran deal,Second day of talks.,bbc",
  "A3,EU passes AI act,The act passed.,dw",
  "A4,Brussels votes on AI rules,Vote held.,politico",
].join("\n");

async function setup(opts: { config?: Partial<ThreadsConfig>; answers?: Partial<Record<Stage, unknown[]>>; attempt?: number; execution?: () => string } = {}) {
  const url = await freshDb([297, 298, 299, RUN, 301]);
  const db = openDb(url);
  await db.exec("UPDATE runs SET status = 'completed', outcome = 'sent' WHERE id < 300");
  const store = new ArtifactStore(url);
  await store.put(RUN, "articles_1.csv", CSV);
  await store.put(RUN, "clusters.json", JSON.stringify({ clusters: [{ story: "Iran talks in Geneva", article_ids: ["A1", "A2"] }, { story: "EU AI act", article_ids: ["A3", "A4"] }] }));
  await store.put(RUN, "selected.json", JSON.stringify({ must_know: [{ article_ids: ["A1", "A2"], cluster_index: 0 }], should_know: [{ article_ids: ["A3", "A4"], cluster_index: 1 }] }));
  const calls: Call[] = [];
  const usage: UsageRow[] = [];
  const config: ThreadsConfig = { ...threadsConfigFrom({}), enabled: true, latebind: null, digestDomain: "news.example", ...opts.config };
  const acts = threadsActivities({ store, dbUrl: url, agentsDir: AGENTS, config, maxAttempts: 3, query: fakeQuery(opts.answers ?? {}, calls), onUsage: (r) => void usage.push(r), attempt: () => opts.attempt ?? 1, ...(opts.execution ? { execution: opts.execution } : {}) });
  const rows = (sql: string) => db.all(sql);
  return { url, db, store, calls, usage, acts, rows };
}

// Every thread as the pipeline sees it, published or not: label and last run from its installments.
const THREADS = `(SELECT t.id, t.created_run_id AS first_run_id,
    (SELECT label FROM thread_updates i WHERE i.thread_id = t.id ORDER BY run_id DESC, id DESC LIMIT 1) AS label,
    (SELECT max(run_id) FROM thread_updates i WHERE i.thread_id = t.id) AS last_run_id
  FROM threads t) AS threads`;
// Every question with its resolution, if any run resolved it.
const QUESTIONS = `(SELECT q.id, q.question, q.raised_run_id, r.resolved_run_id,
    CASE WHEN r.question_id IS NULL THEN 'open' ELSE 'resolved' END AS status
  FROM thread_questions q LEFT JOIN thread_question_resolutions r ON r.question_id = q.id) AS thread_questions`;

// A thread the run can continue: seen in run 299 with a synthesized installment and an open question.
async function seedThread(db: Db): Promise<number> {
  const id = (await db.one<{ id: number }>("INSERT INTO threads (created_run_id) VALUES (298) RETURNING id"))!.id;
  await db.run("INSERT INTO thread_updates (thread_id, run_id, label, is_continuation, content) VALUES ($1, 298, 'Iran nuclear talks open', false, NULL)", [id]);
  await db.run("INSERT INTO thread_updates (thread_id, run_id, label, is_continuation, content) VALUES ($1, 299, 'Iran nuclear talks', true, $2)", [id, JSON.stringify({ whats_new: [{ fact: "Talks opened in Oman.", sources: ["A9"] }] })]);
  await db.run("INSERT INTO thread_questions (thread_id, question, raised_run_id) VALUES ($1, 'Will talks move to Geneva?', 299)", [id]);
  return id;
}
// Fixtures that rewrite a run's history in a way the lifecycle trigger forbids (a completed run failed).
async function rewrite(db: Db, sql: string, params: unknown[] = []): Promise<void> {
  await db.tx(async (t) => {
    await t.exec("ALTER TABLE runs DISABLE TRIGGER runs_transition");
    await t.run(sql, params);
    await t.exec("ALTER TABLE runs ENABLE TRIGGER runs_transition");
  });
}
const sent = (db: Db, id: number) => rewrite(db, "UPDATE runs SET status = 'completed', outcome = 'sent' WHERE id = $1", [id]);
// The day's issue, and its send in `status` (with an id when a draft exists).
async function publish(db: Db, runId: number, sender: number | null, status: string | null, id: string | null = "b1"): Promise<void> {
  const day = (await db.one<{ d: string }>("SELECT (started_at AT TIME ZONE 'UTC')::date AS d FROM runs WHERE id = $1", [runId]))!.d;
  await db.run("INSERT INTO issues (issue_date, revision, run_id, html) VALUES ($1, 1, $2, '')", [day, runId]);
  if (status !== null) await db.run("INSERT INTO sends (issue_date, run_id, revision, status, resend_id, claim_token, claimed_at) VALUES ($1, $2, 1, $3, $4, gen_random_uuid(), now())", [day, sender ?? runId, status, id]);
}

const installment = { whats_new: [{ fact: "Talks resumed in Geneva.", sources: ["A1"] }, { fact: "A deal is imminent.", sources: ["A2"] }], resolved: [{ question: "Will talks move to Geneva?", how: "They did." }], new_questions: ["Will a deal be signed?"], still_open: [] };

describe("threadsLink", () => {
  it("starts a new thread per story on a first run, with no model call", async () => {
    const { acts, rows, calls, store } = await setup();
    expect(await acts.threadsLink(RUN)).toEqual({ plans: [] });
    expect(calls).toHaveLength(0);
    expect(await rows(`SELECT label, first_run_id, last_run_id FROM ${THREADS} ORDER BY id`)).toEqual([
      { label: "Iran talks in Geneva", first_run_id: RUN, last_run_id: RUN },
      { label: "EU AI act", first_run_id: RUN, last_run_id: RUN },
    ]);
    expect(JSON.parse(await store.content(RUN, THREAD_ASSIGNMENTS))).toEqual([{ thread_id: 1, is_new: true, story: "Iran talks in Geneva" }, { thread_id: 2, is_new: true, story: "EU AI act" }]);
    expect(JSON.parse(await store.content(RUN, THREAD_LINKS))).toMatchObject({ linker_ok: true, proposed: 0, validated: 0, candidates: [] });
  });

  it("continues a linked thread, starts the rest, and plans synthesis for the continuation", async () => {
    const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }] } });
    const tid = await seedThread(s.db);
    expect(await s.acts.threadsLink(RUN)).toEqual({ plans: [{ threadId: tid, articleIds: ["A1", "A2"] }] });
    expect(s.calls.map((c) => c.stage)).toEqual(["link"]);
    expect(s.calls[0]!.prompt).toBe("ACTIVE THREADS:\n  [1] Iran nuclear talks open -> Iran nuclear talks\n\nTODAY'S STORIES:\n  (0) Iran talks in Geneva\n  (1) EU AI act\n\nMap each today-story to a thread id or NEW.");
    expect(s.calls[0]!.options.model).toBe("claude-haiku-4-5-20251001");
    expect(s.calls[0]!.options.outputFormat).toBeUndefined(); // free text: the schema cost a continuation a day
    expect(await s.rows(`SELECT id, label, last_run_id FROM ${THREADS} ORDER BY id`)).toEqual([{ id: 1, label: "Iran talks in Geneva", last_run_id: RUN }, { id: 2, label: "EU AI act", last_run_id: RUN }]);
    expect(await s.rows(`SELECT thread_id, is_continuation FROM thread_updates WHERE run_id = ${RUN} ORDER BY id`)).toEqual([{ thread_id: 1, is_continuation: true }, { thread_id: 2, is_continuation: false }]);
    expect(s.usage.map((u) => u.stage)).toEqual(["thread_link"]);
  });

  it("is idempotent: a retried attempt returns the committed identity without a model call or a second thread", async () => {
    const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }] } });
    await seedThread(s.db);
    const first = await s.acts.threadsLink(RUN);
    expect(await s.acts.threadsLink(RUN)).toEqual(first);
    expect(s.calls).toHaveLength(1);
    expect(await s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 2 }]);
    expect(await s.rows(`SELECT COUNT(*) AS n FROM thread_updates WHERE run_id = ${RUN}`)).toEqual([{ n: 2 }]);
  });

  it("two attempts racing on the model commit one identity between them", async () => {
    const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }] }, { links: [{ story: 0, thread: 1 }] }] } });
    await seedThread(s.db);
    const [a, b] = await Promise.all([s.acts.threadsLink(RUN), s.acts.threadsLink(RUN)]);
    expect(a).toEqual(b);
    expect(s.calls).toHaveLength(2);
    expect(await s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 2 }]);
    expect(await s.rows(`SELECT COUNT(*) AS n FROM thread_updates WHERE run_id = ${RUN}`)).toEqual([{ n: 2 }]);
  });

  it("an attempt that fails mid-commit leaves no identity behind", async () => {
    const s = await setup();
    await s.store.put(RUN, THREAD_LINKS, "{}"); // the commit's own record already taken: its insert fails last
    await expect(s.acts.threadsLink(RUN)).rejects.toThrow(/duplicate key/);
    expect(await s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 0 }]);
    expect(await s.rows("SELECT COUNT(*) AS n FROM thread_updates")).toEqual([{ n: 0 }]);
  });

  it("refuses to link a run whose installments exist without their record", async () => {
    const s = await setup();
    const tid = await seedThread(s.db);
    await s.db.run("INSERT INTO thread_updates (thread_id, run_id, label, is_continuation) VALUES ($1, $2, 'x', true)", [tid, RUN]);
    await expect(s.acts.threadsLink(RUN)).rejects.toThrow(/refusing to link again/);
  });

  it("a linker failure retries while attempts remain", async () => {
    const s = await setup({ answers: { link: [new Error("overloaded")] }, attempt: 1 });
    await seedThread(s.db);
    await expect(s.acts.threadsLink(RUN)).rejects.toThrow(/overloaded/);
    expect(await s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 1 }]);
  });

  it("on its last attempt a failed linker falls back to all-new, recorded as linker_ok false", async () => {
    const s = await setup({ answers: { link: [{ links: [] }] }, attempt: 3 });
    await seedThread(s.db);
    expect(await s.acts.threadsLink(RUN)).toEqual({ plans: [] });
    expect(JSON.parse(await s.store.content(RUN, THREAD_LINKS))).toMatchObject({ linker_ok: false, stories: [{ outcome: "new" }, { outcome: "new" }] });
  });

  it("refuses a hallucinated thread id and a second claim on one thread", async () => {
    const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: 1 }, { story: 5, thread: 1 }] }] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    const trace = JSON.parse(await s.store.content(RUN, THREAD_LINKS)) as { proposed: number; validated: number; stories: { refused: string | null }[] };
    expect(trace.stories.map((x) => x.refused)).toEqual([null, "already_claimed"]);
    expect([trace.proposed, trace.validated]).toEqual([3, 2]);
  });

  it("does not offer a thread that has gone quiet: dormancy is its installments' age, nothing writes it", async () => {
    const s = await setup({ config: { dormantAfter: 1 } });
    await s.db.exec("INSERT INTO threads (id, created_run_id) VALUES (50, 297); INSERT INTO thread_updates (thread_id, run_id, label, is_continuation) VALUES (50, 297, 'Old', false)");
    await s.acts.threadsLink(RUN);
    expect(s.calls).toHaveLength(0); // not a candidate, so nothing to ask
  });

  it("does nothing when disabled", async () => {
    const s = await setup({ config: { enabled: false } });
    expect(await s.acts.threadsLink(RUN)).toEqual({ plans: [], skip: "disabled" });
    expect(await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] })).toMatchObject({ name: THREAD_CONTEXT, sha256: "0".repeat(64) });
    expect((await s.store.names(RUN)).filter((n) => n.startsWith("thread"))).toEqual([]);
  });
});

async function linked(answers: Partial<Record<Stage, unknown[]>>) {
  const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }], ...answers } });
  await seedThread(s.db);
  const { plans } = await s.acts.threadsLink(RUN);
  return { ...s, plan: plans[0]! };
}
describe("threadSynthesis", () => {


  it("synthesizes against the thread's memory, drops what the audit rejects, and settles the ledger", async () => {
    const s = await linked({ synthesis: [installment], audit: [{ verdicts: [{ id: 1, supported: true }, { id: 2, supported: false }] }] });
    expect(await s.acts.threadSynthesis(RUN, s.plan)).toEqual({ threadId: 1, auditFailed: false });
    const synth = s.calls.find((c) => c.stage === "synthesis")!;
    expect(synth.prompt).toContain("RECENT UPDATES:\n- Talks opened in Oman.\nOPEN QUESTIONS:\n- Will talks move to Geneva?");
    expect(synth.prompt).toContain("A1: Iran talks resume in Geneva\n   Negotiators met again. Full story at [link]");
    expect(synth.options.model).toBe("claude-sonnet-4-6");
    expect(s.calls.find((c) => c.stage === "audit")!.prompt).toBe(
      "CLAIM 1: Talks resumed in Geneva.\nCITED SOURCE(S):\n  [A1] Iran talks resume in Geneva. Negotiators met again. Full story at [link]\n\nCLAIM 2: A deal is imminent.\nCITED SOURCE(S):\n  [A2] Geneva round two for Iran deal. Second day of talks.",
    );
    const content = JSON.parse(String((await s.rows(`SELECT content FROM thread_updates WHERE thread_id = 1 AND run_id = ${RUN}`))[0]!["content"])) as Record<string, unknown>;
    expect(content).toEqual({ ...installment, whats_new: [installment.whats_new[0]], cited_ids: ["A1", "A2"] });
    expect(await s.rows(`SELECT question, status, resolved_run_id, raised_run_id FROM ${QUESTIONS} ORDER BY id`)).toEqual([
      { question: "Will talks move to Geneva?", status: "resolved", resolved_run_id: RUN, raised_run_id: 299 },
      { question: "Will a deal be signed?", status: "open", resolved_run_id: null, raised_run_id: RUN },
    ]);
    expect(s.usage.map((u) => u.stage)).toEqual(["thread_link", "thread_synthesis", "thread_audit"]);
  });

  it("is idempotent: a retried attempt after the commit makes no call, adds no question, and keeps its audit health", async () => {
    const s = await linked({ synthesis: [installment], audit: [new Error("down")] });
    const first = await s.acts.threadSynthesis(RUN, s.plan);
    expect(first.auditFailed).toBe(true);
    const n = s.calls.length;
    expect(await s.acts.threadSynthesis(RUN, s.plan)).toEqual(first);
    expect(s.calls).toHaveLength(n);
    expect(await s.rows("SELECT COUNT(*) AS n FROM thread_questions")).toEqual([{ n: 2 }]);
  });

  it("a retry after the synthesis landed audits that installment instead of paying for another", async () => {
    const s = await linked({ audit: [{ verdicts: [{ id: 1, supported: true }, { id: 2, supported: true }] }] });
    await s.store.put(RUN, "thread_synthesis_t1.json", JSON.stringify(installment));
    await s.acts.threadSynthesis(RUN, s.plan);
    expect(s.calls.map((c) => c.stage)).toEqual(["link", "audit"]);
  });

  it("re-asks once when the verdicts do not cover the claims", async () => {
    const s = await linked({ synthesis: [installment], audit: [{ verdicts: [{ id: 1, supported: true }] }, { verdicts: [{ id: 1, supported: true }, { id: 2, supported: true }] }] });
    expect(await s.acts.threadSynthesis(RUN, s.plan)).toEqual({ threadId: 1, auditFailed: false });
    const audits = s.calls.filter((c) => c.stage === "audit");
    expect(audits).toHaveLength(2);
    expect(audits[1]!.prompt).toContain("IMPORTANT: an earlier attempt at these exact claims came back unusable (verdicts missing/misaligned for claim(s) [2] (1 element(s), 1 usable, ids [1])). Return EXACTLY 2 verdicts");
  });

  it("fails open when the audit cannot answer, keeping the facts and saying so", async () => {
    const s = await linked({ synthesis: [installment], audit: [new Error("timeout")] });
    expect(await s.acts.threadSynthesis(RUN, s.plan)).toEqual({ threadId: 1, auditFailed: true });
    const content = JSON.parse(String((await s.rows(`SELECT content FROM thread_updates WHERE thread_id = 1 AND run_id = ${RUN}`))[0]!["content"])) as { whats_new: unknown[] };
    expect(content.whats_new).toHaveLength(2);
  });

  it("a synthesis failure is thrown for the retry policy, leaving nothing applied", async () => {
    const s = await linked({ synthesis: [new Error("overloaded")] });
    await expect(s.acts.threadSynthesis(RUN, s.plan)).rejects.toThrow(/overloaded/);
    expect(await s.rows(`SELECT content FROM thread_updates WHERE thread_id = 1 AND run_id = ${RUN}`)).toEqual([{ content: null }]);
  });
});

describe("threadsFinish", () => {
  it("records the phase once and hands the render each continuing story's context", async () => {
    const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }], synthesis: [installment], audit: [new Error("down")] } });
    await seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    const outcome = await s.acts.threadSynthesis(RUN, plans[0]!);
    const p = await s.acts.threadsFinish(RUN, { outcomes: [outcome], failures: [] });
    expect(p.name).toBe(THREAD_CONTEXT);
    expect(JSON.parse(await s.store.get(p))).toEqual({ "Iran talks in Geneva": { thread_id: 1, day: 3, delta: "Talks resumed in Geneva. A deal is imminent.", url: "https://news.example/thread/1" } });
    expect(JSON.parse(await s.store.content(RUN, THREAD_INSTALLMENTS))).toEqual([{ thread_id: 1, ...installment, cited_ids: ["A1", "A2"] }]);
    expect(JSON.parse(await s.store.content(RUN, THREAD_HEALTH))).toEqual({ link: "ok", linker_ok: true, synthesized: 1, audit_failures: 1, failures: [] });
    expect(await s.acts.threadsFinish(RUN, { outcomes: [outcome], failures: [] })).toEqual(p);
    expect(await s.store.statuses(RUN, THREAD_HEALTH)).toEqual(["current"]);
  });

  it("records a failed link and gives the render nothing to find", async () => {
    const s = await setup();
    const p = await s.acts.threadsFinish(RUN, { linkError: "linker down", outcomes: [], failures: [] });
    expect(await s.store.find(RUN, p.name)).toBeUndefined();
    expect(JSON.parse(await s.store.content(RUN, THREAD_HEALTH))).toEqual({ link: "failed", error: "linker down" });
  });
});

describe("a forced re-run", () => {
  const link2 = { links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] };
  it("takes back this run's identity and relinks from the state the run began in (the reviewer's scenario)", async () => {
    const s = await setup({ answers: { link: [link2, { links: [{ story: 0, thread: null }] }] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    await s.store.replace(RUN, "selected.json", JSON.stringify({ must_know: [{ article_ids: ["A3", "A4"], cluster_index: 1 }], should_know: [] }));
    expect(await s.acts.threadsLink(RUN, true)).toEqual({ plans: [] });
    expect(s.calls.filter((c) => c.stage === "link").at(-1)!.prompt).toContain("[1] Iran nuclear talks open -> Iran nuclear talks\n"); // the arc as it stood before the run
    expect(await s.rows(`SELECT thread_id, label FROM thread_updates WHERE run_id = ${RUN}`)).toEqual([{ thread_id: 3, label: "EU AI act" }]);
    expect(await s.rows(`SELECT id, label, last_run_id FROM ${THREADS} ORDER BY id`)).toEqual([{ id: 1, label: "Iran nuclear talks", last_run_id: 299 }, { id: 3, label: "EU AI act", last_run_id: RUN }]);
    expect(await s.rows("SELECT run_id, COUNT(*) AS n FROM thread_updates GROUP BY run_id ORDER BY run_id")).toEqual([{ run_id: 298, n: 1 }, { run_id: 299, n: 1 }, { run_id: RUN, n: 1 }]);
    expect(JSON.parse(await s.store.get(await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] })))).toEqual({});
    for (const name of ["thread_assignments.json", "thread_context.json", "thread_health.json", "thread_installments.json", "thread_links.json"]) expect(await s.store.statuses(RUN, name)).toContain("quarantined");
  });
  it("reopens what this run resolved, drops what it raised, and resynthesizes", async () => {
    const s = await setup({ answers: { link: [link2, link2], synthesis: [installment, installment], audit: [{ verdicts: [{ id: 1, supported: true }, { id: 2, supported: true }] }, { verdicts: [{ id: 1, supported: true }, { id: 2, supported: false }] }] } });
    await seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    await s.acts.threadSynthesis(RUN, plans[0]!);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    const again = await s.acts.threadsLink(RUN, true);
    expect(await s.rows(`SELECT question, status FROM ${QUESTIONS}`)).toEqual([{ question: "Will talks move to Geneva?", status: "open" }]);
    expect(await s.acts.threadSynthesis(RUN, again.plans[0]!)).toEqual({ threadId: 1, auditFailed: false });
    expect(s.calls.map((c) => c.stage)).toEqual(["link", "synthesis", "audit", "link", "synthesis", "audit"]);
    expect(await s.rows(`SELECT question, status FROM ${QUESTIONS} ORDER BY id`)).toEqual([{ question: "Will talks move to Geneva?", status: "resolved" }, { question: "Will a deal be signed?", status: "open" }]);
  });
  it("leaves another run's rows alone", async () => {
    const s = await setup({ answers: { link: [link2, link2] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    const before = await s.rows("SELECT * FROM thread_updates WHERE run_id <> 300 ORDER BY id");
    await s.acts.threadsLink(RUN, true);
    expect(await s.rows("SELECT * FROM thread_updates WHERE run_id <> 300 ORDER BY id")).toEqual(before);
    expect(await s.rows(`SELECT question, status FROM ${QUESTIONS}`)).toEqual([{ question: "Will talks move to Geneva?", status: "open" }]);
  });
});

describe("resumes and records", () => {
  it("does not apply again an installment the Python applied before a resume", async () => {
    const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }] } });
    await seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    await s.db.run(`UPDATE thread_updates SET content = $1 WHERE thread_id = 1 AND run_id = ${RUN}`, [JSON.stringify(installment)]);
    await s.db.run("INSERT INTO thread_questions (thread_id, question, raised_run_id) VALUES (1, 'Will a deal be signed?', $1)", [RUN]);
    expect(await s.acts.threadSynthesis(RUN, plans[0]!)).toEqual({ threadId: 1, auditFailed: false });
    expect(s.calls.map((c) => c.stage)).toEqual(["link"]);
    expect(await s.rows("SELECT COUNT(*) AS n FROM thread_questions")).toEqual([{ n: 2 }]);
  });
  it("plans only from trace entries that carry their assignment's story", () => {
    const trace = { linker_ok: true, proposed: 1, validated: 1, candidates: [], stories: [{ story_index: 0, label: "other story", article_ids: ["A1", "A2"], proposed_thread: 1, refused: null, outcome: "continued" as const }] };
    expect(plansFrom([{ thread_id: 1, is_new: false, story: "Iran" }], trace)).toEqual([]);
    expect(plansFrom([{ thread_id: 1, is_new: false, story: "other story" }], trace)).toEqual([{ threadId: 1, articleIds: ["A1", "A2"] }]);
  });
  it("a failed link then a successful resume: health and context say so", async () => {
    const s = await setup();
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [], linkError: "boom" });
    expect(JSON.parse(await s.store.content(RUN, THREAD_HEALTH))).toMatchObject({ link: "failed" });
    await s.acts.threadsLink(RUN);
    const p = await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    expect(JSON.parse(await s.store.content(RUN, THREAD_HEALTH))).toEqual({ link: "ok", linker_ok: true, synthesized: 0, audit_failures: 0, failures: [] });
    expect(await s.store.find(RUN, p.name)).toEqual(p);
  });
  it("a synthesis that failed and then landed on a resume is no longer a failure", async () => {
    const s = await setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }], synthesis: [installment], audit: [{ verdicts: [{ id: 1, supported: true }, { id: 2, supported: true }] }] } });
    await seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [{ threadId: 1, error: "overloaded" }] });
    expect(JSON.parse(await s.store.content(RUN, THREAD_HEALTH))).toMatchObject({ synthesized: 0, failures: [{ threadId: 1, error: "overloaded" }] });
    expect(JSON.parse(await s.store.content(RUN, THREAD_CONTEXT))).toMatchObject({ "Iran talks in Geneva": { delta: "" } });
    const outcome = await s.acts.threadSynthesis(RUN, plans[0]!);
    const p = await s.acts.threadsFinish(RUN, { outcomes: [outcome], failures: [] });
    expect(JSON.parse(await s.store.content(RUN, THREAD_HEALTH))).toMatchObject({ synthesized: 1, failures: [] });
    expect(JSON.parse(await s.store.get(p))).toMatchObject({ "Iran talks in Geneva": { delta: "Talks resumed in Geneva. A deal is imminent." } });
  });
  it("a phase that ran past its bound is recorded and leaves no context for the render", async () => {
    const s = await setup();
    await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    const p = await s.acts.threadsFinish(RUN, { outcomes: [], failures: [], timedOut: true });
    expect(await s.store.find(RUN, p.name)).toBeUndefined();
    expect(JSON.parse(await s.store.content(RUN, THREAD_HEALTH))).toMatchObject({ timed_out: true });
  });
});

describe("threadsConfigFrom", () => {
  it("is off by default, as config.py is, with config.py's defaults", () => {
    expect(threadsConfigFrom({})).toEqual({ enabled: false, dormantAfter: 3, latebind: null, digestDomain: "" });
    expect(threadsConfigFrom({ THREADS_ENABLED: "true", THREAD_LATEBIND: "yes" })).toEqual({ enabled: true, dormantAfter: 3, latebind: { threshold: 0.35, maxExtra: 12 }, digestDomain: "" });
    expect(threadsConfigFrom({ THREADS_ENABLED: "on" }).enabled).toBe(false); // config.py reads only 1, true, yes
  });
  it.each([["THREAD_DORMANT_AFTER", "three"], ["THREAD_DORMANT_AFTER", "2.5"], ["THREAD_DORMANT_AFTER", "-1"], ["THREAD_DORMANT_AFTER", ""], ["THREAD_LATEBIND_MAX_EXTRA", "x"]])("refuses %s=%j loudly", (name, value) => {
    expect(() => threadsConfigFrom({ THREADS_ENABLED: "1", THREAD_LATEBIND: "1", [name]: value })).toThrow(new RegExp(name));
  });
});

describe("a forced re-run of an earlier run", () => {
  const link2 = { links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] };
  it("refuses when a later run continued a thread this run created, and leaves everything as it was", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await s.db.exec("INSERT INTO thread_updates (thread_id, run_id, label, is_continuation) VALUES (2, 301, 'EU AI act, day two', true)");
    const before = await s.rows("SELECT * FROM thread_updates ORDER BY id");
    await expect(s.acts.threadsLink(RUN, true)).rejects.toThrow(/later run\(s\) 301 build on run 300's threads/);
    expect(await s.rows("SELECT * FROM thread_updates ORDER BY id")).toEqual(before);
    expect(await s.store.statuses(RUN, "thread_assignments.json")).toEqual(["current"]);
  });
  it("refuses when a later run continued a thread this run continued", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await s.db.exec("INSERT INTO thread_updates (thread_id, run_id, label, is_continuation) VALUES (1, 301, 'Iran talks, day three', true)");
    await expect(s.acts.threadsLink(RUN, true)).rejects.toThrow(/301/);
  });
  it("refuses when a later run resolved a question this run raised", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await s.db.exec("INSERT INTO thread_questions (id, thread_id, question, raised_run_id) VALUES (90, 1, 'Asked in 300', 300); INSERT INTO thread_question_resolutions (question_id, resolved_run_id, answer) VALUES (90, 301, 'answered')");
    await expect(s.acts.threadsLink(RUN, true)).rejects.toThrow(/301/);
    expect(await s.rows("SELECT COUNT(*) AS n FROM thread_questions WHERE raised_run_id = 300")).toEqual([{ n: 1 }]);
  });
  it("a retried forced link in the same execution does not undo what its first attempt committed", async () => {
    const s = await setup({ answers: { link: [link2, link2, link2] }, execution: () => "exec-1" });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    const first = await s.acts.threadsLink(RUN, true);
    const ids = await s.rows(`SELECT id FROM ${THREADS} ORDER BY id`);
    expect(await s.acts.threadsLink(RUN, true)).toEqual(first);
    expect(await s.rows(`SELECT id FROM ${THREADS} ORDER BY id`)).toEqual(ids);
    expect(s.calls.filter((c) => c.stage === "link")).toHaveLength(2);
  });
});

describe("an issue that is not sent", () => {
  const link2 = { links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] };
  it("takes back its thread writes, so the web tier never shows an installment nobody was sent", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    const before = await s.rows("SELECT * FROM thread_updates ORDER BY id");
    await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    expect(await s.acts.threadsRetract(RUN)).toEqual({ retracted: true });
    expect(await s.rows("SELECT * FROM thread_updates ORDER BY id")).toEqual(before);
    expect(await s.rows(`SELECT id, label, last_run_id FROM ${THREADS} ORDER BY id`)).toEqual([{ id: 1, label: "Iran nuclear talks", last_run_id: 299 }]);
    expect(await s.acts.threadsRetract(RUN)).toEqual({ retracted: true }); // idempotent
  });
  it.each(["sent", "queued", "claimed"])("declines for a run whose day already has a %s broadcast: a resumed, delivered issue keeps its threads", async (status) => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await publish(s.db, RUN, RUN, status, status === "claimed" ? null : "b1");
    const before = await s.rows("SELECT * FROM thread_updates ORDER BY id");
    expect(await s.acts.threadsRetract(RUN)).toMatchObject({ retracted: false });
    expect(await s.rows("SELECT * FROM thread_updates ORDER BY id")).toEqual(before);
  });
  it("declines for a run whose issue is on the web though its send failed: readers can open it", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await publish(s.db, RUN, RUN, "failed", null);
    const before = await s.rows("SELECT * FROM thread_updates ORDER BY id");
    expect(await quietly(() => s.acts.threadsRetract(RUN))).toMatchObject({ result: { retracted: false } });
    expect(await s.rows("SELECT * FROM thread_updates ORDER BY id")).toEqual(before);
  });
  it("declines, rather than fails, when a later run already builds on it", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await s.db.exec("INSERT INTO thread_updates (thread_id, run_id, label, is_continuation) VALUES (1, 301, 'Iran talks, day three', true)");
    const before = await s.rows("SELECT * FROM thread_updates ORDER BY id");
    expect(await s.acts.threadsRetract(RUN)).toEqual({ retracted: false, reason: "later run(s) 301 build on it" });
    expect(await s.rows("SELECT * FROM thread_updates ORDER BY id")).toEqual(before);
  });
});

async function fail(db: Db, id: number, runAt?: string): Promise<void> {
  await rewrite(db, "UPDATE runs SET status = 'failed', outcome = NULL WHERE id = $1", [id]);
  if (runAt) await db.run("UPDATE runs SET started_at = $1 WHERE id = $2", [runAt, id]);
}
// Runs fn with the JSON log lines on stderr captured and stdout silenced.
async function quietly<T>(fn: () => Promise<T>): Promise<{ result: T; logged: Record<string, unknown>[] }> {
  const logged: Record<string, unknown>[] = [];
  const [err, log] = [console.error, console.log];
  console.error = (m: string) => logged.push(JSON.parse(m) as Record<string, unknown>);
  console.log = () => undefined;
  try {
    return { result: await fn(), logged };
  } finally {
    [console.error, console.log] = [err, log];
  }
}

// abortRun keeps a failed run's thread writes, because a resume needs them. The next run's threads
// phase takes them back once no resume can come, before its linker reads the threads.
describe("a failed run nobody resumed", () => {
  const link2 = { links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] };
  const EARLIER = 299;
  const earlierRows = (s: Awaited<ReturnType<typeof setup>>) => s.rows(`SELECT thread_id, label FROM thread_updates WHERE run_id = ${EARLIER}`);

  it("the resume horizon is the workflow's run timeout", () => {
    expect(`${RESUME_HORIZON_HOURS} hours`).toBe(WORKFLOW_RUN_TIMEOUT);
  });

  it("takes back a failed run's installments before linking, so neither the web tier nor the linker sees them", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await fail(s.db, EARLIER);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(await earlierRows(s)).toEqual([]);
    expect(await s.rows(`SELECT COUNT(*) AS n FROM thread_questions WHERE raised_run_id = ${EARLIER}`)).toEqual([{ n: 0 }]);
    // The linker was shown the thread as run 298 left it, not as the failed run relabelled it.
    expect(s.calls[0]!.prompt).toContain("ACTIVE THREADS:\n  [1] Iran nuclear talks open\n\n");
  });

  it("takes back a chain of failed runs, the later first, so the earlier has no dependent left", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await fail(s.db, 298);
    await fail(s.db, EARLIER);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(await s.rows("SELECT COUNT(*) AS n FROM thread_updates WHERE run_id IN (298, 299)")).toEqual([{ n: 0 }]);
  });

  // saveDigest puts the issue on the web before the send; a send that then fails marks the run failed.
  // Readers can open that issue, so its thread updates are public: published by web is published.
  it("keeps a failed run whose digest was saved to the web but never sent", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    const before = await earlierRows(s);
    await fail(s.db, EARLIER, "2026-09-17 10:25:40");
    await publish(s.db, EARLIER, null, null);
    const { logged } = await quietly(() => s.acts.threadsLink(RUN));
    expect(before).toHaveLength(1);
    expect(await earlierRows(s)).toEqual(before);
    expect(logged).toContainEqual(expect.objectContaining({ runId: EARLIER, error: "unsent issue's thread writes kept" }));
  });

  it("takes back a failed run that never reached the web or a send", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await fail(s.db, EARLIER, "2026-09-17 10:25:40");
    await quietly(() => s.acts.threadsLink(RUN));
    expect(await earlierRows(s)).toEqual([]);
  });

  // The reviewer's case: 299 fails, a forced 300 runs the same day, inside the horizon, and sends.
  // 300 supersedes 299, so 300 takes 299's writes back before it links and links on the state 299 began in.
  it("a later run of the same day takes the failed run's writes back before it links, inside the horizon", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    const recent = new Date(Date.now() - 60 * 60 * 1000).toISOString().replace("T", " ").slice(0, 19);
    await fail(s.db, EARLIER, recent);
    await s.db.run("UPDATE runs SET started_at = $1 WHERE id = $2", [recent, RUN]);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(await earlierRows(s)).toEqual([]);
    expect(s.calls[0]!.prompt).toContain("ACTIVE THREADS:\n  [1] Iran nuclear talks open\n\n");
    await publish(s.db, RUN, RUN, "sent");
    await sent(s.db, RUN);
    expect(await s.rows(`SELECT COUNT(*) AS n FROM thread_updates WHERE run_id = ${RUN}`)).toEqual([{ n: 2 }]);
  });

  it("judges delivery by sender: a broadcast a later, completed run sent does not keep the failed run's writes", async () => {
    const s = await setup();
    await seedThread(s.db);
    await fail(s.db, EARLIER);
    await sent(s.db, RUN);
    await publish(s.db, RUN, RUN, "sent");
    await quietly(() => retractAbandoned(s.db, 301));
    expect(await earlierRows(s)).toEqual([]);
  });

  it("keeps a failed run a resume has taken up again, however old", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await fail(s.db, EARLIER, "2026-09-17 10:25:40");
    const before = await earlierRows(s);
    await s.store.put(EARLIER, "sources.csv", "id,name,bias,factuality,perspective\nf,F,center,high,global\n");
    const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
    writeFileSync(sourcesFile, "[]");
    const runs = runActivities({ store: s.store, dbUrl: s.url, sourcesFile });
    const env = new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-2026-09-17", runId: "exec-resume" } });
    await env.run(() => runs.startRun({ runDate: "2026-09-17", resumeRun: EARLIER }));
    await quietly(() => s.acts.threadsLink(RUN));
    expect(await earlierRows(s)).toEqual(before);
    expect(before).toHaveLength(1);
  });

  // Negative controls: each is a retracting case above with one condition flipped.
  // The reviewer's case: 299 sends, then fails after the send; a forced 300 the same day saves over
  // the row (run_id 300), skips the send as already accepted, and completes. Readers got 299's issue.
  it("keeps a failed run that sent its issue, though a later completed run saved over the day's row", async () => {
    const s = await setup();
    await seedThread(s.db);
    const before = await earlierRows(s);
    await fail(s.db, EARLIER);
    await sent(s.db, RUN);
    await publish(s.db, RUN, EARLIER, "sent");
    const { logged } = await quietly(() => retractAbandoned(s.db, 301));
    expect(await earlierRows(s)).toEqual(before);
    expect(logged).toContainEqual(expect.objectContaining({ runId: EARLIER, error: "unsent issue's thread writes kept" }));
  });

  it("keeps a failed run whose day's broadcast a run that did not complete sent", async () => {
    const s = await setup();
    await seedThread(s.db);
    const before = await earlierRows(s);
    await fail(s.db, EARLIER);
    await fail(s.db, RUN);
    await publish(s.db, RUN, RUN, "sent");
    await quietly(() => retractAbandoned(s.db, 301));
    expect(await earlierRows(s)).toEqual(before);
  });

  it("keeps a completed run's installments", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    const before = await earlierRows(s);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(before).toHaveLength(1);
    expect(await earlierRows(s)).toEqual(before);
  });

  it("keeps a failed run still inside the resume horizon: a resume needs them", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    const before = await earlierRows(s);
    await fail(s.db, EARLIER, new Date(Date.now() - 60 * 60 * 1000).toISOString().replace("T", " ").slice(0, 19));
    await quietly(() => s.acts.threadsLink(RUN));
    expect(await earlierRows(s)).toEqual(before);
  });

  it.each([
    ["sent", "b1"],
    ["claimed", null],
    ["draft", "b1"], // its send may have been accepted
  ])("keeps a failed run whose day's broadcast is %s", async (status, id) => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    const before = await earlierRows(s);
    await fail(s.db, EARLIER, "2026-09-17 10:25:40");
    await publish(s.db, EARLIER, EARLIER, status, id);
    const { logged } = await quietly(() => s.acts.threadsLink(RUN));
    expect(await earlierRows(s)).toEqual(before);
    expect(logged).toContainEqual(expect.objectContaining({ runId: EARLIER, error: "unsent issue's thread writes kept" }));
  });

  it("declines, and says so, when a later delivered run builds on the failed one", async () => {
    const s = await setup({ answers: { link: [link2] } });
    await seedThread(s.db);
    await fail(s.db, 298);
    const before = await s.rows("SELECT * FROM thread_updates WHERE run_id = 298");
    const { logged } = await quietly(() => s.acts.threadsLink(RUN));
    expect(await s.rows("SELECT * FROM thread_updates WHERE run_id = 298")).toEqual(before);
    expect(logged).toContainEqual(expect.objectContaining({ runId: 298, reason: "later run(s) 299 build on it" }));
  });
});
