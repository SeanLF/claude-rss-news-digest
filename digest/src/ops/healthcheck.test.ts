import { afterEach, describe, expect, it, vi } from "vitest";
import { healthcheck } from "./healthcheck.js";

type Seen = { url: string; method: string; body: string | undefined; ua: string | null; signal: boolean };
function fakeFetch(outcome: "ok" | "throw" | "500" = "ok") {
  const seen: Seen[] = [];
  const f = ((url: string, init: RequestInit) => {
    seen.push({ url, method: init.method ?? "GET", body: typeof init.body === "string" ? init.body : undefined, ua: new Headers(init.headers).get("User-Agent"), signal: init.signal instanceof AbortSignal });
    if (outcome === "throw") return Promise.reject(new TypeError("fetch failed"));
    return Promise.resolve(new Response("OK", { status: outcome === "ok" ? 200 : 500 }));
  }) as unknown as typeof fetch;
  return { seen, f };
}
const PING_URL = "https://hc-ping.com/uuid-1/";
afterEach(() => {
  vi.restoreAllMocks();
});

describe("healthcheck", () => {
  it("pings success, start and fail at the configured URL, with a timeout", async () => {
    const { seen, f } = fakeFetch();
    const hc = healthcheck({ HEALTHCHECK_PING_URL: PING_URL }, f);
    await hc.ping();
    await hc.ping("start");
    await hc.ping("fail");
    expect(seen.map((s) => [s.method, s.url])).toEqual([
      ["GET", "https://hc-ping.com/uuid-1"],
      ["GET", "https://hc-ping.com/uuid-1/start"],
      ["GET", "https://hc-ping.com/uuid-1/fail"],
    ]);
    expect(seen.every((s) => s.signal && s.ua === "news-digest-healthcheck/1")).toBe(true);
  });
  it("posts a /log message, capped at 1000 bytes", async () => {
    const { seen, f } = fakeFetch();
    await healthcheck({ HEALTHCHECK_PING_URL: PING_URL }, f).log(`write s03 done 41s $0.0712 ${"x".repeat(2000)}`);
    expect(seen[0]).toMatchObject({ method: "POST", url: "https://hc-ping.com/uuid-1/log" });
    expect(Buffer.byteLength(seen[0]!.body!)).toBe(1000);
    expect(seen[0]!.body!.startsWith("write s03 done 41s $0.0712")).toBe(true);
  });
  it("is a no-op when unconfigured, and refuses a cleartext URL, which would leak its token", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { seen, f } = fakeFetch();
    await healthcheck({}, f).ping();
    await healthcheck({ HEALTHCHECK_PING_URL: "http://hc-ping.com/uuid-1" }, f).ping("start");
    expect(seen).toEqual([]);
    expect(warn).toHaveBeenCalledWith("HEALTHCHECK_PING_URL is not https -- skipping start ping");
  });
  it("never throws: a network error or an error status is a warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await expect(healthcheck({ HEALTHCHECK_PING_URL: PING_URL }, fakeFetch("throw").f).ping()).resolves.toBeUndefined();
    await expect(healthcheck({ HEALTHCHECK_PING_URL: PING_URL }, fakeFetch("500").f).log("m")).resolves.toBeUndefined();
    expect(warn.mock.calls.map((c) => String(c[0]))).toEqual([
      "healthcheck success ping failed (non-fatal): TypeError: fetch failed",
      "healthcheck log ping failed (non-fatal): Error: HTTP 500",
    ]);
  });
});
