import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CancelledFailure } from "@temporalio/common";
import { z } from "zod";
import { CoherenceReportSchema, type CoherenceReport } from "../contracts/coherence.js";
import { assertNoUrls, scrubUrls } from "../contracts/ids.js";
import { leaksInternalId } from "../contracts/leaks.js";
import { itemIds, normHeadline, resultMatches } from "../contracts/match.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { Pointer } from "../store/artifacts.js";
import { draftFrom, runChecker, type CoherenceDeps, type Draft } from "./coherence.js";

export const REPAIR_OUTPUT = "repair_resolution.json";
const FIELDS = ["headline", "summary", "why_it_matters"] as const;
type Field = (typeof FIELDS)[number];

export interface RepairRequest { article_ids: string[]; failed_fields: Field[]; reason: string; fields: Record<Field, string> }
export interface Resolution { article_ids: string[]; status: "repaired" | "guard_failed" | "recheck_failed"; recheck_pass: boolean; patched_fields: Partial<Record<Field, string>>; guard?: string }
export interface ResolutionDoc { input: string; results: Resolution[]; fault?: string }

// A failure repair can handle: failed_fields a non-empty subset of the three text fields, on every
// matching failure (repair.build_repair_requests). Anything else stays on the drop path.
export function buildRepairRequests(draft: Draft, report: CoherenceReport): RepairRequest[] {
  const failed = report.results.filter((r) => !r.pass);
  const out: RepairRequest[] = [];
  for (const item of [...draft.must_know, ...draft.should_know]) {
    const ids = itemIds(item.sources);
    const matches = failed.filter((r) => resultMatches(r, ids, normHeadline(item.headline)));
    if (!matches.length) continue;
    const fields = new Set<Field>();
    let usable = true;
    for (const m of matches) {
      const f = m.failed_fields ?? [];
      if (!f.length) usable = false;
      f.forEach((x) => fields.add(x));
    }
    if (!usable || !fields.size) continue;
    out.push({ article_ids: [...ids].toSorted(), failed_fields: [...fields].toSorted(), reason: matches.map((m) => m.reason).join("; "), fields: { headline: item.headline, summary: item.summary, why_it_matters: item.why_it_matters ?? "" } });
  }
  return out;
}

const Patch = z.object({ article_ids: z.array(z.string()), action: z.string().optional(), headline: z.string().optional(), summary: z.string().optional(), why_it_matters: z.string().optional() });
export const RepairedSchema = z.object({ results: z.array(Patch) });
const key = (ids: string[]) => [...new Set(ids)].toSorted().join(",");

// A story is patched only if the repairer returned exactly the flagged fields, each non-empty and
// free of internal ids (repair.apply_repairs). A story split across objects is merged first, last wins.
export function applyRepairs(requests: RepairRequest[], repaired: z.infer<typeof RepairedSchema>): Resolution[] {
  const merged = new Map<string, Partial<Record<Field, string>>>();
  for (const r of repaired.results) {
    if (!r.article_ids.length) continue;
    const m = merged.get(key(r.article_ids)) ?? {};
    for (const f of FIELDS) if (r[f] !== undefined) m[f] = r[f];
    merged.set(key(r.article_ids), m);
  }
  return requests.map((req) => {
    const base = { article_ids: req.article_ids, recheck_pass: false, patched_fields: {} };
    const patch = merged.get(key(req.article_ids));
    if (!patch) return { ...base, status: "guard_failed", guard: "missing from repaired output" };
    const present = FIELDS.filter((f) => patch[f] !== undefined);
    if (present.join() !== [...req.failed_fields].toSorted((a, b) => FIELDS.indexOf(a) - FIELDS.indexOf(b)).join())
      return { ...base, status: "guard_failed", guard: `repaired fields ${present.join(",")} do not match flagged ${req.failed_fields.join(",")}` };
    for (const f of present) {
      const v = patch[f] ?? "";
      if (!v.trim()) return { ...base, status: "guard_failed", guard: `${f} is empty` };
      if (leaksInternalId(v)) return { ...base, status: "guard_failed", guard: `${f} leaks an internal id` };
    }
    return { ...base, status: "recheck_failed", patched_fields: Object.fromEntries(present.map((f) => [f, patch[f]!])) };
  });
}

// A patched story is kept only if the scoped recheck passed it; no verdict, or contradictory
// verdicts for the same story, confirm nothing (repair.build_repair_resolution).
export function resolve(applied: Resolution[], recheck: CoherenceReport): Resolution[] {
  const verdicts = new Map<string, boolean | "conflict">();
  for (const r of recheck.results) {
    if (!r.article_ids.length) continue;
    const k = key(r.article_ids);
    const prior = verdicts.get(k);
    verdicts.set(k, prior === undefined || prior === r.pass ? r.pass : "conflict");
  }
  return applied.map((a) => (a.status !== "recheck_failed" ? a : verdicts.get(key(a.article_ids)) === true ? { ...a, status: "repaired", recheck_pass: true } : a));
}

export interface RepairDeps extends CoherenceDeps {
  query?: SdkQuery;
}

// Best-effort and fail-closed: any failure (other than a cancellation) leaves a resolution with
// no `repaired` verdict, so assemble drops exactly what the checker failed.
export function repairActivity(deps: RepairDeps) {
  return async (runId: number, drafts: Pointer[], report: Pointer, force = false): Promise<Pointer> => {
    const { store } = deps;
    const draft = draftFrom(store, drafts);
    const reportText = store.get(report);
    const input = `${JSON.stringify(draft)}\n${reportText}`;
    const existing = store.find(runId, REPAIR_OUTPUT);
    if (existing && !force) {
      if ((JSON.parse(store.get(existing)) as ResolutionDoc).input === input) return existing;
      store.quarantine(runId, REPAIR_OUTPUT);
    }
    const write = (doc: ResolutionDoc) => (force ? store.replace(runId, REPAIR_OUTPUT, JSON.stringify(doc, null, 2)) : store.put(runId, REPAIR_OUTPUT, JSON.stringify(doc, null, 2)));
    const requests = buildRepairRequests(draft, CoherenceReportSchema.parse(JSON.parse(reportText)));
    if (!requests.length) return write({ input, results: [] });
    let applied: Resolution[] = [];
    try {
      const dir = mkdtempSync(join(tmpdir(), `repair-${runId}-`));
      let repaired: z.infer<typeof RepairedSchema>;
      try {
        const files: [string, string][] = [["repair_requests.json", JSON.stringify({ requests }, null, 2)]];
        for (const n of store.names(runId).filter((x) => /^articles_\d+\.csv$/.test(x) || x === "article_fulltext.json")) files.push([n, store.get(store.find(runId, n)!)]);
        for (const [n, raw] of files) {
          const text = scrubUrls(raw);
          assertNoUrls(text);
          writeFileSync(join(dir, n), text);
        }
        const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "repair.md"), "utf8"));
        deps.heartbeat?.();
        const r = await runStage(spec, { userMessage: `The input directory is ${dir}. Begin.`, inputDir: dir }, { today: store.runDate(runId), outputSchema: z.toJSONSchema(RepairedSchema, { target: "draft-07" }), ...(deps.query ? { query: deps.query } : {}) });
        deps.onUsage?.({ stage: "repair", runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns, toolCalls: r.toolCalls.length, unbackedFails: 0 });
        repaired = RepairedSchema.parse(r.structured);
      } finally {
        rmSync(dir, { recursive: true, force: true }); // the mkdtemp directory this call created
      }
      applied = applyRepairs(requests, repaired);
      const toCheck = applied.filter((a) => a.status === "recheck_failed");
      if (!toCheck.length) return write({ input, results: applied });
      const byKey = new Map(toCheck.map((a) => [key(a.article_ids), a.patched_fields]));
      const scoped: Draft = { must_know: [], should_know: [], preheader: "" };
      for (const tier of ["must_know", "should_know"] as const)
        for (const s of draft[tier]) {
          const p = byKey.get(key(s.sources.map((x) => x.article_id)));
          if (p) scoped[tier].push({ ...s, ...p });
        }
      const checked = await runChecker(deps, runId, JSON.stringify(scoped, null, 2));
      deps.onUsage?.({ stage: "repair_recheck", runId, costUsd: checked.costUsd, durationMs: checked.durationMs, numTurns: checked.numTurns, toolCalls: checked.toolCalls, unbackedFails: checked.unbacked });
      return write({ input, results: resolve(applied, checked.report) });
    } catch (e) {
      if (e instanceof CancelledFailure) throw e; // an activity cancelled by the workflow
      return write({ input, results: applied.map((a) => ({ ...a, status: a.status === "repaired" ? "recheck_failed" : a.status, recheck_pass: false })), fault: String(e).slice(0, 300) });
    }
  };
}
