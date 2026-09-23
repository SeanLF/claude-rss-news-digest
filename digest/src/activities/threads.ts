import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { DatabaseSync } from "node:sqlite";
import { Context } from "@temporalio/activity";
import { ApplicationFailure } from "@temporalio/common";
import { z } from "zod";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import type { UsageRow } from "../store/usage.js";
import { assignThreads, linkPrompt, LinksSchema, selectedLabels, validateLinks, type Assignment, type LinkHealth, type LinkTrace } from "../threads/link.js";
import { ThreadStore, type RenderContext } from "../threads/store.js";
import { answerFor, applyInstallment, auditPrompt, auditReask, expandNeighbourhood, InstallmentSchema, synthesisPrompt, VerdictsSchema, type Art, type Installment } from "../threads/synthesis.js";
import { loadArticles } from "./cluster.js";
import { THREAD_CONTEXT, type ThreadOutcome, type ThreadPlan, type ThreadsLinked, type ThreadsReport } from "./index.js";

// THREADS (run.py::_process_story_threads): link each selected story to a continuing thread or a
// new one, synthesize and audit today's installment for each continuing thread, then hand the
// render its thread context. Best-effort as in production: the workflow settles every failure here
// and renders without the garnish, and the failure is written to thread_health.json.
//
// Idempotency is the risk the Python never had: it commits identity row by row, so a retried
// attempt would link today's stories to threads the failed attempt created. Here each unit of
// identity commits in ONE transaction with the artifact that records it, on one connection:
// identity with thread_assignments.json, each installment with its thread_audit_tNNN.json, the
// run's health row with thread_context.json. An attempt that finds the record returns it; one that
// does not finds no partial identity either, because the transaction rolled it back.

export const THREAD_ASSIGNMENTS = "thread_assignments.json";
export const THREAD_LINKS = "thread_links.json";
export const THREAD_INSTALLMENTS = "thread_installments.json";
export const THREAD_HEALTH = "thread_health.json";
const synthName = (tid: number) => `thread_synthesis_t${tid}.json`;
const auditName = (tid: number) => `thread_audit_t${tid}.json`;

// Production's per-call bounds (threads.link_threads, thread_synthesis._run_sonnet).
const LINK_TIMEOUT_MS = 300_000;
const SONNET_TIMEOUT_MS = 300_000;
const MIN_ARTICLES = 2;

export interface ThreadsConfig {
  enabled: boolean;
  dormantAfter: number;
  latebind: { threshold: number; maxExtra: number } | null;
  digestDomain: string;
}
const flag = (v: string | undefined, dflt: boolean) => (v === undefined ? dflt : ["1", "true", "yes"].includes(v.toLowerCase()));
// config.py's THREAD_* names. The defaults are production's settings (terraform.tfvars turns both
// on), not config.py's pre-launch "off": the TypeScript pipeline replaces the production run.
export function threadsConfigFrom(env: NodeJS.ProcessEnv): ThreadsConfig {
  return {
    enabled: flag(env["THREADS_ENABLED"], true),
    dormantAfter: Number(env["THREAD_DORMANT_AFTER"] ?? 3),
    latebind: flag(env["THREAD_LATEBIND"], true) ? { threshold: Number(env["THREAD_LATEBIND_THRESHOLD"] ?? 0.35), maxExtra: Number(env["THREAD_LATEBIND_MAX_EXTRA"] ?? 12) } : null,
    digestDomain: env["DIGEST_DOMAIN"] ?? "",
  };
}

export interface ThreadsDeps {
  store: ArtifactStore;
  dbPath: string;
  agentsDir: string;
  config: ThreadsConfig;
  maxAttempts: number;
  query?: SdkQuery;
  heartbeat?: () => void;
  signal?: () => AbortSignal | undefined;
  onUsage?: (row: UsageRow) => void;
  attempt?: () => number;
}

function currentAttempt(): number {
  try {
    return Context.current().info.attempt;
  } catch {
    return Number.POSITIVE_INFINITY; // outside an activity (tests, CLIs): every attempt is the last
  }
}

// digest.thread_url: absolute when the domain is known, else site-relative.
export const threadUrl = (domain: string, id: number): string => (domain ? `https://${domain}/thread/${id}` : `/thread/${id}`);

// An artifact written inside the caller's transaction, on the caller's connection. The unique
// (run_id, artifact_name) index makes a second writer's commit fail rather than duplicate.
function putIn(db: DatabaseSync, runId: number, name: string, content: string): void {
  db.prepare("INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)").run(runId, name, content);
}
function artifactIn(db: DatabaseSync, runId: number, name: string): string | undefined {
  return (db.prepare("SELECT content FROM run_artifacts WHERE run_id = ? AND artifact_name = ?").get(runId, name) as { content: string } | undefined)?.content;
}

function withDb<T>(path: string, fn: (db: DatabaseSync) => T | Promise<T>): Promise<T> {
  const db = openDb(path);
  return Promise.resolve()
    .then(() => fn(db))
    .finally(() => db.close());
}

function articles(store: ArtifactStore, runId: number): Map<string, Art> {
  const arts = new Map<string, Art>();
  for (const a of loadArticles(store, runId)) arts.set(a.article_id, { title: a.title, summary: a.summary });
  return arts;
}

// thread_assignments.json keeps the Python's shape; the article ids ride in thread_links.json's
// story trace, index-aligned, which a resumed Python run also carries.
interface StoredAssignment { thread_id: number; is_new: boolean; story: string }
function plansFrom(assignments: StoredAssignment[], trace: LinkTrace | undefined): ThreadPlan[] {
  return assignments.flatMap((a, i) => {
    const st = trace?.stories[i];
    const ids = st?.label === a.story ? st.article_ids : [];
    return !a.is_new && ids.length >= MIN_ARTICLES ? [{ threadId: a.thread_id, articleIds: ids }] : [];
  });
}

export function threadsActivities(deps: ThreadsDeps) {
  const { store, config } = deps;
  const attempt = deps.attempt ?? currentAttempt;
  const signal = (ms: number): AbortSignal => {
    const s = deps.signal?.();
    return s ? AbortSignal.any([s, AbortSignal.timeout(ms)]) : AbortSignal.timeout(ms);
  };
  const spec = (name: string) => parseAgentSpec(readFileSync(join(deps.agentsDir, `${name}.md`), "utf8"));
  const run = async (name: string, runId: number, stage: string, prompt: string, schema: z.ZodType, ms: number, detail: Record<string, unknown> = {}) => {
    const s = spec(name);
    const clean = scrubUrls(prompt);
    assertNoUrls(clean); // the invariant, checked where text leaves code (spec §1)
    deps.heartbeat?.();
    const r = await runStage(s, { userMessage: clean, inputDir: tmpdir() }, {
      today: store.runDate(runId),
      outputSchema: z.toJSONSchema(schema, { target: "draft-07" }),
      signal: signal(ms),
      ...(deps.query ? { query: deps.query } : {}),
      ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}),
    });
    deps.heartbeat?.();
    deps.onUsage?.({ model: s.model, thinking: s.thinking, tokens: r.usage, stage, runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns, ...detail });
    return { structured: r.structured, prompt: clean };
  };

  // The linker's answer, or null when it failed on the last attempt: production's fallback, every
  // story a new thread, recorded as linker_ok false. Earlier attempts throw for a fresh sample.
  async function link(runId: number, active: ReturnType<ThreadStore["activeThreads"]>, labels: string[]): Promise<{ mapping: (number | null)[]; health: LinkHealth }> {
    if (!active.length || !labels.length) return { mapping: labels.map(() => null), health: { ok: true, proposed: 0, validated: 0 } };
    try {
      const { structured } = await run("thread-link", runId, "thread_link", linkPrompt(active, labels), LinksSchema, LINK_TIMEOUT_MS);
      const links = LinksSchema.parse(structured);
      if (!links.links.length) throw new Error("the linker returned no links");
      return validateLinks(links, active, labels.length);
    } catch (e) {
      if (deps.signal?.()?.aborted) throw e;
      if (attempt() < deps.maxAttempts) throw e;
      console.error(JSON.stringify({ stage: "threads", runId, error: "linker failed on its last attempt; every story starts a new thread", detail: String(e) }));
      return { mapping: labels.map(() => null), health: { ok: false, proposed: 0, validated: 0 } };
    }
  }

  return {
    threadsLink: (runId: number): Promise<ThreadsLinked> =>
      withDb(deps.dbPath, async (db) => {
        if (!config.enabled) return { plans: [], skip: "disabled" as const };
        const ts = new ThreadStore(db);
        const read = () => {
          const a = artifactIn(db, runId, THREAD_ASSIGNMENTS);
          const t = artifactIn(db, runId, THREAD_LINKS);
          return a === undefined ? undefined : plansFrom(JSON.parse(a) as StoredAssignment[], t === undefined ? undefined : (JSON.parse(t) as LinkTrace));
        };
        const done = read();
        if (done) return { plans: done };
        if (ts.runInstallments(runId) > 0)
          throw ApplicationFailure.nonRetryable(`run ${runId} has thread installments but no ${THREAD_ASSIGNMENTS}; refusing to link again and duplicate them`, "ThreadIdentityUnrecorded");
        const need = (name: string) => {
          const p = store.find(runId, name);
          if (!p) throw ApplicationFailure.nonRetryable(`run ${runId} has no ${name}`, "MissingInput");
          return JSON.parse(store.get(p)) as unknown;
        };
        const stories = selectedLabels(need("clusters.json"), need("selected.json"));
        // decay_threads only retires what the active query already excludes, so the candidates
        // read before it are the candidates after it, and the decay can commit with the identity.
        const active = ts.activeThreads(runId, config.dormantAfter);
        const { mapping, health } = await link(runId, active, stories.map((s) => s.story));
        const committed = ts.transaction(() => {
          const again = read();
          if (again) return again; // another attempt committed while this one waited on the model
          ts.decayThreads(runId, config.dormantAfter);
          const { assignments, trace } = assignThreads(ts, stories, runId, active, mapping, health);
          putIn(db, runId, THREAD_LINKS, JSON.stringify(trace, null, 2));
          putIn(db, runId, THREAD_ASSIGNMENTS, JSON.stringify(assignments.map((a: Assignment) => ({ thread_id: a.thread_id, is_new: a.is_new, story: a.story })), null, 2));
          return plansFrom(assignments, trace);
        });
        console.log(JSON.stringify({ stage: "threads-link", runId, stories: stories.length, candidates: active.length, linkerOk: health.ok, proposed: health.proposed, validated: health.validated, toSynthesize: committed.length }));
        return { plans: committed };
      }),

    threadSynthesis: (runId: number, plan: ThreadPlan): Promise<ThreadOutcome> =>
      withDb(deps.dbPath, async (db) => {
        const ts = new ThreadStore(db);
        const tid = plan.threadId;
        const recorded = () => {
          const a = artifactIn(db, runId, auditName(tid));
          return a === undefined ? undefined : { threadId: tid, auditFailed: (JSON.parse(a) as { audit_failed: boolean }).audit_failed };
        };
        const done = recorded();
        if (done) return done;
        const content = ts.installmentContent(tid, runId);
        if (content === undefined) throw ApplicationFailure.nonRetryable(`thread ${tid} has no installment in run ${runId}`, "NoInstallment");
        // Content without the audit record was applied by the Python (a resumed run): done, and its
        // audit health is the Python's own thread_runs row.
        if (content !== null) return { threadId: tid, auditFailed: false };
        const arts = articles(store, runId);
        const openNow = ts.openQuestions(tid);

        // The synthesis is a sample worth keeping: a retry after a failed audit or a lost worker
        // audits the same installment instead of paying for a different one.
        let installment: Installment;
        const kept = store.find(runId, synthName(tid));
        if (kept) installment = InstallmentSchema.parse(JSON.parse(store.get(kept)));
        else {
          const ids = config.latebind ? expandNeighbourhood(plan.articleIds, arts, config.latebind.threshold, config.latebind.maxExtra) : plan.articleIds;
          const { structured } = await run("thread-synthesis", runId, "thread_synthesis", synthesisPrompt(ts.recentDeltas(tid), openNow, ids, arts), InstallmentSchema, SONNET_TIMEOUT_MS, { thread: tid });
          installment = InstallmentSchema.parse(structured);
          store.put(runId, synthName(tid), JSON.stringify(installment));
        }

        // The audit fails OPEN, as in production: keep the facts, count the failure. One re-ask
        // on an answer that does not cover the claims; a transport failure is not re-asked.
        const n = installment.whats_new.length;
        let supported: boolean[] = [];
        let auditFailed = false;
        if (n) {
          const base = auditPrompt(installment.whats_new, arts);
          let problem = "";
          try {
            for (const round of [1, 2]) {
              const { structured } = await run("thread-audit", runId, "thread_audit", round === 1 ? base : base + auditReask(problem, n), VerdictsSchema, SONNET_TIMEOUT_MS, { thread: tid, round });
              const answer = answerFor(VerdictsSchema.parse(structured), n);
              if ("supported" in answer) {
                supported = answer.supported;
                break;
              }
              problem = answer.problem;
              console.warn(JSON.stringify({ stage: "threads", runId, thread: tid, warning: `audit reply unusable on attempt ${round}/2`, problem }));
            }
            if (supported.length !== n) throw new Error(`audit ${problem}`);
          } catch (e) {
            if (deps.signal?.()?.aborted) throw e;
            console.error(JSON.stringify({ stage: "threads", runId, thread: tid, error: "whats_new audit failed; keeping facts (fail-open)", detail: String(e) }));
            auditFailed = true;
            supported = installment.whats_new.map(() => true);
          }
        }
        return ts.transaction(() => {
          const again = recorded();
          if (again) return again;
          applyInstallment(ts, tid, openNow, installment, supported, runId);
          putIn(db, runId, auditName(tid), JSON.stringify({ supported, audit_failed: auditFailed }));
          return { threadId: tid, auditFailed };
        });
      }),

    threadsFinish: (runId: number, report: ThreadsReport): Promise<Pointer> =>
      withDb(deps.dbPath, (db) => {
        const placeholder: Pointer = { runId, name: THREAD_CONTEXT, sha256: "0".repeat(64) };
        if (!config.enabled) return placeholder; // the render finds no context and renders without it
        const done = store.find(runId, THREAD_CONTEXT);
        if (done) return done;
        const ts = new ThreadStore(db);
        const assignmentsText = artifactIn(db, runId, THREAD_ASSIGNMENTS);
        if (assignmentsText === undefined) {
          const health = { link: "failed", error: report.linkError ?? `no ${THREAD_ASSIGNMENTS}` };
          console.error(JSON.stringify({ stage: "threads", runId, error: "thread linking failed; the digest renders without thread context", detail: health.error }));
          if (!artifactIn(db, runId, THREAD_HEALTH)) putIn(db, runId, THREAD_HEALTH, JSON.stringify(health, null, 2));
          return placeholder;
        }
        const assignments = JSON.parse(assignmentsText) as StoredAssignment[];
        // Read back from the database, not from the report, so a retried finish writes the same thing.
        const auditFailures = assignments.filter((a) => {
          const r = artifactIn(db, runId, auditName(a.thread_id));
          return r !== undefined && (JSON.parse(r) as { audit_failed: boolean }).audit_failed;
        }).length;
        ts.transaction(() => {
          // In assignment order, as synthesize_threads appends them; content is set only by an applied installment.
          const installments = assignments.flatMap((a) => {
            const c = a.is_new ? null : ts.installmentContent(a.thread_id, runId);
            return c ? [{ thread_id: a.thread_id, ...(JSON.parse(c) as object) }] : [];
          });
          if (!ts.hasRunHealth(runId)) ts.recordRunHealth(runId, installments.length, auditFailures);
          const contexts: Record<string, RenderContext & { url: string }> = {};
          for (const a of assignments) if (!a.is_new) contexts[a.story] = { ...ts.renderContext(a.thread_id, runId), url: threadUrl(config.digestDomain, a.thread_id) };
          const health = { link: "ok", synthesized: installments.length, audit_failures: auditFailures, failures: report.failures };
          if (!artifactIn(db, runId, THREAD_INSTALLMENTS)) putIn(db, runId, THREAD_INSTALLMENTS, JSON.stringify(installments, null, 2));
          if (!artifactIn(db, runId, THREAD_HEALTH)) putIn(db, runId, THREAD_HEALTH, JSON.stringify(health, null, 2));
          putIn(db, runId, THREAD_CONTEXT, JSON.stringify(contexts, null, 2));
        });
        const continued = assignments.filter((a) => !a.is_new).length;
        console.log(JSON.stringify({ stage: "threads", runId, stories: assignments.length, continued, new: assignments.length - continued, synthesized: report.outcomes.length, failures: report.failures.length, auditFailures }));
        if (auditFailures) console.error(JSON.stringify({ stage: "threads", runId, alert: "thread_audit_failures", auditFailures, detail: "the faithfulness audit failed open; unchecked facts shipped" }));
        if (report.failures.length) console.error(JSON.stringify({ stage: "threads", runId, error: "thread syntheses failed and were skipped", failures: report.failures }));
        return store.find(runId, THREAD_CONTEXT)!;
      }),
  };
}
