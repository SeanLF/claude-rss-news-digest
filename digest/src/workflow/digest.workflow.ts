// A run in flight replays this code (and threads.ts, bounded.ts) after every worker restart. A change
// to the commands it issues (an activity or timer added, removed or reordered) is gated:
//   if (patched("short-change-id")) { new path } else { old path }
// and the old path deleted (deprecatePatch, then nothing) only once no run started before the change
// can still be open (4 h run timeout). replay.test.ts replays recorded histories against this file and
// fails on an ungated change; never re-record the fixtures to make it pass. Activity bodies are free,
// and so are an activity's options: replay matches an activity by type, not by timeout or retry policy.
import { ActivityFailure, ApplicationFailure, CancellationScope, CancelledFailure, condition, isCancellation, log, proxyActivities, setHandler, TimeoutFailure, workflowInfo } from "@temporalio/workflow";
import type { Activities, AlertRequest, DigestInput, DigestOutput, FulltextFetch, FulltextFetcher, GnewsDecode, LinkDecoder } from "../activities/index.js";
import { mapBounded, MODEL_FANOUT_LIMIT } from "./bounded.js";
import { MODEL_MAX_ATTEMPTS, NETWORK_MAX_ATTEMPTS, OPS_MAX_ATTEMPTS, PYTHON_TASK_QUEUE, RUN_TIMEOUT_HOURS, WEEKLY_RECAP_MAX_ATTEMPTS } from "./policy.js";
import { approveSignal, operatorNoteSignal, retrySignal } from "./signals.js";
import { threadsPhase } from "./threads.js";

export const WORKFLOW_RUN_TIMEOUT = `${RUN_TIMEOUT_HOURS} hours` as const;
// The workflow's own deadline, under the run timeout. The server's run timeout ends a run without
// running any of its code, so a run that reached it would fail silently; at this deadline the run is
// still alive to alert, ping the dead-man's switch and fail. DEADLINE_MARGIN_MS covers the alert.
export const RUN_DEADLINE_MS = 230 * 60 * 1000;
export const DEADLINE_MARGIN_MS = 10 * 60 * 1000;
export const HOLD_TIMEOUT = 2 * 60 * 60 * 1000; // 2 h, in ms: the hold notification names its end
// A review shorter than this is no review: a run that cannot hold this long is not sent unreviewed.
export const HOLD_MINIMUM_MS = 30 * 60 * 1000;
// What the tail after the hold is given of the budget; a test holds TAIL_WORST_CASE_MS under it.
export const TAIL_MARGIN_MS = 30 * 60 * 1000;

// One budget: the deadline, and under the run timeout when a start sets one.
function deadlineMs(): number {
  const { runTimeoutMs } = workflowInfo();
  return runTimeoutMs ? Math.max(60_000, Math.min(RUN_DEADLINE_MS, runTimeoutMs - DEADLINE_MARGIN_MS)) : RUN_DEADLINE_MS;
}
// The hold, cut to what the deadline leaves after the tail; 0 when nothing is left.
function holdFor(): number {
  return Math.max(0, Math.min(HOLD_TIMEOUT, workflowInfo().runStartTime.getTime() + deadlineMs() - TAIL_MARGIN_MS - Date.now()));
}
// Workflow identity replaces the dup-run guard for every kind of start (spec §2.1).
export const workflowIdFor = (runDate: string): string => `digest-${runDate}`;

// Three retry classes under one run budget (spec §2.1). Model calls: bounded retries inside the
// outage-sized run timeout, with heartbeats. Network: quick retries. Verdicts and the send: one
// attempt (a verdict is a result; a send is at-most-once).
const model = proxyActivities<Activities>({
  startToCloseTimeout: "45 minutes",
  heartbeatTimeout: "2 minutes",
  retry: { maximumAttempts: MODEL_MAX_ATTEMPTS, initialInterval: "5 minutes", backoffCoefficient: 2 },
});
const network = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: NETWORK_MAX_ATTEMPTS, initialInterval: "10 seconds" } });
const once = proxyActivities<Activities>({ startToCloseTimeout: "10 minutes", retry: { maximumAttempts: 1 } });
// Render and assemble: the same inputs give the same output (render keeps its timestamp and issue
// number), so an attempt a deploy interrupts is retried instead of failing the run: 3 attempts, the
// retries about 10 s then 20 s after (the default backoff coefficient, 2).
const rebuild = proxyActivities<Activities>({ startToCloseTimeout: "10 minutes", retry: { maximumAttempts: NETWORK_MAX_ATTEMPTS, initialInterval: "10 seconds" } });
// The send: one attempt, heartbeating, so a timed-out or cancelled attempt is told to stop.
const send = proxyActivities<Activities>({ startToCloseTimeout: "10 minutes", heartbeatTimeout: "2 minutes", retry: { maximumAttempts: 1 } });
// The run's record: idempotent database writes, so a retry is safe, retried like a fetch.
const record = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: NETWORK_MAX_ATTEMPTS, initialInterval: "10 seconds" } });
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
// Alerts and pings: quick retries, and the activity gives up (logging what it would have said) on the last.
const ops = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: OPS_MAX_ATTEMPTS, initialInterval: "10 seconds" } });
// The tail after the hold: quick, bounded retries, so its worst case is a known sum (below) that the
// budget reserves before the send may start. Everything here is an idempotent write or a ping.
const TAIL_ATTEMPT_MS = 60_000;
const TAIL_ATTEMPTS = 2;
const TAIL_RETRY_MS = 5_000;
const tail = proxyActivities<Activities>({ startToCloseTimeout: TAIL_ATTEMPT_MS, retry: { maximumAttempts: TAIL_ATTEMPTS, initialInterval: TAIL_RETRY_MS, maximumInterval: TAIL_RETRY_MS } });
const SEND_MS = 10 * 60 * 1000; // the send proxy's start-to-close, one attempt
const OPS_WORST_MS = 3 * 2 * 60 * 1000 + 30_000; // the ops policy: 3 attempts of 2 min, 10 s then 20 s apart
// saveDigest, the send, recordShownHeadlines, finishRun, the success ping, the health check, its alert.
export const TAIL_WORST_CASE_MS = SEND_MS + 5 * (TAIL_ATTEMPTS * TAIL_ATTEMPT_MS + (TAIL_ATTEMPTS - 1) * TAIL_RETRY_MS) + OPS_WORST_MS;
const weekly = proxyActivities<Activities>({ startToCloseTimeout: "5 minutes", heartbeatTimeout: "2 minutes", retry: { maximumAttempts: WEEKLY_RECAP_MAX_ATTEMPTS, initialInterval: "10 seconds" } });

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

interface RunState { runId: number | null; sent: boolean }

// The systemd OnFailure alert's successor. Everything the run does is inside a scope with a deadline; on
// any failure but an operator's cancellation the workflow pings the dead-man's switch and emails, then
// fails. What no workflow code can report (the worker or the server down, a workflow task that keeps
// failing) is the dead-man's switch's: its start ping was sent and no success follows.
export async function DigestWorkflow(input: DigestInput): Promise<DigestOutput> {
  const state: RunState = { runId: null, sent: false };
  try {
    return await CancellationScope.withTimeout(deadlineMs(), () => runDigest(input, state));
  } catch (e) {
    if (CancellationScope.current().consideredCancelled) throw e; // the operator cancelled the workflow
    const timedOut = isCancellation(e);
    const failure = timedOut ? ApplicationFailure.nonRetryable(`run deadline of ${deadlineMs() / 60_000} minutes exceeded`, "RunDeadline") : e;
    await CancellationScope.nonCancellable(async () => {
      // A digest that reached readers is the delivery the switch watches for, whatever failed after it.
      if (state.sent) await bestEffort(() => ops.healthcheck("success", `sent, but the run failed after the send: ${causes(failure)}`));
      else if (input.resumeRun === undefined) await bestEffort(() => ops.healthcheck("fail"));
      await bestEffort(() => ops.alert({ kind: "run-failed", workflowId: workflowInfo().workflowId, runId: state.runId, reason: causes(failure), timedOut, sent: state.sent }));
    });
    throw failure;
  }
}

async function runDigest(input: DigestInput, state: RunState): Promise<DigestOutput> {
  let approval: "approve" | "reject" | undefined;
  const approvalNow = () => approval; // read through a call: signals change it across awaits
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
  const monitored = input.resumeRun === undefined; // the day's first run owns the dead-man's switch
  if (monitored) await bestEffort(() => ops.healthcheck("start"));

  async function alertOn(check: () => Promise<AlertRequest | null>): Promise<void> {
    const req = await bestEffort(check);
    if (req) await bestEffort(() => ops.alert(req));
  }
  const notSent = (reason: "disabled" | "held-out", detail: string) => alertOn(() => Promise.resolve({ kind: "not-sent", workflowId: workflowInfo().workflowId, runId, reason, detail }));
  // Delivered: the success ping comes before the health check, since a violated invariant is a quality
  // signal about a run that did deliver. An operator's reject or abort is deliberate, so it closes the
  // day's /start with a note rather than leaving it to page. Disabled is no delivery: no ping.
  async function finish(out: Omit<DigestOutput, "runId">): Promise<DigestOutput> {
    if (out.broadcast !== "sent") await bestEffort(() => tail.threadsRetract(runId));
    await tail.finishRun(runId, out);
    if (out.broadcast === "sent") await bestEffort(() => tail.healthcheck("success"));
    else if (monitored && (out.broadcast === "rejected" || out.broadcast === "skipped")) await bestEffort(() => tail.healthcheck("success", `not sent: ${out.broadcast === "rejected" ? "rejected" : "aborted"} by the operator`));
    else if (out.broadcast === "held-out") await bestEffort(() => tail.healthcheck("fail"));
    // Judged on delivered runs only: the shown headlines are recorded by the send, so every other
    // outcome would read as ZERO_STORIES, and each of those already told the operator itself.
    if (out.broadcast === "sent") await alertOn(() => tail.checkRunHealth(runId, true));
    return { runId, ...out };
  }

  try {
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
    if (!selected) return await finish({ stories: 0, broadcast: "skipped" });
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
    if (!report) return await finish({ stories: 0, broadcast: "skipped" });
    const repair = await model.repair(runId, drafts, report, input.force);
    const selections = await rebuild.assemble(runId, drafts, report, repair, preheader, input.force);
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
    const [gnews, threads] = await Promise.all([decodeLinks(), threadsPhase(runId, input.force)]);

    // db.archive_selections/archive_clusters: a trace, fail-soft as in the Python, never at the
    // cost of the issue.
    await record.archiveRun(runId, selections, clusters).catch((e: unknown) => {
      if (isCancellation(e)) throw e;
      log.warn("archiving the run's selections and clusters failed; the issue goes on", { runId, error: String(e) });
    });
    const { html, email } = await rebuild.render(runId, selections, threads, gnews);

    // Only a real send publishes: with the send disabled the run ends like a rejected one, with no
    // web copy, no shown headlines and no completed_at, and the operator is told.
    if (!(await record.sendEnabled())) {
      log.warn("BROADCAST_ENABLED is not true: nothing published, sent or recorded as shown", { runId });
      await notSent("disabled", "broadcasting disabled on this worker");
      return await finish({ stories: storyCount, broadcast: "disabled" });
    }
    // Pre-broadcast hold (spec §2.3 signal 1): 2 h, then proceed, cut to the run's budget. A run
    // whose budget cannot fit a real review is not sent unreviewed: at most once beats unreviewed.
    // Checked whatever the approval: an early approve skips the review, never the tail's budget.
    if (approval === "reject") return await finish({ stories: storyCount, broadcast: "rejected" });
    const hold = holdFor();
    if (hold < (approval === "approve" ? 1 : HOLD_MINIMUM_MS)) {
      log.warn("no run budget left for a review and the send; not sending", { runId, holdMs: hold });
      await notSent("held-out", `the run's budget left ${Math.floor(hold / 60_000)} minutes for the hold, under the ${HOLD_MINIMUM_MS / 60_000}-minute minimum`);
      return await finish({ stories: storyCount, broadcast: "held-out" });
    }
    // The operator hears of it by email, best-effort. Nothing reaches readers before the hold ends: the
    // web copy, the send and the shown headlines (the next day's dedup) all follow it.
    // The hold ends at a fixed time: the notification's own duration comes out of it, not on top of it.
    const holdEnds = Date.now() + hold;
    await once.notifyHold(runId, selections, new Date(holdEnds).toISOString()).catch((e: unknown) => {
      if (isCancellation(e)) throw e;
      log.warn("the hold notification failed; holding anyway", { runId, error: String(e) });
    });
    const left = holdEnds - Date.now();
    if (left > 0) await condition(() => approval !== undefined, left);
    if (approvalNow() === "reject") return await finish({ stories: storyCount, broadcast: "rejected" });
    // From here the deadline cannot cut in: a send in progress and the record of one that landed are
    // never cancelled, since a cancelled send can still reach readers and a cut record would call it
    // failed. The hold's budget left the tail its margin.
    return await CancellationScope.nonCancellable(async () => {
      await tail.saveDigest(runId, html, selections);
      const sent = await send.broadcast(runId, email); // at most once: one attempt (spec §2.1)
      state.sent = true;
      // After a delivered send every step is an idempotent write, retried: a locked database here
      // must not mark a run that reached readers failed.
      await tail.recordShownHeadlines(runId, selections);
      return await finish({ stories: storyCount, broadcast: "sent", recipients: sent.recipients });
    });
  } catch (e) {
    // db.abort_run: marked failed, never deleted, whatever the cause, cancellation included.
    await CancellationScope.nonCancellable(() => record.abortRun(runId, e instanceof Error ? `${e.name}: ${e.message}${e.cause instanceof Error ? ` (${e.cause.message})` : ""}` : String(e)));
    throw e;
  }
}
