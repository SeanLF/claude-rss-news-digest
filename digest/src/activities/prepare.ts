import { ApplicationFailure } from "@temporalio/common";
import { parse } from "csv-parse/sync";
import { previousHeadlines, recentDigestHeadlines, recentTitlesCsv, recentTxt, runAt, yesterdayHeadlines, yesterdayTxt } from "../prepare/context.js";
import { ARTICLE_HEADER, DEDUP_SIMILARITY_THRESHOLD, prepareArticles, toCsv, type Fetched, type Source } from "../prepare/prepare.js";
import { ConflictError, type ArtifactStore, type Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import type { FetchSummary } from "./index.js";

// PREPARE between fetch and curation (spec §2.1): a pure function of the run's archived raw fetch
// (articles) and its source list, so every downstream stage replays from the archive without
// a refetch. Deterministic, so a re-run writes identical rows; a differing row means the logic or a
// threshold changed, which only force may overwrite.
export function prepareActivity(deps: { store: ArtifactStore; dbUrl: string }) {
  return async (runId: number, _fetched: FetchSummary[], force = false): Promise<{ articles: Pointer[]; index: Pointer }> => {
    const { store } = deps;
    const sourcesPtr = await store.find(runId, "sources.csv");
    if (!sourcesPtr) throw ApplicationFailure.nonRetryable(`run ${runId} has no sources.csv; fetch has not run`, "MissingInput");
    const sources = parse<Source>(await store.get(sourcesPtr), { columns: true, skip_empty_lines: true });
    const db = openDb(deps.dbUrl);
    {
      const fetched = new Map<string, Fetched[]>();
      for (const r of await db.all<Fetched & { source_id: string }>("SELECT source_id, title, url, published_raw AS published, summary FROM articles WHERE run_id=$1 ORDER BY id", [runId]))
        fetched.set(r.source_id, [...(fetched.get(r.source_id) ?? []), r]);
      if (fetched.size === 0) throw ApplicationFailure.nonRetryable(`run ${runId} has no articles`, "MissingInput");
      const at = await runAt(db, runId);
      const recent = await previousHeadlines(db, at);
      const prepared = prepareArticles(sources, fetched, recent.map((h) => h.headline));
      console.log(JSON.stringify({ stage: "prepare", runId, articles: Object.keys(prepared.index).length, deduped: prepared.filtered.length, urlDuplicates: prepared.urlDuplicates }));
      const write = async (name: string, text: string): Promise<Pointer> => {
        if (force) return store.replace(runId, name, text);
        try {
          return await store.put(runId, name, text);
        } catch (e) {
          if (e instanceof ConflictError) throw ApplicationFailure.nonRetryable(`prepare for run ${runId}: ${name} differs from the stored one; the logic or a threshold changed, re-run with force`, "PrepareChanged");
          throw e;
        }
      };
      const articles: Pointer[] = [];
      for (const f of prepared.files) articles.push(await write(f.name, toCsv(ARTICLE_HEADER, f.rows)));
      // One dedup_matches row per title dropped as a repeat, as the Python writes; only once per run, so
      // a re-run of a deterministic prepare does not duplicate them.
      await db.tx(async (t) => {
        if ((await t.one("SELECT 1 FROM dedup_matches WHERE run_id=$1 LIMIT 1", [runId])) !== undefined) return;
        for (const d of prepared.filtered)
          await t.run("INSERT INTO dedup_matches (title, source_id, matched_headline, similarity, threshold, run_id) VALUES ($1, $2, $3, $4, $5, $6)", [d.title, d.source_id, d.matched, d.similarity, DEDUP_SIMILARITY_THRESHOLD, runId]);
      }, `dedup ${runId}`);
      const index = await write("article_index.json", JSON.stringify(prepared.index, null, 2));
      if (recent.length) await write("recent_rss_titles.csv", recentTitlesCsv(recent));
      const yesterday = await yesterdayHeadlines(db, at);
      if (yesterday.length) await write("yesterday_headlines.txt", yesterdayTxt(yesterday));
      const recentDigest = await recentDigestHeadlines(db, at);
      if (recentDigest.length) await write("recent_digest_headlines.txt", recentTxt(recentDigest));
      return { articles, index };
    }
  };
}
