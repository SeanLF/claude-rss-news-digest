import { isCancellation, proxyActivities } from "@temporalio/workflow";
import { THREAD_CONTEXT, type Activities, type ThreadsReport } from "../activities/index.js";
import type { Pointer } from "../store/artifacts.js";
import { mapBounded, MODEL_FANOUT_LIMIT } from "./bounded.js";
import { MODEL_MAX_ATTEMPTS } from "./policy.js";

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
// except a cancellation; a failure is carried to threadsFinish, which writes it down, and the
// render goes without the context whose pointer it cannot find.
export async function threadsPhase(runId: number): Promise<Pointer> {
  const report: ThreadsReport = { outcomes: [], failures: [] };
  try {
    const { plans, skip } = await garnish.threadsLink(runId);
    if (!skip) {
      const settled = await mapBounded(plans, MODEL_FANOUT_LIMIT, (p) => garnish.threadSynthesis(runId, p));
      settled.forEach((s, i) => {
        if (s.status === "fulfilled") report.outcomes.push(s.value);
        else if (isCancellation(s.reason)) throw s.reason;
        else report.failures.push({ threadId: plans[i]!.threadId, error: reason(s.reason) });
      });
    }
  } catch (e) {
    if (isCancellation(e)) throw e;
    report.linkError = reason(e);
  }
  try {
    return await bookkeeping.threadsFinish(runId, report);
  } catch (e) {
    if (isCancellation(e)) throw e;
    return { runId, name: THREAD_CONTEXT, sha256: "0".repeat(64) };
  }
}
