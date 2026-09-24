import { afterEach, describe, expect, it, vi } from "vitest";
import { runWorker } from "./worker.js";

afterEach(() => vi.unstubAllEnvs());

describe("runWorker", () => {
  it("dies on an unset DIGEST_DATABASE_URL before it reaches Temporal, so the deploy's start check sees it", async () => {
    vi.stubEnv("DIGEST_DATABASE_URL", "");
    vi.stubEnv("GIT_SHA", "abc");
    // Port 1 refuses: were the URL checked after connecting, the error would be the connection's.
    await expect(runWorker("127.0.0.1:1")).rejects.toThrow(/DIGEST_DATABASE_URL/);
  });
});
