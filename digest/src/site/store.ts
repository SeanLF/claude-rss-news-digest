import type { Sql } from "../store/db.js";
import type { IndexMeta, IssueRowData, SearchHit, SiteData, StatsData, ThreadData, ThreadIndexData, ThreadSummaryData } from "./data.js";

// The site's reads against the product schema (digest/db/migrations). Readers see an issue date's
// highest revision, and a thread's updates only from published runs (thread_state does the same).

const LATEST = `latest AS (SELECT DISTINCT ON (issue_date) issue_date, revision, run_id, html, preheader FROM issues ORDER BY issue_date, revision DESC)`;
const n = (v: unknown): number => Number(v ?? 0);
const nullable = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v));

// Updates readers can see: a published run's.
const PUBLISHED_UPDATE = "u.run_id IN (SELECT run_id FROM published_runs)";
// Thread cursors compare whole seconds: the cursor travels as "YYYY-MM-DD HH:MM:SS" text.
const SECOND = "date_trunc('second', t.updated_at)";

// A sum of doubles taken exactly and rounded once. SQLite sums with compensation (Kahan-Babuska-
// Neumaier), so its totals are the correctly rounded ones, where a plain float sum here differs in the
// last bit. Through text, because a float8 cast to numeric keeps only 15 digits.
const EXACT_SUM = (col: string): string => `sum(${col}::text::numeric)::float8`;

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);

const SUMMARY_COLS = `t.id, t.label, t.status, t.updated_at, t.update_count,
    (SELECT u.content FROM thread_updates u WHERE u.thread_id = t.id AND ${PUBLISHED_UPDATE} ORDER BY u.run_id DESC LIMIT 1) AS latest_content`;
const summary = (r: Record<string, unknown>): ThreadSummaryData => ({
  id: n(r["id"]),
  label: str(r["label"]),
  status: str(r["status"]),
  updatedAt: str(r["updated_at"]),
  updateCount: n(r["update_count"]),
  latestContent: strOrNull(r["latest_content"]),
});

export function siteStore(db: Sql): SiteData {
  return {
    async indexMeta(): Promise<IndexMeta> {
      const r = (await db.one<Record<string, unknown>>(
        `SELECT count(DISTINCT issue_date) AS total, min(issue_date)::text AS first, max(issue_date)::text AS newest,
                (SELECT count(*) FROM (SELECT DISTINCT run_id, headline FROM story_sources WHERE tier IN ('must_know', 'should_know')) s) AS stories
         FROM issues`,
      ))!;
      return { total: n(r["total"]), firstDate: (r["first"] as string | null) ?? null, newestDate: (r["newest"] as string | null) ?? null, totalStories: n(r["stories"]) };
    },

    async archive(q): Promise<IssueRowData[]> {
      // A year no issue can have is an empty page, as SQLite's string compare answered, not a failed cast.
      if (q.year !== undefined && (q.year < 1 || q.year > 9999)) return [];
      const rows = await db.all<Record<string, unknown>>(
        `WITH ${LATEST},
         numbered AS (
           SELECT issue_date, run_id, preheader, row_number() OVER (ORDER BY issue_date) AS issue_no,
                  issue_date = max(issue_date) OVER (PARTITION BY date_trunc('month', issue_date)) AS month_start
           FROM latest)
         SELECT n.issue_date::text AS date, n.preheader, n.issue_no, n.month_start,
                (SELECT count(DISTINCT headline) FROM story_sources s WHERE s.run_id = n.run_id AND s.tier = 'must_know') AS must,
                (SELECT count(DISTINCT headline) FROM story_sources s WHERE s.run_id = n.run_id AND s.tier = 'should_know') AS should,
                ARRAY(SELECT DISTINCT s.source_id FROM story_sources s WHERE s.run_id = n.run_id AND s.source_id IS NOT NULL) AS source_ids
         FROM numbered n
         WHERE ($1::int IS NULL OR extract(year FROM n.issue_date) = $1)
           AND ($1::int IS NOT NULL OR $2::text IS NULL OR n.issue_date::text < $2)
         ORDER BY n.issue_date DESC
         LIMIT (CASE WHEN $1::int IS NULL THEN $3::int END)`,
        [q.year ?? null, q.before ?? null, q.limit],
      );
      return rows.map((r) => ({
        date: String(r["date"]),
        preheader: str(r["preheader"]),
        issueNo: n(r["issue_no"]),
        must: n(r["must"]),
        should: n(r["should"]),
        isMonthStart: r["month_start"] === true,
        sourceIds: (r["source_ids"] as string[] | null) ?? [],
      }));
    },

    // By the date's text, so a lenient date the route accepts ("2026-1-24") finds nothing rather than
    // failing a cast.
    async issue(date) {
      return db.one<{ html: string; preheader: string; markdown: string | null }>("SELECT html, preheader, markdown FROM issues WHERE issue_date::text = $1 ORDER BY revision DESC LIMIT 1", [date]);
    },

    async latestIssueDate() {
      return (await db.one<{ d: string | null }>("SELECT max(issue_date)::text AS d FROM issues"))?.d ?? undefined;
    },

    async feed(limit) {
      return db.all<{ date: string; preheader: string }>(`WITH ${LATEST} SELECT issue_date::text AS date, preheader FROM latest ORDER BY issue_date DESC LIMIT $1`, [limit]);
    },

    // A literal phrase, stemmed English, ranked by ts_rank; unstemmed (`simple`) only when English
    // leaves no lexeme. One row per story, its best-ranked source's. Chosen by the pre-registered
    // evaluation in docs/proposed/2026-09-23-search-tuning, which `make search-eval` re-runs and which
    // holds this query row for row to its candidate `dedup`. The two branches keep each index usable.
    async search(query, limit): Promise<SearchHit[]> {
      const rows = await db.all<Record<string, unknown>>(
        `WITH q AS (SELECT phraseto_tsquery('english', $1) AS e, phraseto_tsquery('simple', $1) AS s),
         m AS (
           SELECT s.id, s.run_id, s.headline, s.tier, ts_rank(s.search, q.e) AS rank
           FROM story_sources s, q WHERE numnode(q.e) > 0 AND s.search @@ q.e
           UNION ALL
           SELECT s.id, s.run_id, s.headline, s.tier, ts_rank(s.search_simple, q.s)
           FROM story_sources s, q WHERE numnode(q.e) = 0 AND s.search_simple @@ q.s),
         story AS (SELECT DISTINCT ON (run_id, headline) * FROM m ORDER BY run_id, headline, rank DESC, id DESC)
         SELECT x.headline, COALESCE(x.tier, '') AS tier, (SELECT max(i.issue_date)::text FROM issues i WHERE i.run_id = x.run_id) AS date
         FROM story x
         ORDER BY x.rank DESC, x.id DESC
         LIMIT $2`,
        [query, limit],
      );
      return rows.map((r) => ({ headline: String(r["headline"]), tier: String(r["tier"]), date: strOrNull(r["date"]) }));
    },

    async threadIndex(before, limit): Promise<ThreadIndexData> {
      const ongoing = await db.all<Record<string, unknown>>(`SELECT ${SUMMARY_COLS} FROM thread_state t WHERE t.status = 'active' ORDER BY ${SECOND} DESC, t.id DESC`);
      const older = await db.all<Record<string, unknown>>(
        `SELECT ${SUMMARY_COLS} FROM thread_state t
         WHERE t.status NOT IN ('active', 'merged')
           AND ($1::timestamptz IS NULL OR (${SECOND}, t.id) < ($1::timestamptz, $2::bigint))
         ORDER BY ${SECOND} DESC, t.id DESC LIMIT $3`,
        [before ? before.updatedAt : null, before ? before.id : 0, limit],
      );
      const total = await db.one<{ c: number }>("SELECT count(*) AS c FROM thread_state WHERE status NOT IN ('active', 'merged')");
      return { ongoing: ongoing.map(summary), older: older.map(summary), olderTotal: n(total?.c) };
    },

    async mergedInto(id) {
      const r = await db.one<{ m: number | null }>("SELECT merged_into_id AS m FROM threads WHERE id = $1", [id]);
      return r === undefined ? undefined : nullable(r.m);
    },

    async thread(id): Promise<ThreadData | undefined> {
      const head = await db.one<{ label: string; status: string }>("SELECT label, status FROM thread_state WHERE id = $1", [id]);
      if (!head) return undefined;
      const updates = await db.all<Record<string, unknown>>(
        `SELECT to_char(r.started_at, 'YYYY-MM-DD') AS day, (SELECT max(i.issue_date)::text FROM issues i WHERE i.run_id = u.run_id) AS issue_date, u.label, u.content
         FROM thread_updates u JOIN runs r ON r.id = u.run_id
         WHERE u.thread_id = $1 AND ${PUBLISHED_UPDATE}
         ORDER BY u.run_id DESC`,
        [id],
      );
      // Each open question with the update that raised it: its ids ground the question's citations.
      const questions = await db.all<Record<string, unknown>>(
        `SELECT q.question, u.content FROM thread_question_state q
         LEFT JOIN thread_updates u ON u.thread_id = q.thread_id AND u.run_id = q.raised_run_id
         WHERE q.thread_id = $1 AND q.status = 'open'
         ORDER BY q.raised_run_id DESC, q.id DESC`,
        [id],
      );
      return {
        label: head.label,
        status: head.status,
        installments: updates.map((u) => ({ day: String(u["day"]), issueDate: strOrNull(u["issue_date"]), story: str(u["label"]), content: strOrNull(u["content"]) })),
        openQuestions: questions.map((q) => ({ question: String(q["question"]), raisedContent: strOrNull(q["content"]) })),
      };
    },

    async stats(days, now): Promise<StatsData> {
      // Past ~5.8 million days Postgres's interval arithmetic overflows; the whole archive fits far inside.
      // The window never reaches past year 1: SQLite's date arithmetic turned NULL there and matched nothing,
      // where a negative year here would be refused by Postgres.
      const since = new Date(Math.max(now.getTime() - days * 86_400_000, Date.UTC(1, 0, 1))).toISOString().replace(/^\+0*/, "");
      const [health, usage, runs, dedup, never, cost] = await Promise.all([
        db.all<Record<string, unknown>>(
          `SELECT source_id, count(*) AS total, sum(is_success::int) AS successes FROM source_fetches
           WHERE fetched_at >= $1 GROUP BY source_id ORDER BY source_id`,
          [since],
        ),
        db.all<Record<string, unknown>>(
          `SELECT source_id, tier, count(*) AS count FROM story_sources
           WHERE source_id IS NOT NULL AND tier IS NOT NULL AND shown_at >= $1
           GROUP BY source_id, tier ORDER BY count DESC, source_id, tier`,
          [since],
        ),
        // The ten newest emailed runs, whatever the window.
        db.all<Record<string, unknown>>(
          `SELECT to_char(r.started_at, 'YYYY-MM-DD HH24:MI:SS') AS run_at, r.articles_kept,
                  COALESCE((SELECT s.recipients FROM sends s WHERE s.run_id = r.id ORDER BY s.issue_date DESC LIMIT 1), 0) AS recipients,
                  (SELECT ${EXACT_SUM("m.api_cost_usd")} FROM model_calls m WHERE m.run_id = r.id) AS cost
           FROM runs r WHERE r.id IN (SELECT run_id FROM sent_runs)
           ORDER BY r.started_at DESC LIMIT 10`,
        ),
        db.one<Record<string, unknown>>(
          `SELECT count(*) AS c, ${EXACT_SUM("similarity")} / nullif(count(*), 0) AS avg, min(similarity) AS min, max(similarity) AS max
           FROM dedup_matches WHERE matched_at >= $1`,
          [since],
        ),
        db.all<{ source_id: string }>(
          `SELECT DISTINCT f.source_id FROM source_fetches f
           WHERE f.fetched_at >= $1
             AND f.source_id NOT IN (SELECT DISTINCT source_id FROM story_sources WHERE source_id IS NOT NULL AND shown_at >= $1)
           ORDER BY f.source_id`,
          [since],
        ),
        db.one<Record<string, unknown>>(
          `WITH w AS (SELECT r.* FROM runs r WHERE r.id IN (SELECT run_id FROM sent_runs) AND r.started_at >= $1)
           SELECT (SELECT count(*) FROM w) AS runs,
                  (SELECT COALESCE(sum(articles_kept), 0) FROM w) AS kept,
                  (SELECT COALESCE(${EXACT_SUM("m.api_cost_usd")}, 0) FROM model_calls m WHERE m.run_id IN (SELECT id FROM w)) AS cost,
                  (SELECT count(*) FROM (SELECT DISTINCT s.run_id, s.headline FROM story_sources s WHERE s.run_id IN (SELECT id FROM w)) x) AS shipped,
                  (SELECT s.recipients FROM sends s JOIN runs r ON r.id = s.run_id
                     WHERE s.recipients IS NOT NULL AND r.id IN (SELECT run_id FROM sent_runs) ORDER BY r.started_at DESC LIMIT 1) AS recipients`,
          [since],
        ),
      ]);
      return {
        sourceHealth: health.map((h) => ({ sourceId: String(h["source_id"]), total: n(h["total"]), successes: n(h["successes"]) })),
        sourceUsage: usage.map((u) => ({ sourceId: String(u["source_id"]), tier: String(u["tier"]), count: n(u["count"]) })),
        // A run with no kept count has no row to show (the Rust server dropped it the same way).
        recentRuns: runs
          .filter((r) => r["articles_kept"] !== null)
          .map((r) => ({ runAt: String(r["run_at"]), articlesKept: n(r["articles_kept"]), recipients: n(r["recipients"]), apiCostUsd: nullable(r["cost"]) })),
        dedup: { count: n(dedup?.["c"]), avg: nullable(dedup?.["avg"]), min: nullable(dedup?.["min"]), max: nullable(dedup?.["max"]) },
        neverSelected: never.map((r) => r.source_id),
        cost: { runs: n(cost?.["runs"]), keptTotal: n(cost?.["kept"]), costTotal: n(cost?.["cost"]), shippedTotal: n(cost?.["shipped"]), recipientsLatest: n(cost?.["recipients"]) },
      };
    },

    async ping() {
      await db.one("SELECT (SELECT count(*) FROM (SELECT 1 FROM issues LIMIT 1) i) + (SELECT count(*) FROM (SELECT 1 FROM story_sources LIMIT 1) s) AS n");
    },
  };
}
