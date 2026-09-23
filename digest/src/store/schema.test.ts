import type { PGliteInterface as PGlite } from "@electric-sql/pglite";
import { beforeAll, describe, expect, it } from "vitest";
import { freshPglite, PARSERS } from "./pglite.js";

type State = { status: "running" | "failed" } | { status: "completed"; outcome: string };
const OUTCOMES = ["sent", "disabled", "rejected", "held-out", "skipped", "unrecorded"] as const;
const STATES: State[] = [{ status: "running" }, { status: "failed" }, ...OUTCOMES.map((outcome) => ({ status: "completed" as const, outcome }))];
const show = (s: State) => ("outcome" in s ? `completed(${s.outcome})` : s.status);
const outcomeOf = (s: State) => ("outcome" in s ? s.outcome : null);

// §4.1: running -> completed | failed; failed -> running; completed -> running unless sent; a write
// that leaves status and outcome as they were is always allowed.
function legal(from: State, to: State): boolean {
  if (show(from) === show(to)) return true;
  if (from.status === "running") return to.status !== "running";
  if (from.status === "failed") return to.status === "running";
  return from.status === "completed" && from.outcome !== "sent" && to.status === "running";
}

const rows = async (db: PGlite, sql: string, params: unknown[] = []) => (await db.query(sql, params, { parsers: PARSERS })).rows;
async function runIn(db: PGlite, s: State): Promise<number> {
  const [r] = (await rows(db, "INSERT INTO digest_runs (status, outcome, completed_at) VALUES ($1, $2, CASE WHEN $2 = 'sent' THEN now() END) RETURNING id", [s.status, outcomeOf(s)])) as { id: number }[];
  return r!.id;
}
async function runsOn(db: PGlite, days: string[]): Promise<void> {
  for (const [i, d] of days.entries()) await db.query("INSERT INTO digest_runs (id, run_at) VALUES ($1, $2)", [i + 1, `${d} 10:25:00+00`]);
}

describe("digest_runs transitions", () => {
  let db: PGlite;
  beforeAll(async () => {
    db = await freshPglite();
  });
  it("covers every edge between the 8 states: 64, of which 21 are legal", () => {
    const edges = STATES.flatMap((f) => STATES.map((t) => legal(f, t)));
    expect([edges.length, edges.filter(Boolean).length]).toEqual([64, 21]);
  });
  for (const from of STATES)
    for (const to of STATES) {
      const ok = legal(from, to);
      it(`${show(from)} -> ${show(to)} is ${ok ? "legal" : "refused"}`, async () => {
        const id = await runIn(db, from);
        const move = () => db.query("UPDATE digest_runs SET status=$1, outcome=$2, completed_at = CASE WHEN $2 = 'sent' THEN now() END WHERE id=$3", [to.status, outcomeOf(to), id]);
        if (ok) {
          await move();
          expect(await rows(db, "SELECT status, outcome FROM digest_runs WHERE id=$1", [id])).toEqual([{ status: to.status, outcome: outcomeOf(to) }]);
        } else await expect(move()).rejects.toThrow(/illegal digest_runs transition/);
      });
    }

  it("refuses completed without an outcome, and an outcome on a run that is not completed", async () => {
    await expect(db.query("INSERT INTO digest_runs (status) VALUES ('completed')")).rejects.toThrow(/check constraint/);
    await expect(db.query("INSERT INTO digest_runs (status, outcome) VALUES ('failed', 'sent')")).rejects.toThrow(/check constraint/);
    const id = await runIn(db, { status: "running" });
    await expect(db.query("UPDATE digest_runs SET status='completed' WHERE id=$1", [id])).rejects.toThrow(/check constraint/);
  });

  it("refuses to resume an unsent run that readers already have on the web", async () => {
    const id = await runIn(db, { status: "completed", outcome: "unrecorded" });
    await db.query("INSERT INTO issues (date, revision, run_id, html) VALUES ('2026-01-01', 1, $1, '')", [id]);
    await expect(db.query("UPDATE digest_runs SET status='running', outcome=NULL WHERE id=$1", [id])).rejects.toThrow(/illegal digest_runs transition/);
  });

  it("holds completed_at to its meaning: set on every sent run, and only on a completed one", async () => {
    await expect(db.query("INSERT INTO digest_runs (status, outcome) VALUES ('completed', 'sent')")).rejects.toThrow(/check constraint/);
    await expect(db.query("INSERT INTO digest_runs (status, completed_at) VALUES ('running', now())")).rejects.toThrow(/check constraint/);
    const id = await runIn(db, { status: "completed", outcome: "sent" });
    await expect(db.query("UPDATE digest_runs SET completed_at=NULL WHERE id=$1", [id])).rejects.toThrow(/check constraint/);
  });

  it("lets an update that leaves status alone through, even on a sent run", async () => {
    const id = await runIn(db, { status: "completed", outcome: "sent" });
    await db.query("UPDATE digest_runs SET error='late', articles_emailed=3 WHERE id=$1", [id]);
    expect(await rows(db, "SELECT error FROM digest_runs WHERE id=$1", [id])).toEqual([{ error: "late" }]);
  });
});

describe("the product schema", () => {
  it("refuses a row that names a run that does not exist", async () => {
    const db = await freshPglite();
    await expect(db.query("INSERT INTO run_artifacts (run_id, artifact_name, content, sha256) VALUES (999, 'x', 'y', 'z')")).rejects.toThrow(/foreign key/);
  });

  it("keeps one current artifact per name, and any number set aside", async () => {
    const db = await freshPglite();
    await runsOn(db, ["2026-09-18"]);
    const put = () => db.query("INSERT INTO run_artifacts (run_id, artifact_name, content, sha256) VALUES (1, 'recap.txt', 'x', 'h')");
    await put();
    await expect(put()).rejects.toThrow(/duplicate key/);
    await db.query("UPDATE run_artifacts SET state='quarantined'");
    await put();
    await db.query("UPDATE run_artifacts SET state='replaced' WHERE state='current'");
    await put();
    expect(await rows(db, "SELECT state, count(*) AS n FROM run_artifacts GROUP BY state ORDER BY state")).toEqual([
      { state: "current", n: 1 },
      { state: "quarantined", n: 1 },
      { state: "replaced", n: 1 },
    ]);
  });

  it("sends only a published revision, and publishes a run by its send once Resend has it", async () => {
    const db = await freshPglite();
    await runsOn(db, ["2026-09-18"]);
    const published = () => rows(db, "SELECT run_id FROM published_runs");
    await expect(db.query("INSERT INTO broadcasts (date, run_id, revision, status) VALUES ('2026-09-18', 1, 1, 'claimed')")).rejects.toThrow(/foreign key/);
    await db.query("INSERT INTO issues (date, revision, run_id, html) VALUES ('2026-09-18', 1, NULL, 'x')");
    for (const status of ["claimed", "draft"]) {
      await db.query("INSERT INTO broadcasts (date, run_id, revision, status) VALUES ('2026-09-18', 1, 1, $1) ON CONFLICT (date) DO UPDATE SET status = excluded.status", [status]);
      expect(await published()).toEqual([]);
    }
    await db.query("UPDATE broadcasts SET status='queued'");
    expect(await published()).toEqual([{ run_id: 1 }]);
  });

  it("derives a thread's label, last run and status from its published installments", async () => {
    const days = ["2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"];
    const db = await freshPglite();
    await runsOn(db, days);
    for (const [i, d] of days.entries()) {
      await db.query("UPDATE digest_runs SET status='completed', outcome='sent', completed_at=run_at WHERE id=$1", [i + 1]);
      if (i + 1 !== 7) await db.query("INSERT INTO issues (date, revision, run_id, html) VALUES ($1, 1, $2, '')", [d, i + 1]);
    }
    await db.query("INSERT INTO threads (id, created_run_id) VALUES (10, 1), (11, 2)");
    await db.query("INSERT INTO thread_installments (thread_id, run_id, cluster_story, continued) VALUES (10, 1, 'a', false), (10, 2, 'b', true), (11, 2, 'c', false), (11, 7, 'unsent', true)");
    // 11's run-7 installment is unpublished, so it was last seen in run 2, with runs 3-6 after it.
    expect(await rows(db, "SELECT id, label, status, first_run_id, last_run_id, installments FROM thread_state ORDER BY id")).toEqual([
      { id: 10, label: "b", status: "dormant", first_run_id: 1, last_run_id: 2, installments: 2 },
      { id: 11, label: "c", status: "dormant", first_run_id: 2, last_run_id: 2, installments: 1 },
    ]);
    await db.query("INSERT INTO thread_installments (thread_id, run_id, cluster_story, continued) VALUES (10, 5, 'e', true)");
    expect(await rows(db, "SELECT label, status, last_run_id FROM thread_state WHERE id=10")).toEqual([{ label: "e", status: "active", last_run_id: 5 }]);
    await db.query("UPDATE threads SET merged_into=10 WHERE id=11");
    expect(await rows(db, "SELECT status FROM thread_state WHERE id=11")).toEqual([{ status: "merged" }]);
  });

  it("counts dormancy as the newest run's decay did: runs after the last installment, before the newest, more than 3", async () => {
    const db = await freshPglite();
    const days = ["2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15", "2026-09-16"];
    await runsOn(db, days);
    for (const [i, d] of days.entries()) {
      await db.query("UPDATE digest_runs SET status='completed', outcome='sent', completed_at=run_at WHERE id=$1", [i + 1]);
      await db.query("INSERT INTO issues (date, revision, run_id, html) VALUES ($1, 1, $2, '')", [d, i + 1]);
    }
    await db.query("INSERT INTO threads (id, created_run_id) VALUES (10, 2)");
    await db.query("INSERT INTO thread_installments (thread_id, run_id, cluster_story, continued) VALUES (10, 2, 'a', false)");
    // Runs 3, 4, 5 between it and the newest (6): 3, not more than 3. Counting the newest would say 4.
    expect(await rows(db, "SELECT status FROM thread_state")).toEqual([{ status: "active" }]);
    await db.query("INSERT INTO digest_runs (id, run_at) VALUES (7, '2026-09-17 10:25:00+00')");
    expect(await rows(db, "SELECT status FROM thread_state")).toEqual([{ status: "dormant" }]);
  });

  it("shows a question resolved only by a published run", async () => {
    const db = await freshPglite();
    await runsOn(db, ["2026-09-11", "2026-09-12"]);
    await db.query("INSERT INTO issues (date, revision, run_id, html) VALUES ('2026-09-11', 1, 1, '')");
    await db.query("INSERT INTO threads (id, created_run_id) VALUES (10, 1)");
    await db.query("INSERT INTO thread_questions (id, thread_id, question, raised_run_id) VALUES (1, 10, 'why?', 1), (2, 10, 'unsent?', 2)");
    await db.query("INSERT INTO thread_question_resolutions (question_id, run_id, how) VALUES (1, 2, 'because')");
    expect(await rows(db, "SELECT id, status, resolved_run_id FROM thread_question_state")).toEqual([{ id: 1, status: "open", resolved_run_id: null }]);
    await db.query("INSERT INTO issues (date, revision, run_id, html) VALUES ('2026-09-12', 1, 2, '')");
    expect(await rows(db, "SELECT id, status, resolved_run_id, resolved_how FROM thread_question_state ORDER BY id")).toEqual([
      { id: 1, status: "resolved", resolved_run_id: 2, resolved_how: "because" },
      { id: 2, status: "open", resolved_run_id: null, resolved_how: null },
    ]);
  });

  it("searches headlines and source titles, ranking a headline match above a title match", async () => {
    const db = await freshPglite();
    await db.query("INSERT INTO shown_narratives (headline, original_title) VALUES ('Iran and US resume talks in Oman', 'Diplomats meet'), ('Wildfires spread', 'Iran sends aid'), ('Markets fall', 'Stocks slide')");
    const hits = await rows(
      db,
      "SELECT headline FROM shown_narratives, websearch_to_tsquery('english', $1) q WHERE search @@ q ORDER BY ts_rank(search, q) DESC",
      ["Iran"],
    );
    expect(hits).toEqual([{ headline: "Iran and US resume talks in Oman" }, { headline: "Wildfires spread" }]);
  });
});
