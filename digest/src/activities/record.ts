import type { DatabaseSync } from "node:sqlite";
import { ApplicationFailure } from "@temporalio/common";
import { resolveArticleIds, type Selections } from "../render/render.js";
import { webArchiveHtml } from "../render/web-archive.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";

export interface ShownRow { headline: string; tier: "must_know" | "should_know"; source_id: string | null; original_title: string | null; cluster_id: string | null }

// render.extract_headlines: one row per source per story, over the selections as resolved against
// the run's article index. The next day's prepare reads these back for dedup.
export function shownHeadlines(selections: Selections, index: Record<string, unknown>): ShownRow[] {
  const resolved = resolveArticleIds(selections, index);
  return (["must_know", "should_know"] as const).flatMap((tier) =>
    resolved[tier].flatMap((item) => item.sources.map((src) => ({ headline: item.headline ?? "", tier, source_id: src.source_id ?? null, original_title: src.original_title ?? null, cluster_id: item.cluster_id ?? null }))),
  );
}

const withDb = <T>(path: string, fn: (db: DatabaseSync) => T): T => {
  const db = openDb(path);
  try {
    return fn(db);
  } finally {
    db.close();
  }
};
const tx = <T>(db: DatabaseSync, fn: () => T): T => {
  db.exec("BEGIN IMMEDIATE");
  try {
    const out = fn();
    db.exec("COMMIT");
    return out;
  } catch (e) {
    db.exec("ROLLBACK");
    throw e;
  }
};

// One archived blob per run in a (run_id, blob) table: kept when equal, replaced when a forced
// re-run changed it.
const archiveBlob = (db: DatabaseSync, table: "selections" | "cluster_runs", column: "selections_json" | "clusters_json", runId: number, content: string) => {
  const rows = db.prepare(`SELECT ${column} AS c FROM ${table} WHERE run_id=?`).all(runId) as { c: string }[];
  if (rows.length === 1 && rows[0]!.c === content) return;
  db.prepare(`DELETE FROM ${table} WHERE run_id=?`).run(runId);
  db.prepare(`INSERT INTO ${table} (run_id, ${column}) VALUES (?, ?)`).run(runId, content);
};

export interface RecordDeps { store: ArtifactStore; dbPath: string }

// The run's record in the tables the web tier and the next day's run read (db.save_digest,
// record_shown_headlines, archive_selections, archive_clusters, abort_run). Each is idempotent per
// run: a retried or resumed activity leaves the rows one run would have written.
export function recordActivities(deps: RecordDeps) {
  const { store } = deps;
  const selectionsOf = (p: Pointer) => JSON.parse(store.get(p)) as Selections;
  return {
    archiveRun: (runId: number, selections: Pointer, clusters: Pointer): Promise<void> => {
      const sel = store.get(selections);
      const cl = store.get(clusters);
      withDb(deps.dbPath, (db) =>
        tx(db, () => {
          archiveBlob(db, "selections", "selections_json", runId, sel);
          archiveBlob(db, "cluster_runs", "clusters_json", runId, cl);
        }),
      );
      return Promise.resolve();
    },

    // The digests row is keyed by the run's UTC day; the broadcast columns on it are the send's
    // idempotency record, so an upsert never touches them.
    saveDigest: (runId: number, html: Pointer, selections: Pointer): Promise<{ date: string }> => {
      const date = store.runDate(runId);
      const web = webArchiveHtml(store.get(html));
      const preheader = selectionsOf(selections).preheader ?? "";
      withDb(deps.dbPath, (db) =>
        db
          .prepare(
            `INSERT INTO digests (date, html, preheader, run_id) VALUES (?, ?, ?, ?)
             ON CONFLICT(date) DO UPDATE SET
               html = excluded.html,
               preheader = CASE WHEN excluded.preheader = '' THEN digests.preheader ELSE excluded.preheader END,
               run_id = excluded.run_id`,
          )
          .run(date, web, preheader, runId),
      );
      return Promise.resolve({ date });
    },

    recordShownHeadlines: async (runId: number, selections: Pointer): Promise<{ rows: number }> => {
      // Without the index every row would carry no source_id and no original_title, and the next
      // day's dedup would match nothing: fail rather than write them.
      const indexPtr = store.find(runId, "article_index.json");
      if (!indexPtr) throw ApplicationFailure.nonRetryable(`run ${runId} has no article_index.json to resolve its shown headlines`, "MissingInput");
      const rows = shownHeadlines(selectionsOf(selections), JSON.parse(store.get(indexPtr)) as Record<string, unknown>);
      withDb(deps.dbPath, (db) =>
        tx(db, () => {
          db.prepare("DELETE FROM shown_narratives WHERE run_id=?").run(runId);
          const ins = db.prepare("INSERT INTO shown_narratives (headline, tier, source_id, original_title, cluster_id, run_id) VALUES (?, ?, ?, ?, ?, ?)");
          for (const r of rows) ins.run(r.headline, r.tier, r.source_id, r.original_title, r.cluster_id, runId);
        }),
      );
      return { rows: rows.length };
    },

    // Marked, never deleted: a run that failed after its send keeps its record (2026-06-16).
    abortRun: (runId: number, error: string): Promise<void> => {
      withDb(deps.dbPath, (db) => db.prepare("UPDATE digest_runs SET status='failed', error=? WHERE id=?").run(error, runId));
      return Promise.resolve();
    },
  };
}
