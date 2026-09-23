import { parse } from "csv-parse/sync";
import { describe, expect, it } from "vitest";
import { previousHeadlines, recentDigestHeadlines, recentTitlesCsv, recentTxt, runAt, yesterdayHeadlines, yesterdayTxt } from "./context.js";
import { ARTICLE_HEADER, prepareArticles, toCsv, type Fetched, type Source } from "./prepare.js";
import { openDb } from "../store/db.js";

// Host-only: the production clone, imported into Postgres (bin/import-legacy), is not in the CI
// image. Replays run 300's prepare from its archived raw fetch and source list and holds the result
// equal to the archived article CSVs. PARITY_DATABASE_URL names the imported database.
const flat = (fs: { rows: string[][] }[]) => fs.flatMap((f) => f.rows);
const stripWire = (i: Record<string, Record<string, unknown>>) => Object.fromEntries(Object.entries(i).map(([k, v]) => [k, { ...v, wire_agency: undefined }]));
// The archive never stored author, so an author-derived label cannot be replayed; every other label must match.
const wireMismatches = (ours: Record<string, { wire_agency: string | null }>, arch: Record<string, Record<string, unknown>>) =>
  Object.entries(ours).filter(([k, v]) => v.wire_agency !== null && v.wire_agency !== arch[k]?.["wire_agency"]).map(([k]) => k);
const PARITY_URL = process.env["PARITY_DATABASE_URL"];

describe.skipIf(!PARITY_URL)("prepare parity with the Python on run 300", () => {
  it("reproduces the archived article CSVs row for row and file for file", async () => {
    const db = openDb(PARITY_URL!);
    const contents = new Map((await db.all<{ n: string; c: string }>("SELECT artifact_name AS n, content AS c FROM run_artifacts WHERE run_id=300 AND state='current'")).map((r) => [r.n, r.c]));
    const art = (name: string) => contents.get(name);
    const sources = parse<Source>(art("sources.csv")!, { columns: true });
    const fetched = new Map<string, Fetched[]>();
    for (const r of await db.all<Fetched & { source_id: string }>("SELECT source_id, title, url, published, summary FROM fetched_articles WHERE run_id=300 ORDER BY id"))
      fetched.set(r.source_id, [...(fetched.get(r.source_id) ?? []), r]);
    const titles = (await previousHeadlines(db, await runAt(db, 300))).map((h) => h.headline);
    const ours = prepareArticles(sources, fetched, titles, { scrubLinks: false });
    console.log(`run 300: ${ours.files.length} files, ${ours.files.reduce((n, f) => n + f.rows.length, 0)} articles, ${ours.filtered.length} deduped, ${ours.urlDuplicates} repeated URLs, ${titles.length} recent titles`);
    const archivedNames = [...contents.keys()].filter((n) => /^articles_\d+\.csv$/.test(n)).toSorted();
    const archived = archivedNames.map((n) => ({ name: n, rows: parse(art(n)!, { relax_column_count: true }).slice(1) }));
    expect(ours.files.map((f) => [f.name, f.rows.length])).toEqual(archived.map((f) => [f.name, f.rows.length]));
    const a = flat(ours.files);
    const b = flat(archived);
    const firstDiff = a.findIndex((row, i) => JSON.stringify(row) !== JSON.stringify(b[i]));
    expect(firstDiff === -1 ? null : { ours: a[firstDiff], archived: b[firstDiff] }).toBeNull();
    for (const f of ours.files) expect(toCsv(ARTICLE_HEADER, f.rows), f.name).toBe(art(f.name));
    const at = await runAt(db, 300);
    expect(recentTitlesCsv(await previousHeadlines(db, at))).toBe(art("recent_rss_titles.csv"));
    expect(yesterdayTxt(await yesterdayHeadlines(db, at))).toBe(art("yesterday_headlines.txt"));
    expect(recentTxt(await recentDigestHeadlines(db, at))).toBe(art("recent_digest_headlines.txt"));
    const archivedIndex = JSON.parse(art("article_index.json")!) as Record<string, Record<string, unknown>>;
    expect(stripWire(ours.index as unknown as Record<string, Record<string, unknown>>)).toEqual(stripWire(archivedIndex));
    expect(wireMismatches(ours.index, archivedIndex)).toEqual([]);
    const derived = Object.values(ours.index).filter((v) => v.wire_agency).length;
    const archivedLabels = Object.values(archivedIndex).filter((v) => v["wire_agency"]).length;
    console.log(`wire labels: ${derived} derivable of ${archivedLabels} archived (the rest came from author)`);
  });
});
