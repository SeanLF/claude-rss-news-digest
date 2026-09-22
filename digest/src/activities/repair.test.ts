import { describe, expect, it } from "vitest";
import { applyRepairs, buildRepairRequests, resolve, type RepairRequest } from "./repair.js";

const s = (h: string, ids: string[]) => ({ headline: h, summary: "S", why_it_matters: "W", sources: ids.map((article_id) => ({ article_id })) });
const draft = { must_know: [s("Talks resume", ["A1"]), s("Vote passes", ["A2", "A3"])], should_know: [s("Yen jumps", ["A4"])], preheader: "" };

describe("repair", () => {
  it("requests only stories whose every matching failure names repairable fields", () => {
    const report = { results: [
      { headline: "Talks resume", article_ids: ["A1"], pass: false, reason: "summary: 58% absent", failed_fields: ["summary" as const] },
      { headline: "Vote passes", article_ids: ["A3", "A2"], pass: false, reason: "no fields named" },
      { headline: "Yen jumps", article_ids: ["A4"], pass: true, reason: "ok" },
    ] };
    const reqs = buildRepairRequests(draft, report);
    expect(reqs).toEqual([{ article_ids: ["A1"], failed_fields: ["summary"], reason: "summary: 58% absent", fields: { headline: "Talks resume", summary: "S", why_it_matters: "W" } }]);
  });
  const req: RepairRequest = { article_ids: ["A1"], failed_fields: ["summary"], reason: "r", fields: { headline: "H", summary: "S", why_it_matters: "W" } };
  it("patches only exactly the flagged fields, non-empty and free of ids; merges a split story", () => {
    expect(applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "Fixed." }] })[0]).toMatchObject({ status: "recheck_failed", patched_fields: { summary: "Fixed." } });
    expect(applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "Fixed.", headline: "New" }] })[0]?.status).toBe("guard_failed");
    expect(applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "See (A7)." }] })[0]?.guard).toMatch(/internal id/);
    expect(applyRepairs([req], { results: [] })[0]?.guard).toBe("missing from repaired output");
    const two = { ...req, failed_fields: ["headline", "summary"] as ("headline" | "summary")[] };
    expect(applyRepairs([two], { results: [{ article_ids: ["A1"], headline: "H2" }, { article_ids: ["A1"], summary: "S2" }] })[0]?.status).toBe("recheck_failed");
  });
  it("keeps a patch only on a passed recheck; a contradictory or missing verdict confirms nothing", () => {
    const applied = applyRepairs([req], { results: [{ article_ids: ["A1"], summary: "Fixed." }] });
    const scoped = { must_know: [{ ...s("H", ["A1"]), summary: "Fixed." }], should_know: [], preheader: "" };
    expect(resolve(applied, { results: [{ headline: "H", article_ids: ["A1"], pass: true, reason: "ok" }] }, scoped)[0]).toMatchObject({ status: "repaired", recheck_pass: true });
    expect(resolve(applied, { results: [{ headline: "H", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "H", article_ids: ["A1"], pass: false, reason: "no" }] }, scoped)[0]?.status).toBe("recheck_failed");
    expect(resolve(applied, { results: [] }, scoped)[0]?.status).toBe("recheck_failed");
    // a failure matched only by headline still fails the recheck
    expect(resolve(applied, { results: [{ headline: "H", article_ids: ["A1"], pass: true, reason: "ok" }, { headline: "H", article_ids: [], pass: false, reason: "no", failed_fields: ["summary"] }] }, scoped)[0]?.status).toBe("recheck_failed");
  });
});
