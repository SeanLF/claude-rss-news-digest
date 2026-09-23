// A promptfoo provider (its JavaScript extension point) that runs one closed day through the digest
// workflow on local Temporal and reports what the band compares: the assembled selections as output,
// cost from model_calls, latency from the call. promptfoo's --repeat makes the reps; its cost and
// latency assertions hold the old system's band as thresholds.
import { approveSignal } from "../workflow/signals.js";
import { connect, startDigest } from "../client.js";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import { runCost } from "../store/usage.js";

interface Vars { run: number | string; date: string }

export default class DigestWorkflowProvider {
  private readonly dbUrl: string;
  constructor(options: { config?: { dbUrl?: string } } = {}) {
    const p = options.config?.dbUrl ?? process.env["BAND_DB"];
    if (!p) throw new Error("digest-workflow provider needs config.dbUrl or BAND_DB: the scratch DB the worker also uses");
    this.dbUrl = p;
  }
  id(): string {
    return "digest-workflow";
  }
  async callApi(_prompt: string, context: { vars: Vars }) {
    const run = Number(context.vars.run);
    const since = new Date().toISOString().replace("T", " ").slice(0, 19); // model_calls's recorded_at format
    const t0 = Date.now();
    const handle = await startDigest(await connect(), context.vars.date, { resumeRun: run, force: true });
    await handle.signal(approveSignal, { decision: "approve" }); // the hold is not part of the band
    const result = await handle.result();
    const latencyMs = Date.now() - t0;
    const { costUsd, calls } = await runCost(openDb(this.dbUrl), run, since);
    const store = new ArtifactStore(this.dbUrl);
    const selections = await store.find(run, "selections.json");
    const output = selections ? await store.get(selections) : "";
    return { output, cost: costUsd, latencyMs, metadata: { ...result, calls } };
  }
}
