import { ApplicationFailure } from "@temporalio/common";
import { parse } from "csv-parse/sync";
import { previousHeadlines, recentDigestHeadlines, recentTitlesCsv, recentTxt, runAt, yesterdayHeadlines, yesterdayTxt } from "../prepare/context.js";
import { ARTICLE_HEADER, prepareArticles, toCsv, type Fetched, type Source } from "../prepare/prepare.js";
import { ConflictError, type ArtifactStore, type Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";

// PREPARE between fetch and curation (spec §2.1): a pure function of the run's archived raw fetch
// (fetched_articles) and its source list, so every downstream stage replays from the archive without
// a refetch. Deterministic, so a re-run writes identical rows; a differing row means the logic or a
// threshold changed, which only force may overwrite.
export function prepareActivity(deps: { store: ArtifactStore; dbPath: string }) {
  return async (runId: number, _fetched: Pointer[], force = false): Promise<{ articles: Pointer[]; index: Pointer }> => {
    const { store } = deps;
    const sourcesPtr = store.find(runId, "sources.csv");
    if (!sourcesPtr) throw ApplicationFailure.nonRetryable(`run ${runId} has no sources.csv; fetch has not run`, "MissingInput");
    const sources = parse<Source>(store.get(sourcesPtr), { columns: true, skip_empty_lines: true });
    const db = openDb(deps.dbPath);
    try {
      const fetched = new Map<string, Fetched[]>();
      for (const r of db.prepare("SELECT source_id, title, url, published, summary FROM fetched_articles WHERE run_id=? ORDER BY id").all(runId) as unknown as (Fetched & { source_id: string })[])
        fetched.set(r.source_id, [...(fetched.get(r.source_id) ?? []), r]);
      if (fetched.size === 0) throw ApplicationFailure.nonRetryable(`run ${runId} has no fetched_articles`, "MissingInput");
      const at = runAt(db, runId);
      const recent = previousHeadlines(db, at);
      const prepared = prepareArticles(sources, fetched, recent.map((h) => h.headline));
      console.log(JSON.stringify({ stage: "prepare", runId, articles: Object.keys(prepared.index).length, deduped: prepared.filtered.length, urlDuplicates: prepared.urlDuplicates }));
      const write = (name: string, text: string): Pointer => {
        if (force) return store.replace(runId, name, text);
        try {
          return store.put(runId, name, text);
        } catch (e) {
          if (e instanceof ConflictError) throw ApplicationFailure.nonRetryable(`prepare for run ${runId}: ${name} differs from the stored one; the logic or a threshold changed, re-run with force`, "PrepareChanged");
          throw e;
        }
      };
      const articles = prepared.files.map((f) => write(f.name, toCsv(ARTICLE_HEADER, f.rows)));
      const index = write("article_index.json", JSON.stringify(prepared.index, null, 2));
      if (recent.length) write("recent_rss_titles.csv", recentTitlesCsv(recent));
      const yesterday = yesterdayHeadlines(db, at);
      if (yesterday.length) write("yesterday_headlines.txt", yesterdayTxt(yesterday));
      const recentDigest = recentDigestHeadlines(db, at);
      if (recentDigest.length) write("recent_digest_headlines.txt", recentTxt(recentDigest));
      return { articles, index };
    } finally {
      db.close();
    }
  };
}
