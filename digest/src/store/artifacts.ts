import { createHash } from "node:crypto";
import { artifactKind } from "./artifact-kinds.js";
import { openDb, type Db, type Sql } from "./db.js";

export interface Pointer {
  runId: number;
  name: string;
  sha256: string;
}
export class IntegrityError extends Error {}
export class ConflictError extends Error {}

export const sha256 = (s: string): string => createHash("sha256").update(s, "utf8").digest("hex");

// These take the caller's connection, so they join its transaction. At most one row per
// (run_id, name) is 'current'; the partial unique index makes a second writer fail rather
// than duplicate it.
export async function artifactIn(db: Sql, runId: number, name: string): Promise<string | undefined> {
  return (await db.one<{ content: string }>("SELECT content FROM artifacts WHERE run_id=$1 AND name=$2 AND status='current'", [runId, name]))?.content;
}

// Attributed to the run's latest attempt: one attempt runs at a time.
export async function putIn(db: Sql, runId: number, name: string, content: string): Promise<void> {
  const k = artifactKind(name);
  await db.run(
    `INSERT INTO artifacts (run_id, attempt_id, name, content, sha256, stage, kind, branch)
     VALUES ($1, (SELECT max(id) FROM run_attempts WHERE run_id = $1), $2, $3, $4, $5, $6, $7)`,
    [runId, name, content, sha256(content), k.stage, k.kind, k.branch],
  );
}

// Set aside, never deleted: the row stays under its name, attributed to its attempt.
export async function quarantineIn(db: Sql, runId: number, name: string): Promise<boolean> {
  return (await db.run("UPDATE artifacts SET status='quarantined' WHERE run_id=$1 AND name=$2 AND status='current'", [runId, name])) === 1;
}

// A new current row over the old one, which is kept as 'replaced'. Equal content changes nothing.
export async function setIn(db: Sql, runId: number, name: string, content: string): Promise<void> {
  const old = await artifactIn(db, runId, name);
  if (old === content) return;
  if (old !== undefined) await db.run("UPDATE artifacts SET status='replaced' WHERE run_id=$1 AND name=$2 AND status='current'", [runId, name]);
  await putIn(db, runId, name, content);
}

// The run's UTC day: the one date every stage reasons from (spec §1, run date).
export async function runDateIn(db: Sql, runId: number): Promise<string> {
  const r = await db.one<{ d: string }>("SELECT to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS d FROM runs WHERE id=$1", [runId]);
  if (!r) throw new IntegrityError(`no run ${runId}`);
  return r.d;
}

// Over the production artifacts table, content as text. A pointer is (run, name, sha256): the
// hash is an integrity check, never a lookup key. put never replaces a row; the explicit force path
// is replace, and a row that fails its validator is quarantined so the activity can produce a fresh
// sample (spec §2.1). Reads see the current row only.
export class ArtifactStore {
  private readonly db: Db;
  constructor(dbUrl: string) {
    this.db = openDb(dbUrl);
  }
  async find(runId: number, name: string): Promise<Pointer | undefined> {
    const c = await artifactIn(this.db, runId, name);
    return c === undefined ? undefined : { runId, name, sha256: sha256(c) };
  }
  async put(runId: number, name: string, content: string): Promise<Pointer> {
    const existing = await artifactIn(this.db, runId, name);
    if (existing !== undefined) {
      if (existing === content) return { runId, name, sha256: sha256(content) };
      throw new ConflictError(`artifact ${name} for run ${runId} exists with different content; quarantine or replace explicitly`);
    }
    await putIn(this.db, runId, name, content);
    return { runId, name, sha256: sha256(content) };
  }
  async get(p: Pointer): Promise<string> {
    const c = await artifactIn(this.db, p.runId, p.name);
    if (c === undefined) throw new IntegrityError(`no artifact ${p.name} for run ${p.runId}`);
    if (sha256(c) !== p.sha256) throw new IntegrityError(`artifact ${p.name} for run ${p.runId} does not match its pointer`);
    return c;
  }
  // Refuses to report a quarantine that touched no row: a second caller on an already-quarantined
  // name would otherwise be told it set aside something it never saw.
  async quarantine(runId: number, name: string): Promise<void> {
    if (!(await quarantineIn(this.db, runId, name))) throw new IntegrityError(`no artifact ${name} for run ${runId} to quarantine`);
  }
  async replace(runId: number, name: string, content: string): Promise<Pointer> {
    await this.db.tx((t) => setIn(t, runId, name, content), `artifact ${runId} ${name}`);
    return { runId, name, sha256: sha256(content) };
  }
  // Every row under the name, oldest first: 'current', 'quarantined' or 'replaced'.
  async statuses(runId: number, name: string): Promise<string[]> {
    return (await this.db.all<{ status: string }>("SELECT status FROM artifacts WHERE run_id=$1 AND name=$2 ORDER BY id", [runId, name])).map((r) => r.status);
  }
  async names(runId: number): Promise<string[]> {
    return (await this.db.all<{ n: string }>(`SELECT name AS n FROM artifacts WHERE run_id=$1 AND status='current' ORDER BY name COLLATE "C"`, [runId])).map((r) => r.n);
  }
  // The current content under a name, which must exist.
  async content(runId: number, name: string): Promise<string> {
    const c = await artifactIn(this.db, runId, name);
    if (c === undefined) throw new IntegrityError(`no artifact ${name} for run ${runId}`);
    return c;
  }
  runDate(runId: number): Promise<string> {
    return runDateIn(this.db, runId);
  }
}
