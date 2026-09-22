import { ActivityFailure, ApplicationFailure, CancelledFailure, condition, proxyActivities, setHandler } from "@temporalio/workflow";
import type { Activities, DigestInput, DigestOutput } from "../activities/index.js";
import { SOURCE_IDS_STUB } from "../activities/index.js";
import { mapBounded, MODEL_FANOUT_LIMIT } from "./bounded.js";
import { approveSignal, operatorNoteSignal, retrySignal } from "./signals.js";

export const WORKFLOW_RUN_TIMEOUT = "4 hours";
export const HOLD_TIMEOUT = "2 hours";
// Workflow identity replaces the dup-run guard for every kind of start (spec §2.1).
export const workflowIdFor = (runDate: string): string => `digest-${runDate}`;

// Three retry classes under one run budget (spec §2.1). Model calls: bounded retries inside the
// outage-sized run timeout, with heartbeats. Network: quick retries. Verdicts, the assemble and
// the send: one attempt (a verdict is a result; a send is at-most-once).
const model = proxyActivities<Activities>({
  startToCloseTimeout: "45 minutes",
  heartbeatTimeout: "2 minutes",
  retry: { maximumAttempts: 3, initialInterval: "5 minutes", backoffCoefficient: 2 },
});
const network = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: 3, initialInterval: "10 seconds" } });
const once = proxyActivities<Activities>({ startToCloseTimeout: "10 minutes", retry: { maximumAttempts: 1 } });

export async function DigestWorkflow(input: DigestInput): Promise<DigestOutput> {
  if (input.resumeRun !== undefined && !input.force) throw ApplicationFailure.nonRetryable("resumeRun requires force", "BadInput");
  let approval: "approve" | "reject" | undefined;
  const retryDecisions: ("retry" | "abort")[] = []; // a queue: a decision sent before the failure is kept
  const notes: Record<string, string> = {};
  setHandler(approveSignal, ({ decision }) => {
    approval = decision;
  });
  setHandler(retrySignal, ({ decision }) => {
    retryDecisions.push(decision);
  });
  setHandler(operatorNoteSignal, ({ stage, note }) => {
    notes[stage] = note;
  });

  const { runId } = await once.startRun(input);

  async function finish(out: Omit<DigestOutput, "runId">): Promise<DigestOutput> {
    await once.finishRun(runId, out);
    return { runId, ...out };
  }

  // Retries exhausted: park on the retry signal (spec §2.3 signal 2). Returns undefined on abort.
  // Only an activity's own failure parks; a cancellation or a workflow-code error propagates.
  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    for (;;) {
      try {
        return await fn();
      } catch (e) {
        if (!(e instanceof ActivityFailure) || e.cause instanceof CancelledFailure) throw e;
        await condition(() => retryDecisions.length > 0);
        if (retryDecisions.shift() === "abort") return undefined;
      }
    }
  }

  const fetched = await Promise.all(SOURCE_IDS_STUB.map((s) => network.fetchFeed(runId, s)));
  const { articles } = await once.prepare(runId, fetched);
  // CLUSTER = plan → extract fan-out (one model call per batch, each under its own retry policy)
  // → deterministic join; a batch that exhausts its retries is a lost batch the join title-falls back.
  const recapP = model.recap(runId, input.force);
  const { batches } = await once.planBatches(runId, articles);
  const settled = await mapBounded(batches, MODEL_FANOUT_LIMIT, (b) => model.extractBatch(runId, b, input.force));
  const tagBatches = settled.map((s) => (s.status === "fulfilled" ? s.value : null));
  const [clusters, recap] = await Promise.all([once.joinClusters(runId, tagBatches, input.force), recapP]);
  const selected = await guarded(() => model.select(runId, clusters, recap, notes["select"], input));
  if (!selected) return finish({ stories: 0, broadcast: "skipped" });
  const fulltext = await network.fulltext(runId, selected);
  // WRITE fans out one story per call, four at a time; a story that exhausts its retries fails the
  // phase rather than letting the digest ship one story short.
  const { plans } = await once.planStories(runId, selected, clusters);
  const storyCount = plans.length;
  const written = await mapBounded(plans, MODEL_FANOUT_LIMIT, (p) => model.writeStory(runId, p, selected, notes["write"], input.force));
  const failed = written.find((w) => w.status === "rejected");
  if (failed) throw failed.reason;
  const drafts = written.flatMap((w) => (w.status === "fulfilled" ? [w.value] : []));
  const [preheader, report] = await Promise.all([model.preheader(runId, drafts), model.coherence(runId, drafts, fulltext, notes["coherence"])]);
  const repair = await model.repair(runId, drafts, report);
  const selections = await once.assemble(runId, drafts, report, repair, preheader);
  const [gnews, threads] = await Promise.all([network.gnews(runId, selections), model.threads(runId, selections)]);
  const { email } = await once.render(runId, selections, threads, gnews);

  // Pre-broadcast hold (spec §2.3 signal 1): 2 h, then proceed.
  await condition(() => approval !== undefined, HOLD_TIMEOUT);
  if (approval === "reject") return finish({ stories: storyCount, broadcast: "rejected" });
  await once.broadcast(runId, email);
  return finish({ stories: storyCount, broadcast: "sent" });
}
