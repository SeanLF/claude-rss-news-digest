import { describe, expect, it } from "vitest";
import { openDb, type Db } from "../store/db.js";
import { freshDb } from "../store/test-db.js";
import { feedHealthAlert } from "./feed-health.js";

async function seed(rows: [source: string, success: number, minutesAgo: number, runId: number][]): Promise<Db> {
  const db = openDb(await freshDb([300, 301]));
  for (const [s, ok, ago, run] of rows)
    await db.run("INSERT INTO source_fetches (source_id, is_success, fetched_at, run_id) VALUES ($1, $2, now() - make_interval(mins => $3), $4)", [s, ok === 1, ago, run]);
  return db;
}

describe("feedHealthAlert", () => {
  it("names the fetched sources whose latest failures in a row reach the threshold, worst first", async () => {
    const db = await seed([
      ["the_hindu", 0, 1, 301], ["the_hindu", 0, 1440, 300], ["the_hindu", 0, 2880, 300], ["the_hindu", 0, 4320, 300],
      ["france24", 0, 1, 301], ["france24", 0, 1440, 300], ["france24", 0, 2880, 300], ["france24", 1, 4320, 300],
      ["bbc", 0, 1, 301], ["bbc", 1, 1440, 300], ["bbc", 0, 2880, 300], ["bbc", 0, 4320, 300],
      ["reuters", 1, 1, 301],
    ]);
    expect(await feedHealthAlert(db, 301, ["the_hindu", "france24", "bbc", "reuters"], 3)).toEqual({ kind: "source-health", failing: [["the_hindu", 4], ["france24", 3]], failedThisRun: 3, totalSources: 4, threshold: 3 });
  });
  it("leaves out a parked source: its failures are history, not news", async () => {
    const db = await seed([["parked", 0, 1, 301], ["parked", 0, 1440, 300], ["parked", 0, 2880, 300]]);
    expect(await feedHealthAlert(db, 301, ["reuters"], 3)).toBeNull();
  });
  it("ignores sources not seen in the last seven days", async () => {
    const db = await seed([["old", 0, 8 * 1440, 300], ["old", 0, 9 * 1440, 300], ["old", 0, 10 * 1440, 300]]);
    expect(await feedHealthAlert(db, 301, ["old"], 3)).toBeNull();
  });
});
