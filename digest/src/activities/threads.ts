import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { DatabaseSync } from "node:sqlite";
import { Context } from "@temporalio/activity";
import { ApplicationFailure } from "@temporalio/common";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import type { UsageRow } from "../store/usage.js";
import { assignThreads, linkPrompt, parseLinks, selectedLabels, validateLinks, type Assignment, type LinkHealth, type LinkTrace } from "../threads/link.js";
import { ThreadStore, type RenderContext } from "../threads/store.js";
import { applyInstallment, auditPrompt, auditReask, expandNeighbourhood, parseInstallment, readAudit, synthesisPrompt, whatsNewOf, type Art, type Installment } from "../threads/synthesis.js";
import { loadArticles } from "./cluster.js";
import { THREAD_CONTEXT, type ThreadOutcome, type ThreadPlan, type ThreadsLinked, type ThreadsReport } from "./index.js";
import { ACCEPTED_BROADCAST_STATES, broadcastState, CLAIMED } from "../ops/broadcast-state.js";
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
  dbPath: string;
  agentsDir: string;
  config: ThreadsConfig;
  maxAttempts: number;
  query?: SdkQuery;
  heartbeat?: () => void;
  signal?: () => AbortSignal | undefined;
  onUsage?: (row: UsageRow) => void;
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

// Artifacts read and written inside the caller's transaction, on the caller's connection. The
// unique (run_id, artifact_name) index makes a second writer's commit fail rather than duplicate.
function putIn(db: DatabaseSync, runId: number, name: string, content: string): void {
  db.prepare("INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)").run(runId, name, content);
}
function artifactIn(db: DatabaseSync, runId: number, name: string): string | undefined {
  return (db.prepare("SELECT content FROM run_artifacts WHERE run_id = ? AND artifact_name = ?").get(runId, name) as { content: string } | undefined)?.content;
}
// Health, context and the installments list are derived from the run's thread rows, so a finish
// replaces them rather than keeping a first answer a later resume has made wrong.
function setIn(db: DatabaseSync, runId: number, name: string, content: string): void {
  const old = artifactIn(db, runId, name);
  if (old === undefined) putIn(db, runId, name, content);
  else if (old !== content) db.prepare("UPDATE run_artifacts SET content = ? WHERE run_id = ? AND artifact_name = ?").run(content, runId, name);
}
// ArtifactStore.quarantine's naming, on this connection so it joins the caller's transaction.
function quarantineIn(db: DatabaseSync, runId: number, name: string): void {
  const { c } = db.prepare("SELECT COUNT(*) AS c FROM run_artifacts WHERE run_id = ? AND artifact_name LIKE ?").get(runId, `${name}.corrupt.%`) as { c: number };
  db.prepare("UPDATE run_artifacts SET artifact_name = ? WHERE run_id = ? AND artifact_name = ?").run(`${name}.corrupt.${c + 1}`, runId, name);
}
function threadArtifacts(db: DatabaseSync, runId: number): string[] {
  const names = (db.prepare("SELECT artifact_name AS n FROM run_artifacts WHERE run_id = ? AND artifact_name LIKE 'thread%'").all(runId) as { n: string }[]).map((r) => r.n);
  return names.filter((n) => !n.includes(".corrupt."));
}

// A forced re-run's first step: take back everything THIS run wrote to the thread tables, and set
// its thread artifacts aside, so relinking starts from the state the run began in. Another run's
// rows are never touched: a thread this run continued gets back the label and last run of its
// latest remaining installment, and a thread this run created goes only if nothing else holds it.
// Later runs that build on this run's thread writes: an installment on a thread this run created
// or continued, or a resolution of a question this run raised. Undoing under them would strip a
// thread's first day or delete their resolutions, so a force refuses instead.
export function dependentRuns(db: DatabaseSync, runId: number): number[] {
  const rows = db
    .prepare(
      `SELECT run_id AS r FROM thread_installments
       WHERE run_id > ? AND thread_id IN (SELECT thread_id FROM thread_installments WHERE run_id = ?)
       UNION
       SELECT resolved_run_id FROM thread_questions WHERE raised_run_id = ? AND resolved_run_id > ?
       ORDER BY 1`,
    )
    .all(runId, runId, runId, runId) as { r: number }[];
  return rows.map((x) => x.r);
}

// Which execution last undid this run's threads: a retried forced link in the same execution must
// not take back what its first attempt committed. Not named thread*, so the undo does not set it aside.
const UNDO_MARKER = "force_undo_threads.json";

export function undoRun(db: DatabaseSync, runId: number): void {
  const later = dependentRuns(db, runId);
  if (later.length)
    throw ApplicationFailure.nonRetryable(`refusing to force run ${runId}'s threads: later run(s) ${later.join(", ")} build on run ${runId}'s threads; they stay as they are, relink by hand`, "ThreadsHaveDependents");
  const touched = (db.prepare("SELECT DISTINCT thread_id AS t FROM thread_installments WHERE run_id = ?").all(runId) as { t: number }[]).map((r) => r.t);
  db.prepare("UPDATE thread_questions SET status = 'open', resolved_run_id = NULL, resolved_how = NULL WHERE resolved_run_id = ?").run(runId);
  db.prepare("DELETE FROM thread_questions WHERE raised_run_id = ?").run(runId);
  db.prepare("DELETE FROM thread_installments WHERE run_id = ?").run(runId);
  db.prepare("DELETE FROM thread_runs WHERE run_id = ?").run(runId);
  for (const tid of touched) {
    const latest = db.prepare("SELECT run_id, cluster_story FROM thread_installments WHERE thread_id = ? ORDER BY run_id DESC LIMIT 1").get(tid) as { run_id: number; cluster_story: string | null } | undefined;
    if (!latest) {
      const orphan = db.prepare("SELECT 1 FROM thread_questions WHERE thread_id = ?").get(tid) === undefined && db.prepare("SELECT 1 FROM threads WHERE merged_into = ?").get(tid) === undefined;
      if (orphan) db.prepare("DELETE FROM threads WHERE id = ? AND first_run_id = ?").run(tid, runId);
      continue;
    }
    db.prepare("UPDATE threads SET last_run_id = ?, label = COALESCE(?, label), updated_at = datetime('now', 'utc') WHERE id = ? AND last_run_id = ? AND merged_into IS NULL").run(latest.run_id, latest.cluster_story, tid, runId);
  }
  for (const name of threadArtifacts(db, runId)) quarantineIn(db, runId, name);
}

export interface Retraction { retracted: boolean; reason?: string }

function retract(db: DatabaseSync, runId: number): Retraction {
  const decline = (reason: string) => {
    console.error(JSON.stringify({ stage: "threads", runId, error: "unsent issue's thread writes kept", reason }));
    return { retracted: false, reason };
  };
  const hasDigests = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'digests'").get() !== undefined;
  const day = hasDigests ? broadcastState(db, runId) : null;
  const mayHaveGone = day !== null && (day.id !== null || (day.status !== null && (ACCEPTED_BROADCAST_STATES.has(day.status) || day.status.startsWith(CLAIMED))));
  // Delivery is judged by sender: the day's broadcast is another run's only when broadcast_run_id (set
  // with the claim) names a different, completed run. digests.run_id is the last run to save the row,
  // which a forced re-run of a sent day takes over, so it says nothing about who sent. A broadcast
  // with no recorded sender (claimed before the column, or by Python) is unknown, and declines.
  if (day && mayHaveGone) {
    const hasSender = (db.prepare("SELECT COUNT(*) AS n FROM pragma_table_info('digests') WHERE name = 'broadcast_run_id'").get() as { n: number }).n > 0;
    const other = hasSender && db.prepare("SELECT 1 FROM digests d JOIN digest_runs r ON r.id = d.broadcast_run_id WHERE d.date = ? AND r.id != ? AND r.status = 'completed'").get(day.date, runId) !== undefined;
    if (!other) return decline(`the day's broadcast is ${day.status ?? "unknown"}${day.id ? ` (${day.id})` : ""}`);
  }
  const later = dependentRuns(db, runId);
  if (later.length) return decline(`later run(s) ${later.join(", ")} build on it`);
  new ThreadStore(db).transaction(() => undoRun(db, runId));
  return { retracted: true };
}

// How long a failed run can still be resumed: a resume is a new execution under the same run timeout,
// so a run older than this has no attempt left that could deliver its installments.
export const RESUME_HORIZON_HOURS = RUN_TIMEOUT_HOURS;

// abortRun keeps a failed run's thread writes for a resume. A failed run is abandoned once the
// horizon has passed or a later run of its day supersedes it; a resume sets it back to 'running'
// (startRun), which is never swept. Unless its own issue may have gone out, nobody was sent its writes:
// take them back before this run links, so it links on the state the failed run began in, newest
// first so a chain of failed runs unwinds without the later one counting as a dependent.
// A 'running' leftover (a crash the workflow never marked) is left alone: nothing can tell it from a
// run still in progress, and no crash has left one with thread writes (prod clone, 2026-09-23).
export function retractAbandoned(db: DatabaseSync, runId: number): number[] {
  const abandoned = db
    .prepare(
      `SELECT id FROM digest_runs
       WHERE id < ? AND completed_at IS NULL AND status = 'failed'
         AND (run_at < datetime('now', ?)
              OR EXISTS (SELECT 1 FROM digest_runs l WHERE l.id > digest_runs.id AND date(l.run_at) = date(digest_runs.run_at)))
         AND (EXISTS (SELECT 1 FROM thread_installments WHERE run_id = digest_runs.id)
              OR EXISTS (SELECT 1 FROM thread_questions WHERE raised_run_id = digest_runs.id OR resolved_run_id = digest_runs.id))
       ORDER BY id DESC`,
    )
    .all(runId, `-${RESUME_HORIZON_HOURS} hours`) as { id: number }[];
  return abandoned.filter(({ id }) => retract(db, id).retracted).map(({ id }) => id);
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
      today: store.runDate(runId),
      signal: signal(ms),
      ...(deps.query ? { query: deps.query } : {}),
      ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}),
    });
    deps.heartbeat?.();
    deps.onUsage?.({ model: s.model, thinking: s.thinking, tokens: r.usage, stage, runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns, ...detail });
    return r.text;
  };

  // The linker's answer. A failure throws for a fresh sample while attempts remain; on the last one
  // it is production's fallback, every story a new thread, recorded as linker_ok false.
  async function link(runId: number, active: ReturnType<ThreadStore["activeThreads"]>, labels: string[]): Promise<{ mapping: (number | null)[]; health: LinkHealth }> {
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
    threadsLink: (runId: number, force = false): Promise<ThreadsLinked> =>
      withDb(deps.dbPath, async (db) => {
        if (!config.enabled) return { plans: [], skip: "disabled" as const };
        const ts = new ThreadStore(db);
        if (force)
          ts.transaction(() => {
            const exec = execution();
            if (exec !== undefined && artifactIn(db, runId, UNDO_MARKER) === JSON.stringify({ execution: exec })) return;
            undoRun(db, runId);
            if (exec !== undefined) setIn(db, runId, UNDO_MARKER, JSON.stringify({ execution: exec }));
          });
        const read = () => {
          const a = artifactIn(db, runId, THREAD_ASSIGNMENTS);
          const t = artifactIn(db, runId, THREAD_LINKS);
          return a === undefined ? undefined : plansFrom(JSON.parse(a) as StoredAssignment[], t === undefined ? undefined : (JSON.parse(t) as LinkTrace));
        };
        const done = read();
        if (done) return { plans: done };
        const retracted = retractAbandoned(db, runId);
        if (retracted.length) console.log(JSON.stringify({ stage: "threads", runId, retractedAbandonedRuns: retracted }));
        if (ts.runInstallments(runId) > 0)
          throw ApplicationFailure.nonRetryable(`run ${runId} has thread installments but no ${THREAD_ASSIGNMENTS}; refusing to link again and duplicate them (a forced re-run undoes them)`, "ThreadIdentityUnrecorded");
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
        console.log(JSON.stringify({ stage: "threads-link", runId, forced: force, stories: stories.length, candidates: active.length, linkerOk: health.ok, proposed: health.proposed, validated: health.validated, toSynthesize: committed.length }));
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
        // audit health is the Python's own thread_runs row. Applying again would duplicate its questions.
        if (content !== null) return { threadId: tid, auditFailed: false };
        const arts = articles(store, runId);
        const openNow = ts.openQuestions(tid);

        // The synthesis is a sample worth keeping: a retry after a failed audit or a lost worker
        // audits the same installment instead of paying for a different one.
        let installment: Installment;
        const kept = store.find(runId, synthName(tid));
        if (kept) installment = JSON.parse(store.get(kept)) as Installment;
        else {
          const ids = config.latebind ? expandNeighbourhood(plan.articleIds, arts, config.latebind.threshold, config.latebind.maxExtra) : plan.articleIds;
          installment = parseInstallment(await run("thread-synthesis", runId, "thread_synthesis", synthesisPrompt(ts.recentDeltas(tid), openNow, ids, arts), SONNET_TIMEOUT_MS, { thread: tid }));
          store.put(runId, synthName(tid), JSON.stringify(installment));
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
        return ts.transaction(() => {
          const again = recorded();
          if (again) return again;
          applyInstallment(ts, tid, openNow, installment, supported, runId);
          putIn(db, runId, auditName(tid), JSON.stringify({ supported, audit_failed: auditFailed }));
          return { threadId: tid, auditFailed };
        });
      }),

    // An issue that was not sent (rejected, held out, disabled, skipped) takes back its thread writes:
    // circulation's thread pages read installments whether or not the issue went out, and the next
    // run's synthesis would build on facts no reader was sent. Declines when the database says the
    // day was broadcast (a resume of a delivered run) or a send may be in flight, and when a later
    // run already builds on it, as a force does. A decline is logged: its installments stay public.
    threadsRetract: (runId: number): Promise<Retraction> => withDb(deps.dbPath, (db) => retract(db, runId)),
    // Records the phase from the run's thread rows and returns the render's context. Recomputed
    // every time, so a resume that lands what an earlier attempt could not says so.
    threadsFinish: (runId: number, report: ThreadsReport): Promise<Pointer> =>
      withDb(deps.dbPath, (db) => {
        const placeholder: Pointer = { runId, name: THREAD_CONTEXT, sha256: "0".repeat(64) };
        if (!config.enabled) return placeholder; // the render finds no context and renders without it
        const ts = new ThreadStore(db);
        const assignmentsText = artifactIn(db, runId, THREAD_ASSIGNMENTS);
        if (assignmentsText === undefined) {
          const health = { link: "failed", error: report.linkError ?? `no ${THREAD_ASSIGNMENTS}`, ...(report.timedOut ? { timed_out: true } : {}) };
          console.error(JSON.stringify({ stage: "threads", runId, error: "thread linking failed; the digest renders without thread context", detail: health.error }));
          ts.transaction(() => {
            setIn(db, runId, THREAD_HEALTH, JSON.stringify(health, null, 2));
            if (artifactIn(db, runId, THREAD_CONTEXT) !== undefined) quarantineIn(db, runId, THREAD_CONTEXT);
          });
          return placeholder;
        }
        const assignments = JSON.parse(assignmentsText) as StoredAssignment[];
        const trace = JSON.parse(artifactIn(db, runId, THREAD_LINKS) ?? "{}") as Partial<LinkTrace>;
        const auditFailures = assignments.filter((a) => {
          const r = artifactIn(db, runId, auditName(a.thread_id));
          return r !== undefined && (JSON.parse(r) as { audit_failed: boolean }).audit_failed;
        }).length;
        const synthesizable = plansFrom(assignments, trace as LinkTrace).map((p) => p.threadId);
        ts.transaction(() => {
          // In assignment order, as synthesize_threads appends them; content is set only by an applied installment.
          const installments = assignments.flatMap((a) => {
            const c = a.is_new ? null : ts.installmentContent(a.thread_id, runId);
            return c ? [{ thread_id: a.thread_id, ...(JSON.parse(c) as object) }] : [];
          });
          const landed = new Set(installments.map((i) => i.thread_id));
          // A resumed Python run's audit count is its own; this never lowers it.
          if (ts.hasRunHealth(runId)) db.prepare("UPDATE thread_runs SET threads_synthesized = ?, audit_failures = MAX(audit_failures, ?) WHERE run_id = ?").run(installments.length, auditFailures, runId);
          else ts.recordRunHealth(runId, installments.length, auditFailures);
          const health = {
            link: "ok",
            linker_ok: trace.linker_ok ?? true,
            synthesized: installments.length,
            audit_failures: auditFailures,
            // What is still missing, not what once failed: a thread a resume landed is no failure.
            failures: synthesizable.filter((t) => !landed.has(t)).map((t) => ({ threadId: t, error: report.failures.find((f) => f.threadId === t)?.error ?? (report.timedOut ? "timed out" : "not synthesized") })),
            ...(report.timedOut ? { timed_out: true } : {}),
          };
          setIn(db, runId, THREAD_INSTALLMENTS, JSON.stringify(installments, null, 2));
          setIn(db, runId, THREAD_HEALTH, JSON.stringify(health, null, 2));
          if (report.timedOut) {
            if (artifactIn(db, runId, THREAD_CONTEXT) !== undefined) quarantineIn(db, runId, THREAD_CONTEXT);
            return;
          }
          const contexts: Record<string, RenderContext & { url: string }> = {};
          for (const a of assignments) if (!a.is_new) contexts[a.story] = { ...ts.renderContext(a.thread_id, runId), url: threadUrl(config.digestDomain, a.thread_id) };
          setIn(db, runId, THREAD_CONTEXT, JSON.stringify(contexts, null, 2));
        });
        const continued = assignments.filter((a) => !a.is_new).length;
        console.log(JSON.stringify({ stage: "threads", runId, stories: assignments.length, continued, new: assignments.length - continued, synthesized: report.outcomes.length, failures: report.failures.length, auditFailures, timedOut: report.timedOut ?? false }));
        if (auditFailures) console.error(JSON.stringify({ stage: "threads", runId, alert: "thread_audit_failures", auditFailures, detail: "the faithfulness audit failed open; unchecked facts shipped" }));
        if (report.failures.length) console.error(JSON.stringify({ stage: "threads", runId, error: "thread syntheses failed and were skipped", failures: report.failures }));
        if (report.timedOut) {
          console.error(JSON.stringify({ stage: "threads", runId, error: "the threads phase ran past its bound; the digest renders without thread context" }));
          return placeholder;
        }
        return store.find(runId, THREAD_CONTEXT)!;
      }),
  };
}
