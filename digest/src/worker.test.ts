import { existsSync, mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ALIVE_FILE, runWorker, touchWhileRunning } from "./worker.js";

afterEach(() => vi.unstubAllEnvs());

describe("runWorker", () => {
  it("dies on an unset DIGEST_DATABASE_URL before it reaches Temporal, so the deploy's start check sees it", async () => {
    vi.stubEnv("DIGEST_DATABASE_URL", "");
    vi.stubEnv("GIT_SHA", "abc");
    // Port 1 refuses: were the URL checked after connecting, the error would be the connection's.
    await expect(runWorker("127.0.0.1:1")).rejects.toThrow(/DIGEST_DATABASE_URL/);
  });
});

describe("touchWhileRunning", () => {
  it("touches the file both images' HEALTHCHECK reads, the path the Python worker touches too", () => {
    for (const dockerfile of ["Dockerfile", "python/Dockerfile"]) expect(readFileSync(dockerfile, "utf8")).toContain(`find ${ALIVE_FILE} -mmin -1`);
    expect(readFileSync("python/worker.py", "utf8")).toContain(`Path("${ALIVE_FILE}")`);
  });

  it("touches the file only while the worker is RUNNING, so it goes stale once the worker stops", async () => {
    const path = join(mkdtempSync(join(tmpdir(), "alive-")), "worker-alive");
    let state = "INITIALIZED";
    const stop = touchWhileRunning({ getState: () => state as "RUNNING" }, path, 10);
    try {
      await sleep(50);
      expect(existsSync(path)).toBe(false);
      state = "RUNNING";
      await sleep(50);
      const running = statSync(path).mtimeMs;
      await sleep(50);
      expect(statSync(path).mtimeMs).toBeGreaterThan(running);
      state = "STOPPING";
      await sleep(30);
      const stopped = statSync(path).mtimeMs;
      await sleep(50);
      expect(statSync(path).mtimeMs).toBe(stopped);
    } finally {
      stop();
    }
  });
});
