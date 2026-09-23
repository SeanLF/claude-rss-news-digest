import type { DatabaseSync } from "node:sqlite";
import { deltaFromFacts, slugify, whatsNew } from "./text.js";

// threads.ThreadStore, ported over the same tables (circulation reads them for /thread/{id}). The
// SQL is the Python's, statement for statement, so a thread written by either runtime is the same
// row. Unlike the Python, no method commits on its own: the activities wrap each unit of identity
// in one transaction so a retried attempt finds all of it or none of it.

// How many recent installment labels (the story arc) the linker sees per active thread.
export const RECENT_LABELS_K = 4;

export interface ActiveThread { thread_id: number; label: string; recent_labels: string[] }
export interface RenderContext { thread_id: number; day: number; delta: string }

export class ThreadStore {
  constructor(readonly db: DatabaseSync) {}

  // BEGIN IMMEDIATE takes the write lock up front, so a check-then-write inside `fn` cannot race a
  // concurrent attempt of the same activity.
  transaction<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const out = fn();
      this.db.exec("COMMIT");
      return out;
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
  }

  // Active threads seen within the last `dormantAfter` COMPLETED runs (failed-run id gaps do not age a thread).
  activeThreads(beforeRunId: number, dormantAfter: number): ActiveThread[] {
    const rows = this.db
      .prepare(
        `SELECT id, label FROM threads
         WHERE status = 'active'
           AND last_run_id IS NOT NULL
           AND (SELECT COUNT(*) FROM digest_runs
                WHERE id > threads.last_run_id AND id < ? AND completed_at IS NOT NULL) <= ?
         ORDER BY last_run_id DESC, id`,
      )
      .all(beforeRunId, dormantAfter) as { id: number; label: string }[];
    if (!rows.length) return [];
    const history = this.recentLabels(rows.map((r) => r.id), beforeRunId);
    return rows.map((r) => ({ thread_id: r.id, label: r.label, recent_labels: history.get(r.id) ?? [r.label] }));
  }

  private recentLabels(ids: number[], beforeRunId: number): Map<number, string[]> {
    const rows = this.db
      .prepare(
        `SELECT thread_id, cluster_story FROM (
           SELECT thread_id, run_id, cluster_story,
                  ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY run_id DESC) AS rn
           FROM thread_installments
           WHERE run_id < ? AND cluster_story IS NOT NULL AND thread_id IN (${ids.map(() => "?").join(",")})
         ) WHERE rn <= ? ORDER BY thread_id, run_id`,
      )
      .all(beforeRunId, ...ids, RECENT_LABELS_K) as { thread_id: number; cluster_story: string }[];
    const out = new Map<number, string[]>();
    for (const r of rows) out.set(r.thread_id, [...(out.get(r.thread_id) ?? []), r.cluster_story]);
    return out;
  }

  openQuestions(threadId: number): string[] {
    return (this.db.prepare("SELECT question FROM thread_questions WHERE thread_id = ? AND status = 'open' ORDER BY id").all(threadId) as { question: string }[]).map((r) => r.question);
  }

  // The thread's memory: the last `limit` runs' deltas, oldest first.
  recentDeltas(threadId: number, limit = 3): string[] {
    const rows = this.db
      .prepare("SELECT content FROM thread_installments WHERE thread_id = ? AND content IS NOT NULL ORDER BY run_id DESC LIMIT ?")
      .all(threadId, limit) as { content: string }[];
    return rows.toReversed().map((r) => deltaFromFacts(whatsNew(r.content))).filter(Boolean);
  }

  installmentContent(threadId: number, runId: number): string | null | undefined {
    const row = this.db.prepare("SELECT content FROM thread_installments WHERE thread_id = ? AND run_id = ?").get(threadId, runId) as { content: string | null } | undefined;
    return row ? row.content : undefined;
  }

  // What the render needs: the id, the "day N" count, and this run's delta ("" on a quiet day).
  renderContext(threadId: number, runId: number): RenderContext {
    const { n } = this.db.prepare("SELECT COUNT(*) AS n FROM thread_installments WHERE thread_id = ?").get(threadId) as { n: number };
    return { thread_id: threadId, day: n, delta: deltaFromFacts(whatsNew(this.installmentContent(threadId, runId))) };
  }

  createThread(label: string, runId: number): number {
    const r = this.db.prepare("INSERT INTO threads (slug, label, status, first_run_id, last_run_id) VALUES (?, ?, 'active', ?, ?)").run(slugify(label), label, runId, runId);
    return Number(r.lastInsertRowid);
  }

  touchThread(threadId: number, label: string, runId: number): void {
    this.db
      .prepare("UPDATE threads SET last_run_id = ?, label = ?, status = 'active', updated_at = datetime('now', 'utc') WHERE id = ? AND merged_into IS NULL")
      .run(runId, label, threadId);
  }

  // matched_score: NULL for a new thread, 1.0 for a linker continuation (a binary decision).
  recordInstallment(threadId: number, runId: number, clusterStory: string, isNew: boolean): void {
    this.db.prepare("INSERT INTO thread_installments (thread_id, run_id, cluster_story, matched_score) VALUES (?, ?, ?, ?)").run(threadId, runId, clusterStory, isNew ? null : 1.0);
  }

  decayThreads(currentRunId: number, dormantAfter: number): void {
    this.db
      .prepare(
        `UPDATE threads SET status = 'dormant', updated_at = datetime('now', 'utc')
         WHERE status = 'active'
           AND last_run_id IS NOT NULL
           AND (SELECT COUNT(*) FROM digest_runs
                WHERE id > threads.last_run_id AND id < ? AND completed_at IS NOT NULL) > ?`,
      )
      .run(currentRunId, dormantAfter);
  }

  hasRunHealth(runId: number): boolean {
    return this.db.prepare("SELECT 1 FROM thread_runs WHERE run_id = ?").get(runId) !== undefined;
  }

  recordRunHealth(runId: number, synthesized: number, auditFailures: number): void {
    this.db.prepare("INSERT INTO thread_runs (run_id, threads_synthesized, audit_failures) VALUES (?, ?, ?)").run(runId, synthesized, auditFailures);
  }

  setInstallmentContent(threadId: number, runId: number, content: string): void {
    this.db.prepare("UPDATE thread_installments SET content = ? WHERE thread_id = ? AND run_id = ?").run(content, threadId, runId);
  }

  addQuestions(threadId: number, questions: string[], runId: number): void {
    const stmt = this.db.prepare("INSERT INTO thread_questions (thread_id, question, status, raised_run_id) VALUES (?, ?, 'open', ?)");
    for (const q of questions) stmt.run(threadId, q, runId);
  }

  resolveQuestion(threadId: number, question: string, runId: number, how: string): void {
    this.db
      .prepare("UPDATE thread_questions SET status = 'resolved', resolved_run_id = ?, resolved_how = ? WHERE thread_id = ? AND question = ? AND status = 'open'")
      .run(runId, how, threadId, question);
  }

  runInstallments(runId: number): number {
    return (this.db.prepare("SELECT COUNT(*) AS n FROM thread_installments WHERE run_id = ?").get(runId) as { n: number }).n;
  }
}
