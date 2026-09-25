import { ApplicationFailure } from "@temporalio/common";
import { resolveArticleIds, type Selections } from "../render/render.js";
import { webArchiveHtml } from "../render/web-archive.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { openDb, type RowOf } from "../store/db.js";
import { MARKDOWN_OUTPUT } from "./render.js";
import type { Track } from "../telemetry.js";
import { endAttempt, tellEnding } from "./run.js";

export interface ShownRow { headline: string; tier: "must_know" | "should_know"; source_id: string | null; source_title: string | null; cluster_id: string | null }

// render.extract_headlines: one row per source per story, over the selections as resolved against
// the run's article index. The next day's prepare reads these back for dedup.
export function shownHeadlines(selections: Selections, index: Record<string, unknown>): ShownRow[] {
  const resolved = resolveArticleIds(selections, index);
  return (["must_know", "should_know"] as const).flatMap((tier) =>
    resolved[tier].flatMap((item) => item.sources.map((src) => ({ headline: item.headline ?? "", tier, source_id: src.source_id ?? null, source_title: src.original_title ?? null, cluster_id: item.cluster_id ?? null }))),
  );
}

export interface RecordDeps { store: ArtifactStore; dbUrl: string; track?: Track }

// The run's record in the tables the web tier and the next day's run read (db.save_digest,
// record_shown_headlines, abort_run). Each is idempotent per run: a retried or resumed activity
// leaves the rows one run would have written.
export function recordActivities(deps: RecordDeps) {
  const { store } = deps;
  const db = () => openDb(deps.dbUrl);
  const selectionsOf = async (p: Pointer) => JSON.parse(await store.get(p)) as Selections;
  return {
    // The selections and clusters are already the run's artifacts; the tables this once copied them
    // into are gone. Kept as an activity because recorded workflow histories schedule it.
    archiveRun: (_runId: number, _selections: Pointer, _clusters: Pointer): Promise<void> => Promise.resolve(),

    // The web publication: a new revision of the run's UTC day, unless the latest one is already
    // this run's page (a retried or resumed save). A forced re-run of a published day adds a
    // revision; the old one stays, and the send record names the revision it mailed.
    saveDigest: async (runId: number, html: Pointer, selections: Pointer): Promise<{ date: string }> => {
      const date = await store.runDate(runId);
      const web = webArchiveHtml(await store.get(html));
      const preheader = (await selectionsOf(selections)).preheader ?? "";
      // NULL when the run rendered no Markdown (rendered before the render wrote it): the site 404s its .md.
      const mdPtr = await store.find(runId, MARKDOWN_OUTPUT);
      const markdown = mdPtr ? await store.get(mdPtr) : null;
      await db().tx(async (t) => {
        const latest = await t.one<Pick<RowOf<"issues">, "revision" | "run_id" | "html" | "preheader">>("SELECT revision, run_id, html, preheader FROM issues WHERE issue_date=$1 ORDER BY revision DESC LIMIT 1", [date]);
        const keep = preheader === "" && latest ? latest.preheader : preheader;
        if (latest && latest.run_id === runId && latest.html === web && latest.preheader === keep) return;
        await t.run("INSERT INTO issues (issue_date, revision, run_id, html, preheader, markdown) VALUES ($1, $2, $3, $4, $5, $6)", [date, (latest?.revision ?? 0) + 1, runId, web, keep, markdown]);
      }, `issue ${date}`);
      return { date };
    },

    recordShownHeadlines: async (runId: number, selections: Pointer): Promise<{ rows: number }> => {
      // Without the index every row would carry no source_id and no source_title, and the next
      // day's dedup would match nothing: fail rather than write them.
      const indexPtr = await store.find(runId, "article_index.json");
      if (!indexPtr) throw ApplicationFailure.nonRetryable(`run ${runId} has no article_index.json to resolve its shown headlines`, "MissingInput");
      const rows = shownHeadlines(await selectionsOf(selections), JSON.parse(await store.get(indexPtr)) as Record<string, unknown>);
      await db().tx(async (t) => {
        await t.run("DELETE FROM story_sources WHERE run_id=$1", [runId]);
        for (const r of rows) await t.run("INSERT INTO story_sources (headline, tier, source_id, source_title, cluster_id, run_id) VALUES ($1, $2, $3, $4, $5, $6)", [r.headline, r.tier, r.source_id, r.source_title, r.cluster_id, runId]);
      }, `shown ${runId}`);
      return { rows: rows.length };
    },

    // Marked, never deleted: a run that failed after its send keeps its record (2026-06-16). Only a
    // running run fails; a completed one keeps its outcome, and the attempt carries the error.
    abortRun: async (runId: number, error: string): Promise<void> => {
      await db().tx(async (t) => {
        await t.run("UPDATE runs SET status='failed', error=$1 WHERE id=$2 AND status='running'", [error, runId]);
        await endAttempt(t, runId, "failed", error);
      });
      await tellEnding(deps.track, db(), runId, { outcome: "failed", error }, "digest_run_failed");
    },
  };
}
