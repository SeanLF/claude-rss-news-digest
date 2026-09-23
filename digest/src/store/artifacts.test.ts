import { describe, expect, it } from "vitest";
import { artifactKind } from "./artifact-kinds.js";
import { ArtifactStore, ConflictError, IntegrityError } from "./artifacts.js";
import { openDb } from "./db.js";
import { freshDb } from "./test-db.js";
import { recordUsage, runCost } from "./usage.js";

describe("ArtifactStore on the product schema", () => {
  it("put returns a pointer whose hash is the content's sha256, and get round-trips", async () => {
    const s = new ArtifactStore(await freshDb());
    const p = await s.put(300, "recap.txt", "hello");
    expect(p.sha256).toBe("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    expect(await s.get(p)).toBe("hello");
    expect(await s.find(300, "recap.txt")).toEqual(p);
  });
  it("put of identical content is idempotent; put of different content is a conflict, never a replace", async () => {
    const s = new ArtifactStore(await freshDb());
    const first = await s.put(300, "recap.txt", "one");
    expect(await s.put(300, "recap.txt", "one")).toEqual(first);
    await expect(s.put(300, "recap.txt", "two")).rejects.toThrow(ConflictError);
    expect(await s.get(first)).toBe("one");
  });
  it("get throws IntegrityError when the row no longer matches the pointer", async () => {
    const url = await freshDb();
    const s = new ArtifactStore(url);
    const p = await s.put(300, "recap.txt", "one");
    await openDb(url).run("UPDATE run_artifacts SET content='tampered'");
    await expect(s.get(p)).rejects.toThrow(IntegrityError);
    await expect(s.get({ ...p, name: "missing.txt" })).rejects.toThrow(IntegrityError);
  });
  it("quarantine sets the row aside under its name and frees it; replace keeps the old row as replaced", async () => {
    const s = new ArtifactStore(await freshDb());
    await s.put(300, "recap.txt", "bad");
    await s.quarantine(300, "recap.txt");
    expect(await s.find(300, "recap.txt")).toBeUndefined();
    const p = await s.replace(300, "recap.txt", "good");
    expect(await s.get(p)).toBe("good");
    await s.quarantine(300, "recap.txt");
    expect(await s.get(await s.replace(300, "recap.txt", "better"))).toBe("better");
    expect(await s.get(await s.replace(300, "recap.txt", "best"))).toBe("best");
    expect(await s.states(300, "recap.txt")).toEqual(["quarantined", "quarantined", "replaced", "current"]);
  });
  it("records the stage, kind and fan-out branch its name encodes, and the run's latest attempt", async () => {
    const url = await freshDb([300]);
    const db = openDb(url);
    await db.run("INSERT INTO run_attempts (id, run_id, pipeline) VALUES (7, 300, 'temporal'), (8, 300, 'temporal')");
    await new ArtifactStore(url).put(300, "draft_s03.json", "{}");
    expect(await db.one("SELECT stage, kind, branch, attempt_id FROM run_artifacts")).toEqual({ stage: "write", kind: "output", branch: "s03", attempt_id: 8 });
    expect(artifactKind("articles_2.csv")).toEqual({ stage: "prepare", kind: "input", branch: "c2" });
    expect(artifactKind("something_new.txt")).toEqual({ stage: null, kind: null, branch: null });
  });
  it("names lists a run's current artifacts in byte order", async () => {
    const s = new ArtifactStore(await freshDb([300]));
    await s.put(300, "articles_2.csv", "b");
    await s.put(300, "articles_1.csv", "a");
    await s.put(300, "Z.txt", "z");
    await s.put(300, "gone.txt", "x");
    await s.quarantine(300, "gone.txt");
    expect(await s.names(300)).toEqual(["Z.txt", "articles_1.csv", "articles_2.csv"]);
  });
  it("runDate reads the run's UTC day and refuses an unknown run", async () => {
    const s = new ArtifactStore(await freshDb([300]));
    expect(await s.runDate(300)).toBe("2026-09-18");
    await expect(s.runDate(301)).rejects.toThrow(/no run 301/);
  });
  it("quarantine of a name that is not there throws instead of reporting a quarantine that never happened", async () => {
    const s = new ArtifactStore(await freshDb());
    await expect(s.quarantine(300, "missing.txt")).rejects.toThrow(IntegrityError);
    await s.put(300, "recap.txt", "bad");
    await s.quarantine(300, "recap.txt");
    await expect(s.quarantine(300, "recap.txt")).rejects.toThrow(IntegrityError);
    expect(await s.states(300, "recap.txt")).toEqual(["quarantined"]);
  });
});

describe("run_usage", () => {
  it("records a call and sums a run's cost since a moment", async () => {
    const db = openDb(await freshDb([300]));
    await recordUsage(db, { stage: "write", runId: 300, model: "claude-sonnet-5", thinking: "adaptive", effort: "(sdk default)", costUsd: 0.25, durationMs: 9, tokens: { input_tokens: 10, output_tokens: 3, cache_read_input_tokens: 7 }, story: 2 });
    await recordUsage(db, { stage: "coherence", runId: 300, model: "claude-sonnet-5", thinking: "adaptive", effort: "high", costUsd: 0.5, durationMs: 9, tokens: {} });
    expect(await runCost(db, 300, "2000-01-01 00:00:00")).toEqual({ costUsd: 0.75, calls: 2 });
    expect(await runCost(db, 300, "2999-01-01 00:00:00")).toEqual({ costUsd: 0, calls: 0 });
    expect(await db.one("SELECT cache_read_tokens AS c FROM run_usage WHERE subagent='write'")).toEqual({ c: 7 });
    // config-drift.sql reads NULL as "(not recorded)": each call records the effort it ran under.
    expect(await db.all("SELECT subagent, effort, branch, result FROM run_usage ORDER BY id")).toEqual([
      { subagent: "write", effort: "(sdk default)", branch: "s02", result: "ok" },
      { subagent: "coherence", effort: "high", branch: null, result: "ok" },
    ]);
  });
  it("versions the prompt by content hash, once per distinct prompt", async () => {
    const db = openDb(await freshDb([300]));
    const prompt = { name: "recap", body: "Summarise. {{CURRENT_DATE}}", tools: [], thinking: "disabled" as const };
    const row = { stage: "recap", runId: 300, model: "m", thinking: "disabled", effort: "(sdk default)", costUsd: 0, durationMs: 1, tokens: {}, prompt };
    await recordUsage(db, row);
    await recordUsage(db, row);
    await recordUsage(db, { ...row, prompt: { ...prompt, tools: ["Read" as const] } });
    const shas = await db.all<{ prompt_sha: string }>("SELECT prompt_sha FROM run_usage ORDER BY id");
    expect(shas[0]!.prompt_sha).toBe(shas[1]!.prompt_sha);
    expect(shas[2]!.prompt_sha).not.toBe(shas[0]!.prompt_sha);
    expect(await db.all("SELECT name, first_run_id FROM prompts")).toEqual([
      { name: "recap", first_run_id: 300 },
      { name: "recap", first_run_id: 300 },
    ]);
  });
});
