import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Context } from "@temporalio/activity";
import { ApplicationFailure } from "@temporalio/common";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import { artifactIn, putIn, quarantineIn, setIn, type ArtifactStore, type Pointer } from "../store/artifacts.js";
import { openDb, type Db, type Sql } from "../store/db.js";
import type { UsageRow } from "../store/usage.js";
import { assignThreads, linkPrompt, parseLinks, selectedLabels, validateLinks, type Assignment, type LinkHealth, type LinkTrace } from "../threads/link.js";
import { ThreadStore, type ActiveThread, type RenderContext } from "../threads/store.js";
import { applyInstallment, auditPrompt, auditReask, expandNeighbourhood, parseInstallment, readAudit, synthesisPrompt, whatsNewOf, type Art, type Installment } from "../threads/synthesis.js";
import { loadArticles } from "./cluster.js";
import { THREAD_CONTEXT, type ThreadOutcome, type ThreadPlan, type ThreadsLinked, type ThreadsReport } from "./index.js";
import { ACCEPTED_BROADCAST_STATES, sendRow } from "../ops/broadcast-state.js";
import { RUN_TIMEOUT_HOURS } from "../workflow/policy.js";

// THREADS (run.py::_process_story_threads): link each selected story to a continuing thread or a
// new one, synthesize and audit today's installment for each continuing thread, then hand the
// render its thread context. Best-effort as in production: the workflow settles every failure here
// and renders without the garnish, and thread_health.json says what happened.
//
// Idempotency is the risk the Python never had: it commits identity row by row, so a retried
// attempt would link today's stories to threads the failed attempt created. Here each unit of
// identity commits in ONE transaction with the artifact that records it, on one connection:
// identity with thread_assignments.json, each installment with its thread_audit_tNNN.json. An
// attempt that finds the record returns it; one that does not finds no partial identity either,
// because the transaction rolled it back. Health and context are views, recomputed on every finish.

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
const flag = (v: string | undefined): boolean => ["1", "true", "yes"].includes((v ?? "false").toLowerCase());
function numberFrom(env: NodeJS.ProcessEnv, name: string, dflt: number, integer: boolean): number {
  const raw = env[name];
  if (raw === undefined) return dflt;
  const v = Number(raw);
  if (raw.trim() === "" || !Number.isFinite(v) || v < 0 || (integer && !Number.isInteger(v))) throw new Error(`${name}=${JSON.stringify(raw)} is not a non-negative ${integer ? "integer" : "number"}`);
  return v;
}
// config.py's THREAD_* names and defaults: off unless the deployment turns them on.
export function threadsConfigFrom(env: NodeJS.ProcessEnv): ThreadsConfig {
  return {
    enabled: flag(env["THREADS_ENABLED"]),
    dormantAfter: numberFrom(env, "THREAD_DORMANT_AFTER", 3, true),
    latebind: flag(env["THREAD_LATEBIND"]) ? { threshold: numberFrom(env, "THREAD_LATEBIND_THRESHOLD", 0.35, false), maxExtra: numberFrom(env, "THREAD_LATEBIND_MAX_EXTRA", 12, true) } : null,
    digestDomain: env["DIGEST_DOMAIN"] ?? "",
  };
}

export interface ThreadsDeps {
  store: ArtifactStore;
  dbUrl: string;
  agentsDir: string;
  config: ThreadsConfig;
  maxAttempts: number;
  query?: SdkQuery;
  heartbeat?: () => void;
  signal?: () => AbortSignal | undefined;
  onUsage?: (row: UsageRow) => void | Promise<void>;
  attempt?: () => number;
  execution?: () => string | undefined;
}

// The workflow execution this activity runs in; outside an activity (tests, CLIs) there is none.
function currentExecution(): string | undefined {
  try {
    return Context.current().info.workflowExecution?.runId;
  } catch {
    return undefined;
  }
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

// Artifacts are read and written through the store's connection-level helpers, inside the caller's
// transaction. Health, context and the installments list are derived from the run's thread rows, so
// a finish replaces them (setIn) rather than keeping a first answer a later resume has made wrong.
async function threadArtifacts(db: Sql, runId: number): Promise<string[]> {
  return (await db.all<{ n: string }>("SELECT name AS n FROM artifacts WHERE run_id = $1 AND status = 'current' AND name LIKE 'thread%'", [runId])).map((r) => r.n);
}

// One writer at a time for a run's thread identity: the link, each installment, the finish.
const lockOf = (runId: number) => `threads ${runId}`;

// Later runs that build on this run's thread writes: an installment on a thread this run created
// or continued, or a resolution of a question this run raised. Undoing under them would strip a
// thread's first day or delete their resolutions, so a force refuses instead.
export async function dependentRuns(db: Sql, runId: number): Promise<number[]> {
  const rows = await db.all<{ r: number }>(
    `SELECT run_id AS r FROM thread_updates
     WHERE run_id > $1 AND thread_id IN (SELECT thread_id FROM thread_updates WHERE run_id = $1)
     UNION
     SELECT r.resolved_run_id FROM thread_question_resolutions r JOIN thread_questions q ON q.id = r.question_id
     WHERE q.raised_run_id = $1 AND r.resolved_run_id > $1
     ORDER BY 1`,
    [runId],
  );
  return rows.map((x) => x.r);
}

// Which execution last undid this run's threads: a retried forced link in the same execution must
// not take back what its first attempt committed. Not named thread*, so the undo does not set it aside.
const UNDO_MARKER = "force_undo_threads.json";

// A forced re-run's first step: take back everything THIS run wrote to the thread tables, and set
// its thread artifacts aside, so relinking starts from the state the run began in. Another run's
// rows are never touched: a thread's label and last run follow from the installments that remain,
// and a thread this run created goes only if nothing else holds it. Runs inside the caller's
// transaction.
export async function undoRun(db: Sql, runId: number): Promise<void> {
  const later = await dependentRuns(db, runId);
  if (later.length)
    throw ApplicationFailure.nonRetryable(`refusing to force run ${runId}'s threads: later run(s) ${later.join(", ")} build on run ${runId}'s threads; they stay as they are, relink by hand`, "ThreadsHaveDependents");
  const touched = (await db.all<{ t: number }>("SELECT DISTINCT thread_id AS t FROM thread_updates WHERE run_id = $1", [runId])).map((r) => r.t);
  await db.run("DELETE FROM thread_question_resolutions WHERE resolved_run_id = $1", [runId]);
  await db.run("DELETE FROM thread_questions WHERE raised_run_id = $1", [runId]);
  await db.run("DELETE FROM thread_updates WHERE run_id = $1", [runId]);
  for (const tid of touched)
    await db.run(
      `DELETE FROM threads t WHERE id = $1 AND created_run_id = $2
         AND NOT EXISTS (SELECT 1 FROM thread_updates WHERE thread_id = t.id)
         AND NOT EXISTS (SELECT 1 FROM thread_questions WHERE thread_id = t.id)
         AND NOT EXISTS (SELECT 1 FROM threads m WHERE m.merged_into_id = t.id)`,
      [tid, runId],
    );
  for (const name of await threadArtifacts(db, runId)) await quarantineIn(db, runId, name);
}

export interface Retraction { retracted: boolean; reason?: string }

async function retract(db: Db, runId: number): Promise<Retraction> {
  const decline = (reason: string) => {
    console.error(JSON.stringify({ stage: "threads", runId, error: "unsent issue's thread writes kept", reason }));
    return { retracted: false, reason };
  };
  // A run readers got, on the web or by email, is public, and so are its thread updates: saveDigest
  // puts the issue on the web before the send, so a send that fails after it does not unpublish them.
  if (await db.one("SELECT 1 FROM published_runs WHERE run_id = $1", [runId])) return decline("its issue is published");
  // The run's own send first, whatever day it is dated: a run started for tomorrow sends under tomorrow.
  const own = await db.one<{ date: string }>("SELECT issue_date::text AS date FROM sends WHERE run_id = $1", [runId]);
  const run = own ?? (await db.one<{ date: string }>("SELECT (started_at AT TIME ZONE 'UTC')::date AS date FROM runs WHERE id = $1", [runId]));
  const day = run ? await sendRow(db, run.date) : undefined;
  const mayHaveGone = day !== undefined && (day.id !== null || day.status === "claimed" || ACCEPTED_BROADCAST_STATES.has(day.status));
  // Delivery is judged by sender: the day's broadcast is another run's only when its claiming run is
  // a different, completed one.
  if (day && mayHaveGone) {
    const other = day.runId !== runId && (await db.one("SELECT 1 FROM runs WHERE id = $1 AND status = 'completed'", [day.runId])) !== undefined;
    if (!other) return decline(`the day's broadcast is ${day.status}${day.id ? ` (${day.id})` : ""}`);
  }
  const later = await dependentRuns(db, runId);
  if (later.length) return decline(`later run(s) ${later.join(", ")} build on it`);
  await db.tx((t) => undoRun(t, runId), `threads ${runId}`);
  return { retracted: true };
}

// How long a failed run can still be resumed: a resume is a new execution under the same run timeout,
// so a run older than this has no attempt left that could deliver its installments.
export const RESUME_HORIZON_HOURS = RUN_TIMEOUT_HOURS;

// abortRun keeps a failed run's thread writes for a resume. A failed run is abandoned once the
// horizon has passed or a later run of its day supersedes it; a resume sets it back to 'running'
// (startRun), which is never swept. Unless it is published (web or email) or its day's send may have
// gone out, no reader has its writes: take them back before this run links, so it links on the state the failed run began in, newest
// first so a chain of failed runs unwinds without the later one counting as a dependent.
// A 'running' leftover (a crash the workflow never marked) is left alone: nothing can tell it from a
// run still in progress, and no crash has left one with thread writes (prod clone, 2026-09-23).
export async function retractAbandoned(db: Db, runId: number): Promise<number[]> {
  const abandoned = await db.all<{ id: number }>(
    `SELECT id FROM runs d
     WHERE id < $1 AND status = 'failed'
       AND (started_at < now() - make_interval(hours => $2)
            OR EXISTS (SELECT 1 FROM runs l WHERE l.id > d.id AND (l.started_at AT TIME ZONE 'UTC')::date = (d.started_at AT TIME ZONE 'UTC')::date))
       AND (EXISTS (SELECT 1 FROM thread_updates WHERE run_id = d.id)
            OR EXISTS (SELECT 1 FROM thread_questions WHERE raised_run_id = d.id)
            OR EXISTS (SELECT 1 FROM thread_question_resolutions WHERE resolved_run_id = d.id))
     ORDER BY id DESC`,
    [runId, RESUME_HORIZON_HOURS],
  );
  const out: number[] = [];
  for (const { id } of abandoned) if ((await retract(db, id)).retracted) out.push(id);
  return out;
}

async function articles(store: ArtifactStore, runId: number): Promise<Map<string, Art>> {
  const arts = new Map<string, Art>();
  for (const a of await loadArticles(store, runId)) arts.set(a.article_id, { title: a.title, summary: a.summary });
  return arts;
}
// thread_assignments.json keeps the Python's shape; the article ids ride in thread_links.json's
// story trace, index-aligned, which a resumed Python run also carries. A trace entry whose label is
// not its assignment's story is not that story's evidence.
interface StoredAssignment { thread_id: number; is_new: boolean; story: string }
export function plansFrom(assignments: StoredAssignment[], trace: LinkTrace | undefined): ThreadPlan[] {
  return assignments.flatMap((a, i) => {
    const st = trace?.stories[i];
    const ids = st?.label === a.story ? st.article_ids : [];
    return !a.is_new && ids.length >= MIN_ARTICLES ? [{ threadId: a.thread_id, articleIds: ids }] : [];
  });
}

export function threadsActivities(deps: ThreadsDeps) {
  const { store, config } = deps;
  const attempt = deps.attempt ?? currentAttempt;
  const execution = deps.execution ?? currentExecution;
  const signal = (ms: number): AbortSignal => {
    const s = deps.signal?.();
    return s ? AbortSignal.any([s, AbortSignal.timeout(ms)]) : AbortSignal.timeout(ms);
  };
  const spec = (name: string) => parseAgentSpec(readFileSync(join(deps.agentsDir, `${name}.md`), "utf8"));
  // Every thread call is free text, as production's are (no output format): the answer is parsed as
  // the Python parses it.
  const run = async (name: string, runId: number, stage: string, prompt: string, ms: number, detail: Record<string, unknown> = {}): Promise<string> => {
    const s = spec(name);
    const clean = scrubUrls(prompt);
    assertNoUrls(clean); // the invariant, checked where text leaves code (spec §1)
    deps.heartbeat?.();
    const r = await runStage(s, { userMessage: clean, inputDir: tmpdir() }, {
      today: await store.runDate(runId),
      signal: signal(ms),
      ...(deps.query ? { query: deps.query } : {}),
      ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}),
    });
    deps.heartbeat?.();
    await deps.onUsage?.({ model: s.model, thinking: s.thinking, prompt: s, effort: r.effort, tokens: r.usage, stage, runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns, ...detail });
    return r.text;
  };

  // The linker's answer. A failure throws for a fresh sample while attempts remain; on the last one
  // it is production's fallback, every story a new thread, recorded as linker_ok false.
  async function link(runId: number, active: ActiveThread[], labels: string[]): Promise<{ mapping: (number | null)[]; health: LinkHealth }> {
    if (!active.length || !labels.length) return { mapping: labels.map(() => null), health: { ok: true, proposed: 0, validated: 0 } };
    try {
      const links = parseLinks(await run("thread-link", runId, "thread_link", linkPrompt(active, labels), LINK_TIMEOUT_MS));
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
    threadsLink: async (runId: number, force = false): Promise<ThreadsLinked> => {
      if (!config.enabled) return { plans: [], skip: "disabled" as const };
      const db = openDb(deps.dbUrl);
      if (force)
        await db.tx(async (t) => {
          const exec = execution();
          if (exec !== undefined && (await artifactIn(t, runId, UNDO_MARKER)) === JSON.stringify({ execution: exec })) return;
          await undoRun(t, runId);
          if (exec !== undefined) await setIn(t, runId, UNDO_MARKER, JSON.stringify({ execution: exec }));
        }, lockOf(runId));
      const read = async (q: Sql) => {
        const a = await artifactIn(q, runId, THREAD_ASSIGNMENTS);
        const t = await artifactIn(q, runId, THREAD_LINKS);
        return a === undefined ? undefined : plansFrom(JSON.parse(a) as StoredAssignment[], t === undefined ? undefined : (JSON.parse(t) as LinkTrace));
      };
      const done = await read(db);
      if (done) return { plans: done };
      const retracted = await retractAbandoned(db, runId);
      if (retracted.length) console.log(JSON.stringify({ stage: "threads", runId, retractedAbandonedRuns: retracted }));
      if ((await new ThreadStore(db).countRunUpdates(runId)) > 0)
        throw ApplicationFailure.nonRetryable(`run ${runId} has thread installments but no ${THREAD_ASSIGNMENTS}; refusing to link again and duplicate them (a forced re-run undoes them)`, "ThreadIdentityUnrecorded");
      const need = async (name: string) => {
        const p = await store.find(runId, name);
        if (!p) throw ApplicationFailure.nonRetryable(`run ${runId} has no ${name}`, "MissingInput");
        return JSON.parse(await store.get(p)) as unknown;
      };
      const stories = selectedLabels(await need("clusters.json"), await need("selected.json"));
      const active = await new ThreadStore(db).activeThreads(runId, config.dormantAfter);
      const { mapping, health } = await link(runId, active, stories.map((s) => s.story));
      const committed = await db.tx(async (t) => {
        const again = await read(t);
        if (again) return again; // another attempt committed while this one waited on the model
        const { assignments, trace } = await assignThreads(new ThreadStore(t), stories, runId, active, mapping, health);
        await putIn(t, runId, THREAD_LINKS, JSON.stringify(trace, null, 2));
        await putIn(t, runId, THREAD_ASSIGNMENTS, JSON.stringify(assignments.map((a: Assignment) => ({ thread_id: a.thread_id, is_new: a.is_new, story: a.story })), null, 2));
        return plansFrom(assignments, trace);
      }, lockOf(runId));
      console.log(JSON.stringify({ stage: "threads-link", runId, forced: force, stories: stories.length, candidates: active.length, linkerOk: health.ok, proposed: health.proposed, validated: health.validated, toSynthesize: committed.length }));
      return { plans: committed };
    },

    threadSynthesis: async (runId: number, plan: ThreadPlan): Promise<ThreadOutcome> => {
      const db = openDb(deps.dbUrl);
      const ts = new ThreadStore(db);
      const tid = plan.threadId;
      const recorded = async (q: Sql) => {
        const a = await artifactIn(q, runId, auditName(tid));
        return a === undefined ? undefined : { threadId: tid, auditFailed: (JSON.parse(a) as { audit_failed: boolean }).audit_failed };
      };
      const done = await recorded(db);
      if (done) return done;
      const content = await ts.updateContent(tid, runId);
      if (content === undefined) throw ApplicationFailure.nonRetryable(`thread ${tid} has no installment in run ${runId}`, "NoInstallment");
      // Content without the audit record was applied by the Python (a resumed run): done. Applying
      // again would duplicate its questions.
      if (content !== null) return { threadId: tid, auditFailed: false };
      const arts = await articles(store, runId);
      const openNow = await ts.openQuestions(tid);

      // The synthesis is a sample worth keeping: a retry after a failed audit or a lost worker
      // audits the same installment instead of paying for a different one.
      let installment: Installment;
      const kept = await store.find(runId, synthName(tid));
      if (kept) installment = JSON.parse(await store.get(kept)) as Installment;
      else {
        const ids = config.latebind ? expandNeighbourhood(plan.articleIds, arts, config.latebind.threshold, config.latebind.maxExtra) : plan.articleIds;
        installment = parseInstallment(await run("thread-synthesis", runId, "thread_synthesis", synthesisPrompt(await ts.recentDeltas(tid), openNow, ids, arts), SONNET_TIMEOUT_MS, { thread: tid }));
        await store.put(runId, synthName(tid), JSON.stringify(installment));
      }

      // The audit fails OPEN, as in production: keep the facts, count the failure. One re-ask on a
      // reply that does not answer the claims; a transport failure is not re-asked.
      const facts = whatsNewOf(installment);
      const n = facts.length;
      let supported: boolean[] = [];
      let auditFailed = false;
      if (n) {
        try {
          const base = auditPrompt(facts, arts);
          let problem = "";
          for (const round of [1, 2]) {
            const answer = readAudit(await run("thread-audit", runId, "thread_audit", round === 1 ? base : base + auditReask(problem, n), SONNET_TIMEOUT_MS, { thread: tid, round }), n);
            if ("supported" in answer) {
              if (answer.unreadable) console.warn(JSON.stringify({ stage: "threads", runId, thread: tid, warning: "audit verdicts with an unreadable `supported`, read as unsupported", count: answer.unreadable }));
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
          supported = facts.map(() => true);
        }
      }
      return db.tx(async (t) => {
        const again = await recorded(t);
        if (again) return again;
        await applyInstallment(new ThreadStore(t), tid, openNow, installment, supported, runId);
        await putIn(t, runId, auditName(tid), JSON.stringify({ supported, audit_failed: auditFailed }));
        return { threadId: tid, auditFailed };
      }, lockOf(runId));
    },

    // An issue that was not sent (rejected, held out, disabled, skipped) takes back its thread writes:
    // the next run's synthesis would build on facts no reader was sent. Declines when the database
    // says the day was broadcast (a resume of a delivered run) or a send may be in flight, and when a
    // later run already builds on it, as a force does. A decline is logged.
    threadsRetract: (runId: number): Promise<Retraction> => retract(openDb(deps.dbUrl), runId),
    // Records the phase from the run's thread rows and returns the render's context. Recomputed
    // every time, so a resume that lands what an earlier attempt could not says so.
    threadsFinish: async (runId: number, report: ThreadsReport): Promise<Pointer> => {
      const placeholder: Pointer = { runId, name: THREAD_CONTEXT, sha256: "0".repeat(64) };
      if (!config.enabled) return placeholder; // the render finds no context and renders without it
      const db = openDb(deps.dbUrl);
      const assignmentsText = await artifactIn(db, runId, THREAD_ASSIGNMENTS);
      if (assignmentsText === undefined) {
        const health = { link: "failed", error: report.linkError ?? `no ${THREAD_ASSIGNMENTS}`, ...(report.timedOut ? { timed_out: true } : {}) };
        console.error(JSON.stringify({ stage: "threads", runId, error: "thread linking failed; the digest renders without thread context", detail: health.error }));
        await db.tx(async (t) => {
          await setIn(t, runId, THREAD_HEALTH, JSON.stringify(health, null, 2));
          await quarantineIn(t, runId, THREAD_CONTEXT);
        }, lockOf(runId));
        return placeholder;
      }
      const assignments = JSON.parse(assignmentsText) as StoredAssignment[];
      const trace = JSON.parse((await artifactIn(db, runId, THREAD_LINKS)) ?? "{}") as Partial<LinkTrace>;
      let auditFailures = 0;
      for (const a of assignments) {
        const r = await artifactIn(db, runId, auditName(a.thread_id));
        if (r !== undefined && (JSON.parse(r) as { audit_failed: boolean }).audit_failed) auditFailures++;
      }
      const synthesizable = plansFrom(assignments, trace as LinkTrace).map((p) => p.threadId);
      await db.tx(async (t) => {
        const ts = new ThreadStore(t);
        // In assignment order, as synthesize_threads appends them; content is set only by an applied installment.
        const installments: ({ thread_id: number } & object)[] = [];
        for (const a of assignments) {
          const c = a.is_new ? null : await ts.updateContent(a.thread_id, runId);
          if (c) installments.push({ thread_id: a.thread_id, ...(JSON.parse(c) as object) });
        }
        const landed = new Set(installments.map((i) => i.thread_id));
        const health = {
          link: "ok",
          linker_ok: trace.linker_ok ?? true,
          synthesized: installments.length,
          audit_failures: auditFailures,
          // What is still missing, not what once failed: a thread a resume landed is no failure.
          failures: synthesizable.filter((id) => !landed.has(id)).map((id) => ({ threadId: id, error: report.failures.find((f) => f.threadId === id)?.error ?? (report.timedOut ? "timed out" : "not synthesized") })),
          ...(report.timedOut ? { timed_out: true } : {}),
        };
        await setIn(t, runId, THREAD_INSTALLMENTS, JSON.stringify(installments, null, 2));
        await setIn(t, runId, THREAD_HEALTH, JSON.stringify(health, null, 2));
        if (report.timedOut) {
          await quarantineIn(t, runId, THREAD_CONTEXT);
          return;
        }
        const contexts: Record<string, RenderContext & { url: string }> = {};
        for (const a of assignments) if (!a.is_new) contexts[a.story] = { ...(await ts.renderContext(a.thread_id, runId)), url: threadUrl(config.digestDomain, a.thread_id) };
        await setIn(t, runId, THREAD_CONTEXT, JSON.stringify(contexts, null, 2));
      }, lockOf(runId));
      const continued = assignments.filter((a) => !a.is_new).length;
      console.log(JSON.stringify({ stage: "threads", runId, stories: assignments.length, continued, new: assignments.length - continued, synthesized: report.outcomes.length, failures: report.failures.length, auditFailures, timedOut: report.timedOut ?? false }));
      if (auditFailures) console.error(JSON.stringify({ stage: "threads", runId, alert: "thread_audit_failures", auditFailures, detail: "the faithfulness audit failed open; unchecked facts shipped" }));
      if (report.failures.length) console.error(JSON.stringify({ stage: "threads", runId, error: "thread syntheses failed and were skipped", failures: report.failures }));
      if (report.timedOut) {
        console.error(JSON.stringify({ stage: "threads", runId, error: "the threads phase ran past its bound; the digest renders without thread context" }));
        return placeholder;
      }
      return (await store.find(runId, THREAD_CONTEXT))!;
    },
  };
}
