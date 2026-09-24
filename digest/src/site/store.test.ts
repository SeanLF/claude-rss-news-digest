import { beforeEach, describe, expect, it } from "vitest";
import { type Db, openDb } from "../store/db.js";
import { migratedDb } from "../store/test-db.js";
import { siteStore } from "./store.js";

// The site's reads against the real schema, on PGlite. Runs 1-5: 1-4 completed and sent on
// 2026-08-28..31, run 5 held (completed, not sent, not on the web).
let db: Db;
const store = () => siteStore(db);

async function seed(): Promise<void> {
  db = openDb(await migratedDb([1, 2, 3, 4, 5].map((id) => ({ id, runAt: new Date(Date.UTC(2026, 7, 27 + id, 10, 25)).toISOString() }))));
  await db.exec(`
    UPDATE runs SET status = 'completed', outcome = 'sent', articles_kept = 500 + id WHERE id <= 4;
    UPDATE runs SET status = 'completed', outcome = 'held-out' WHERE id = 5;
    INSERT INTO issues (issue_date, revision, run_id, html, preheader) VALUES
      ('2026-08-28', 1, 1, '<main>one</main>', 'Pre one'),
      ('2026-08-29', 1, 2, '<main>two</main>', 'Pre two'),
      ('2026-08-31', 1, 3, '<main>three, first</main>', 'Pre three'),
      ('2026-08-31', 2, 4, '<main>three, revised</main>', 'Pre three revised'),
      ('2026-09-01', 1, NULL, '<main>unlinked</main>', '');
    INSERT INTO sends (issue_date, run_id, revision, status, recipients) VALUES
      ('2026-08-28', 1, 1, 'sent', 10), ('2026-08-29', 2, 1, 'sent', 11), ('2026-08-31', 3, 1, 'sent', 12);
    INSERT INTO story_sources (run_id, headline, tier, source_id, source_title, shown_at) VALUES
      (1, 'Ceasefire holds in the north', 'must_know', 'reuters', 'Ceasefire holds', '2026-08-28 10:40'),
      (1, 'Ceasefire holds in the north', 'must_know', 'bbc_world', 'Truce holds', '2026-08-28 10:40'),
      (1, 'Chips export rules tighten', 'should_know', 'reuters', 'Chips', '2026-08-28 10:40'),
      (2, 'Election results contested', 'must_know', 'al_jazeera', 'Vote', '2026-08-29 10:40'),
      (4, 'Holds and ceasefire talk', 'should_know', 'reuters', 'x', '2026-08-31 10:40');
    INSERT INTO model_calls (run_id, stage, request_model, api_cost_usd) VALUES (1, 'write', 'm', 0.1), (1, 'write', 'm', 0.2), (2, 'write', 'm', 1.5);
    INSERT INTO source_fetches (source_id, is_success, fetched_at) VALUES
      ('reuters', true, '2026-08-30'), ('reuters', false, '2026-08-30'), ('dead_feed', false, '2026-08-30'), ('reuters', true, '2026-07-01');
    INSERT INTO dedup_matches (title, matched_headline, similarity, threshold, matched_at) VALUES ('a', 'b', 0.1, 0.8, '2026-08-30'), ('a', 'b', 0.2, 0.8, '2026-08-30'), ('a', 'b', 0.99, 0.8, '2026-07-01');
    INSERT INTO threads (id, created_run_id) VALUES (10, 1), (11, 5), (12, 1);
    UPDATE threads SET merged_into_id = 10 WHERE id = 12;
    INSERT INTO thread_updates (thread_id, run_id, label, is_continuation, content) VALUES
      (10, 1, 'Ceasefire', false, '{"whats_new":[{"fact":"Guns fell silent [A1].","sources":["A1"]}],"cited_ids":["A1"]}'),
      (10, 2, 'Ceasefire holds', true, '{"whats_new":[{"fact":"Talks resume.","sources":["A2"]}]}'),
      (10, 5, 'Ceasefire (held run)', true, '{"whats_new":[{"fact":"Held, never published.","sources":[]}]}'),
      (11, 5, 'Only in a held run', false, NULL);
    INSERT INTO thread_questions (thread_id, question, raised_run_id) VALUES (10, 'Will it hold?', 2), (10, 'Does A2 say so?', 2), (10, 'Held question?', 5);
  `);
}

describe("the site's reads", () => {
  beforeEach(seed);

  it("counts issues by date and serves each date's highest revision", async () => {
    expect(await store().indexMeta()).toEqual({ total: 4, firstDate: "2026-08-28", newestDate: "2026-09-01", totalStories: 4 });
    expect(await store().issue("2026-08-31")).toEqual({ html: "<main>three, revised</main>", preheader: "Pre three revised", markdown: null });
    await db.exec("UPDATE issues SET markdown = 'three, as Markdown' WHERE issue_date = '2026-08-31' AND revision = 2");
    expect((await store().issue("2026-08-31"))?.markdown).toBe("three, as Markdown");
    expect(await store().issue("2026-8-31")).toBeUndefined();
    expect(await store().latestIssueDate()).toBe("2026-09-01");
    expect((await store().feed(30)).map((r) => r.preheader)).toEqual(["", "Pre three revised", "Pre two", "Pre one"]);
  });

  it("numbers the running order, marks each month's newest issue, and lists each run's sources", async () => {
    const rows = await store().archive({ limit: 10 });
    expect(rows.map((r) => [r.date, r.issueNo, r.isMonthStart])).toEqual([
      ["2026-09-01", 4, true],
      ["2026-08-31", 3, true],
      ["2026-08-29", 2, false],
      ["2026-08-28", 1, false],
    ]);
    const first = rows.at(-1)!;
    expect([first.must, first.should, first.sourceIds.toSorted()]).toEqual([1, 1, ["bbc_world", "reuters"]]);
    expect(rows[0]!.sourceIds).toEqual([]);
    expect((await store().archive({ before: "2026-08-31", limit: 1 })).map((r) => r.date)).toEqual(["2026-08-29"]);
    expect((await store().archive({ year: 2026, before: "2026-08-29", limit: 1 })).length).toBe(4);
    // Any text is a cursor, compared as text as SQLite did, never a failed cast.
    expect((await store().archive({ before: "not a date", limit: 5 })).length).toBe(4);
  });

  it("searches a literal phrase, operators and all, one row per story, with the issue it ran in", async () => {
    // Run 1's story is cited by two sources: one row, not two (docs/proposed/2026-09-23-search-tuning).
    const hits = await store().search("ceasefire holds", 50);
    expect(hits.map((h) => h.headline)).toEqual(["Ceasefire holds in the north"]);
    expect(hits[0]!.date).toBe("2026-08-28");
    expect(await store().search('ceasefire" OR (x) headline:*', 50)).toEqual([]);
  });

  it("answers a query of English stop words only unstemmed, as the Rust site did, not with nothing", async () => {
    expect((await store().search("the", 50)).map((h) => h.headline)).toEqual(["Ceasefire holds in the north"]);
    expect(await store().search("zzqq", 50)).toEqual([]);
  });

  it("shows a thread only through published runs", async () => {
    const idx = await store().threadIndex(undefined, 30);
    expect(idx.ongoing.map((t) => [t.id, t.label, t.updateCount])).toEqual([[10, "Ceasefire holds", 2]]);
    expect(idx.ongoing[0]!.latestContent).toContain("Talks resume.");
    expect(await store().thread(11)).toBeUndefined();
    const t = (await store().thread(10))!;
    expect(t.installments.map((i) => [i.day, i.issueDate, i.story])).toEqual([
      ["2026-08-29", "2026-08-29", "Ceasefire holds"],
      ["2026-08-28", "2026-08-28", "Ceasefire"],
    ]);
    expect(t.openQuestions.map((q) => q.question)).toEqual(["Does A2 say so?", "Will it hold?"]);
    expect(await store().mergedInto(12)).toBe(10);
    expect(await store().mergedInto(10)).toBeNull();
    expect(await store().mergedInto(999)).toBeUndefined();
  });

  it("windows the stats from the clock it is given, not the database's", async () => {
    const s = await store().stats(7, new Date("2026-09-01T00:00:00Z"));
    expect(s.sourceHealth).toEqual([
      { sourceId: "dead_feed", total: 1, successes: 0 },
      { sourceId: "reuters", total: 2, successes: 1 },
    ]);
    expect(s.dedup).toMatchObject({ count: 2, min: 0.1, max: 0.2 });
    expect(s.dedup.avg).toBeCloseTo(0.15, 12);
    expect(s.neverSelected).toEqual(["dead_feed"]);
    expect(s.sourceUsage[0]).toEqual({ sourceId: "reuters", tier: "should_know", count: 2 });
    // Emailed runs only (run 5 was held), newest first, recipients from the send.
    expect(s.recentRuns.map((r) => [r.runAt, r.recipients, r.apiCostUsd])).toEqual([
      ["2026-08-31 10:25:00", 0, null],
      ["2026-08-30 10:25:00", 12, null],
      ["2026-08-29 10:25:00", 11, 1.5],
      // Summed exactly and rounded once: 0.3, where a float sum gives 0.30000000000000004.
      ["2026-08-28 10:25:00", 10, 0.3],
    ]);
    // Run 4 has no send yet, so the subscriber count is the newest one recorded, run 3's.
    expect(s.cost).toEqual({ runs: 4, keptTotal: 2010, costTotal: 1.8, shippedTotal: 4, recipientsLatest: 12 });
    // A week back from 2026-09-20 reaches none of the fetches (the database's own clock is later still).
    const later = await store().stats(7, new Date("2026-09-20T00:00:00Z"));
    expect([later.sourceHealth, later.dedup.count, later.cost.runs]).toEqual([[], 0, 0]);
  });
});

describe("the site's reads, at the edges of their inputs", () => {
  beforeEach(seed);

  it("answers a year no issue can have with an empty page, and a window past year 1 with every row", async () => {
    expect(await store().archive({ year: 99_999_999_999, limit: 10 })).toEqual([]);
    const all = await store().stats(740_000, new Date("2026-09-01T00:00:00Z"));
    expect(all.sourceHealth.map((h) => h.sourceId)).toEqual(["dead_feed", "reuters"]);
  });

  it("counts subscribers from the newest send that recorded a count", async () => {
    await db.exec("UPDATE runs SET status = 'running', outcome = NULL WHERE id = 5");
    await db.exec("UPDATE runs SET status = 'completed', outcome = 'sent' WHERE id = 5");
    await db.exec("INSERT INTO issues (issue_date, revision, run_id, html) VALUES ('2026-09-02', 1, 5, 'x')");
    await db.exec("INSERT INTO sends (issue_date, run_id, revision, status, recipients) VALUES ('2026-09-02', 5, 1, 'sent', NULL)");
    expect((await store().stats(30, new Date("2026-09-03T00:00:00Z"))).cost.recipientsLatest).toBe(12);
  });
});
