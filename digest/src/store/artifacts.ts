import { createHash } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";
import { openDb } from "./db.js";

export interface Pointer {
  runId: number;
  name: string;
  sha256: string;
}
export class IntegrityError extends Error {}
export class ConflictError extends Error {}

const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");
type Row = { content: string } | undefined;

// Over the production run_artifacts table: one row per (run_id, artifact_name), content as text.
// A pointer is (run, name, sha256): the hash is an integrity check, never a lookup key. put never
// replaces a row; the explicit force path is replace, and a row that fails its validator is
// quarantined under a new name so the activity can produce a fresh sample (spec §2.1).
export class ArtifactStore {
  private readonly db: DatabaseSync;
  constructor(dbPath: string) {
    this.db = openDb(dbPath);
  }
  private row(runId: number, name: string): Row {
    return this.db.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=?").get(runId, name) as Row;
  }
  find(runId: number, name: string): Pointer | undefined {
    const r = this.row(runId, name);
    return r ? { runId, name, sha256: sha(r.content) } : undefined;
  }
  put(runId: number, name: string, content: string): Pointer {
    const existing = this.row(runId, name);
    if (existing) {
      if (existing.content === content) return { runId, name, sha256: sha(content) };
      throw new ConflictError(`artifact ${name} for run ${runId} exists with different content; quarantine or replace explicitly`);
    }
    this.db.prepare("INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)").run(runId, name, content);
    return { runId, name, sha256: sha(content) };
  }
  get(p: Pointer): string {
    const r = this.row(p.runId, p.name);
    if (!r) throw new IntegrityError(`no artifact ${p.name} for run ${p.runId}`);
    if (sha(r.content) !== p.sha256) throw new IntegrityError(`artifact ${p.name} for run ${p.runId} does not match its pointer`);
    return r.content;
  }
  quarantine(runId: number, name: string): string {
    // Count and rename under one write lock, and refuse to report a rename that touched no row:
    // a second caller on an already-quarantined name would otherwise get a fabricated name back.
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const { c } = this.db
        .prepare("SELECT COUNT(*) AS c FROM run_artifacts WHERE run_id=? AND artifact_name LIKE ?")
        .get(runId, `${name}.corrupt.%`) as { c: number };
      const renamed = `${name}.corrupt.${c + 1}`;
      const { changes } = this.db
        .prepare("UPDATE run_artifacts SET artifact_name=? WHERE run_id=? AND artifact_name=?")
        .run(renamed, runId, name);
      if (Number(changes) !== 1) throw new IntegrityError(`no artifact ${name} for run ${runId} to quarantine`);
      this.db.exec("COMMIT");
      return renamed;
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
  }
  replace(runId: number, name: string, content: string): Pointer {
    this.db.prepare("INSERT OR REPLACE INTO run_artifacts (run_id, artifact_name, content) VALUES (?, ?, ?)").run(runId, name, content);
    return { runId, name, sha256: sha(content) };
  }
  close(): void {
    this.db.close();
  }
}
