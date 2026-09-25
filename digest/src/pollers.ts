import type { Client } from "@temporalio/client";
import { ACTIVITY_QUEUE } from "./deployment.js";

// Every queue in `queues` that no worker polls, named; empty when each has one. Asked of the activity
// queue: both workers run activities on their own queue, and the Python worker runs nothing else.
// A queue that cannot be described counts as missing.
export async function missingPollers(client: Client, queues: string[]): Promise<string[]> {
  const missing: string[] = [];
  for (const name of queues) {
    try {
      const { pollers } = await client.workflowService.describeTaskQueue({ namespace: client.options.namespace, taskQueue: { name }, taskQueueType: ACTIVITY_QUEUE });
      if (!pollers?.length) missing.push(`no worker polls the ${name} task queue`);
    } catch (e) {
      missing.push(`could not describe the ${name} task queue: ${String(e)}`);
    }
  }
  return missing;
}
