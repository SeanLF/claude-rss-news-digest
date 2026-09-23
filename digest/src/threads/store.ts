import type { Sql } from "../store/db.js";
import { deltaFromFacts, whatsNew } from "./text.js";

// threads.ThreadStore, over the thread tables. Every row belongs to the run that wrote it: a
// thread's label, last run and status are derived from its updates, never written. No method
// commits on its own: the activities build one over a transaction (Db.tx) for each unit of identity,
// so a retried attempt finds all of it or none of it.

// How many recent update labels (the story arc) the linker sees per active thread.
export const RECENT_LABELS_K = 4;

export interface ActiveThread { thread_id: number; label: string; recent_labels: string[] }
export interface RenderContext { thread_id: number; day: number; delta: string }

export class ThreadStore {
  constructor(readonly db: Sql) {}

  // Unmerged threads seen within the last `dormantAfter` SENT runs before this one (failed-run
  // id gaps do not age a thread), labelled by their latest update.
  async activeThreads(beforeRunId: number, dormantAfter: number): Promise<ActiveThread[]> {
    const rows = await this.db.all<{ id: number; label: string }>(
      `SELECT t.id, l.label
       FROM threads t
       JOIN (SELECT DISTINCT ON (thread_id) thread_id, run_id AS last_run_id, label
             FROM thread_updates ORDER BY thread_id, run_id DESC, id DESC) l ON l.thread_id = t.id
       WHERE t.merged_into_id IS NULL
         AND (SELECT COUNT(*) FROM sent_runs
              WHERE run_id > l.last_run_id AND run_id < $1) <= $2
       ORDER BY l.last_run_id DESC, t.id`,
      [beforeRunId, dormantAfter],
    );
    if (!rows.length) return [];
    const history = await this.recentLabels(rows.map((r) => r.id), beforeRunId);
    return rows.map((r) => ({ thread_id: r.id, label: r.label, recent_labels: history.get(r.id) ?? [r.label] }));
  }

  private async recentLabels(ids: number[], beforeRunId: number): Promise<Map<number, string[]>> {
    const rows = await this.db.all<{ thread_id: number; label: string }>(
      `SELECT thread_id, label FROM (
         SELECT thread_id, run_id, label,
                ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY run_id DESC) AS rn
         FROM thread_updates
         WHERE run_id < $1 AND thread_id = ANY($2::bigint[])
       ) x WHERE rn <= $3 ORDER BY thread_id, run_id`,
      [beforeRunId, ids, RECENT_LABELS_K],
    );
    const out = new Map<number, string[]>();
    for (const r of rows) out.set(r.thread_id, [...(out.get(r.thread_id) ?? []), r.label]);
    return out;
  }

  async openQuestions(threadId: number): Promise<string[]> {
    return (
      await this.db.all<{ question: string }>(
        "SELECT question FROM thread_questions q WHERE thread_id = $1 AND NOT EXISTS (SELECT 1 FROM thread_question_resolutions r WHERE r.question_id = q.id) ORDER BY id",
        [threadId],
      )
    ).map((r) => r.question);
  }

  // The thread's memory: the last `limit` runs' deltas, oldest first.
  async recentDeltas(threadId: number, limit = 3): Promise<string[]> {
    const rows = await this.db.all<{ content: string }>("SELECT content FROM thread_updates WHERE thread_id = $1 AND content IS NOT NULL ORDER BY run_id DESC LIMIT $2", [threadId, limit]);
    return rows.toReversed().map((r) => deltaFromFacts(whatsNew(r.content))).filter(Boolean);
  }

  async updateContent(threadId: number, runId: number): Promise<string | null | undefined> {
    const row = await this.db.one<{ content: string | null }>("SELECT content FROM thread_updates WHERE thread_id = $1 AND run_id = $2", [threadId, runId]);
    return row ? row.content : undefined;
  }

  // What the render needs: the id, the "day N" count, and this run's delta ("" on a quiet day).
  async renderContext(threadId: number, runId: number): Promise<RenderContext> {
    const r = await this.db.one<{ n: number }>("SELECT COUNT(*) AS n FROM thread_updates WHERE thread_id = $1", [threadId]);
    return { thread_id: threadId, day: r!.n, delta: deltaFromFacts(whatsNew(await this.updateContent(threadId, runId))) };
  }

  // The thread's label is its first update's story label, which the caller records next.
  async createThread(runId: number): Promise<number> {
    return (await this.db.one<{ id: number }>("INSERT INTO threads (created_run_id) VALUES ($1) RETURNING id", [runId]))!.id;
  }

  // is_continuation: the linker continued an existing thread (a binary decision), rather than starting one.
  async addUpdate(threadId: number, runId: number, label: string, isNew: boolean): Promise<void> {
    await this.db.run("INSERT INTO thread_updates (thread_id, run_id, label, is_continuation) VALUES ($1, $2, $3, $4)", [threadId, runId, label, !isNew]);
  }

  async setUpdateContent(threadId: number, runId: number, content: string): Promise<void> {
    await this.db.run("UPDATE thread_updates SET content = $1 WHERE thread_id = $2 AND run_id = $3", [content, threadId, runId]);
  }

  async addQuestions(threadId: number, questions: string[], runId: number): Promise<void> {
    for (const q of questions) await this.db.run("INSERT INTO thread_questions (thread_id, question, raised_run_id) VALUES ($1, $2, $3)", [threadId, q, runId]);
  }

  // Every open question of that wording on the thread, as the Python's UPDATE resolves them.
  async resolveQuestion(threadId: number, question: string, runId: number, answer: string): Promise<void> {
    await this.db.run(
      `INSERT INTO thread_question_resolutions (question_id, resolved_run_id, answer)
       SELECT id, $1, $2 FROM thread_questions q
       WHERE thread_id = $3 AND question = $4 AND NOT EXISTS (SELECT 1 FROM thread_question_resolutions r WHERE r.question_id = q.id)`,
      [runId, answer, threadId, question],
    );
  }

  async countRunUpdates(runId: number): Promise<number> {
    return (await this.db.one<{ n: number }>("SELECT COUNT(*) AS n FROM thread_updates WHERE run_id = $1", [runId]))!.n;
  }
}
