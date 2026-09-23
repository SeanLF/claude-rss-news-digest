import type { Sql } from "../store/db.js";

// A broadcast in any of these states was accepted for delivery, so a send whose response failed
// actually landed. "sent" is safe only because a first send always goes to a fresh draft.
export const ACCEPTED_BROADCAST_STATES: ReadonlySet<string> = new Set(["queued", "sending", "sent"]);
// How a held claim reads to the alerting and the operator: `claimed <iso> <token>`.
export const CLAIMED = "claimed ";

export interface BroadcastRow { date: string; runId: number; id: string | null; status: string; recipients: number | null; token: string | null; claimedAt: string | null }

export function broadcastRow(db: Sql, date: string): Promise<BroadcastRow | undefined> {
  return db.one<BroadcastRow>('SELECT date, run_id AS "runId", resend_id AS id, status, recipients, claim_token AS token, claimed_at AS "claimedAt" FROM broadcasts WHERE date=$1', [date]);
}

export const claimText = (row: Pick<BroadcastRow, "claimedAt" | "token">): string => `${CLAIMED}${row.claimedAt ?? "?"} ${row.token ?? "?"}`;

// The send for a run's UTC day, null when the day has none. A held claim reads as its claim text.
export async function broadcastState(db: Sql, runId: number): Promise<{ date: string; id: string | null; status: string | null } | null> {
  const run = await db.one<{ date: string }>("SELECT (run_at AT TIME ZONE 'UTC')::date AS date FROM digest_runs WHERE id = $1", [runId]);
  const row = run && (await broadcastRow(db, run.date));
  if (!row) return null;
  return { date: row.date, id: row.id, status: row.status === "claimed" ? claimText(row) : row.status };
}

// A claim is never taken over automatically: an attempt that looks dead may still be in Resend's
// create, and taking over sent twice. Only an operator, having checked Resend, clears it.
export async function clearClaim(db: Sql, date: string): Promise<boolean> {
  return (await db.run("DELETE FROM broadcasts WHERE date=$1 AND resend_id IS NULL AND status='claimed'", [date])) === 1;
}
export const clearClaimCommand = (date: string): string => `node dist/cli/clear-claim.js ${date}`;
