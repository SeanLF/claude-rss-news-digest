import type { Client } from "@temporalio/client";
import { describe, expect, it } from "vitest";
import { missingPollers } from "./pollers.js";

const client = (pollers: Record<string, unknown[]>, seen: unknown[] = []) =>
  ({
    options: { namespace: "news-digest" },
    workflowService: {
      describeTaskQueue: (req: { taskQueue: { name: string } }) => {
        seen.push(req);
        const p = pollers[req.taskQueue.name];
        return p ? Promise.resolve({ pollers: p }) : Promise.reject(new Error("14 UNAVAILABLE"));
      },
    },
  }) as unknown as Client;

// Against a real server in deployment.test.ts ("missingPollers on a dev server").
describe("missingPollers", () => {
  it("names each queue with no poller, and asks for the activity queue in the client's namespace", async () => {
    const seen: unknown[] = [];
    expect(await missingPollers(client({ python: [], digest: [{ identity: "1@w" }] }, seen), ["python", "digest"])).toEqual(["no worker polls the python task queue"]);
    expect(seen).toEqual([
      { namespace: "news-digest", taskQueue: { name: "python" }, taskQueueType: 2 },
      { namespace: "news-digest", taskQueue: { name: "digest" }, taskQueueType: 2 },
    ]);
  });
  it("counts a queue it cannot describe as missing, rather than guessing", async () => {
    expect(await missingPollers(client({}), ["python"])).toEqual(["could not describe the python task queue: Error: 14 UNAVAILABLE"]);
  });
});
