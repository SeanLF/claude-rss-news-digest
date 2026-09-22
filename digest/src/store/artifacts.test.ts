import { recordUsage, runCost } from "./usage.js";
import { openDb } from "./db.js";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ArtifactStore, ConflictError, IntegrityError } from "./artifacts.js";
import { freshDb } from "./test-db.js";

describe("ArtifactStore on the production run_artifacts schema", () => {
  it("put returns a pointer whose hash is the content's sha256, and get round-trips", () => {
    const s = new ArtifactStore(freshDb());
    const p = s.put(300, "recap.txt", "hello");
    expect(p.sha256).toBe("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    expect(s.get(p)).toBe("hello");
    expect(s.find(300, "recap.txt")).toEqual(p);
  });
  it("put of identical content is idempotent; put of different content is a conflict, never a replace", () => {
    const s = new ArtifactStore(freshDb());
    const first = s.put(300, "recap.txt", "one");
    expect(s.put(300, "recap.txt", "one")).toEqual(first);
    expect(() => s.put(300, "recap.txt", "two")).toThrow(ConflictError);
    expect(s.get(first)).toBe("one");
  });
  it("get throws IntegrityError when the row no longer matches the pointer", () => {
    const path = freshDb();
    const s = new ArtifactStore(path);
    const p = s.put(300, "recap.txt", "one");
    new DatabaseSync(path).prepare("UPDATE run_artifacts SET content='tampered'").run();
    expect(() => s.get(p)).toThrow(IntegrityError);
    expect(() => s.get({ ...p, name: "missing.txt" })).toThrow(IntegrityError);
  });
  it("quarantine renames and frees the name, numbering successive quarantines; replace is the only overwrite", () => {
    const s = new ArtifactStore(freshDb());
    s.put(300, "recap.txt", "bad");
    expect(s.quarantine(300, "recap.txt")).toBe("recap.txt.corrupt.1");
    expect(s.find(300, "recap.txt")).toBeUndefined();
    const p = s.replace(300, "recap.txt", "good");
    expect(s.get(p)).toBe("good");
    expect(s.quarantine(300, "recap.txt")).toBe("recap.txt.corrupt.2");
    expect(s.get(s.replace(300, "recap.txt", "better"))).toBe("better");
  });
  it("names lists a run's artifacts in name order", () => {
    const s = new ArtifactStore(freshDb([300]));
    s.put(300, "articles_2.csv", "b");
    s.put(300, "articles_1.csv", "a");
    expect(s.names(300)).toEqual(["articles_1.csv", "articles_2.csv"]);
  });
  it("runDate reads the run's UTC day and refuses an unknown run", () => {
    const s = new ArtifactStore(freshDb([300]));
    expect(s.runDate(300)).toBe("2026-09-18");
    expect(() => s.runDate(301)).toThrow(/no run 301/);
  });
  it("quarantine of a name that is not there throws instead of returning a name it never wrote", () => {
    const s = new ArtifactStore(freshDb());
    expect(() => s.quarantine(300, "missing.txt")).toThrow(IntegrityError);
    s.put(300, "recap.txt", "bad");
    s.quarantine(300, "recap.txt");
    expect(() => s.quarantine(300, "recap.txt")).toThrow(IntegrityError);
    expect(s.find(300, "recap.txt.corrupt.1")).toBeDefined();
  });
});

describe("run_usage", () => {
  it("records a call and sums a run's cost since a moment", () => {
    const path = freshDb([300]);
    const db = openDb(path);
    db.exec("CREATE TABLE run_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, subagent TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0, api_cost_usd REAL NOT NULL DEFAULT 0.0, recorded_at DATETIME DEFAULT (datetime('now', 'utc')), duration_ms INTEGER, thinking TEXT, effort TEXT)");
    recordUsage(db, { stage: "write", runId: 300, model: "claude-sonnet-5", thinking: "adaptive", costUsd: 0.25, durationMs: 9, tokens: { input_tokens: 10, output_tokens: 3, cache_read_input_tokens: 7 } });
    recordUsage(db, { stage: "coherence", runId: 300, model: "claude-sonnet-5", thinking: "adaptive", costUsd: 0.5, durationMs: 9, tokens: {} });
    expect(runCost(db, 300, "2000-01-01")).toEqual({ costUsd: 0.75, calls: 2 });
    expect(runCost(db, 300, "2999-01-01")).toEqual({ costUsd: 0, calls: 0 });
    expect(db.prepare("SELECT cache_read_tokens AS c FROM run_usage WHERE subagent='write'").get()).toEqual({ c: 7 });
  });
});
