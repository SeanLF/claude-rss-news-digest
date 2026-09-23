import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { MockActivityEnvironment } from "@temporalio/testing";
import { runActivities } from "./run.js";
import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import type { UsageRow } from "../store/usage.js";
import { THREAD_CONTEXT } from "./index.js";
import { WORKFLOW_RUN_TIMEOUT } from "../workflow/digest.workflow.js";
import { plansFrom, RESUME_HORIZON_HOURS, retractAbandoned, THREAD_ASSIGNMENTS, THREAD_HEALTH, THREAD_INSTALLMENTS, THREAD_LINKS, threadsActivities, threadsConfigFrom, type ThreadsConfig } from "./threads.js";

const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const MIGRATIONS = new URL("../../../migrations/", import.meta.url).pathname;
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

function setup(opts: { config?: Partial<ThreadsConfig>; answers?: Partial<Record<Stage, unknown[]>>; attempt?: number; execution?: () => string } = {}) {
  const path = freshDb([297, 298, 299, RUN, 301]);
  const db = new DatabaseSync(path);
  for (const f of readdirSync(MIGRATIONS).filter((n) => /thread|installment/.test(n)).toSorted()) db.exec(readFileSync(`${MIGRATIONS}${f}`, "utf8"));
  db.exec("UPDATE digest_runs SET completed_at = run_at WHERE id < 300");
  const store = new ArtifactStore(path);
  store.put(RUN, "articles_1.csv", CSV);
  store.put(RUN, "clusters.json", JSON.stringify({ clusters: [{ story: "Iran talks in Geneva", article_ids: ["A1", "A2"] }, { story: "EU AI act", article_ids: ["A3", "A4"] }] }));
  store.put(RUN, "selected.json", JSON.stringify({ must_know: [{ article_ids: ["A1", "A2"], cluster_index: 0 }], should_know: [{ article_ids: ["A3", "A4"], cluster_index: 1 }] }));
  const calls: Call[] = [];
  const usage: UsageRow[] = [];
  const config: ThreadsConfig = { ...threadsConfigFrom({}), enabled: true, latebind: null, digestDomain: "news.example", ...opts.config };
  const acts = threadsActivities({ store, dbPath: path, agentsDir: AGENTS, config, maxAttempts: 3, query: fakeQuery(opts.answers ?? {}, calls), onUsage: (r) => usage.push(r), attempt: () => opts.attempt ?? 1, ...(opts.execution ? { execution: opts.execution } : {}) });
  const rows = (sql: string) => db.prepare(sql).all() as Record<string, unknown>[];
  return { db, store, calls, usage, acts, rows };
}

// A thread the run can continue: seen in run 299 with a synthesized installment and an open question.
function seedThread(db: DatabaseSync): number {
  const id = Number(db.prepare("INSERT INTO threads (slug, label, status, first_run_id, last_run_id) VALUES ('iran', 'Iran nuclear talks', 'active', 298, 299)").run().lastInsertRowid);
  db.prepare("INSERT INTO thread_installments (thread_id, run_id, cluster_story, content) VALUES (?, 298, 'Iran nuclear talks open', NULL)").run(id);
  db.prepare("INSERT INTO thread_installments (thread_id, run_id, cluster_story, content) VALUES (?, 299, 'Iran nuclear talks', ?)").run(id, JSON.stringify({ whats_new: [{ fact: "Talks opened in Oman.", sources: ["A9"] }] }));
  db.prepare("INSERT INTO thread_questions (thread_id, question, status, raised_run_id) VALUES (?, 'Will talks move to Geneva?', 'open', 299)").run(id);
  return id;
}

const installment = { whats_new: [{ fact: "Talks resumed in Geneva.", sources: ["A1"] }, { fact: "A deal is imminent.", sources: ["A2"] }], resolved: [{ question: "Will talks move to Geneva?", how: "They did." }], new_questions: ["Will a deal be signed?"], still_open: [] };

describe("threadsLink", () => {
  it("starts a new thread per story on a first run, with no model call", async () => {
    const { acts, rows, calls, store } = setup();
    expect(await acts.threadsLink(RUN)).toEqual({ plans: [] });
    expect(calls).toHaveLength(0);
    expect(rows("SELECT label, slug, status, first_run_id, last_run_id FROM threads")).toEqual([
      { label: "Iran talks in Geneva", slug: "iran-talks-in-geneva", status: "active", first_run_id: RUN, last_run_id: RUN },
      { label: "EU AI act", slug: "eu-ai-act", status: "active", first_run_id: RUN, last_run_id: RUN },
    ]);
    expect(JSON.parse(store.get(store.find(RUN, THREAD_ASSIGNMENTS)!))).toEqual([{ thread_id: 1, is_new: true, story: "Iran talks in Geneva" }, { thread_id: 2, is_new: true, story: "EU AI act" }]);
    expect(JSON.parse(store.get(store.find(RUN, THREAD_LINKS)!))).toMatchObject({ linker_ok: true, proposed: 0, validated: 0, candidates: [] });
  });

  it("continues a linked thread, starts the rest, and plans synthesis for the continuation", async () => {
    const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }] } });
    const tid = seedThread(s.db);
    expect(await s.acts.threadsLink(RUN)).toEqual({ plans: [{ threadId: tid, articleIds: ["A1", "A2"] }] });
    expect(s.calls.map((c) => c.stage)).toEqual(["link"]);
    expect(s.calls[0]!.prompt).toBe("ACTIVE THREADS:\n  [1] Iran nuclear talks open -> Iran nuclear talks\n\nTODAY'S STORIES:\n  (0) Iran talks in Geneva\n  (1) EU AI act\n\nMap each today-story to a thread id or NEW.");
    expect(s.calls[0]!.options.model).toBe("claude-haiku-4-5-20251001");
    expect(s.calls[0]!.options.outputFormat).toBeUndefined(); // free text: the schema cost a continuation a day
    expect(s.rows("SELECT id, label, last_run_id FROM threads ORDER BY id")).toEqual([{ id: 1, label: "Iran talks in Geneva", last_run_id: RUN }, { id: 2, label: "EU AI act", last_run_id: RUN }]);
    expect(s.rows(`SELECT thread_id, matched_score FROM thread_installments WHERE run_id = ${RUN} ORDER BY id`)).toEqual([{ thread_id: 1, matched_score: 1 }, { thread_id: 2, matched_score: null }]);
    expect(s.usage.map((u) => u.stage)).toEqual(["thread_link"]);
  });

  it("is idempotent: a retried attempt returns the committed identity without a model call or a second thread", async () => {
    const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }] } });
    seedThread(s.db);
    const first = await s.acts.threadsLink(RUN);
    expect(await s.acts.threadsLink(RUN)).toEqual(first);
    expect(s.calls).toHaveLength(1);
    expect(s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 2 }]);
    expect(s.rows(`SELECT COUNT(*) AS n FROM thread_installments WHERE run_id = ${RUN}`)).toEqual([{ n: 2 }]);
  });

  it("two attempts racing on the model commit one identity between them", async () => {
    const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }] }, { links: [{ story: 0, thread: 1 }] }] } });
    seedThread(s.db);
    const [a, b] = await Promise.all([s.acts.threadsLink(RUN), s.acts.threadsLink(RUN)]);
    expect(a).toEqual(b);
    expect(s.calls).toHaveLength(2);
    expect(s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 2 }]);
    expect(s.rows(`SELECT COUNT(*) AS n FROM thread_installments WHERE run_id = ${RUN}`)).toEqual([{ n: 2 }]);
  });

  it("an attempt that fails mid-commit leaves no identity behind", async () => {
    const s = setup();
    s.store.put(RUN, THREAD_LINKS, "{}"); // the commit's own record already taken: its insert fails last
    await expect(s.acts.threadsLink(RUN)).rejects.toThrow(/UNIQUE/);
    expect(s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 0 }]);
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_installments")).toEqual([{ n: 0 }]);
  });

  it("refuses to link a run whose installments exist without their record", async () => {
    const s = setup();
    const tid = seedThread(s.db);
    s.db.prepare("INSERT INTO thread_installments (thread_id, run_id, cluster_story) VALUES (?, ?, 'x')").run(tid, RUN);
    await expect(s.acts.threadsLink(RUN)).rejects.toThrow(/refusing to link again/);
  });

  it("a linker failure retries while attempts remain", async () => {
    const s = setup({ answers: { link: [new Error("overloaded")] }, attempt: 1 });
    seedThread(s.db);
    await expect(s.acts.threadsLink(RUN)).rejects.toThrow(/overloaded/);
    expect(s.rows("SELECT COUNT(*) AS n FROM threads")).toEqual([{ n: 1 }]);
  });

  it("on its last attempt a failed linker falls back to all-new, recorded as linker_ok false", async () => {
    const s = setup({ answers: { link: [{ links: [] }] }, attempt: 3 });
    seedThread(s.db);
    expect(await s.acts.threadsLink(RUN)).toEqual({ plans: [] });
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_LINKS)!))).toMatchObject({ linker_ok: false, stories: [{ outcome: "new" }, { outcome: "new" }] });
  });

  it("refuses a hallucinated thread id and a second claim on one thread", async () => {
    const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: 1 }, { story: 5, thread: 1 }] }] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    const trace = JSON.parse(s.store.get(s.store.find(RUN, THREAD_LINKS)!)) as { proposed: number; validated: number; stories: { refused: string | null }[] };
    expect(trace.stories.map((x) => x.refused)).toEqual([null, "already_claimed"]);
    expect([trace.proposed, trace.validated]).toEqual([3, 2]);
  });

  it("decays a thread that has gone quiet, in the same commit", async () => {
    const s = setup({ config: { dormantAfter: 1 } });
    s.db.prepare("INSERT INTO threads (slug, label, status, first_run_id, last_run_id) VALUES ('old', 'Old', 'active', 297, 297)").run();
    await s.acts.threadsLink(RUN);
    expect(s.rows("SELECT status FROM threads WHERE label = 'Old'")).toEqual([{ status: "dormant" }]);
    expect(s.calls).toHaveLength(0); // not a candidate, so nothing to ask
  });

  it("does nothing when disabled", async () => {
    const s = setup({ config: { enabled: false } });
    expect(await s.acts.threadsLink(RUN)).toEqual({ plans: [], skip: "disabled" });
    expect(await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] })).toMatchObject({ name: THREAD_CONTEXT, sha256: "0".repeat(64) });
    expect(s.store.names(RUN).filter((n) => n.startsWith("thread"))).toEqual([]);
  });
});

async function linked(answers: Partial<Record<Stage, unknown[]>>) {
  const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }], ...answers } });
  seedThread(s.db);
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
    const content = JSON.parse(String(s.rows(`SELECT content FROM thread_installments WHERE thread_id = 1 AND run_id = ${RUN}`)[0]!["content"])) as Record<string, unknown>;
    expect(content).toEqual({ ...installment, whats_new: [installment.whats_new[0]], cited_ids: ["A1", "A2"] });
    expect(s.rows("SELECT question, status, resolved_run_id, raised_run_id FROM thread_questions ORDER BY id")).toEqual([
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
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_questions")).toEqual([{ n: 2 }]);
  });

  it("a retry after the synthesis landed audits that installment instead of paying for another", async () => {
    const s = await linked({ audit: [{ verdicts: [{ id: 1, supported: true }, { id: 2, supported: true }] }] });
    s.store.put(RUN, "thread_synthesis_t1.json", JSON.stringify(installment));
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
    const content = JSON.parse(String(s.rows(`SELECT content FROM thread_installments WHERE thread_id = 1 AND run_id = ${RUN}`)[0]!["content"])) as { whats_new: unknown[] };
    expect(content.whats_new).toHaveLength(2);
  });

  it("a synthesis failure is thrown for the retry policy, leaving nothing applied", async () => {
    const s = await linked({ synthesis: [new Error("overloaded")] });
    await expect(s.acts.threadSynthesis(RUN, s.plan)).rejects.toThrow(/overloaded/);
    expect(s.rows(`SELECT content FROM thread_installments WHERE thread_id = 1 AND run_id = ${RUN}`)).toEqual([{ content: null }]);
  });
});

describe("threadsFinish", () => {
  it("records the phase once and hands the render each continuing story's context", async () => {
    const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }], synthesis: [installment], audit: [new Error("down")] } });
    seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    const outcome = await s.acts.threadSynthesis(RUN, plans[0]!);
    const p = await s.acts.threadsFinish(RUN, { outcomes: [outcome], failures: [] });
    expect(p.name).toBe(THREAD_CONTEXT);
    expect(JSON.parse(s.store.get(p))).toEqual({ "Iran talks in Geneva": { thread_id: 1, day: 3, delta: "Talks resumed in Geneva. A deal is imminent.", url: "https://news.example/thread/1" } });
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_INSTALLMENTS)!))).toEqual([{ thread_id: 1, ...installment, cited_ids: ["A1", "A2"] }]);
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_HEALTH)!))).toEqual({ link: "ok", linker_ok: true, synthesized: 1, audit_failures: 1, failures: [] });
    expect(s.rows("SELECT run_id, threads_synthesized, audit_failures FROM thread_runs")).toEqual([{ run_id: RUN, threads_synthesized: 1, audit_failures: 1 }]);
    expect(await s.acts.threadsFinish(RUN, { outcomes: [outcome], failures: [] })).toEqual(p);
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_runs")).toEqual([{ n: 1 }]);
  });

  it("recounts a resumed Python run's health row but never lowers its audit failures", async () => {
    const s = setup();
    await s.acts.threadsLink(RUN);
    s.db.prepare("INSERT INTO thread_runs (run_id, threads_synthesized, audit_failures) VALUES (?, 4, 2)").run(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    expect(s.rows("SELECT threads_synthesized, audit_failures FROM thread_runs")).toEqual([{ threads_synthesized: 0, audit_failures: 2 }]);
  });

  it("records a failed link and gives the render nothing to find", async () => {
    const s = setup();
    const p = await s.acts.threadsFinish(RUN, { linkError: "linker down", outcomes: [], failures: [] });
    expect(s.store.find(RUN, p.name)).toBeUndefined();
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_HEALTH)!))).toEqual({ link: "failed", error: "linker down" });
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_runs")).toEqual([{ n: 0 }]);
  });
});

describe("a forced re-run", () => {
  const link2 = { links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] };
  it("takes back this run's identity and relinks from the state the run began in (the reviewer's scenario)", async () => {
    const s = setup({ answers: { link: [link2, { links: [{ story: 0, thread: null }] }] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    s.store.replace(RUN, "selected.json", JSON.stringify({ must_know: [{ article_ids: ["A3", "A4"], cluster_index: 1 }], should_know: [] }));
    expect(await s.acts.threadsLink(RUN, true)).toEqual({ plans: [] });
    expect(s.calls.filter((c) => c.stage === "link").at(-1)!.prompt).toContain("[1] Iran nuclear talks open -> Iran nuclear talks\n"); // the arc as it stood before the run
    expect(s.rows(`SELECT thread_id, cluster_story FROM thread_installments WHERE run_id = ${RUN}`)).toEqual([{ thread_id: 3, cluster_story: "EU AI act" }]);
    expect(s.rows("SELECT id, label, last_run_id FROM threads ORDER BY id")).toEqual([{ id: 1, label: "Iran nuclear talks", last_run_id: 299 }, { id: 3, label: "EU AI act", last_run_id: RUN }]);
    expect(s.rows("SELECT run_id, COUNT(*) AS n FROM thread_installments GROUP BY run_id")).toEqual([{ run_id: 298, n: 1 }, { run_id: 299, n: 1 }, { run_id: RUN, n: 1 }]);
    expect(JSON.parse(s.store.get(await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] })))).toEqual({});
    expect(s.store.names(RUN).filter((n) => n.includes(".corrupt."))).toEqual(["thread_assignments.json.corrupt.1", "thread_context.json.corrupt.1", "thread_health.json.corrupt.1", "thread_installments.json.corrupt.1", "thread_links.json.corrupt.1"]);
  });
  it("reopens what this run resolved, drops what it raised, and resynthesizes", async () => {
    const s = setup({ answers: { link: [link2, link2], synthesis: [installment, installment], audit: [{ verdicts: [{ id: 1, supported: true }, { id: 2, supported: true }] }, { verdicts: [{ id: 1, supported: true }, { id: 2, supported: false }] }] } });
    seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    await s.acts.threadSynthesis(RUN, plans[0]!);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    const again = await s.acts.threadsLink(RUN, true);
    expect(s.rows("SELECT question, status FROM thread_questions")).toEqual([{ question: "Will talks move to Geneva?", status: "open" }]);
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_runs")).toEqual([{ n: 0 }]);
    expect(await s.acts.threadSynthesis(RUN, again.plans[0]!)).toEqual({ threadId: 1, auditFailed: false });
    expect(s.calls.map((c) => c.stage)).toEqual(["link", "synthesis", "audit", "link", "synthesis", "audit"]);
    expect(s.rows("SELECT question, status FROM thread_questions ORDER BY id")).toEqual([{ question: "Will talks move to Geneva?", status: "resolved" }, { question: "Will a deal be signed?", status: "open" }]);
  });
  it("leaves another run's rows alone", async () => {
    const s = setup({ answers: { link: [link2, link2] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    const before = s.rows("SELECT * FROM thread_installments WHERE run_id <> 300 ORDER BY id");
    await s.acts.threadsLink(RUN, true);
    expect(s.rows("SELECT * FROM thread_installments WHERE run_id <> 300 ORDER BY id")).toEqual(before);
    expect(s.rows("SELECT question, status FROM thread_questions")).toEqual([{ question: "Will talks move to Geneva?", status: "open" }]);
  });
});

describe("resumes and records", () => {
  it("does not apply again an installment the Python applied before a resume", async () => {
    const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }] } });
    seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    s.db.prepare(`UPDATE thread_installments SET content = ? WHERE thread_id = 1 AND run_id = ${RUN}`).run(JSON.stringify(installment));
    s.db.prepare("INSERT INTO thread_questions (thread_id, question, status, raised_run_id) VALUES (1, 'Will a deal be signed?', 'open', ?)").run(RUN);
    expect(await s.acts.threadSynthesis(RUN, plans[0]!)).toEqual({ threadId: 1, auditFailed: false });
    expect(s.calls.map((c) => c.stage)).toEqual(["link"]);
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_questions")).toEqual([{ n: 2 }]);
  });
  it("plans only from trace entries that carry their assignment's story", () => {
    const trace = { linker_ok: true, proposed: 1, validated: 1, candidates: [], stories: [{ story_index: 0, label: "other story", article_ids: ["A1", "A2"], proposed_thread: 1, refused: null, outcome: "continued" as const }] };
    expect(plansFrom([{ thread_id: 1, is_new: false, story: "Iran" }], trace)).toEqual([]);
    expect(plansFrom([{ thread_id: 1, is_new: false, story: "other story" }], trace)).toEqual([{ threadId: 1, articleIds: ["A1", "A2"] }]);
  });
  it("a failed link then a successful resume: health and context say so", async () => {
    const s = setup();
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [], linkError: "boom" });
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_HEALTH)!))).toMatchObject({ link: "failed" });
    await s.acts.threadsLink(RUN);
    const p = await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_HEALTH)!))).toEqual({ link: "ok", linker_ok: true, synthesized: 0, audit_failures: 0, failures: [] });
    expect(s.store.find(RUN, p.name)).toEqual(p);
  });
  it("a synthesis that failed and then landed on a resume is no longer a failure", async () => {
    const s = setup({ answers: { link: [{ links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] }], synthesis: [installment], audit: [{ verdicts: [{ id: 1, supported: true }, { id: 2, supported: true }] }] } });
    seedThread(s.db);
    const { plans } = await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [{ threadId: 1, error: "overloaded" }] });
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_HEALTH)!))).toMatchObject({ synthesized: 0, failures: [{ threadId: 1, error: "overloaded" }] });
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_CONTEXT)!))).toMatchObject({ "Iran talks in Geneva": { delta: "" } });
    const outcome = await s.acts.threadSynthesis(RUN, plans[0]!);
    const p = await s.acts.threadsFinish(RUN, { outcomes: [outcome], failures: [] });
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_HEALTH)!))).toMatchObject({ synthesized: 1, failures: [] });
    expect(JSON.parse(s.store.get(p))).toMatchObject({ "Iran talks in Geneva": { delta: "Talks resumed in Geneva. A deal is imminent." } });
    expect(s.rows("SELECT threads_synthesized FROM thread_runs")).toEqual([{ threads_synthesized: 1 }]);
  });
  it("a phase that ran past its bound is recorded and leaves no context for the render", async () => {
    const s = setup();
    await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    const p = await s.acts.threadsFinish(RUN, { outcomes: [], failures: [], timedOut: true });
    expect(s.store.find(RUN, p.name)).toBeUndefined();
    expect(JSON.parse(s.store.get(s.store.find(RUN, THREAD_HEALTH)!))).toMatchObject({ timed_out: true });
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
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    s.db.prepare("INSERT INTO thread_installments (thread_id, run_id, cluster_story) VALUES (2, 301, 'EU AI act, day two')").run();
    const before = s.rows("SELECT * FROM thread_installments ORDER BY id");
    await expect(s.acts.threadsLink(RUN, true)).rejects.toThrow(/later run\(s\) 301 build on run 300's threads/);
    expect(s.rows("SELECT * FROM thread_installments ORDER BY id")).toEqual(before);
    expect(s.store.names(RUN).filter((n) => n.includes(".corrupt."))).toEqual([]);
  });
  it("refuses when a later run continued a thread this run continued", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    s.db.prepare("INSERT INTO thread_installments (thread_id, run_id, cluster_story) VALUES (1, 301, 'Iran talks, day three')").run();
    await expect(s.acts.threadsLink(RUN, true)).rejects.toThrow(/301/);
  });
  it("refuses when a later run resolved a question this run raised", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    s.db.prepare("INSERT INTO thread_questions (thread_id, question, status, raised_run_id, resolved_run_id) VALUES (1, 'Asked in 300', 'resolved', 300, 301)").run();
    await expect(s.acts.threadsLink(RUN, true)).rejects.toThrow(/301/);
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_questions WHERE raised_run_id = 300")).toEqual([{ n: 1 }]);
  });
  it("a retried forced link in the same execution does not undo what its first attempt committed", async () => {
    const s = setup({ answers: { link: [link2, link2, link2] }, execution: () => "exec-1" });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    const first = await s.acts.threadsLink(RUN, true);
    const ids = s.rows("SELECT id FROM threads ORDER BY id");
    expect(await s.acts.threadsLink(RUN, true)).toEqual(first);
    expect(s.rows("SELECT id FROM threads ORDER BY id")).toEqual(ids);
    expect(s.calls.filter((c) => c.stage === "link")).toHaveLength(2);
  });
});

describe("an issue that is not sent", () => {
  const link2 = { links: [{ story: 0, thread: 1 }, { story: 1, thread: null }] };
  it("takes back its thread writes, so the web tier never shows an installment nobody was sent", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    const before = s.rows("SELECT * FROM thread_installments ORDER BY id");
    await s.acts.threadsLink(RUN);
    await s.acts.threadsFinish(RUN, { outcomes: [], failures: [] });
    expect(await s.acts.threadsRetract(RUN)).toEqual({ retracted: true });
    expect(s.rows("SELECT * FROM thread_installments ORDER BY id")).toEqual(before);
    expect(s.rows("SELECT id, label, last_run_id FROM threads ORDER BY id")).toEqual([{ id: 1, label: "Iran nuclear talks", last_run_id: 299 }]);
    expect(await s.acts.threadsRetract(RUN)).toEqual({ retracted: true }); // idempotent
  });
  it.each(["sent", "queued", "claimed exec-1"])("declines for a run whose day already has a %s broadcast: a resumed, delivered issue keeps its threads", async (status) => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    s.db.exec("CREATE TABLE IF NOT EXISTS digests (date TEXT PRIMARY KEY, run_id INTEGER, html TEXT, broadcast_id TEXT, broadcast_status TEXT, broadcast_run_id INTEGER)");
    s.db.prepare("INSERT INTO digests (date, run_id, broadcast_id, broadcast_status) SELECT date(run_at), id, 'b1', ? FROM digest_runs WHERE id = ?").run(status, RUN);
    const before = s.rows("SELECT * FROM thread_installments ORDER BY id");
    expect(await s.acts.threadsRetract(RUN)).toMatchObject({ retracted: false });
    expect(s.rows("SELECT * FROM thread_installments ORDER BY id")).toEqual(before);
  });
  it("declines, rather than fails, when a later run already builds on it", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    await s.acts.threadsLink(RUN);
    s.db.prepare("INSERT INTO thread_installments (thread_id, run_id, cluster_story) VALUES (1, 301, 'Iran talks, day three')").run();
    const before = s.rows("SELECT * FROM thread_installments ORDER BY id");
    expect(await s.acts.threadsRetract(RUN)).toEqual({ retracted: false, reason: "later run(s) 301 build on it" });
    expect(s.rows("SELECT * FROM thread_installments ORDER BY id")).toEqual(before);
  });
});

function fail(db: DatabaseSync, id: number, status = "failed", runAt?: string): void {
  db.prepare("UPDATE digest_runs SET status = ?, completed_at = NULL WHERE id = ?").run(status, id);
  if (runAt) db.prepare("UPDATE digest_runs SET run_at = ? WHERE id = ?").run(runAt, id);
}
function withDigests(db: DatabaseSync): void {
  db.exec("CREATE TABLE IF NOT EXISTS digests (date TEXT PRIMARY KEY, run_id INTEGER, html TEXT, broadcast_id TEXT, broadcast_status TEXT, broadcast_run_id INTEGER)");
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
  const earlierRows = (s: ReturnType<typeof setup>) => s.rows(`SELECT thread_id, cluster_story FROM thread_installments WHERE run_id = ${EARLIER}`);

  it("the resume horizon is the workflow's run timeout", () => {
    expect(`${RESUME_HORIZON_HOURS} hours`).toBe(WORKFLOW_RUN_TIMEOUT);
  });

  it("takes back a failed run's installments before linking, so neither the web tier nor the linker sees them", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    fail(s.db, EARLIER);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(earlierRows(s)).toEqual([]);
    expect(s.rows(`SELECT COUNT(*) AS n FROM thread_questions WHERE raised_run_id = ${EARLIER}`)).toEqual([{ n: 0 }]);
    // The linker was shown the thread as run 298 left it, not as the failed run relabelled it.
    expect(s.calls[0]!.prompt).toContain("ACTIVE THREADS:\n  [1] Iran nuclear talks open\n\n");
  });

  it("takes back a chain of failed runs, the later first, so the earlier has no dependent left", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    fail(s.db, 298);
    fail(s.db, EARLIER);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(s.rows("SELECT COUNT(*) AS n FROM thread_installments WHERE run_id IN (298, 299)")).toEqual([{ n: 0 }]);
  });

  it("takes back a failed run whose digest was saved but never sent", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    fail(s.db, EARLIER, "failed", "2026-09-17 10:25:40");
    withDigests(s.db);
    s.db.prepare("INSERT INTO digests (date, run_id, html) VALUES ('2026-09-17', ?, '')").run(EARLIER);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(earlierRows(s)).toEqual([]);
  });

  // The reviewer's case: 299 fails, a forced 300 runs the same day, inside the horizon, and sends.
  // 300 supersedes 299, so 300 takes 299's writes back before it links and links on the state 299 began in.
  it("a later run of the same day takes the failed run's writes back before it links, inside the horizon", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    const recent = new Date(Date.now() - 60 * 60 * 1000).toISOString().replace("T", " ").slice(0, 19);
    fail(s.db, EARLIER, "failed", recent);
    s.db.prepare("UPDATE digest_runs SET run_at = ? WHERE id = ?").run(recent, RUN);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(earlierRows(s)).toEqual([]);
    expect(s.calls[0]!.prompt).toContain("ACTIVE THREADS:\n  [1] Iran nuclear talks open\n\n");
    withDigests(s.db);
    s.db.prepare("INSERT INTO digests (date, run_id, html, broadcast_id, broadcast_status) VALUES (date(?), ?, '', 'b1', 'sent')").run(recent, RUN);
    s.db.prepare("UPDATE digest_runs SET status = 'completed', completed_at = run_at WHERE id = ?").run(RUN);
    expect(s.rows(`SELECT COUNT(*) AS n FROM thread_installments WHERE run_id = ${RUN}`)).toEqual([{ n: 2 }]);
  });

  it("judges delivery by sender: a broadcast a later, completed run sent does not keep the failed run's writes", async () => {
    const s = setup();
    seedThread(s.db);
    fail(s.db, EARLIER);
    s.db.prepare("UPDATE digest_runs SET status = 'completed', completed_at = run_at WHERE id = ?").run(RUN);
    withDigests(s.db);
    s.db.prepare("INSERT INTO digests (date, run_id, html, broadcast_id, broadcast_status, broadcast_run_id) VALUES ('2026-09-18', ?, '', 'b1', 'sent', ?)").run(RUN, RUN);
    await quietly(() => Promise.resolve(retractAbandoned(s.db, 301)));
    expect(earlierRows(s)).toEqual([]);
  });

  it("keeps a failed run a resume has taken up again, however old", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    fail(s.db, EARLIER, "failed", "2026-09-17 10:25:40");
    const before = earlierRows(s);
    s.store.put(EARLIER, "sources.csv", "id,name,bias,factuality,perspective\nf,F,center,high,global\n");
    const sourcesFile = join(mkdtempSync(join(tmpdir(), "src-")), "sources.json");
    writeFileSync(sourcesFile, "[]");
    const runs = runActivities({ store: s.store, dbPath: s.db.location()!, sourcesFile });
    const env = new MockActivityEnvironment({ workflowExecution: { workflowId: "digest-2026-09-17", runId: "exec-resume" } });
    await env.run(() => runs.startRun({ runDate: "2026-09-17", resumeRun: EARLIER }));
    await quietly(() => s.acts.threadsLink(RUN));
    expect(earlierRows(s)).toEqual(before);
    expect(before).toHaveLength(1);
  });

  // Negative controls: each is a retracting case above with one condition flipped.
  // The reviewer's case: 299 sends, then fails after the send; a forced 300 the same day saves over
  // the row (run_id 300), skips the send as already accepted, and completes. Readers got 299's issue.
  it("keeps a failed run that sent its issue, though a later completed run saved over the day's row", async () => {
    const s = setup();
    seedThread(s.db);
    const before = earlierRows(s);
    fail(s.db, EARLIER);
    s.db.prepare("UPDATE digest_runs SET status = 'completed', completed_at = run_at WHERE id = ?").run(RUN);
    withDigests(s.db);
    s.db.prepare("INSERT INTO digests (date, run_id, html, broadcast_id, broadcast_status, broadcast_run_id) VALUES ('2026-09-18', ?, '', 'b1', 'sent', ?)").run(RUN, EARLIER);
    const { logged } = await quietly(() => Promise.resolve(retractAbandoned(s.db, 301)));
    expect(earlierRows(s)).toEqual(before);
    expect(logged).toContainEqual(expect.objectContaining({ runId: EARLIER, error: "unsent issue's thread writes kept" }));
  });

  it("keeps a failed run whose day's broadcast names no sender (claimed before the sender was recorded, or Python's)", async () => {
    const s = setup();
    seedThread(s.db);
    const before = earlierRows(s);
    fail(s.db, EARLIER);
    s.db.prepare("UPDATE digest_runs SET status = 'completed', completed_at = run_at WHERE id = ?").run(RUN);
    withDigests(s.db);
    s.db.prepare("INSERT INTO digests (date, run_id, html, broadcast_id, broadcast_status) VALUES ('2026-09-18', ?, '', 'b1', 'sent')").run(RUN);
    await quietly(() => Promise.resolve(retractAbandoned(s.db, 301)));
    expect(earlierRows(s)).toEqual(before);
  });

  it("keeps a failed run whose day's broadcast a run that did not complete sent", async () => {
    const s = setup();
    seedThread(s.db);
    const before = earlierRows(s);
    fail(s.db, EARLIER);
    fail(s.db, RUN);
    withDigests(s.db);
    s.db.prepare("INSERT INTO digests (date, run_id, html, broadcast_id, broadcast_status, broadcast_run_id) VALUES ('2026-09-18', ?, '', 'b1', 'sent', ?)").run(RUN, RUN);
    await quietly(() => Promise.resolve(retractAbandoned(s.db, 301)));
    expect(earlierRows(s)).toEqual(before);
  });

  it("keeps a completed run's installments", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    const before = earlierRows(s);
    await quietly(() => s.acts.threadsLink(RUN));
    expect(before).toHaveLength(1);
    expect(earlierRows(s)).toEqual(before);
  });

  it("keeps a failed run still inside the resume horizon: a resume needs them", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    const before = earlierRows(s);
    fail(s.db, EARLIER, "failed", new Date(Date.now() - 60 * 60 * 1000).toISOString().replace("T", " ").slice(0, 19));
    await quietly(() => s.acts.threadsLink(RUN));
    expect(earlierRows(s)).toEqual(before);
  });

  it.each([
    ["sent", "b1"],
    ["claimed 2026-09-17T10:00:00Z x", null],
    ["created", "b1"], // a draft: its send may have been accepted
  ])("keeps a failed run whose day's broadcast is %s", async (status, id) => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    const before = earlierRows(s);
    fail(s.db, EARLIER, "failed", "2026-09-17 10:25:40");
    withDigests(s.db);
    s.db.prepare("INSERT INTO digests (date, run_id, html, broadcast_id, broadcast_status) VALUES ('2026-09-17', ?, '', ?, ?)").run(EARLIER, id, status);
    const { logged } = await quietly(() => s.acts.threadsLink(RUN));
    expect(earlierRows(s)).toEqual(before);
    expect(logged).toContainEqual(expect.objectContaining({ runId: EARLIER, error: "unsent issue's thread writes kept" }));
  });

  it("declines, and says so, when a later delivered run builds on the failed one", async () => {
    const s = setup({ answers: { link: [link2] } });
    seedThread(s.db);
    fail(s.db, 298);
    const before = s.rows("SELECT * FROM thread_installments WHERE run_id = 298");
    const { logged } = await quietly(() => s.acts.threadsLink(RUN));
    expect(s.rows("SELECT * FROM thread_installments WHERE run_id = 298")).toEqual(before);
    expect(logged).toContainEqual(expect.objectContaining({ runId: 298, reason: "later run(s) 299 build on it" }));
  });
});
