import { NativeConnection, Worker } from "@temporalio/worker";
import { workerActivities } from "./activities/real.js";
import { operationsEnvWarning } from "./ops/env.js";
export const TASK_QUEUE = "digest";
export async function runWorker(address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233"): Promise<void> {
  const warning = operationsEnvWarning(process.env);
  if (warning) console.warn(`WARN ${warning}`);
  const connection = await NativeConnection.connect({ address });
  const worker = await Worker.create({
    connection,
    taskQueue: TASK_QUEUE,
    workflowsPath: new URL("./workflow/digest.workflow.js", import.meta.url).pathname,
    activities: workerActivities(),
  });
  await worker.run();
}
if (process.argv[1]?.endsWith("worker.js")) await runWorker();
