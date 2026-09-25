import { isGrpcServiceError, type Client } from "@temporalio/client";
import { decodeVersioningBehavior } from "@temporalio/common";
import type { WorkerDeploymentOptions } from "@temporalio/worker";

export const DEPLOYMENT_NAME = "digest";

// Pinned as the worker's default rather than on DigestWorkflow: the SDK requires a default once
// versioning is on, and every workflow this worker runs must finish on the build that started it.
// The build id is the image's GIT_SHA (CI's build arg): two builds sharing one would replay
// each other's runs.
export function deploymentOptions(env: Record<string, string | undefined> = process.env): WorkerDeploymentOptions {
  const buildId = env["GIT_SHA"]?.trim();
  if (!buildId) throw new Error("GIT_SHA is unset: the worker's build id is the image's git sha (build with --build-arg GIT_SHA)");
  return { version: { deploymentName: DEPLOYMENT_NAME, buildId }, useWorkerVersioning: true, defaultVersioningBehavior: "PINNED" };
}

export type Stranded = { workflowId: string; buildId: string };
const RUNNING_DIGESTS = 'WorkflowType="DigestWorkflow" AND ExecutionStatus="Running"';

// temporal.api.enums.v1.TaskQueueType; @temporalio/proto is only a dev dependency.
const WORKFLOW_QUEUE = 1;
export const ACTIVITY_QUEUE = 2;

// Makes `buildId` the version new runs (manual and scheduled) start on, once a worker of that build
// polls `taskQueue` and the server has registered the build on its workflow and activity queues:
// waited for up to `waitMs`. The poller alone is not enough. The server lists a poller before it
// registers the poller's build, one queue type at a time, and until both are registered it refuses
// the build: NOT_FOUND, or FAILED_PRECONDITION ("missing active task queues") while the current
// build has work on a queue the new one has not registered yet. Nor is registration alone enough:
// a build can be made current before its worker exists (allowNoPollers, by hand).
export async function setCurrentVersion(client: Client, buildId: string, taskQueue: string, waitMs = 120_000): Promise<void> {
  const namespace = client.options.namespace;
  const until = Date.now() + waitMs;
  for (;;) {
    const { pollers } = await client.workflowService.describeTaskQueue({ namespace, taskQueue: { name: taskQueue } }).catch(() => ({ pollers: [] }));
    const polls = pollers?.some((p) => p.deploymentOptions?.deploymentName === DEPLOYMENT_NAME && p.deploymentOptions.buildId === buildId);
    const { types, error } = polls ? await registeredQueueTypes(client, buildId, taskQueue) : { types: new Set<number>() };
    if (types.has(WORKFLOW_QUEUE) && types.has(ACTIVITY_QUEUE)) {
      await client.workflowService.setWorkerDeploymentCurrentVersion({ namespace, deploymentName: DEPLOYMENT_NAME, buildId, identity: client.options.identity });
      return;
    }
    if (Date.now() > until) {
      if (!polls) throw new Error(`no worker of ${DEPLOYMENT_NAME}:${buildId} polled ${taskQueue} within ${waitMs / 1000} s`);
      throw new Error(`a worker of ${DEPLOYMENT_NAME}:${buildId} polls ${taskQueue}, but the server had not registered the build on its workflow and activity queues within ${waitMs / 1000} s${error ? `: ${error.message}` : ""}`);
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
}

const NOT_FOUND = 5; // gRPC status: the build has no queue registered yet

async function registeredQueueTypes(client: Client, buildId: string, taskQueue: string): Promise<{ types: Set<number>; error?: Error }> {
  try {
    const res = await client.workflowService.describeWorkerDeploymentVersion({ namespace: client.options.namespace, deploymentVersion: { deploymentName: DEPLOYMENT_NAME, buildId } });
    return { types: new Set(res.versionTaskQueues.filter((q) => q.name === taskQueue).map((q) => Number(q.type))) };
  } catch (e) {
    if (isGrpcServiceError(e) && (e.code as number) === NOT_FOUND) return { types: new Set() };
    return { types: new Set(), error: e instanceof Error ? e : new Error(String(e)) };
  }
}

// The running digests that no worker has taken yet: not pinned, and no workflow task completed. Such a
// run starts on whatever version is current once that version's worker polls; while current names a
// build with no worker (a deploy that could not make its build current) it sits, with no alert, and
// strandedRuns cannot see it, since it is pinned to nothing. set-current names these when it fails.
export async function waitingRuns(client: Client): Promise<string[]> {
  const waiting: string[] = [];
  for await (const w of client.workflow.list({ query: RUNNING_DIGESTS })) {
    const h = client.workflow.getHandle(w.workflowId, w.runId);
    const info = (await h.describe()).raw.workflowExecutionInfo?.versioningInfo;
    if (info?.versioningOverride?.pinned || decodeVersioningBehavior(info?.behavior) === "PINNED") continue;
    if (!((await h.fetchHistory()).events ?? []).some((e) => e.workflowTaskCompletedEventAttributes)) waiting.push(w.workflowId);
  }
  return waiting;
}

// set-current's line for them, for the operator reading the deploy's output.
export const waitingLine = (ids: string[]): string => `waiting: ${ids.join(" ")} -- no worker has taken these; they start once a polling build is current`;

// The running digests pinned to a build other than `buildId`. On this one-worker box that build's
// worker is gone, so each sits, with no alert, until moved (runbook, "Stranded runs").
export async function strandedRuns(client: Client, buildId: string): Promise<Stranded[]> {
  const stranded: Stranded[] = [];
  for await (const w of client.workflow.list({ query: RUNNING_DIGESTS })) {
    const info = (await client.workflow.getHandle(w.workflowId, w.runId).describe()).raw.workflowExecutionInfo?.versioningInfo;
    const pinnedTo = info?.versioningOverride?.pinned?.version?.buildId ?? (decodeVersioningBehavior(info?.behavior) === "PINNED" ? info?.deploymentVersion?.buildId : undefined);
    if (pinnedTo && pinnedTo !== buildId) stranded.push({ workflowId: w.workflowId, buildId: pinnedTo });
  }
  return stranded;
}
