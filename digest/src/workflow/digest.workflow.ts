import { ActivityFailure, ApplicationFailure, CancellationScope, CancelledFailure, condition, isCancellation, proxyActivities, setHandler, workflowInfo } from "@temporalio/workflow";
import type { Activities, AlertRequest, DigestInput, DigestOutput, FulltextFetch, FulltextFetcher } from "../activities/index.js";
import { mapBounded, MODEL_FANOUT_LIMIT } from "./bounded.js";
import { FULLTEXT_TASK_QUEUE, MODEL_MAX_ATTEMPTS, NETWORK_MAX_ATTEMPTS, OPS_MAX_ATTEMPTS, WEEKLY_RECAP_MAX_ATTEMPTS } from "./policy.js";
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
  retry: { maximumAttempts: MODEL_MAX_ATTEMPTS, initialInterval: "5 minutes", backoffCoefficient: 2 },
});
const network = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: NETWORK_MAX_ATTEMPTS, initialInterval: "10 seconds" } });
const once = proxyActivities<Activities>({ startToCloseTimeout: "10 minutes", retry: { maximumAttempts: 1 } });
// A verdict is a result, never re-sampled until something passes (spec §2.2 tier 3): one attempt,
// and a failure parks on the retry signal for an operator.
const verdict = proxyActivities<Activities>({ startToCloseTimeout: "45 minutes", heartbeatTimeout: "2 minutes", retry: { maximumAttempts: 1 } });
// The Python fetch bounds itself (a 120 s deadline plus 30 s grace, then SIGKILL); the start-to-close
// covers that with room. A worker that never picks the task up is the schedule-to-start timeout.
const python = proxyActivities<FulltextFetcher>({ taskQueue: FULLTEXT_TASK_QUEUE, scheduleToStartTimeout: "5 minutes", startToCloseTimeout: "4 minutes", retry: { maximumAttempts: 2 } });
// Alerts and pings: quick retries, and the activity gives up (logging what it would have said) on the last.
const ops = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: OPS_MAX_ATTEMPTS, initialInterval: "10 seconds" } });
const weekly = proxyActivities<Activities>({ startToCloseTimeout: "5 minutes", heartbeatTimeout: "2 minutes", retry: { maximumAttempts: WEEKLY_RECAP_MAX_ATTEMPTS, initialInterval: "10 seconds" } });

// The workflow's own deadline, under WORKFLOW_RUN_TIMEOUT. The server's run timeout ends a run without
// running any of its code, so a run that reached it would fail silently; at this deadline the run is
// still alive to alert, ping the dead-man's switch and fail. The margin covers the alert's retries.
export const RUN_DEADLINE = "230 minutes";

// Never fails the run it serves; only a cancellation passes through.
async function bestEffort<T>(fn: () => Promise<T>): Promise<T | undefined> {
  try {
    return await fn();
  } catch (e) {
    if (isCancellation(e)) throw e;
    return undefined;
  }
}
const causes = (e: unknown): string => {
  const parts: string[] = [];
  for (let c: unknown = e; c instanceof Error && parts.length < 5; c = c.cause) parts.push(c.message);
  return parts.join(" <- ") || String(e);
};

// The systemd OnFailure alert's successor. Everything the run does is inside a scope with a deadline; on
// any failure but an operator's cancellation the workflow pings the dead-man's switch "fail" (for the
// day's first run only, as the Python does) and emails, then fails. What no workflow code can report
// (the worker or the server down, a workflow task that keeps failing) is the dead-man's switch's: its
// start ping was sent and no success follows.
export async function DigestWorkflow(input: DigestInput): Promise<DigestOutput> {
  const state: { runId: number | null } = { runId: null };
  try {
    return await CancellationScope.withTimeout(RUN_DEADLINE, () => runDigest(input, state));
  } catch (e) {
    if (CancellationScope.current().consideredCancelled) throw e; // the operator cancelled the workflow
    const timedOut = isCancellation(e);
    const failure = timedOut ? ApplicationFailure.nonRetryable(`run deadline of ${RUN_DEADLINE} exceeded`, "RunDeadline") : e;
    await CancellationScope.nonCancellable(async () => {
      if (input.resumeRun === undefined) await bestEffort(() => ops.healthcheck("fail"));
      await bestEffort(() => ops.alert({ kind: "run-failed", workflowId: workflowInfo().workflowId, runId: state.runId, reason: causes(failure), timedOut }));
    });
    throw failure;
  }
}

async function runDigest(input: DigestInput, state: { runId: number | null }): Promise<DigestOutput> {
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

  const { runId, sourceIds, lastRun } = await once.startRun(input);
  state.runId = runId;
  if (input.resumeRun === undefined) await bestEffort(() => ops.healthcheck("start"));

  async function alertOn(check: () => Promise<AlertRequest | null>): Promise<void> {
    const req = await bestEffort(check);
    if (req) await bestEffort(() => ops.alert(req));
  }
  // A delivered run clears the dead-man's switch before its health is judged: a violated invariant is a
  // quality signal about a run that did deliver. An aborted run is the operator's own decision.
  async function finish(out: Omit<DigestOutput, "runId">): Promise<DigestOutput> {
    await once.finishRun(runId, out);
    if (out.broadcast === "sent") await bestEffort(() => ops.healthcheck("success"));
    if (out.broadcast !== "skipped") await alertOn(() => ops.checkRunHealth(runId, out.broadcast === "sent"));
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

  // One activity per feed under the network policy; a feed that fails is recorded and the run goes on
  // thin (a thin day ships and logs, spec §2), so failures are settled rather than thrown.
  const fetched = (await Promise.allSettled(sourceIds.map((s) => network.fetchFeed(runId, s, lastRun)))).flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
  await alertOn(() => ops.checkFeeds(runId, sourceIds));
  const { articles } = await once.prepare(runId, fetched, input.force);
  await bestEffort(() => ops.healthcheckLog(`curation start: run ${runId}`));
  // Best-effort and awaited before SELECT, which reads it (as WRITE does).
  const weeklyP = bestEffort(() => weekly.weeklyRecap(runId, input.force));
  weeklyP.catch(() => undefined); // observed; awaited below
  // CLUSTER = plan → extract fan-out (one model call per batch, each under its own retry policy)
  // → deterministic join; a batch that exhausts its retries is a lost batch the join title-falls back.
  const recapP = model.recap(runId, input.force);
  const { batches } = await once.planBatches(runId, articles);
  const settled = await mapBounded(batches, MODEL_FANOUT_LIMIT, (b) => model.extractBatch(runId, b, input.force));
  const tagBatches = settled.map((s) => (s.status === "fulfilled" ? s.value : null));
  const [clusters, recap] = await Promise.all([once.joinClusters(runId, tagBatches, input.force), recapP]);
  await weeklyP;
  const selected = await guarded(() => model.select(runId, clusters, recap, notes["select"], input));
  if (!selected) return finish({ stories: 0, broadcast: "skipped" });
  // Full text is best-effort, as in production: a fetcher that is down or fails leaves the run on
  // the RSS summaries, recorded as "unavailable" in fulltext_health.json.
  const plan = await once.planFulltext(runId, selected, input.force);
  const fetch = async (): Promise<FulltextFetch> => {
    if (plan.skip) return { tasks: 0, results: {}, outcome: plan.skip }; // nothing to send to Python
    return python.fetchFulltext(plan.tasks).catch((e: unknown): FulltextFetch => {
      if (isCancellation(e)) throw e;
      return { tasks: plan.tasks.length, results: {}, outcome: "unavailable" };
    });
  };
  const fulltext = plan.existing ?? (await once.storeFulltext(runId, await fetch(), input.force));
  // WRITE fans out one story per call, four at a time; a story that exhausts its retries fails the
  // phase rather than letting the digest ship one story short.
  const { plans } = await once.planStories(runId, selected, clusters);
  const storyCount = plans.length;
  const written = await mapBounded(plans, MODEL_FANOUT_LIMIT, (p) => model.writeStory(runId, p, selected, notes["write"], input.force));
  const failed = written.find((w) => w.status === "rejected");
  if (failed) throw failed.reason;
  const drafts = written.flatMap((w) => (w.status === "fulfilled" ? [w.value] : []));
  const preheaderP = model.preheader(runId, drafts, input.force).catch((e: unknown) => {
    if (isCancellation(e)) throw e; // best-effort, never at the cost of a cancellation
    return null;
  });
  preheaderP.catch(() => undefined); // observed while the checker may be parked; awaited below
  const report = await guarded(() => verdict.coherence(runId, drafts, fulltext, notes["coherence"], input.force));
  const preheader = await preheaderP;
  if (!report) return finish({ stories: 0, broadcast: "skipped" });
  const repair = await model.repair(runId, drafts, report, input.force);
  const selections = await once.assemble(runId, drafts, report, repair, preheader, input.force);
  const [gnews, threads] = await Promise.all([network.gnews(runId, selections), model.threads(runId, selections)]);
  const { email } = await once.render(runId, selections, threads, gnews);

  // Pre-broadcast hold (spec §2.3 signal 1): 2 h, then proceed.
  await condition(() => approval !== undefined, HOLD_TIMEOUT);
  if (approval === "reject") return finish({ stories: storyCount, broadcast: "rejected" });
  await once.broadcast(runId, email);
  return finish({ stories: storyCount, broadcast: "sent" });
}
