import type { Duration } from "@temporalio/common";
import { CancellationScope, isCancellation, proxyActivities } from "@temporalio/workflow";
import { THREAD_CONTEXT, type Activities, type ThreadsReport } from "../activities/index.js";
import type { Pointer } from "../store/artifacts.js";
import { mapBounded, MODEL_FANOUT_LIMIT } from "./bounded.js";
import { MODEL_MAX_ATTEMPTS } from "./policy.js";

// The longest the render waits on threads. Production's phase takes a few minutes; retries at the
// garnish's interval stay well inside this.
export const THREADS_PHASE_TIMEOUT = "30 minutes";

// Model calls retry as any other, but on a short interval: the render waits on this phase, and
// what a failure costs is the badge, not the digest.
const garnish = proxyActivities<Activities>({
  startToCloseTimeout: "15 minutes",
  heartbeatTimeout: "2 minutes",
  retry: { maximumAttempts: MODEL_MAX_ATTEMPTS, initialInterval: "30 seconds", backoffCoefficient: 2 },
});
const bookkeeping = proxyActivities<Activities>({ startToCloseTimeout: "2 minutes", retry: { maximumAttempts: 3, initialInterval: "5 seconds" } });

const reason = (e: unknown): string => (e instanceof Error ? (e.cause instanceof Error ? e.cause.message : e.message) : String(e));

// THREADS, best-effort as in production: link, synthesize each continuing thread (four at a time),
// then record the phase and hand back the render's thread context. Nothing here fails the digest
// except a cancellation of the workflow itself; a failure, or the phase outrunning its bound, is
// carried to threadsFinish, which writes it down, and the render goes without the context.
export async function threadsPhase(runId: number, force = false, bound: Duration = THREADS_PHASE_TIMEOUT): Promise<Pointer> {
  const report: ThreadsReport = { outcomes: [], failures: [] };
  try {
    await CancellationScope.withTimeout(bound, async () => {
      const { plans, skip } = await garnish.threadsLink(runId, force);
      if (skip) return;
      const settled = await mapBounded(plans, MODEL_FANOUT_LIMIT, (p) => garnish.threadSynthesis(runId, p));
      settled.forEach((s, i) => {
        if (s.status === "fulfilled") report.outcomes.push(s.value);
        else if (isCancellation(s.reason)) throw s.reason;
        else report.failures.push({ threadId: plans[i]!.threadId, error: reason(s.reason) });
      });
    });
  } catch (e) {
    if (!isCancellation(e)) report.linkError = reason(e);
    else if (CancellationScope.current().consideredCancelled) throw e; // the workflow's own cancellation
    else report.timedOut = true;
  }
  try {
    return await bookkeeping.threadsFinish(runId, report);
  } catch (e) {
    if (isCancellation(e)) throw e;
    return { runId, name: THREAD_CONTEXT, sha256: "0".repeat(64) };
  }
}
