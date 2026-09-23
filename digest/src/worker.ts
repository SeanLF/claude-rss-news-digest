import { NativeConnection, Worker } from "@temporalio/worker";
import { workerActivities } from "./activities/real.js";
export const TASK_QUEUE = "digest";
// Production sets the repo's own namespace (spec §5); the local dev server only has "default".
export const temporalNamespace = (env: Record<string, string | undefined> = process.env): string => env["TEMPORAL_NAMESPACE"] ?? "default";
export async function runWorker(address = process.env["TEMPORAL_ADDRESS"] ?? "localhost:7233"): Promise<void> {
  const connection = await NativeConnection.connect({ address });
  const worker = await Worker.create({
    connection,
    namespace: temporalNamespace(),
    taskQueue: TASK_QUEUE,
    workflowsPath: new URL("./workflow/digest.workflow.js", import.meta.url).pathname,
    activities: workerActivities(),
  });
  await worker.run();
}
if (process.argv[1]?.endsWith("worker.js")) await runWorker();
