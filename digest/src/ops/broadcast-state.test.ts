import { describe, expect, it } from "vitest";
import { openDb, type Db } from "../store/db.js";
import { migratedDb } from "../store/test-db.js";
import { broadcastState, clearClaim, clearClaimCommand } from "./broadcast-state.js";

// Run 300 on 2026-09-08, published, with the day's send in `status` (none when null).
async function day(status: string | null, id: string | null = null): Promise<Db> {
  const d = openDb(await migratedDb([{ id: 300, runAt: "2026-09-08 10:25:40" }]));
  await d.run("INSERT INTO issues (date, revision, run_id, html) VALUES ('2026-09-08', 1, 300, '')");
  if (status !== null)
    await d.run("INSERT INTO broadcasts (date, run_id, revision, status, resend_id, claim_token, claimed_at) VALUES ('2026-09-08', 300, 1, $1, $2, 'tok', '2026-09-08 10:00:00+00')", [status, id]);
  return d;
}

describe("the day's broadcast state", () => {
  it("reads the status of the run's day, and none for a day with no send", async () => {
    expect(await broadcastState(await day("sent", "b1"), 300)).toEqual({ date: "2026-09-08", id: "b1", status: "sent" });
    expect(await broadcastState(await day(null), 300)).toBeNull();
    expect(await broadcastState(await day("sent", "b1"), 301)).toBeNull();
  });
  it("shows a held claim as its claim text", async () => {
    expect(await broadcastState(await day("claimed"), 300)).toEqual({ date: "2026-09-08", id: null, status: "claimed 2026-09-08 10:00:00 tok" });
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
