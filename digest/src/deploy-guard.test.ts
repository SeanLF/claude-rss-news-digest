import type { Client } from "@temporalio/client";
import { describe, expect, it } from "vitest";
import { guard, inRunWindow, runningDigests } from "./deploy-guard.js";

describe("inRunWindow", () => {
  it.each([
    ["2026-07-15T09:59:00Z", false], // 11:59 Paris, summer (UTC+2)
    ["2026-07-15T10:00:00Z", true], // 12:00 Paris
    ["2026-07-15T11:44:00Z", true], // 13:44 Paris
    ["2026-07-15T11:45:00Z", false], // 13:45 Paris
    ["2026-01-15T10:30:00Z", false], // 11:30 Paris, winter (UTC+1)
    ["2026-01-15T11:00:00Z", true], // 12:00 Paris
    ["2026-01-15T12:44:00Z", true], // 13:44 Paris
  ])("%s -> %s: 12:00 to 13:45 Europe/Paris, whatever the offset", (iso, want) => {
    expect(inRunWindow(new Date(iso))).toBe(want);
  });
});

const listing = (runs: { workflowId: string; stuck?: boolean }[], seen: string[] = []) =>
  ({
    workflow: {
      list: ({ query }: { query: string }) => {
        seen.push(query);
        return (async function* () {
          for (const r of runs) yield { workflowId: r.workflowId, raw: { searchAttributes: { indexedFields: r.stuck ? { TemporalReportedProblems: {} } : {} } } };
        })();
      },
    },
  }) as unknown as Client;

describe("runningDigests", () => {
  it("asks for running DigestWorkflows and names a run whose workflow task keeps failing", async () => {
    const seen: string[] = [];
    expect(await runningDigests(listing([{ workflowId: "digest-2026-09-25" }, { workflowId: "digest-2026-09-24", stuck: true }], seen))).toEqual([
      "digest-2026-09-25",
      "digest-2026-09-24 (stuck: its workflow task keeps failing)",
    ]);
    expect(seen).toEqual(['WorkflowType="DigestWorkflow" AND ExecutionStatus="Running"']);
  });
});

describe("guard", () => {
  const quiet = new Date("2026-07-15T15:00:00Z");
  it("passes outside the window with nothing running", async () => {
    expect(await guard(listing([]), quiet)).toEqual([]);
  });
  it("refuses inside the window and while a digest runs, naming both", async () => {
    expect(await guard(listing([{ workflowId: "digest-2026-07-15" }]), new Date("2026-07-15T10:30:00Z"))).toEqual([
      "12:30 Europe/Paris is inside the run window (12:00-13:45 Europe/Paris)",
      "digest workflow(s) running: digest-2026-07-15",
    ]);
  });
  it("refuses when the running digests cannot be listed, rather than guessing", async () => {
    const broken = { workflow: { list: () => { throw new Error("14 UNAVAILABLE"); } } } as unknown as Client;
    expect(await guard(broken, quiet)).toEqual(["could not list the running digest workflows: Error: 14 UNAVAILABLE"]);
  });
});
