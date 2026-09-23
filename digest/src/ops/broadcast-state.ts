import type { DatabaseSync } from "node:sqlite";

// A broadcast in any of these states was accepted for delivery, so a send whose response failed
// actually landed. "sent" is safe only because a first send always goes to a fresh draft.
export const ACCEPTED_BROADCAST_STATES: ReadonlySet<string> = new Set(["queued", "sending", "sent"]);
// The prefix of a send attempt's claim on its date, held in digests.broadcast_status.
export const CLAIMED = "claimed ";

// The day's broadcast columns for a run, by the run's UTC day (the digests key); null with no row.
export function broadcastState(db: DatabaseSync, runId: number): { date: string; id: string | null; status: string | null } | null {
  const row = db.prepare("SELECT d.date AS date, d.broadcast_id AS id, d.broadcast_status AS status FROM digests d JOIN digest_runs r ON d.date = date(r.run_at) WHERE r.id = ?").get(runId) as { date: string; id: string | null; status: string | null } | undefined;
  return row ?? null;
}

// A claim is never taken over automatically: an attempt that looks dead may still be in Resend's
// create, and taking over sent twice. Only an operator, having checked Resend, clears it.
export function clearClaim(db: DatabaseSync, date: string): boolean {
  const { changes } = db.prepare("UPDATE digests SET broadcast_status=NULL WHERE date=? AND broadcast_id IS NULL AND broadcast_status LIKE ?").run(date, `${CLAIMED}%`);
  return Number(changes) === 1;
}
export const clearClaimCommand = (date: string): string => `node dist/cli/clear-claim.js ${date}`;
