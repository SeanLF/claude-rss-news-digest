import type { DatabaseSync } from "node:sqlite";

// The recent-headline context, read for the run's OWN moment (digest_runs.run_at) rather than
// datetime('now'): the run date is an input read once (spec §1), and a replayed day must see the
// history it saw then, not rows written after it.
export function runAt(db: DatabaseSync, runId: number): string {
  const r = db.prepare("SELECT run_at FROM digest_runs WHERE id=?").get(runId) as { run_at: string } | undefined;
  if (!r) throw new Error(`no run ${runId}`);
  return r.run_at;
}

// db.get_previous_headlines: RSS titles shown in the last 7 days, newest first, for dedup and RECAP.
export function previousHeadlines(db: DatabaseSync, at: string, days = 7): { headline: string; tier: string; date: string }[] {
  return db
    .prepare(
      `SELECT COALESCE(original_title, headline) AS headline, tier, date(shown_at) AS date FROM shown_narratives
       WHERE shown_at > datetime(?, ?) AND shown_at < ? ORDER BY shown_at DESC`,
    )
    .all(at, `-${days} days`, at) as { headline: string; tier: string; date: string }[];
}

// db.get_yesterday_digest_headlines: the editorial headlines of the last completed run before this one.
export function yesterdayHeadlines(db: DatabaseSync, at: string): { headline: string; tier: string }[] {
  return db
    .prepare(
      `SELECT headline, tier FROM shown_narratives
       WHERE run_id = (SELECT id FROM digest_runs WHERE completed_at IS NOT NULL AND run_at < ? ORDER BY run_at DESC LIMIT 1)
         AND tier IN ('must_know', 'should_know') ORDER BY tier, headline`,
    )
    .all(at) as { headline: string; tier: string }[];
}

// db.get_recent_digest_headlines: headlines of completed runs in the last 7 days, one per headline,
// tier and date from its newest showing (SQLite's bare-column rule with a single MAX()).
export function recentDigestHeadlines(db: DatabaseSync, at: string, days = 7): { headline: string; tier: string; date: string }[] {
  return (
    db
      .prepare(
        `SELECT sn.headline AS headline, sn.tier AS tier, MAX(dr.run_at) AS last_shown FROM shown_narratives sn
         JOIN digest_runs dr ON dr.id = sn.run_id
         WHERE dr.completed_at IS NOT NULL AND date(dr.run_at) >= date(?, ?) AND dr.run_at < ?
           AND sn.tier IN ('must_know', 'should_know') AND sn.headline IS NOT NULL AND sn.headline != ''
         GROUP BY sn.headline ORDER BY last_shown DESC`,
      )
      .all(at, `-${days} days`, at) as { headline: string; tier: string; last_shown: string }[]
  ).map((r) => ({ headline: r.headline, tier: r.tier, date: r.last_shown.slice(0, 10) }));
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
