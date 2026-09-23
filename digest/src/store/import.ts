import { readFileSync } from "node:fs";
import { artifactKind } from "./artifact-kinds.js";
import type { Db, Sql } from "./db.js";
import { RESET_IDENTITIES } from "./schema.js";

// The legacy SQLite database into the product schema (data-model design §5.1). copyLegacy
// (legacy-copy.ts) copies the file table for table into this database's `legacy` schema, the
// migrations build the product schema in public, and transform copies across in one transaction.
// verify holds the copy against the legacy tables before they are dropped.
export const TRANSFORM = new URL("../../db/import/transform.sql", import.meta.url).pathname;

// Refuses a database that already holds anything in public: the import writes a fresh database only.
export async function assertEmpty(db: Sql): Promise<void> {
  const r = await db.one<{ n: number }>("SELECT count(*) AS n FROM pg_tables WHERE schemaname IN ('public', 'legacy')");
  if (r!.n > 0) throw new Error(`the target database already has ${r!.n} table(s) in public or legacy; the import writes only into a fresh database`);
}

export async function transform(db: Db, sql = readFileSync(TRANSFORM, "utf8")): Promise<void> {
  await db.tx(async (t) => {
    await t.exec(sql);
    for (const { n } of await t.all<{ n: string }>("SELECT DISTINCT name AS n FROM artifacts")) {
      const k = artifactKind(n);
      if (k.stage !== null) await t.run("UPDATE artifacts SET stage = $2, kind = $3, branch = $4 WHERE name = $1", [n, k.stage, k.kind, k.branch]);
    }
    await t.exec(RESET_IDENTITIES);
  });
}

// Each check is a query over both schemas that returns the number of rows that break it; all must be 0.
const utc = (col: string) => `to_char(${col} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')`;
export const CHECKS: [name: string, sql: string][] = [
  // Times are compared as the text SQLite held, not through the transform's own conversion. A legacy
  // run's completed_at is its one attempt's end.
  ["every legacy run is a run, with its times, counts and error", `SELECT count(*) FROM legacy.digest_runs l FULL JOIN runs r ON r.id = l.id LEFT JOIN run_attempts a ON a.id = l.id
     WHERE r.id IS NULL OR l.id IS NULL OR ${utc("r.started_at")} IS DISTINCT FROM l.run_at OR ${utc("a.ended_at")} IS DISTINCT FROM l.completed_at
        OR r.articles_kept IS DISTINCT FROM l.articles_kept OR r.git_sha IS DISTINCT FROM l.git_sha OR r.error IS DISTINCT FROM l.error`],
  ["a sent outcome only where the legacy run emailed someone", `SELECT count(*) FROM runs r JOIN legacy.digest_runs l ON l.id = r.id
     WHERE (r.outcome = 'sent') <> (l.status = 'completed' AND COALESCE(l.articles_emailed, 0) > 0)`],
  ["every model call, column for column", `SELECT count(*) FROM legacy.run_usage l FULL JOIN model_calls c ON c.id = l.id
     WHERE c.id IS NULL OR l.id IS NULL OR c.run_id IS DISTINCT FROM l.run_id OR c.stage <> l.subagent OR c.request_model <> l.model
        OR c.input_tokens <> l.input_tokens OR c.output_tokens <> l.output_tokens OR c.cache_creation_input_tokens <> l.cache_write_tokens
        OR c.cache_read_input_tokens <> l.cache_read_tokens OR c.api_cost_usd <> l.api_cost_usd OR c.duration_ms IS DISTINCT FROM l.duration_ms
        OR c.thinking IS DISTINCT FROM l.thinking OR c.effort IS DISTINCT FROM l.effort OR ${utc("c.recorded_at")} IS DISTINCT FROM l.recorded_at`],
  ["every artifact, content byte for byte", `SELECT count(*) FROM legacy.run_artifacts l LEFT JOIN artifacts a ON a.id = l.id
     WHERE a.id IS NULL OR a.content IS DISTINCT FROM l.content OR a.sha256 <> encode(sha256(convert_to(l.content, 'UTF8')), 'hex')`],
  ["a selections.json for every run the retired table held", `SELECT count(*) FROM legacy.selections s
     WHERE NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.run_id = s.run_id AND a.name = 'selections.json' AND a.status = 'current' AND a.content = s.selections_json)`],
  ["every issue, html byte for byte", `SELECT count(*) FROM legacy.digests d LEFT JOIN issues i ON i.issue_date = d.date::date AND i.revision = 1
     WHERE i.issue_date IS NULL OR i.html IS DISTINCT FROM d.html OR i.run_id IS DISTINCT FROM d.run_id`],
  ["no issue the legacy file did not have", `SELECT (SELECT count(*) FROM issues) - (SELECT count(*) FROM legacy.digests)`],
  // A send is a broadcast, or, before broadcasts, a run that emailed someone.
  ["every send, with its broadcast id and recipients", `SELECT count(*) FROM (legacy.digests d LEFT JOIN legacy.digest_runs r ON r.id = d.run_id) FULL JOIN sends s ON s.issue_date = d.date::date
     WHERE (d.broadcast_status IS NOT NULL OR COALESCE(r.articles_emailed, 0) > 0) IS DISTINCT FROM (s.issue_date IS NOT NULL)
        OR (s.issue_date IS NOT NULL AND (s.resend_id IS DISTINCT FROM d.broadcast_id OR s.run_id IS DISTINCT FROM d.run_id OR s.status <> 'sent' OR s.revision <> 1
            OR s.recipients IS DISTINCT FROM COALESCE(d.broadcast_recipients, r.articles_emailed)))`],
  ["every shown story source, column for column", `SELECT count(*) FROM legacy.shown_narratives l FULL JOIN story_sources s ON s.id = l.id
     WHERE s.id IS NULL OR l.id IS NULL OR s.headline <> l.headline OR s.tier IS DISTINCT FROM l.tier OR ${utc("s.shown_at")} IS DISTINCT FROM l.shown_at
        OR s.source_id IS DISTINCT FROM l.source_id OR s.run_id IS DISTINCT FROM l.run_id OR s.source_title IS DISTINCT FROM l.original_title OR s.cluster_id IS DISTINCT FROM l.cluster_id`],
  ["every fetched article, column for column", `SELECT count(*) FROM legacy.fetched_articles l FULL JOIN articles f ON f.id = l.id
     WHERE f.id IS NULL OR l.id IS NULL OR f.run_id IS DISTINCT FROM l.run_id OR f.source_id <> l.source_id OR f.title <> l.title OR f.url <> l.url
        OR f.published_raw IS DISTINCT FROM l.published OR f.summary IS DISTINCT FROM l.summary OR ${utc("f.fetched_at")} IS DISTINCT FROM l.fetched_at`],
  ["every source fetch, column for column", `SELECT count(*) FROM legacy.source_health l FULL JOIN source_fetches h ON h.id = l.id
     WHERE h.id IS NULL OR l.id IS NULL OR h.source_id <> l.source_id OR h.is_success <> (l.success <> 0) OR h.error IS DISTINCT FROM l.error_message
        OR ${utc("h.fetched_at")} IS DISTINCT FROM l.recorded_at OR h.articles_fetched IS DISTINCT FROM l.articles_fetched OR h.articles_kept IS DISTINCT FROM l.articles_kept OR h.run_id IS DISTINCT FROM l.run_id`],
  ["every dedup match, column for column", `SELECT count(*) FROM legacy.dedup_log l FULL JOIN dedup_matches d ON d.id = l.id
     WHERE d.id IS NULL OR l.id IS NULL OR ${utc("d.matched_at")} IS DISTINCT FROM l.logged_at OR d.title <> l.article_title
        OR d.source_id IS DISTINCT FROM l.article_source_id OR d.matched_headline <> l.matched_headline OR d.similarity <> l.similarity
        OR d.threshold <> l.threshold OR d.run_id IS DISTINCT FROM l.run_id`],
  ["every thread's derived label is its stored label", `SELECT count(*) FROM legacy.threads l
     WHERE l.label IS DISTINCT FROM (SELECT label FROM thread_updates u WHERE u.thread_id = l.id ORDER BY run_id DESC, id DESC LIMIT 1)`],
  ["every thread's derived status is its stored status", `SELECT count(*) FROM legacy.threads l LEFT JOIN thread_state s ON s.id = l.id WHERE s.status IS DISTINCT FROM l.status`],
  ["every thread update, a continuation as the linker decided", `SELECT count(*) FROM legacy.thread_installments l FULL JOIN thread_updates u ON u.id = l.id
     WHERE u.id IS NULL OR l.id IS NULL OR u.thread_id <> l.thread_id OR u.run_id <> l.run_id OR u.label IS DISTINCT FROM l.cluster_story
        OR u.is_continuation <> (l.matched_score IS NOT NULL) OR u.content IS DISTINCT FROM l.content`],
  ["every question, open or resolved as it was", `SELECT count(*) FROM legacy.thread_questions l LEFT JOIN thread_questions q ON q.id = l.id
     WHERE q.id IS NULL OR (l.status = 'resolved') <> EXISTS (SELECT 1 FROM thread_question_resolutions r
       WHERE r.question_id = l.id AND r.resolved_run_id = l.resolved_run_id AND r.answer = COALESCE(l.resolved_how, ''))`],
];

export async function verify(db: Sql): Promise<{ name: string; broken: number }[]> {
  const out: { name: string; broken: number }[] = [];
  for (const [name, sql] of CHECKS) out.push({ name, broken: Number(Object.values((await db.one<Record<string, unknown>>(sql))!)[0]) });
  return out;
}

export async function dropLegacy(db: Sql): Promise<void> {
  await db.exec("DROP SCHEMA legacy CASCADE");
}
