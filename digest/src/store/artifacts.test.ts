import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ArtifactStore, ConflictError, IntegrityError } from "./artifacts.js";

// The repo's own migrations, so the schema under test is production's.
const MIGRATIONS = new URL("../../../migrations/", import.meta.url).pathname;

function freshDb(): string {
  const path = join(mkdtempSync(join(tmpdir(), "digest-")), "digest.db");
  const db = new DatabaseSync(path);
  db.exec("CREATE TABLE digest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT)");
  db.exec("INSERT INTO digest_runs (id) VALUES (300)"); // node:sqlite enforces the FK the Python side leaves off
  for (const f of ["20260615100000_add_run_artifacts.sql", "20260729120000_unique_run_artifact_per_run.sql"]) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.close();
  return path;
}

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
});
