import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { migratedDb } from "../store/migrated-db.js";
import { broadcastState, clearClaim, clearClaimCommand } from "./broadcast-state.js";

function db(status: string | null, id: string | null = null): DatabaseSync {
  const d = new DatabaseSync(migratedDb([{ id: 300, runAt: "2026-09-08 10:25:40" }]));
  d.prepare("INSERT INTO digests (date, html, run_id, broadcast_id, broadcast_status) VALUES ('2026-09-08', '', 300, ?, ?)").run(id, status);
  return d;
}

describe("the day's broadcast state", () => {
  it("reads the status of the run's day", () => {
    expect(broadcastState(db("sent", "b1"), 300)).toEqual({ date: "2026-09-08", id: "b1", status: "sent" });
    expect(broadcastState(db(null), 301)).toBeNull();
  });
  it("clearClaim clears only a claim with no broadcast behind it, and says whether it did", () => {
    const claimed = db("claimed 2026-09-08T10:00:00.000Z tok");
    expect(clearClaim(claimed, "2026-09-08")).toBe(true);
    expect(broadcastState(claimed, 300)).toMatchObject({ status: null });
    const created = db("created", "b1");
    expect(clearClaim(created, "2026-09-08")).toBe(false);
    expect(broadcastState(created, 300)).toMatchObject({ status: "created", id: "b1" });
  });
  it("the clear command names the CLI and the date", () => {
    expect(clearClaimCommand("2026-09-08")).toBe("node dist/cli/clear-claim.js 2026-09-08");
  });
});
