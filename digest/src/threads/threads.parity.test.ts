import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import { THREAD_CONTEXT } from "../activities/index.js";
import { THREAD_ASSIGNMENTS, THREAD_INSTALLMENTS, THREAD_LINKS, threadsActivities } from "../activities/threads.js";
import { scrubUrls } from "../contracts/ids.js";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";

// Host-only: bin/threads-oracle RUN... writes, per archived run, the thread tables rolled back to just
// before it (pre.db) and what the Python linker, synthesis, audit and late binding did from there with
// the model calls answered from the run's archive (expected.json). The TypeScript starts from the same
// state, imported into Postgres (bin/import-legacy pre.db into the database THREADS_PARITY_DB_PREFIX
// followed by the case's directory name), with the same answers, and every prompt, artifact, context
// and row must come out equal. Python's decay status and slug, and thread_runs, have no counterpart.
const REPO = new URL("../../../", import.meta.url).pathname;
const ORACLE = process.env["THREADS_ORACLE"] ?? `${REPO}data/replay/threads-oracle`;
const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const DOMAIN = "news-digest.seanfloyd.dev";
const PREFIX = process.env["THREADS_PARITY_DB_PREFIX"];

interface Expected {
  run: number;
  config: { dormant_after: number; latebind_threshold: number; latebind_max_extra: number };
  answers: { link: unknown; synthesis: Record<string, unknown> };
  prompts: { link: string | null; synthesis: Record<string, string>; audit: Record<string, string[]> };
  assignments: unknown;
  trace: unknown;
  installments: unknown;
  contexts: unknown;
  tables: { threads: unknown[]; installments: { content: unknown }[]; questions: unknown[]; thread_runs: unknown[] };
}

const EMPTY = { whats_new: [], resolved: [], new_questions: [], still_open: [] };
const cases = PREFIX && existsSync(ORACLE) ? readdirSync(ORACLE).filter((d) => existsSync(join(ORACLE, d, "expected.json"))).toSorted() : [];

// Answers the Python's oracle gave, found by what the TypeScript asked: a synthesis prompt the Python
// never saw has no answer, so a prompt that drifts fails here as well as in the prompt comparison.
function oracleQuery(exp: Expected, seen: { link: string[]; synthesis: Map<string, string>; audit: Map<string, string[]> }, current: { thread?: string }): SdkQuery {
  const bySynthPrompt = new Map(Object.entries(exp.prompts.synthesis).map(([tid, p]) => [scrubUrls(p), tid]));
  return (({ prompt, options }: { prompt: string; options: Options }) => {
    const system = typeof options.systemPrompt === "string" ? options.systemPrompt : "";
    let answer: unknown;
    if (system.includes("You track ongoing news stories")) {
      seen.link.push(prompt);
      answer = exp.answers.link;
    } else if (system.includes("EVOLVING daily digest thread")) {
      const tid = bySynthPrompt.get(prompt) ?? `unmatched:${seen.synthesis.size}`;
      current.thread = tid;
      seen.synthesis.set(tid, prompt);
      answer = exp.answers.synthesis[tid] ?? EMPTY;
    } else {
      const tid = current.thread ?? "none";
      seen.audit.set(tid, [...(seen.audit.get(tid) ?? []), prompt]);
      const n = prompt.split("\nCITED SOURCE(S):\n").length - 1;
      answer = { verdicts: Array.from({ length: n }, (_, i) => ({ id: i + 1, supported: i + 1 !== 2 })) };
    }
    return (async function* () {
      await Promise.resolve();
      yield { type: "result", subtype: "success", result: JSON.stringify(answer), structured_output: answer, total_cost_usd: 0, usage: {}, duration_ms: 1, is_error: false, num_turns: 1, session_id: "s" } as unknown as SDKMessage;
    })();
  }) as unknown as SdkQuery;
}

describe("threads parity with the Python on archived runs", () => {
  if (cases.length === 0) it.skip(`no oracle in ${ORACLE}, or THREADS_PARITY_DB_PREFIX unset: generate it with DB=<prod clone> bin/threads-oracle 300 301 302 303 304 and import each pre.db`, () => undefined);
  it.each(cases)("%s", async (dir) => {
    const exp = JSON.parse(readFileSync(join(ORACLE, dir, "expected.json"), "utf8")) as Expected;
    const url = `${PREFIX!}${dir}`;
    const store = new ArtifactStore(url);
    const seen = { link: [] as string[], synthesis: new Map<string, string>(), audit: new Map<string, string[]>() };
    const acts = threadsActivities({
      store,
      dbUrl: url,
      agentsDir: AGENTS,
      maxAttempts: 3,
      config: { enabled: true, dormantAfter: exp.config.dormant_after, latebind: { threshold: exp.config.latebind_threshold, maxExtra: exp.config.latebind_max_extra }, digestDomain: DOMAIN },
      query: oracleQuery(exp, seen, {}),
    });
    const run = exp.run;
    const { plans } = await acts.threadsLink(run);
    const outcomes = [];
    for (const p of plans) outcomes.push(await acts.threadSynthesis(run, p)); // in order, as the Python loops
    const ctx = await acts.threadsFinish(run, { outcomes, failures: [] });

    const json = async (name: string) => JSON.parse(await store.content(run, name)) as unknown;
    expect(seen.link.length ? seen.link[0] : null).toBe(exp.prompts.link === null ? null : scrubUrls(exp.prompts.link));
    expect(Object.fromEntries(seen.synthesis)).toEqual(Object.fromEntries(Object.entries(exp.prompts.synthesis).map(([k, v]) => [k, scrubUrls(v)])));
    expect(Object.fromEntries(seen.audit)).toEqual(Object.fromEntries(Object.entries(exp.prompts.audit).map(([k, v]) => [k, v.map(scrubUrls)])));
    expect(await json(THREAD_ASSIGNMENTS)).toEqual(exp.assignments);
    expect(await json(THREAD_LINKS)).toEqual(exp.trace);
    expect(await json(THREAD_INSTALLMENTS)).toEqual(exp.installments);
    expect(JSON.parse(await store.get(ctx))).toEqual(exp.contexts);
    expect(await json(THREAD_CONTEXT)).toEqual(exp.contexts);

    const db = openDb(url);
    const installments = (await db.all<Record<string, unknown>>("SELECT thread_id, run_id, label AS cluster_story, CASE WHEN is_continuation THEN 1.0 END AS matched_score, content FROM thread_updates WHERE run_id = $1 ORDER BY id", [run])).map((r) => ({ ...r, content: typeof r["content"] === "string" ? (JSON.parse(r["content"]) as unknown) : null }));
    const threads = await db.all(
      `SELECT t.id, (SELECT label FROM thread_updates i WHERE i.thread_id = t.id ORDER BY run_id DESC, id DESC LIMIT 1) AS label,
              t.created_run_id AS first_run_id, (SELECT max(run_id) FROM thread_updates i WHERE i.thread_id = t.id) AS last_run_id, t.merged_into_id AS merged_into
       FROM threads t ORDER BY t.id`,
    );
    const questions = await db.all(
      `SELECT q.thread_id, q.question, CASE WHEN r.question_id IS NULL THEN 'open' ELSE 'resolved' END AS status, q.raised_run_id, r.resolved_run_id, r.answer AS resolved_how
       FROM thread_questions q LEFT JOIN thread_question_resolutions r ON r.question_id = q.id
       WHERE q.raised_run_id = $1 OR r.resolved_run_id = $1 ORDER BY q.thread_id, q.question COLLATE "C", q.raised_run_id`,
      [run],
    );
    const legacyThreads = (exp.tables.threads as Record<string, unknown>[]).map(({ slug: _slug, status: _status, ...t }) => t);
    expect({ threads, installments, questions }).toEqual({ threads: legacyThreads, installments: exp.tables.installments, questions: exp.tables.questions });
  });
});
