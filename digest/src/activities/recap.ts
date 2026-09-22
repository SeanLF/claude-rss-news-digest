import type { UsageRow } from "../store/usage.js";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ApplicationFailure } from "@temporalio/common";
import { assertNoUrls } from "../contracts/ids.js";
import { parseAgentSpec } from "../runner/prompt.js";
import { runStage, type SdkQuery } from "../runner/run-stage.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";

export const RECAP_INPUT = "recent_rss_titles.csv";
export const RECAP_OUTPUT = "recap.txt";

// orchestrate.validate_recap, ported: present and non-empty. Shape only.
export function validRecap(text: string): boolean {
  return text.trim().length > 0;
}

export interface RecapDeps {
  signal?: () => AbortSignal | undefined;
  store: ArtifactStore;
  agentsDir: string;
  query?: SdkQuery;
  heartbeat?: () => void;
  onUsage?: (row: UsageRow) => void;
}

// The first real activity. Idempotent on output (spec §2.1): a valid archived recap is returned
// without a model call; one that fails the validator is quarantined and a fresh sample produced;
// force replaces. Text in (the titles CSV inline), text out (the final message): no tools.
export function recapActivity(deps: RecapDeps): (runId: number, force?: boolean) => Promise<Pointer> {
  return async (runId, force = false) => {
    const { store } = deps;
    const existing = store.find(runId, RECAP_OUTPUT);
    if (existing && !force) {
      if (validRecap(store.get(existing))) return existing;
      store.quarantine(runId, RECAP_OUTPUT);
    }
    const input = store.find(runId, RECAP_INPUT);
    if (!input) throw ApplicationFailure.nonRetryable(`run ${runId} has no ${RECAP_INPUT}; prepare has not run`, "MissingInput");
    const titles = store.get(input);
    assertNoUrls(titles); // the invariant, checked where text leaves code (spec §1)
    const spec = parseAgentSpec(readFileSync(join(deps.agentsDir, "recap.md"), "utf8"));
    deps.heartbeat?.();
    const r = await runStage(
      spec,
      { userMessage: `Recent RSS titles (title,date):\n\n${titles}`, inputDir: tmpdir() },
      { today: store.runDate(runId), ...(deps.query ? { query: deps.query } : {}), ...(deps.heartbeat ? { heartbeat: deps.heartbeat } : {}), ...(deps.signal?.() ? { signal: deps.signal()! } : {}) },
    );
    deps.heartbeat?.();
    deps.onUsage?.({ model: spec.model, thinking: spec.thinking, tokens: r.usage, stage: "recap", runId, costUsd: r.costUsd, durationMs: r.durationMs, numTurns: r.numTurns });
    const text = r.text.trim();
    if (!validRecap(text)) throw new Error(`recap for run ${runId}: model returned an empty recap`);
    return force ? store.replace(runId, RECAP_OUTPUT, text) : store.put(runId, RECAP_OUTPUT, text);
  };
}
