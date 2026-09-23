import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { freshDb } from "../store/test-db.js";
import { feedHealthAlert } from "./feed-health.js";

function seed(rows: [source: string, success: number, minutesAgo: number, runId: number][]): DatabaseSync {
  const db = new DatabaseSync(freshDb([300, 301]));
  const ins = db.prepare("INSERT INTO source_health (source_id, success, recorded_at, run_id) VALUES (?, ?, datetime('now', ?), ?)");
  for (const [s, ok, ago, run] of rows) ins.run(s, ok, `-${ago} minutes`, run);
  return db;
}

describe("feedHealthAlert", () => {
  it("names the fetched sources whose latest failures in a row reach the threshold, worst first", () => {
    const db = seed([
      ["the_hindu", 0, 1, 301], ["the_hindu", 0, 1440, 300], ["the_hindu", 0, 2880, 300], ["the_hindu", 0, 4320, 300],
      ["france24", 0, 1, 301], ["france24", 0, 1440, 300], ["france24", 0, 2880, 300], ["france24", 1, 4320, 300],
      ["bbc", 0, 1, 301], ["bbc", 1, 1440, 300], ["bbc", 0, 2880, 300], ["bbc", 0, 4320, 300],
      ["reuters", 1, 1, 301],
    ]);
    expect(feedHealthAlert(db, 301, ["the_hindu", "france24", "bbc", "reuters"], 3)).toEqual({ kind: "source-health", failing: [["the_hindu", 4], ["france24", 3]], failedThisRun: 3, totalSources: 4, threshold: 3 });
  });
  it("leaves out a parked source: its failures are history, not news", () => {
    const db = seed([["parked", 0, 1, 301], ["parked", 0, 1440, 300], ["parked", 0, 2880, 300]]);
    expect(feedHealthAlert(db, 301, ["reuters"], 3)).toBeNull();
  });
  it("ignores sources not seen in the last seven days", () => {
    const db = seed([["old", 0, 8 * 1440, 300], ["old", 0, 9 * 1440, 300], ["old", 0, 10 * 1440, 300]]);
    expect(feedHealthAlert(db, 301, ["old"], 3)).toBeNull();
  });
});
