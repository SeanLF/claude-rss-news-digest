import { Context } from "@temporalio/activity";
import { WorkflowFailedError, type WorkflowHandle } from "@temporalio/client";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Activities } from "../activities/index.js";
import { stubActivities } from "../activities/stub.js";
import { changedWorkflowPath } from "./deploy-variant.js";
import { DigestWorkflow, WORKFLOW_RUN_TIMEOUT, workflowIdFor } from "./digest.workflow.js";
import { PYTHON_TASK_QUEUE } from "./policy.js";
import { approveSignal, retrySignal } from "./signals.js";

// What a deploy does to a run in flight: the old worker stops mid-run (SIGTERM is Worker.shutdown:
// polling stops, running activities are cancelled) and a new one, built from the new code, picks the
// run up. On the time-skipping server by default. That server does not skip an activity's retry
// backoff, so there a retried model call is checked as far as its next attempt being scheduled. With
// DEPLOY_SAFETY_ADDRESS naming a dev server (`temporal server start-dev`), the activity tests wait out
// the real 2 min heartbeat timeout and 5 min backoff and see the run send (about 15 minutes).
const devServer = process.env["DEPLOY_SAFETY_ADDRESS"];
const LONG = devServer ? 20 * 60_000 : 180_000;
let env: TestWorkflowEnvironment;
let python: { shutdown: () => void; done: Promise<void> } | undefined;
beforeAll(async () => {
  env = devServer ? await TestWorkflowEnvironment.createFromExistingServer({ address: devServer }) : await TestWorkflowEnvironment.createTimeSkipping();
  // The Python worker's queue answers throughout: its container is not what these deploys restart.
  const w = await Worker.create({
    connection: env.nativeConnection,
    taskQueue: PYTHON_TASK_QUEUE,
    activities: {
      fetchFulltext: (tasks: unknown[]) => Promise.resolve({ tasks: tasks.length, results: {}, outcome: "completed" }),
      decodeLinks: (urls: string[]) => Promise.resolve({ links: urls.length, decoded: {}, attempted: urls.length, outcome: "completed" }),
    },
  });
  python = { shutdown: () => w.shutdown(), done: w.run() };
}, 180_000);
afterAll(async () => {
  python?.shutdown();
  await python?.done;
  await env?.teardown();
});

const currentCode = new URL("./digest.workflow.ts", import.meta.url).pathname;
const stub = stubActivities();
const nonce = Date.now().toString(36); // a dev server keeps its workflows between runs
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
};
// No workflow cache, so no sticky queue: a restarted process has no cache either, and the
// time-skipping server does not move its clock to a dead worker's sticky-queue timeout (seen: the
// run waits on it).
const worker = (taskQueue: string, activities: Partial<Activities>, workflowsPath = currentCode) =>
  Worker.create({ connection: env.nativeConnection, taskQueue: `${taskQueue}-${nonce}`, workflowsPath, maxCachedWorkflows: 0, activities: { ...stub, ...activities } });
const start = (taskQueue: string, runDate: string, workflowRunTimeout: string = WORKFLOW_RUN_TIMEOUT) =>
  env.client.workflow.start(DigestWorkflow, { taskQueue: `${taskQueue}-${nonce}`, workflowId: `${workflowIdFor(runDate)}-${nonce}`, args: [{ runDate }], workflowRunTimeout });
const realPause = (ms: number) => new Promise((r) => setTimeout(r, ms));
const events = async (h: WorkflowHandle) => (await h.fetchHistory()).events ?? [];
async function pending(h: WorkflowHandle, type: string) {
  const a = (await h.describe()).raw.pendingActivities?.find((p) => p.activityType?.name === type);
  return a && { attempt: a.attempt, lastFailure: a.lastFailure?.message };
}
async function untilCompleted(h: WorkflowHandle, type: string): Promise<void> {
  for (let i = 0; i < 600; i++) {
    const evs = await events(h);
    const ids = new Set(evs.filter((e) => e.activityTaskScheduledEventAttributes?.activityType?.name === type).map((e) => String(e.eventId)));
    if (evs.some((e) => ids.has(String(e.activityTaskCompletedEventAttributes?.scheduledEventId)))) return;
    await realPause(100);
  }
  throw new Error(`${type} never completed`);
}
// The run is in the hold once the hold's timer is started after the hold notification.
async function untilInHold(h: WorkflowHandle): Promise<void> {
  for (let i = 0; i < 600; i++) {
    const evs = await events(h);
    const notified = evs.findIndex((e) => e.activityTaskScheduledEventAttributes?.activityType?.name === "notifyHold");
    if (notified >= 0 && evs.slice(notified).some((e) => e.timerStartedEventAttributes)) return;
    await realPause(100);
  }
  throw new Error("the run never reached the hold");
}
// The old worker runs until `interrupted`, then is shut down as a deploy shuts it down.
async function oldWorkerUntil(taskQueue: string, activities: Partial<Activities>, interrupted: Promise<void>, awaitDrain = true) {
  const w = await worker(taskQueue, activities);
  const drained = w.run();
  await interrupted;
  w.shutdown();
  if (awaitDrain) await drained;
  return { drained }; // wrapped: an async function returning the promise itself would wait on it
}
function opsRecorder() {
  const calls: { name: string; args: unknown[] }[] = [];
  const acts: Partial<Activities> = {
    alert: (...args) => {
      calls.push({ name: "alert", args });
      return Promise.resolve();
    },
    healthcheck: (...args) => {
      calls.push({ name: "healthcheck", args });
      return Promise.resolve();
    },
  };
  return { calls, acts };
}
// writeStory for story 0: the old worker's version blocks (until cancelled, or for ever), the new
// worker's answers. Every other story answers at once.
function writes(old: "cancellable" | "killed") {
  const attempts: string[] = [];
  const inFlight = deferred();
  const zombie = deferred();
  let cancelled = false;
  const oldWrite: Activities["writeStory"] = async (runId, plan, ...rest) => {
    if (plan.index !== 0) return stub.writeStory(runId, plan, ...rest);
    attempts.push(`old:${Context.current().info.attempt}`);
    inFlight.resolve();
    if (old === "killed") await zombie.promise; // no heartbeat, no cancellation: SIGKILL as the server sees it
    else
      try {
        await Context.current().cancelled; // the real model calls abort on this (run-stage.ts)
      } catch (e) {
        cancelled = true;
        throw e;
      }
    return stub.writeStory(runId, plan, ...rest);
  };
  const newWrite: Activities["writeStory"] = (runId, plan, ...rest) => {
    if (plan.index === 0) attempts.push(`new:${Context.current().info.attempt}`);
    return stub.writeStory(runId, plan, ...rest);
  };
  return { attempts, inFlight, zombie, oldWrite, newWrite, wasCancelled: () => cancelled };
}

// Runs a day to its hold on the current code, then restarts the worker onto `newCode`.
async function restartInHold(q: string, runDate: string, newCode: string, runTimeout?: string) {
  const { calls, acts } = opsRecorder();
  const inHold = deferred();
  const h = await start(q, runDate, runTimeout);
  const waiting = untilInHold(h).then(() => inHold.resolve());
  await oldWorkerUntil(q, {}, inHold.promise);
  await waiting;
  return { h, calls, next: await worker(q, acts, newCode) };
}

describe("a deploy that changes only activity code, restarting the worker mid-activity", () => {
  it("a model call the shutdown cancels fails retryably and is retried by its policy on the new worker", async () => {
    const w = writes("cancellable");
    const h = await start("graceful", "2026-12-01");
    await h.signal(approveSignal, { decision: "approve" });
    await oldWorkerUntil("graceful", { writeStory: w.oldWrite }, w.inFlight.promise);
    expect(w.wasCancelled()).toBe(true); // control: the restart landed mid-activity
    expect(await pending(h, "writeStory")).toEqual({ attempt: 2, lastFailure: "Worker is shutting down and this activity did not complete in time" });
    if (devServer) {
      const out = await (await worker("graceful", { writeStory: w.newWrite })).runUntil(h.result());
      expect(w.attempts).toEqual(["old:1", "new:2"]);
      expect(out.broadcast).toBe("sent");
    } else await h.terminate("the time-skipping server does not skip the retry backoff");
  }, LONG);

  // Dev server only: the time-skipping server holds its clock while an activity attempt is out on a
  // worker, so a hung attempt never reaches its heartbeat timeout there.
  it.runIf(devServer)("a model call on a worker killed outright is retried after its 2 min heartbeat timeout, and the run sends", async () => {
    const w = writes("killed");
    const h = await start("killed", "2026-12-02");
    await h.signal(approveSignal, { decision: "approve" });
    const { drained } = await oldWorkerUntil("killed", { writeStory: w.oldWrite }, w.inFlight.promise, false);
    const next = await worker("killed", { writeStory: w.newWrite });
    const out = await next.runUntil(async () => {
      expect(await pending(h, "writeStory")).toMatchObject({ attempt: 1 }); // control: not retried before the timeout
      return h.result();
    });
    expect(w.attempts).toEqual(["old:1", "new:2"]);
    expect(out.broadcast).toBe("sent");
    w.zombie.resolve();
    await drained;
  }, LONG);

  // Render and assemble rebuild the same output from the same inputs (render keeps its timestamp and
  // issue number in render_context.json), so they are retried like a fetch rather than run once.
  it.each(["render", "assemble"] as const)("a deterministic step (%s) the shutdown interrupts is retried on the new worker, and the run sends", async (step) => {
    const inFlight = deferred();
    const attempts: string[] = [];
    const interrupted = async () => {
      attempts.push(`old:${Context.current().info.attempt}`);
      inFlight.resolve();
      await Context.current().cancelled;
      throw new Error("unreachable");
    };
    const retried = (...a: Parameters<Activities[typeof step]>) => {
      attempts.push(`new:${Context.current().info.attempt}`);
      return (stub[step] as (...x: typeof a) => ReturnType<Activities[typeof step]>)(...a);
    };
    const { calls, acts } = opsRecorder();
    const h = await start(step, step === "render" ? "2026-12-03" : "2026-12-07");
    await h.signal(approveSignal, { decision: "approve" });
    await oldWorkerUntil(step, { [step]: interrupted }, inFlight.promise);
    const next = await worker(step, { ...acts, [step]: retried });
    const out = await next.runUntil(h.result());
    expect(attempts).toEqual(["old:1", "new:2"]);
    expect(out.broadcast).toBe("sent");
    expect(calls.filter((c) => c.name === "alert")).toEqual([]);
  }, LONG);

  it.skipIf(devServer)("a one-attempt verdict (COHERENCE) the shutdown interrupts parks the run until an operator retries it", async () => {
    const inFlight = deferred();
    let checks = 0;
    const oldCheck: Activities["coherence"] = async () => {
      checks++;
      inFlight.resolve();
      await Context.current().cancelled;
      throw new Error("unreachable");
    };
    const newCheck: Activities["coherence"] = (...a) => {
      checks++;
      return stub.coherence(...a);
    };
    const h = await start("coherence", "2026-12-04");
    await h.signal(approveSignal, { decision: "approve" });
    // The preheader runs beside the checker: let it finish, so the restart interrupts COHERENCE alone.
    await oldWorkerUntil("coherence", { coherence: oldCheck }, inFlight.promise.then(() => untilCompleted(h, "preheader")));
    const out = await (await worker("coherence", { coherence: newCheck })).runUntil(async () => {
      await realPause(3000);
      const d = await h.describe();
      expect(checks).toBe(1); // parked: the new worker did not retry it
      expect(d.status.name).toBe("RUNNING");
      expect(d.raw.pendingActivities ?? []).toEqual([]);
      await h.signal(retrySignal, { decision: "retry" });
      return h.result();
    });
    expect(checks).toBe(2);
    expect(out.broadcast).toBe("sent");
  }, LONG);
});

describe("a deploy that changes workflow code while a run waits in the hold", () => {
  it.skipIf(devServer)("control: a restart onto the SAME workflow code resumes the hold and sends on approval", async () => {
    const { h, next } = await restartInHold("same", "2026-12-05", currentCode);
    const out = await next.runUntil(async () => {
      await h.signal(approveSignal, { decision: "approve" });
      return h.result();
    });
    expect(out.broadcast).toBe("sent");
  }, 180_000);

  // On the time-skipping server the clock stays put while a workflow task keeps failing, so the run
  // timeout is seen only on a dev server, with a 71 min run timeout (the least that still holds).
  it("changed workflow code fails replay with a nondeterminism error: the run is stuck, with no alert", async () => {
    const { h, calls, next } = await restartInHold("changed", "2026-12-06", changedWorkflowPath(), devServer ? "71 minutes" : undefined);
    const seen = await next.runUntil(async () => {
      await h.signal(approveSignal, { decision: "approve" });
      let failure: string | undefined;
      for (let i = 0; i < 300 && !failure; i++) {
        failure = (await events(h)).find((e) => e.workflowTaskFailedEventAttributes)?.workflowTaskFailedEventAttributes?.failure?.message ?? undefined;
        if (!failure) await realPause(100);
      }
      await realPause(5000);
      const d = await h.describe();
      const ended = devServer ? await h.result().then(() => "COMPLETED", (e: unknown) => (e instanceof WorkflowFailedError ? `${e.cause?.name}` : String(e))) : undefined;
      return { failure, status: d.status.name, taskAttempt: d.raw.pendingWorkflowTask?.attempt ?? 0, ended, finalStatus: (await h.describe()).status.name };
    });
    console.log("deploy-safety evidence", JSON.stringify({ ...seen, opsCalls: calls.length }));
    expect(seen.failure).toMatch(/nondetermin/i);
    expect(seen.status).toBe("RUNNING");
    if (devServer) {
      expect(seen.taskAttempt).toBeGreaterThan(1); // the workflow task is retried, never failed (the test server reports no attempt)
      expect(seen.finalStatus).toBe("TIMED_OUT");
    }
    expect(calls).toEqual([]); // no alert and no failure ping: only the dead-man's switch can notice
  }, devServer ? 90 * 60_000 : 180_000);
});
