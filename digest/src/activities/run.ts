import { readFileSync } from "node:fs";
import { Context } from "@temporalio/activity";
import { ApplicationFailure } from "@temporalio/common";
import { NETWORK_MAX_ATTEMPTS } from "../workflow/policy.js";
import { parse } from "csv-parse/sync";
import { activeSources, newerThan, parseArticles, type CatalogueSource } from "../fetch/feeds.js";
import { toCsv, type Fetched } from "../prepare/prepare.js";
import type { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import type { DigestInput, DigestOutput } from "./index.js";

export const SOURCES_HEADER = ["id", "name", "bias", "factuality", "perspective"] as const;
export const FETCH_TIMEOUT_MS = 15_000;
const lastCompleted = (db: ReturnType<typeof openDb>, before?: string): string | null =>
  ((before
    ? db.prepare("SELECT MAX(run_at) AS t FROM digest_runs WHERE completed_at IS NOT NULL AND run_at < ?").get(before)
    : db.prepare("SELECT MAX(run_at) AS t FROM digest_runs WHERE completed_at IS NOT NULL").get()) as { t: string | null }).t;

export interface RunDeps {
  store: ArtifactStore;
  dbPath: string;
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
const currentExecution = (): string | undefined => {
  try {
    return Context.current().info.workflowExecution?.runId;
  } catch {
    return undefined;
  }
};

export function runActivities(deps: RunDeps) {
  const catalogue = (): CatalogueSource[] => activeSources(JSON.parse(readFileSync(deps.sourcesFile, "utf8")));
  return {
    // A new run: its row, its source list as an artifact (what prepare replays from), and the last
    // completed run's time for the age filter. A resume: the named run as it was.
    startRun: async (input: DigestInput): Promise<{ runId: number; sourceIds: string[]; lastRun: string | null }> => {
      const db = openDb(deps.dbPath);
      try {
        if (input.resumeRun !== undefined) {
          const row = db.prepare("SELECT run_at FROM digest_runs WHERE id=?").get(input.resumeRun) as { run_at: string } | undefined;
          if (!row) throw ApplicationFailure.nonRetryable(`no run ${input.resumeRun} to resume`, "BadInput");
          const csv = deps.store.find(input.resumeRun, "sources.csv");
          // Never today's catalogue: a resume refetches what the run was meant to fetch, or nothing.
          if (!csv) throw ApplicationFailure.nonRetryable(`run ${input.resumeRun} has no sources.csv to resume from`, "MissingInput");
          const ids = parse<{ id: string }>(deps.store.get(csv), { columns: true }).map((s) => s.id);
          // A resumed run is running again, owned by this execution: the next run's cleanup of
          // abandoned runs never takes back a 'running' run's thread writes. A delivered run stays completed.
          const execution = currentExecution();
          if (execution !== undefined) db.prepare("UPDATE digest_runs SET status = 'running', workflow_run_id = ? WHERE id = ? AND completed_at IS NULL").run(execution, input.resumeRun);
          return { runId: input.resumeRun, sourceIds: ids, lastRun: lastCompleted(db, row.run_at) };
        }
        // Idempotent per workflow execution: a retry after this execution's INSERT committed gets
        // that row back rather than a second one, and the guard below never counts it.
        const execution = currentExecution();
        const own = execution === undefined ? undefined : db.prepare("SELECT id FROM digest_runs WHERE workflow_run_id = ?").get(execution);
        const lastRun = lastCompleted(db);
        const sourceIds = (runId: number): string[] => {
          const csv = deps.store.find(runId, "sources.csv");
          if (csv) return parse<{ id: string }>(deps.store.get(csv), { columns: true }).map((s) => s.id);
          const sources = catalogue();
          deps.store.put(runId, "sources.csv", toCsv(SOURCES_HEADER, sources.map((s) => [s.id, s.name, s.bias, s.factuality, s.perspective])));
          return sources.map((s) => s.id);
        };
        if (own) {
          const runId = Number(own["id"]);
          return { runId, sourceIds: sourceIds(runId), lastRun };
        }
        // Both pipelines write digest_runs, so this is the one place a half-applied switch that
        // armed both is caught: a day already sent, or one still running (under the 4 h run
        // budget, so a crashed run's stale "running" row does not block the day), is refused.
        const day = input.runDate || new Date().toISOString().slice(0, 10);
        if (!input.force) {
          const clash = db
            .prepare("SELECT id, status FROM digest_runs WHERE date(run_at) = ? AND (completed_at IS NOT NULL OR (status = 'running' AND run_at >= datetime('now', '-4 hours'))) ORDER BY id DESC LIMIT 1")
            .get(day);
          if (clash) throw ApplicationFailure.nonRetryable(`${day} already has run ${String(clash["id"])} (${String(clash["status"])}); start with force to run it again`, "AlreadyRan");
        }
        const { lastInsertRowid } = db.prepare("INSERT INTO digest_runs (articles_kept, articles_emailed, git_sha, workflow_run_id) VALUES (NULL, NULL, ?, ?)").run(process.env["GIT_SHA"] ?? null, execution ?? null);
        const runId = Number(lastInsertRowid);
        return { runId, sourceIds: sourceIds(runId), lastRun };
      } finally {
        db.close();
      }
    },

    // One source: GET, parse, keep what is newer than the last run, archive the raw rows and a health
    // row. Idempotent per run and source, so a resume never refetches. A parse failure is a result
    // (recorded, not retried); a network failure throws for the network retry policy.
    fetchFeed: async (runId: number, sourceId: string, lastRun: string | null): Promise<{ sourceId: string; ok: boolean; fetched: number; kept: number; error?: string }> => {
      const db = openDb(deps.dbPath);
      try {
        const health = db.prepare("SELECT success, error_message AS error, articles_fetched AS fetched, articles_kept AS kept FROM source_health WHERE run_id=? AND source_id=?").get(runId, sourceId) as { success: number; error: string | null; fetched: number; kept: number } | undefined;
        if (health) return { sourceId, ok: health.success === 1, fetched: health.fetched, kept: health.kept, ...(health.error ? { error: health.error } : {}) };
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
          db.prepare("INSERT INTO source_health (source_id, success, error_message, articles_fetched, articles_kept, run_id) VALUES (?, 0, ?, 0, 0, ?)").run(sourceId, error, runId);
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
        const ins = db.prepare("INSERT INTO fetched_articles (run_id, source_id, title, url, published, summary) VALUES (?, ?, ?, ?, ?, ?)");
        db.exec("BEGIN");
        for (const a of kept) ins.run(runId, sourceId, a.title, a.url, a.published, a.summary);
        db.prepare("INSERT INTO source_health (source_id, success, error_message, articles_fetched, articles_kept, run_id) VALUES (?, ?, ?, ?, ?, ?)").run(sourceId, error ? 0 : 1, error ?? null, articles.length, kept.length, runId);
        db.exec("COMMIT");
        return { sourceId, ok: !error, fetched: articles.length, kept: kept.length, ...(error ? { error } : {}) };
      } finally {
        db.close();
      }
    },

    // completed_at is what "readers saw this run" means to every context query, so only a sent digest
    // sets it; any other ending leaves it NULL and its status says why (rejected, disabled, skipped).
    // db.complete_run: articles_emailed is the send's recipient count (the column is misnamed), and
    // articles_kept the fetch-time count, SUM(source_health.articles_kept), as the Python snapshots it.
    finishRun: async (runId: number, out: Omit<DigestOutput, "runId">): Promise<void> => {
      const db = openDb(deps.dbPath);
      try {
        if (out.broadcast === "sent")
          db.prepare("UPDATE digest_runs SET completed_at = COALESCE(completed_at, datetime('now', 'utc')), status='completed', articles_emailed=?, articles_kept=(SELECT SUM(articles_kept) FROM source_health WHERE run_id=?) WHERE id=?").run(out.recipients ?? 0, runId, runId);
        else db.prepare("UPDATE digest_runs SET status=? WHERE id=? AND completed_at IS NULL").run(out.broadcast, runId);
      } finally {
        db.close();
      }
    },
  };
}
