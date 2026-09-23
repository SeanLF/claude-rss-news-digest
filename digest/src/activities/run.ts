import { readFileSync } from "node:fs";
import { Context } from "@temporalio/activity";
import { ApplicationFailure } from "@temporalio/common";
import { NETWORK_MAX_ATTEMPTS } from "../workflow/policy.js";
import { parse } from "csv-parse/sync";
import { activeSources, newerThan, parseArticles, type CatalogueSource } from "../fetch/feeds.js";
import { toCsv, type Fetched } from "../prepare/prepare.js";
import type { ArtifactStore } from "../store/artifacts.js";
import { openDb, type Sql } from "../store/db.js";
import type { DigestInput, DigestOutput } from "./index.js";

export const SOURCES_HEADER = ["id", "name", "bias", "factuality", "perspective"] as const;
export const FETCH_TIMEOUT_MS = 15_000;
const SENT = "id IN (SELECT run_id FROM sent_runs)";
const lastSent = async (db: Sql, before?: string): Promise<string | null> =>
  (before
    ? await db.one<{ t: string | null }>(`SELECT MAX(started_at) AS t FROM runs WHERE ${SENT} AND started_at < $1`, [before])
    : await db.one<{ t: string | null }>(`SELECT MAX(started_at) AS t FROM runs WHERE ${SENT}`))!.t;

export interface RunDeps {
  store: ArtifactStore;
  dbUrl: string;
  sourcesFile: string;
  fetch?: typeof fetch;
  maxAttempts?: number;
}

const currentAttempt = (): number => {
  try {
    return Context.current().info.attempt;
  } catch {
    return Number.POSITIVE_INFINITY; // outside an activity every attempt is the last
  }
};

// The workflow execution calling this activity; undefined outside one (a CLI or a plain test call).
const currentExecution = (): { workflowId: string; runId: string } | undefined => {
  try {
    return Context.current().info.workflowExecution;
  } catch {
    return undefined;
  }
};

// This execution's attempt at the run: one row per workflow execution, so a retried activity finds
// its own row rather than adding one.
const recordAttempt = (db: Sql, runId: number, execution: { workflowId: string; runId: string }, forced: boolean) =>
  db.run(
    "INSERT INTO run_attempts (run_id, pipeline, workflow_id, workflow_run_id, git_sha, is_forced) VALUES ($1, 'temporal', $2, $3, $4, $5) ON CONFLICT (workflow_run_id) DO NOTHING",
    [runId, execution.workflowId, execution.runId, process.env["GIT_SHA"] ?? null, forced],
  );

// The attempt a run's ending closes: this execution's, else the run's latest.
export async function endAttempt(db: Sql, runId: number, status: "completed" | "failed", error: string | null = null): Promise<void> {
  const execution = currentExecution();
  const where = execution ? "workflow_run_id = $3" : "id = (SELECT max(id) FROM run_attempts WHERE run_id = $3::bigint)";
  await db.run(`UPDATE run_attempts SET status = $1, error = COALESCE($2, error), ended_at = now() WHERE ${where} AND status = 'running'`, [status, error, execution ? execution.runId : runId]);
}

export function runActivities(deps: RunDeps) {
  const catalogue = (): CatalogueSource[] => activeSources(JSON.parse(readFileSync(deps.sourcesFile, "utf8")));
  const db = () => openDb(deps.dbUrl);
  // The run's source list: its archived sources.csv, else today's catalogue, archived for the resume.
  const sourceIds = async (runId: number): Promise<string[]> => {
    const csv = await deps.store.find(runId, "sources.csv");
    if (csv) return parse<{ id: string }>(await deps.store.get(csv), { columns: true }).map((s) => s.id);
    const sources = catalogue();
    await deps.store.put(runId, "sources.csv", toCsv(SOURCES_HEADER, sources.map((s) => [s.id, s.name, s.bias, s.factuality, s.perspective])));
    return sources.map((s) => s.id);
  };
  return {
    // A new run: its row, its source list as an artifact (what prepare replays from), and the last
    // sent run's time for the age filter. A resume: the named run as it was.
    startRun: async (input: DigestInput): Promise<{ runId: number; sourceIds: string[]; lastRun: string | null }> => {
      const execution = currentExecution();
      if (input.resumeRun !== undefined) {
        const resumed = input.resumeRun;
        const row = await db().one<{ started_at: string }>("SELECT started_at FROM runs WHERE id=$1", [resumed]);
        if (!row) throw ApplicationFailure.nonRetryable(`no run ${resumed} to resume`, "BadInput");
        const csv = await deps.store.find(resumed, "sources.csv");
        // Never today's catalogue: a resume refetches what the run was meant to fetch, or nothing.
        if (!csv) throw ApplicationFailure.nonRetryable(`run ${resumed} has no sources.csv to resume from`, "MissingInput");
        const ids = parse<{ id: string }>(await deps.store.get(csv), { columns: true }).map((s) => s.id);
        // A resumed run is running again, under a new attempt: the next run's cleanup of abandoned
        // runs never takes back a 'running' run's thread writes. A sent run stays completed.
        if (execution !== undefined)
          await db().tx(async (t) => {
            await t.run(`UPDATE runs SET status = 'running', outcome = NULL WHERE id = $1 AND NOT ${SENT}`, [resumed]);
            await recordAttempt(t, resumed, execution, input.force ?? false);
          });
        return { runId: resumed, sourceIds: ids, lastRun: await lastSent(db(), row.started_at) };
      }
      // Idempotent per workflow execution: a retry after this execution's INSERT committed gets
      // that row back rather than a second one, and the guard below never counts it.
      const own = execution === undefined ? undefined : await db().one<{ id: number }>("SELECT run_id AS id FROM run_attempts WHERE workflow_run_id = $1", [execution.runId]);
      const lastRun = await lastSent(db());
      if (own) return { runId: own.id, sourceIds: await sourceIds(own.id), lastRun };
      // A day already sent, or one still running (under the 4 h run budget, so a crashed run's stale
      // "running" row does not block the day), is refused. The check and the insert hold one lock,
      // so two starts of the same day cannot both pass it.
      const day = input.runDate || new Date().toISOString().slice(0, 10);
      const runId = await db().tx(async (t) => {
        // Again under the lock: an earlier attempt of this execution may have committed while this one waited.
        const mine = execution === undefined ? undefined : await t.one<{ id: number }>("SELECT run_id AS id FROM run_attempts WHERE workflow_run_id = $1", [execution.runId]);
        if (mine) return mine.id;
        if (!input.force) {
          const clash = await t.one<{ id: number; status: string }>(
            `SELECT id, status FROM runs WHERE (started_at AT TIME ZONE 'UTC')::date = $1::date AND (${SENT} OR (status = 'running' AND started_at >= now() - interval '4 hours')) ORDER BY id DESC LIMIT 1`,
            [day],
          );
          if (clash) throw ApplicationFailure.nonRetryable(`${day} already has run ${clash.id} (${clash.status}); start with force to run it again`, "AlreadyRan");
        }
        const inserted = await t.one<{ id: number }>("INSERT INTO runs (git_sha) VALUES ($1) RETURNING id", [process.env["GIT_SHA"] ?? null]);
        if (execution !== undefined) await recordAttempt(t, inserted!.id, execution, input.force ?? false);
        return inserted!.id;
      }, "startRun");
      return { runId, sourceIds: await sourceIds(runId), lastRun };
    },

    // One source: GET, parse, keep what is newer than the last run, archive the raw rows and a health
    // row. Idempotent per run and source, so a resume never refetches. A parse failure is a result
    // (recorded, not retried); a network failure throws for the network retry policy.
    fetchFeed: async (runId: number, sourceId: string, lastRun: string | null): Promise<{ sourceId: string; ok: boolean; fetched: number; kept: number; error?: string }> => {
      const health = await db().one<{ ok: boolean; error: string | null; fetched: number; kept: number }>(
        "SELECT is_success AS ok, error, articles_fetched AS fetched, articles_kept AS kept FROM source_fetches WHERE run_id=$1 AND source_id=$2",
        [runId, sourceId],
      );
      if (health) return { sourceId, ok: health.ok, fetched: health.fetched, kept: health.kept, ...(health.error ? { error: health.error } : {}) };
      const source = catalogue().find((s) => s.id === sourceId);
      if (!source) throw ApplicationFailure.nonRetryable(`${sourceId} is not an active source`, "BadInput");
      let body: string;
      try {
        const res = await (deps.fetch ?? fetch)(source.url, { headers: { "User-Agent": "Mozilla/5.0" }, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        body = await res.text();
      } catch (e) {
        // Every source's outcome is recorded, as the Python records it: retry while attempts remain,
        // and on the last one write the failure so health and alerting see it.
        if (currentAttempt() < (deps.maxAttempts ?? NETWORK_MAX_ATTEMPTS)) throw e;
        const error = `Failed after ${deps.maxAttempts ?? NETWORK_MAX_ATTEMPTS} attempts: ${String(e).slice(0, 200)}`;
        await db().run("INSERT INTO source_fetches (source_id, is_success, error, articles_fetched, articles_kept, run_id) VALUES ($1, false, $2, 0, 0, $3)", [sourceId, error, runId]);
        return { sourceId, ok: false, fetched: 0, kept: 0, error };
      }
      let articles: Fetched[];
      let error: string | undefined;
      try {
        articles = parseArticles(body);
      } catch (e) {
        articles = [];
        error = `Feed parse error: ${String(e).slice(0, 200)}`;
      }
      const kept = newerThan(articles, lastRun);
      await db().tx(async (t) => {
        for (const a of kept) await t.run("INSERT INTO articles (run_id, source_id, title, url, published_raw, summary) VALUES ($1, $2, $3, $4, $5, $6)", [runId, sourceId, a.title, a.url, a.published, a.summary]);
        await t.run("INSERT INTO source_fetches (source_id, is_success, error, articles_fetched, articles_kept, run_id) VALUES ($1, $2, $3, $4, $5, $6)", [sourceId, !error, error ?? null, articles.length, kept.length, runId]);
      });
      return { sourceId, ok: !error, fetched: articles.length, kept: kept.length, ...(error ? { error } : {}) };
    },

    // The outcome says how the run ended (sent, rejected, disabled, skipped); the recipient count is
    // the send's. A sent run's outcome is never rewritten. db.complete_run: articles_kept is the
    // fetch-time count, SUM(source_fetches.articles_kept), as the Python snapshots it.
    finishRun: async (runId: number, out: Omit<DigestOutput, "runId">): Promise<void> => {
      await db().tx(async (t) => {
        if (out.broadcast === "sent")
          await t.run("UPDATE runs SET status='completed', outcome='sent', articles_kept=(SELECT SUM(articles_kept) FROM source_fetches WHERE run_id=$1) WHERE id=$1", [runId]);
        else await t.run("UPDATE runs SET status='completed', outcome=$1 WHERE id=$2 AND outcome IS DISTINCT FROM 'sent'", [out.broadcast, runId]);
        await endAttempt(t, runId, "completed");
      });
    },
  };
}
