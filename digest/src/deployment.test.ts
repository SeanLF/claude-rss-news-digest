import type { Client, WorkflowHandle } from "@temporalio/client";
import { temporal } from "@temporalio/proto";
import { TestWorkflowEnvironment } from "@temporalio/testing";
import { Worker } from "@temporalio/worker";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { stubActivities } from "./activities/stub.js";
import { scheduleOptions, startOptions } from "./client.js";
import { DEPLOYMENT_NAME, deploymentOptions, setCurrentVersion, strandedRuns, waitingLine, waitingRuns } from "./deployment.js";
import { TASK_QUEUE } from "./worker.js";
import { approveSignal } from "./workflow/signals.js";
import { PYTHON_TASK_QUEUE } from "./workflow/policy.js";

const PINNED = temporal.api.enums.v1.VersioningBehavior.VERSIONING_BEHAVIOR_PINNED;
const PIN = temporal.api.workflow.v1.VersioningOverride.PinnedOverrideBehavior.PINNED_OVERRIDE_BEHAVIOR_PINNED;

describe("deploymentOptions", () => {
  it("pins every workflow to the build that started it; the build id is the image's GIT_SHA", () => {
    expect(deploymentOptions({ GIT_SHA: "934ce99" })).toEqual({
      version: { deploymentName: DEPLOYMENT_NAME, buildId: "934ce99" },
      useWorkerVersioning: true,
      defaultVersioningBehavior: "PINNED",
    });
  });
  it.each([undefined, "", "  "])("refuses a worker with no build id (GIT_SHA %j): it would share a version with every other such build", (sha) => {
    expect(() => deploymentOptions(sha === undefined ? {} : { GIT_SHA: sha })).toThrow(/GIT_SHA/);
  });
});

describe("waitingLine", () => {
  it("is the line bin/deploy matches: newsroom/tests/test_deploy_run_guard.py plays this exact text", () => {
    expect(waitingLine(["digest-2026-10-05"])).toBe("waiting: digest-2026-10-05 -- no worker has taken these; they start once a polling build is current");
  });
});

// The server lists a poller before it has registered the poller's build (Temporal 1.32: matching's
// PollTask records the poller, then registers the build in the deployment). In that window the build
// is not in the deployment, and setting it current fails NOT_FOUND. The window is one registration
// call, too short to hold open on a real server; this fake server holds it for two describes.
describe("setCurrentVersion", () => {
  it("does not set a build that polls but is not yet registered in the deployment (NOT_FOUND)", async () => {
    let describes = 0;
    const registered = () => describes > 2;
    const notFound = Object.assign(new Error("5 NOT_FOUND: build ID 'build-z' not found in Worker Deployment 'digest'"), { code: 5, details: "", metadata: {} });
    const sets: string[] = [];
    const workflowService = {
      describeTaskQueue: () => Promise.resolve({ pollers: [{ deploymentOptions: { deploymentName: DEPLOYMENT_NAME, buildId: "build-z" } }] }),
      describeWorkerDeploymentVersion: () => {
        describes++;
        return registered() ? Promise.resolve({ versionTaskQueues: [{ name: TASK_QUEUE, type: 1 }, { name: TASK_QUEUE, type: 2 }] }) : Promise.reject(notFound);
      },
      setWorkerDeploymentCurrentVersion: ({ buildId }: { buildId: string }) => {
        if (!registered()) return Promise.reject(notFound);
        sets.push(buildId);
        return Promise.resolve({});
      },
    };
    const client = { options: { namespace: "default", identity: "test" }, workflowService } as unknown as Client;
    await setCurrentVersion(client, "build-z", TASK_QUEUE, 30_000);
    expect(sets).toEqual(["build-z"]);
  }, 30_000);
});

// Worker Deployments need a real server: the time-skipping test server has no deployment API. The
// dev server is the Temporal CLI's, pinned to the CLI in the local compose (temporalio/temporal:1.9.1,
// server 1.32.0), and downloaded like the time-skipping server.
let env: TestWorkflowEnvironment;
let python: { shutdown: () => void; done: Promise<void> } | undefined;
beforeAll(async () => {
  env = await TestWorkflowEnvironment.createLocal({ server: { executable: { type: "cached-download", version: "v1.9.1" } } });
  const w = await Worker.create({
    connection: env.nativeConnection,
    taskQueue: PYTHON_TASK_QUEUE, // unversioned, as the Python worker is in production
    activities: {
      fetchFulltext: (tasks: unknown[]) => Promise.resolve({ tasks: tasks.length, results: {}, outcome: "completed" }),
    },
  });
  python = { shutdown: () => w.shutdown(), done: w.run() };
}, 300_000);
afterAll(async () => {
  python?.shutdown();
  await python?.done;
  await env?.teardown();
});

const workflowsPath = new URL("./workflow/digest.workflow.ts", import.meta.url).pathname;
// Every run here fails a pre-send check, so it waits in the hold until approved, as the moves need.
const held = () => ({ ...stubActivities(), checkPreSend: () => Promise.resolve(["TEST: held for the operator"]) });
// The production worker's options on the production queue, with stub activities.
const versioned = (buildId: string) =>
  Worker.create({ connection: env.nativeConnection, taskQueue: TASK_QUEUE, workflowsPath, maxCachedWorkflows: 0, workerDeploymentOptions: deploymentOptions({ GIT_SHA: buildId }), activities: held() });
const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));
const completedTasks = async (h: WorkflowHandle) => ((await h.fetchHistory()).events ?? []).flatMap((e) => (e.workflowTaskCompletedEventAttributes ? [e.workflowTaskCompletedEventAttributes] : []));
async function versionOf(h: WorkflowHandle) {
  const v = (await h.describe()).raw.workflowExecutionInfo?.versioningInfo;
  return { behavior: v?.behavior, buildId: v?.deploymentVersion?.buildId };
}
// The runbook's `temporal workflow update-options --versioning-override-behavior pinned`, over gRPC.
async function moveToVersion(workflowId: string, buildId: string): Promise<void> {
  await env.client.workflowService.updateWorkflowExecutionOptions({
    namespace: env.client.options.namespace,
    workflowExecution: { workflowId },
    workflowExecutionOptions: { versioningOverride: { pinned: { behavior: PIN, version: { deploymentName: DEPLOYMENT_NAME, buildId } } } },
    updateMask: { paths: ["versioning_override"] },
  });
}
async function untilInHold(h: WorkflowHandle): Promise<void> {
  for (let i = 0; i < 600; i++) {
    const evs = (await h.fetchHistory()).events ?? [];
    const notified = evs.findIndex((e) => e.activityTaskScheduledEventAttributes?.activityType?.name === "notifyHold");
    if (notified >= 0 && evs.slice(notified).some((e) => e.timerStartedEventAttributes)) return;
    await pause(100);
  }
  throw new Error("the run never reached the hold");
}

describe("worker versioning on a dev server", () => {
  it("a run started before any version is current waits; setting the version current starts it, pinned, and it records the version in its history", async () => {
    const a = await versioned("build-a");
    await a.runUntil(async () => {
      const h = await env.client.workflow.start("DigestWorkflow", startOptions("2026-10-01", {}));
      await pause(3000);
      expect(await completedTasks(h)).toEqual([]); // control: a polling worker is not enough
      await setCurrentVersion(env.client, "build-a", TASK_QUEUE);
      expect(await strandedRuns(env.client, "build-a")).toEqual([]);
      await h.signal(approveSignal, { decision: "approve" });
      expect((await h.result()).broadcast).toBe("sent");
      const first = (await completedTasks(h))[0];
      expect(first?.versioningBehavior).toBe(PINNED);
      expect(first?.deploymentVersion).toMatchObject({ deploymentName: DEPLOYMENT_NAME, buildId: "build-a" });
      expect(await versionOf(h)).toEqual({ behavior: PINNED, buildId: "build-a" });
      // The Python worker is unversioned, on its own queue; a pinned run's activities there still reach it.
      const evs = (await h.fetchHistory()).events ?? [];
      const scheduled = evs.filter((e) => e.activityTaskScheduledEventAttributes?.activityType?.name === "fetchFulltext").map((e) => String(e.eventId));
      const completed = evs.filter((e) => scheduled.includes(String(e.activityTaskCompletedEventAttributes?.scheduledEventId)));
      expect(scheduled.length).toBe(1);
      expect(completed.length).toBe(1);
    });
  }, 120_000);

  it("a scheduled start (and the bootstrap's trigger of it) lands on the current version", async () => {
    const a = await versioned("build-a");
    await a.runUntil(async () => {
      await setCurrentVersion(env.client, "build-a", TASK_QUEUE);
      const schedule = await env.client.schedule.create({ ...scheduleOptions(), scheduleId: "digest-daily-versioning-test" });
      await schedule.trigger();
      let started: string | undefined;
      for (let i = 0; i < 100 && !started; i++) {
        started = (await schedule.describe()).info.recentActions[0]?.action.workflow.workflowId;
        if (!started) await pause(100);
      }
      const h = env.client.workflow.getHandle(started ?? "none");
      for (let i = 0; i < 100 && (await completedTasks(h)).length === 0; i++) await pause(100);
      expect(await versionOf(h)).toEqual({ behavior: PINNED, buildId: "build-a" });
      await h.terminate("checked");
      await schedule.delete();
    });
  }, 120_000);

  // A worker shows as a poller before the server has registered its build, one queue type at a time.
  // Here the gap is held open: build-q polls workflow tasks only until its activity worker starts,
  // while the current build-p has an activity backlog. Setting build-q current inside that gap is
  // refused (FAILED_PRECONDITION: "missing active task queues"); under CI load, NOT_FOUND.
  it("a build is made current once the server has registered it on both queue types, not when its first poller shows", async () => {
    const opts = (buildId: string) => ({ connection: env.nativeConnection, taskQueue: TASK_QUEUE, maxCachedWorkflows: 0, workerDeploymentOptions: deploymentOptions({ GIT_SHA: buildId }) });
    const p = await versioned("build-p");
    await p.runUntil(() => setCurrentVersion(env.client, "build-p", TASK_QUEUE));
    const pWorkflows = await Worker.create({ ...opts("build-p"), workflowsPath });
    const pDone = pWorkflows.run();
    const h = await env.client.workflow.start("DigestWorkflow", startOptions("2026-10-06", {}));
    const qWorkflows = await Worker.create({ ...opts("build-q"), workflowsPath });
    const qActivities = await Worker.create({ ...opts("build-q"), activities: held() });
    const qDone = qWorkflows.run();
    let actDone: Promise<void> | undefined;
    try {
      for (let i = 0; i < 100 && (await completedTasks(h)).length === 0; i++) await pause(100); // its first activity is now build-p's backlog
      const setting = setCurrentVersion(env.client, "build-q", TASK_QUEUE, 30_000);
      setting.catch(() => undefined);
      await pause(4000);
      actDone = qActivities.run();
      await setting;
      const { routingConfig } = (await env.client.workflowService.describeWorkerDeployment({ namespace: env.client.options.namespace, deploymentName: DEPLOYMENT_NAME })).workerDeploymentInfo ?? {};
      expect(routingConfig?.currentDeploymentVersion?.buildId).toBe("build-q");
    } finally {
      await h.terminate("checked").catch(() => undefined);
      for (const w of [pWorkflows, qWorkflows, qActivities]) if (w.getState() === "RUNNING") w.shutdown();
      await Promise.all([pDone, qDone, actDone]);
    }
  }, 120_000);

  // bin/deploy points current at the build it ships before the apply, whose bootstrap may start a run
  // while the old worker still polls. That run must wait for the new build, not pin to the old one.
  it("a build made current before its worker exists takes the runs started in between, and is confirmed only once it polls", async () => {
    const d = await versioned("build-d");
    const drained = d.run();
    await setCurrentVersion(env.client, "build-d", TASK_QUEUE);
    await env.client.workflowService.setWorkerDeploymentCurrentVersion({ namespace: env.client.options.namespace, deploymentName: DEPLOYMENT_NAME, buildId: "build-e", allowNoPollers: true, ignoreMissingTaskQueues: true });
    const h = await env.client.workflow.start("DigestWorkflow", startOptions("2026-10-03", {}));
    await pause(3000);
    expect(await completedTasks(h)).toEqual([]); // the old build's worker does not take it
    await expect(setCurrentVersion(env.client, "build-e", TASK_QUEUE, 3000)).rejects.toThrow(/no worker of digest:build-e/);
    d.shutdown();
    await drained;
    const e = await versioned("build-e");
    await e.runUntil(async () => {
      await setCurrentVersion(env.client, "build-e", TASK_QUEUE);
      expect(await strandedRuns(env.client, "build-e")).toEqual([]);
      await h.signal(approveSignal, { decision: "approve" });
      expect((await h.result()).broadcast).toBe("sent");
      expect(await versionOf(h)).toEqual({ behavior: PINNED, buildId: "build-e" });
    });
  }, 120_000);

  // bin/deploy's exit trap after a deploy that died between pointing current at the new build and its
  // worker polling: the bootstrap's run is waiting, unpinned, on the new build; the trap makes the
  // running (old) build current again. The run must follow current to the old build and run there.
  it("a run waiting, unpinned, on a build with no worker follows current back to the running build", async () => {
    const f = await versioned("build-f");
    await f.runUntil(async () => {
      await setCurrentVersion(env.client, "build-f", TASK_QUEUE);
      await env.client.workflowService.setWorkerDeploymentCurrentVersion({ namespace: env.client.options.namespace, deploymentName: DEPLOYMENT_NAME, buildId: "build-g", allowNoPollers: true, ignoreMissingTaskQueues: true });
      const h = await env.client.workflow.start("DigestWorkflow", startOptions("2026-10-04", {}));
      await pause(3000);
      expect(await completedTasks(h)).toEqual([]); // control: it waits for build-g
      await setCurrentVersion(env.client, "build-f", TASK_QUEUE); // the trap's set-current.js in the old container
      expect(await strandedRuns(env.client, "build-f")).toEqual([]);
      await untilInHold(h);
      await h.signal(approveSignal, { decision: "approve" });
      expect((await h.result()).broadcast).toBe("sent");
      expect(await versionOf(h)).toEqual({ behavior: PINNED, buildId: "build-f" });
    });
  }, 120_000);

  // The trap's other ending: no worker's build can be made current (the new worker never polls), so
  // current stays on a build nobody runs. A run already waiting there is pinned to nothing, so
  // strandedRuns cannot see it; set-current names it as waiting, or the deploy's error would not
  // say that today's run is sitting.
  it("a run waiting, unpinned, while current names a build with no worker, is named as waiting", async () => {
    const f = await versioned("build-h");
    await f.runUntil(async () => {
      await setCurrentVersion(env.client, "build-h", TASK_QUEUE);
      await env.client.workflowService.setWorkerDeploymentCurrentVersion({ namespace: env.client.options.namespace, deploymentName: DEPLOYMENT_NAME, buildId: "build-i", allowNoPollers: true, ignoreMissingTaskQueues: true });
      const h = await env.client.workflow.start("DigestWorkflow", startOptions("2026-10-05", {}));
      try {
        await pause(3000);
        expect(await completedTasks(h)).toEqual([]);
        await expect(setCurrentVersion(env.client, "build-i", TASK_QUEUE, 3000)).rejects.toThrow(/no worker of digest:build-i/);
        expect(await strandedRuns(env.client, "build-i")).toEqual([]); // what set-current listed before
        expect(await waitingRuns(env.client)).toEqual([h.workflowId]);
        await setCurrentVersion(env.client, "build-h", TASK_QUEUE);
        await untilInHold(h);
        expect(await waitingRuns(env.client)).toEqual([]);
      } finally {
        await h.terminate("checked").catch(() => undefined);
      }
    });
  }, 120_000);

  // What a deploy that goes ahead under a live run leaves (staged, or --force): the run stays on the
  // old build, whose worker the deploy stopped. strandedRuns names it; moving it to the new build
  // is the way out (the runbook's update-options), safe when its history replays on the new code.
  it("a run pinned to a build whose worker is gone is named as stranded, sits still, and runs on once moved", async () => {
    const a = await versioned("build-b");
    const drained = a.run();
    await setCurrentVersion(env.client, "build-b", TASK_QUEUE);
    const h = await env.client.workflow.start("DigestWorkflow", startOptions("2026-10-02", {}));
    await untilInHold(h);
    a.shutdown();
    await drained;
    const c = await versioned("build-c");
    await c.runUntil(async () => {
      await setCurrentVersion(env.client, "build-c", TASK_QUEUE);
      expect(await strandedRuns(env.client, "build-c")).toEqual([{ workflowId: h.workflowId, buildId: "build-b" }]);
      const before = (await completedTasks(h)).length;
      await h.signal(approveSignal, { decision: "approve" });
      await pause(3000);
      expect((await completedTasks(h)).length).toBe(before); // nobody polls build-b
      expect((await h.describe()).status.name).toBe("RUNNING");
      await moveToVersion(h.workflowId, "build-c");
      expect((await h.result()).broadcast).toBe("sent");
      expect((await completedTasks(h)).at(-1)?.deploymentVersion?.buildId).toBe("build-c");
      expect(await strandedRuns(env.client, "build-c")).toEqual([]);
    });
  }, 180_000);
});
