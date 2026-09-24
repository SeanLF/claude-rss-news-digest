import type { RowOf, Sql } from "../store/db.js";

// The recent-headline context, read for the run's OWN moment (runs.started_at) rather than now():
// the run date is an input read once (spec §1), and a replayed day must see the history it saw then,
// not rows written after it. Times are the UTC text the store returns ("YYYY-MM-DD HH:MM:SS").
export async function runAt(db: Sql, runId: number): Promise<string> {
  const r = await db.one<Pick<RowOf<"runs">, "started_at">>("SELECT started_at FROM runs WHERE id=$1", [runId]);
  if (!r) throw new Error(`no run ${runId}`);
  return r.started_at;
}

const at = "$1::timestamp AT TIME ZONE 'UTC'";
const utcDay = (col: string) => `to_char(${col} AT TIME ZONE 'UTC', 'YYYY-MM-DD')`;

// db.get_previous_headlines: RSS titles shown in the last 7 days, newest first, for dedup and RECAP.
export function previousHeadlines(db: Sql, when: string, days = 7): Promise<{ headline: string; tier: string; date: string }[]> {
  return db.all(
    `SELECT COALESCE(source_title, headline) AS headline, tier, ${utcDay("shown_at")} AS date FROM story_sources
     WHERE shown_at > ${at} - make_interval(days => $2) AND shown_at < ${at} ORDER BY shown_at DESC, id DESC`,
    [when, days],
  );
}

// db.get_yesterday_digest_headlines: the editorial headlines of the last sent run before this one.
export function yesterdayHeadlines(db: Sql, when: string): Promise<{ headline: string; tier: string }[]> {
  return db.all(
    `SELECT headline, tier FROM story_sources
     WHERE run_id = (SELECT id FROM runs WHERE id IN (SELECT run_id FROM sent_runs) AND started_at < ${at} ORDER BY started_at DESC LIMIT 1)
       AND tier IN ('must_know', 'should_know') ORDER BY tier COLLATE "C", headline COLLATE "C"`,
    [when],
  );
}

// db.get_recent_digest_headlines: headlines of sent runs in the last 7 days, one per headline,
// tier and date from its newest showing, newest first; a tie in the same run in descending byte order
// of the headline, as SQLite's sorter left it in the archived runs (run 300's file).
export async function recentDigestHeadlines(db: Sql, when: string, days = 7): Promise<{ headline: string; tier: string; date: string }[]> {
  const rows = await db.all<{ headline: string; tier: string; last_shown: string }>(
    `SELECT * FROM (SELECT DISTINCT ON (sn.headline COLLATE "C") sn.headline AS headline, sn.tier AS tier, dr.started_at AS last_shown
     FROM story_sources sn JOIN runs dr ON dr.id = sn.run_id
     WHERE dr.id IN (SELECT run_id FROM sent_runs) AND (dr.started_at AT TIME ZONE 'UTC')::date >= (${at} AT TIME ZONE 'UTC')::date - $2::int AND dr.started_at < ${at}
       AND sn.tier IN ('must_know', 'should_know') AND sn.headline IS NOT NULL AND sn.headline != ''
     ORDER BY sn.headline COLLATE "C", dr.started_at DESC, sn.id) x
     ORDER BY last_shown DESC, headline COLLATE "C" DESC`,
    [when, days],
  );
  return rows.map((r) => ({ headline: r.headline, tier: r.tier, date: r.last_shown.slice(0, 10) }));
}

// The three context files, byte for byte as the archive holds them (the Python's CRLF CSV lines are
// normalised to LF when it archives; the store holds what the archive holds).
export const recentTitlesCsv = (rows: { headline: string; date: string }[]): string =>
  ["title,date", ...rows.map((r) => [r.headline, r.date].map(csvField).join(","))].join("\n") + "\n";
export const yesterdayTxt = (rows: { headline: string; tier: string }[]): string => rows.map((r) => `${r.tier}: ${r.headline}\n`).join("");
export const recentTxt = (rows: { headline: string; tier: string; date: string }[]): string => rows.map((r) => `${r.date} | ${r.tier}: ${r.headline}\n`).join("");
function csvField(v: string): string {
  return /[",\r\n]/.test(v) ? `"${v.replaceAll('"', '""')}"` : v;
}
