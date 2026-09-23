import { copyFileSync, existsSync, mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { describe, expect, it } from "vitest";
import { THREAD_CONTEXT } from "../activities/index.js";
import { THREAD_ASSIGNMENTS, THREAD_INSTALLMENTS, THREAD_LINKS, threadsActivities } from "../activities/threads.js";
import { scrubUrls } from "../contracts/ids.js";
import type { SdkQuery } from "../runner/run-stage.js";
import { ArtifactStore } from "../store/artifacts.js";

// Host-only: bin/threads-oracle RUN... writes, per archived run, the thread tables rolled back to just
// before it (pre.db) and what the Python linker, synthesis, audit and late binding did from there with
// the model calls answered from the run's archive (expected.json). The TypeScript starts from the same
// database with the same answers, and every prompt, artifact, context and row must come out equal.
const REPO = new URL("../../../", import.meta.url).pathname;
const ORACLE = process.env["THREADS_ORACLE"] ?? `${REPO}data/replay/threads-oracle`;
const AGENTS = new URL("../../agents/", import.meta.url).pathname;
const DOMAIN = "news-digest.seanfloyd.dev";

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
const cases = existsSync(ORACLE) ? readdirSync(ORACLE).filter((d) => existsSync(join(ORACLE, d, "expected.json"))).toSorted() : [];

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

describe.skipIf(cases.length === 0)("threads parity with the Python on archived runs", () => {
  it.each(cases)("%s", async (dir) => {
    const exp = JSON.parse(readFileSync(join(ORACLE, dir, "expected.json"), "utf8")) as Expected;
    const path = join(mkdtempSync(join(tmpdir(), "threads-parity-")), "digest.db");
    copyFileSync(join(ORACLE, dir, "pre.db"), path);
    const store = new ArtifactStore(path);
    const seen = { link: [] as string[], synthesis: new Map<string, string>(), audit: new Map<string, string[]>() };
    const acts = threadsActivities({
      store,
      dbPath: path,
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

    const json = (name: string) => JSON.parse(store.get(store.find(run, name)!)) as unknown;
    expect(seen.link.length ? seen.link[0] : null).toBe(exp.prompts.link === null ? null : scrubUrls(exp.prompts.link));
    expect(Object.fromEntries(seen.synthesis)).toEqual(Object.fromEntries(Object.entries(exp.prompts.synthesis).map(([k, v]) => [k, scrubUrls(v)])));
    expect(Object.fromEntries(seen.audit)).toEqual(Object.fromEntries(Object.entries(exp.prompts.audit).map(([k, v]) => [k, v.map(scrubUrls)])));
    expect(json(THREAD_ASSIGNMENTS)).toEqual(exp.assignments);
    expect(json(THREAD_LINKS)).toEqual(exp.trace);
    expect(json(THREAD_INSTALLMENTS)).toEqual(exp.installments);
    expect(JSON.parse(store.get(ctx))).toEqual(exp.contexts);
    expect(json(THREAD_CONTEXT)).toEqual(exp.contexts);

    const db = new DatabaseSync(path, { readOnly: true });
    const rows = (sql: string, ...args: number[]) => db.prepare(sql).all(...args) as Record<string, unknown>[];
    const installments = rows("SELECT thread_id, run_id, cluster_story, matched_score, content FROM thread_installments WHERE run_id = ? ORDER BY id", run).map((r) => ({ ...r, content: typeof r["content"] === "string" ? (JSON.parse(r["content"]) as unknown) : null }));
    expect({
      threads: rows("SELECT id, slug, label, status, first_run_id, last_run_id, merged_into FROM threads ORDER BY id"),
      installments,
      questions: rows("SELECT thread_id, question, status, raised_run_id, resolved_run_id, resolved_how FROM thread_questions WHERE raised_run_id = ? OR resolved_run_id = ? ORDER BY thread_id, question, raised_run_id", run, run),
      thread_runs: rows("SELECT run_id, threads_synthesized, audit_failures FROM thread_runs WHERE run_id = ?", run),
    }).toEqual(exp.tables);
    db.close();
  });
});
