import { describe, expect, it } from "vitest";
import { openDb, type Db } from "../store/db.js";
import { migratedDb } from "../store/test-db.js";
import { broadcastState, clearClaim, clearClaimCommand } from "./broadcast-state.js";

// Run 300 on 2026-09-08, published, with the day's send in `status` (none when null).
async function day(status: string | null, id: string | null = null): Promise<Db> {
  const d = openDb(await migratedDb([{ id: 300, runAt: "2026-09-08 10:25:40" }]));
  await d.run("INSERT INTO issues (issue_date, revision, run_id, html) VALUES ('2026-09-08', 1, 300, '')");
  if (status !== null)
    await d.run("INSERT INTO sends (issue_date, run_id, revision, status, resend_id, claim_token, claimed_at) VALUES ('2026-09-08', 300, 1, $1, $2, '00000000-0000-4000-8000-000000000001', '2026-09-08 10:00:00+00')", [status, id]);
  return d;
}

describe("the day's broadcast state", () => {
  it("reads the status of the run's day, and none for a day with no send", async () => {
    expect(await broadcastState(await day("sent", "b1"), 300)).toEqual({ date: "2026-09-08", id: "b1", status: "sent" });
    expect(await broadcastState(await day(null), 300)).toBeNull();
    expect(await broadcastState(await day("sent", "b1"), 301)).toBeNull();
  });
  it("reads the issue date the run is for, not the day it started: a run for tomorrow is not yesterday's send", async () => {
    const d = await day("sent", "b1");
    await d.run("INSERT INTO runs (id, started_at, status) VALUES (301, '2026-09-08 11:00:00+00', 'failed')");
    expect(await broadcastState(d, 301, "2026-09-09")).toBeNull();
    expect(await broadcastState(d, 300, "2026-09-08")).toMatchObject({ date: "2026-09-08", status: "sent" });
  });
  it("shows a held claim as its claim text", async () => {
    expect(await broadcastState(await day("claimed"), 300)).toEqual({ date: "2026-09-08", id: null, status: "claimed 2026-09-08 10:00:00 00000000-0000-4000-8000-000000000001" });
  });
  it("clearClaim clears only a claim with no broadcast behind it, and says whether it did", async () => {
    const claimed = await day("claimed");
    expect(await clearClaim(claimed, "2026-09-08")).toBe(true);
    expect(await broadcastState(claimed, 300)).toBeNull();
    const draft = await day("draft", "b1");
    expect(await clearClaim(draft, "2026-09-08")).toBe(false);
    expect(await broadcastState(draft, 300)).toMatchObject({ status: "draft", id: "b1" });
  });
  it("the clear command names the CLI and the date", () => {
    expect(clearClaimCommand("2026-09-08")).toBe("node dist/cli/clear-claim.js 2026-09-08");
  });
});
