import { existsSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { parse } from "csv-parse/sync";
import { describe, expect, it } from "vitest";
import { previousHeadlines, recentDigestHeadlines, recentTitlesCsv, recentTxt, runAt, yesterdayHeadlines, yesterdayTxt } from "./context.js";
import { ARTICLE_HEADER, prepareArticles, toCsv, type Fetched, type Source } from "./prepare.js";

// Host-only: the production DB clone is not in the CI image. Replays run 300's prepare from its
// archived raw fetch and source list and holds the result equal to the archived article CSVs.
const flat = (fs: { rows: string[][] }[]) => fs.flatMap((f) => f.rows);
const stripWire = (i: Record<string, Record<string, unknown>>) => Object.fromEntries(Object.entries(i).map(([k, v]) => [k, { ...v, wire_agency: undefined }]));
// The archive never stored author, so an author-derived label cannot be replayed; every other label must match.
const wireMismatches = (ours: Record<string, { wire_agency: string | null }>, arch: Record<string, Record<string, unknown>>) =>
  Object.entries(ours).filter(([k, v]) => v.wire_agency !== null && v.wire_agency !== arch[k]?.["wire_agency"]).map(([k]) => k);
const DB = new URL("../../../data/digest.db", import.meta.url).pathname;

describe.skipIf(!existsSync(DB))("prepare parity with the Python on run 300", () => {
  it("reproduces the archived article CSVs row for row and file for file", () => {
    const db = new DatabaseSync(DB, { readOnly: true });
    const art = (name: string) => (db.prepare("SELECT content FROM run_artifacts WHERE run_id=300 AND artifact_name=?").get(name) as { content: string } | undefined)?.content;
    const sources = parse<Source>(art("sources.csv")!, { columns: true });
    const fetched = new Map<string, Fetched[]>();
    for (const r of db.prepare("SELECT source_id, title, url, published, summary FROM fetched_articles WHERE run_id=300 ORDER BY id").all() as unknown as (Fetched & { source_id: string })[])
      fetched.set(r.source_id, [...(fetched.get(r.source_id) ?? []), r]);
    const titles = previousHeadlines(db, runAt(db, 300)).map((h) => h.headline);
    const ours = prepareArticles(sources, fetched, titles, { scrubLinks: false });
    console.log(`run 300: ${ours.files.length} files, ${ours.files.reduce((n, f) => n + f.rows.length, 0)} articles, ${ours.filtered.length} deduped, ${ours.urlDuplicates} repeated URLs, ${titles.length} recent titles`);
    const archivedNames = (db.prepare("SELECT artifact_name AS n FROM run_artifacts WHERE run_id=300 AND artifact_name LIKE 'articles_%.csv' ORDER BY artifact_name").all() as { n: string }[]).map((r) => r.n);
    const archived = archivedNames.map((n) => ({ name: n, rows: parse(art(n)!, { relax_column_count: true }).slice(1) }));
    expect(ours.files.map((f) => [f.name, f.rows.length])).toEqual(archived.map((f) => [f.name, f.rows.length]));
    const a = flat(ours.files);
    const b = flat(archived);
    const firstDiff = a.findIndex((row, i) => JSON.stringify(row) !== JSON.stringify(b[i]));
    expect(firstDiff === -1 ? null : { ours: a[firstDiff], archived: b[firstDiff] }).toBeNull();
    for (const f of ours.files) expect(toCsv(ARTICLE_HEADER, f.rows), f.name).toBe(art(f.name));
    const at = runAt(db, 300);
    expect(recentTitlesCsv(previousHeadlines(db, at))).toBe(art("recent_rss_titles.csv"));
    expect(yesterdayTxt(yesterdayHeadlines(db, at))).toBe(art("yesterday_headlines.txt"));
    expect(recentTxt(recentDigestHeadlines(db, at))).toBe(art("recent_digest_headlines.txt"));
    const archivedIndex = JSON.parse(art("article_index.json")!) as Record<string, Record<string, unknown>>;
    expect(stripWire(ours.index as unknown as Record<string, Record<string, unknown>>)).toEqual(stripWire(archivedIndex));
    expect(wireMismatches(ours.index, archivedIndex)).toEqual([]);
    const derived = Object.values(ours.index).filter((v) => v.wire_agency).length;
    const archivedLabels = Object.values(archivedIndex).filter((v) => v["wire_agency"]).length;
    console.log(`wire labels: ${derived} derivable of ${archivedLabels} archived (the rest came from author)`);
  });
});
