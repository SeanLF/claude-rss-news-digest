import { ActivityFailure, CancelledFailure, condition, isCancellation, proxyActivities, setHandler, TimeoutFailure } from "@temporalio/workflow";
import type { Activities, DigestInput, DigestOutput, FulltextFetch, FulltextFetcher, GnewsDecode, LinkDecoder } from "../activities/index.js";
import { mapBounded, MODEL_FANOUT_LIMIT } from "./bounded.js";
import { PYTHON_TASK_QUEUE, MODEL_MAX_ATTEMPTS, NETWORK_MAX_ATTEMPTS } from "./policy.js";
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
const python = proxyActivities<FulltextFetcher>({ taskQueue: PYTHON_TASK_QUEUE, scheduleToStartTimeout: "5 minutes", startToCloseTimeout: "4 minutes", retry: { maximumAttempts: 2 } });
// The decode checks its 120 s deadline between links, so a pass can overrun it by one link: up to
// three requests at 15 s connect plus 15 s read each, then the 2 s pace, about 212 s in all. It
// heartbeats before each link, so a timed-out pass stops at the next one. One attempt: a retry would
// spend the per-IP daily budget again on links the first attempt already tried.
const decoder = proxyActivities<LinkDecoder>({ taskQueue: PYTHON_TASK_QUEUE, scheduleToStartTimeout: "5 minutes", startToCloseTimeout: "6 minutes", heartbeatTimeout: "3 minutes", retry: { maximumAttempts: 1 } });
// Only a decode no worker picked up spent nothing; any other failure may have sent requests.
const neverStarted = (e: unknown): boolean => e instanceof ActivityFailure && e.cause instanceof TimeoutFailure && e.cause.timeoutType === "SCHEDULE_TO_START";

export async function DigestWorkflow(input: DigestInput): Promise<DigestOutput> {
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

  // One activity per feed under the network policy; a feed that fails is recorded and the run goes on
  // thin (a thin day ships and logs, spec §2), so failures are settled rather than thrown.
  const fetched = (await Promise.allSettled(sourceIds.map((s) => network.fetchFeed(runId, s, lastRun)))).flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
  const { articles } = await once.prepare(runId, fetched, input.force);
  // CLUSTER = plan → extract fan-out (one model call per batch, each under its own retry policy)
  // → deterministic join; a batch that exhausts its retries is a lost batch the join title-falls back.
  const recapP = model.recap(runId, input.force);
  const { batches } = await once.planBatches(runId, articles);
  const settled = await mapBounded(batches, MODEL_FANOUT_LIMIT, (b) => model.extractBatch(runId, b, input.force));
  const tagBatches = settled.map((s) => (s.status === "fulfilled" ? s.value : null));
  const [clusters, recap] = await Promise.all([once.joinClusters(runId, tagBatches, input.force), recapP]);
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
  // Best-effort, as in production: a decoder that is down or fails ships the raw Google-News links.
  const decodeLinks = async () => {
    const links = await once.planGnews(runId, selections, input.force);
    if (links.existing) return links.existing;
    const decoded = links.skip
      ? { links: 0, decoded: {}, attempted: 0, outcome: links.skip }
      : await decoder.decodeLinks(links.urls).catch((e: unknown): GnewsDecode => {
          if (isCancellation(e)) throw e;
          return { links: links.urls.length, decoded: {}, attempted: 0, outcome: neverStarted(e) ? "unavailable" : "failed" };
        });
    return once.storeGnews(runId, decoded, input.force);
  };
  const [gnews, threads] = await Promise.all([decodeLinks(), model.threads(runId, selections)]);
  const { email } = await once.render(runId, selections, threads, gnews);

  // Pre-broadcast hold (spec §2.3 signal 1): 2 h, then proceed.
  await condition(() => approval !== undefined, HOLD_TIMEOUT);
  if (approval === "reject") return finish({ stories: storyCount, broadcast: "rejected" });
  await once.broadcast(runId, email);
  return finish({ stories: storyCount, broadcast: "sent" });
}
