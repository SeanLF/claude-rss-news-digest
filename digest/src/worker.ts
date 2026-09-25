import { writeFileSync } from "node:fs";
import { NativeConnection, Worker } from "@temporalio/worker";
import { workerActivities } from "./activities/real.js";
import { deploymentOptions } from "./deployment.js";
import { operationsEnvWarning } from "./ops/env.js";
import { dbUrl } from "./store/db.js";
export const TASK_QUEUE = "digest";
// Production sets the repo's own namespace (spec §5); the local dev server only has "default".
export const temporalNamespace = (env: Record<string, string | undefined> = process.env): string => env["TEMPORAL_NAMESPACE"] ?? "default";
// The image's HEALTHCHECK passes while this file is under a minute old, so Kamal counts a new worker
// deployed only once it polls. A write that fails leaves the file stale, and the check reports it.
export const ALIVE_FILE = "/tmp/worker-alive";
export function touchWhileRunning(worker: Pick<Worker, "getState">, path = ALIVE_FILE, everyMs = 5_000): () => void {
  const timer = setInterval(() => {
    if (worker.getState() !== "RUNNING") return;
    try {
      writeFileSync(path, "");
    } catch {}
  }, everyMs);
  return () => clearInterval(timer);
}
export async function runWorker(address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233"): Promise<void> {
  const warning = operationsEnvWarning(process.env);
  if (warning) console.warn(`WARN ${warning}`);
  dbUrl(); // throws when unset, before the worker connects or polls
  const connection = await NativeConnection.connect({ address });
  const worker = await Worker.create({
    connection,
    namespace: temporalNamespace(),
    taskQueue: TASK_QUEUE,
    workflowsPath: new URL("./workflow/digest.workflow.js", import.meta.url).pathname,
    activities: workerActivities(),
    // A run stays on the build that started it; a deploy makes a new build current (cli/set-current.ts).
    workerDeploymentOptions: deploymentOptions(),
  });
  const stopTouching = touchWhileRunning(worker);
  try {
    await worker.run();
  } finally {
    stopTouching();
  }
}
if (process.argv[1]?.endsWith("worker.js")) await runWorker();
