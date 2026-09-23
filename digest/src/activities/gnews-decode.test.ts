import type { DecodeResult } from "gnews-decoder";
import { describe, expect, it, vi } from "vitest";
import { linkDecoder, type LinkDecoderDeps } from "./gnews-decode.js";

const GN = (token: string) => `https://news.google.com/rss/articles/${token}?oc=5`;
const tokenOf = (url: string) => url.split("/articles/")[1]!.split("?")[0]!;
const ok = (url: string): DecodeResult => ({ ok: true, url });
const failed = (reason: "http" | "parse" | "timeout" | "network" = "parse"): DecodeResult => ({ ok: false, reason, message: reason });
const real = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const RATE_LIMITED: DecodeResult = { ok: false, reason: "rate_limited", status: 429, message: "HTTP 429" };

// The pass as production configures it (GNEWS_RESOLVE_*: 15 s, 2 s, 120 s), on a fake clock and a
// fake decoder that answers by token.
function harness(answers: Record<string, DecodeResult>, over: Partial<LinkDecoderDeps> = {}) {
  let clock = 0;
  const seen: { token: string; timeoutMs: number }[] = [];
  const sleeps: number[] = [];
  const beats: number[] = [];
  const controller = new AbortController();
  const deps: LinkDecoderDeps = {
    decode: (url, opts) => {
      seen.push({ token: tokenOf(url), timeoutMs: opts.timeoutMs });
      return Promise.resolve(answers[tokenOf(url)] ?? failed());
    },
    timeoutMs: 15_000,
    delayMs: 2_000,
    deadlineMs: 120_000,
    now: () => clock,
    sleep: (ms) => {
      sleeps.push(ms);
      clock += ms;
      return Promise.resolve();
    },
    heartbeat: () => beats.push(clock),
    signal: () => controller.signal,
    ...over,
  };
  return { deps, decoder: linkDecoder(deps), seen, sleeps, beats, controller, tick: (ms: number) => (clock += ms) };
}

describe("decodeLinks (gnews-decoder)", () => {
  it("decodes each link serially at the production timeout, paced between network decodes", async () => {
    const h = harness({ R1: ok("https://www.reuters.com/r1"), R2: failed(), N3: ok("https://asia.nikkei.com/n3") });
    expect(await h.decoder.decodeLinks([GN("R1"), GN("R2"), GN("N3")])).toEqual({
      links: 3,
      decoded: { [GN("R1")]: "https://www.reuters.com/r1", [GN("N3")]: "https://asia.nikkei.com/n3" },
      attempted: 3,
      outcome: "completed",
    });
    expect(h.seen).toEqual([
      { token: "R1", timeoutMs: 15_000 },
      { token: "R2", timeoutMs: 15_000 },
      { token: "N3", timeoutMs: 15_000 },
    ]);
    expect(h.sleeps).toEqual([2_000, 2_000]);
  });

  it("a rate limit stops the pass and keeps what was decoded", async () => {
    const h = harness({ R1: ok("https://www.reuters.com/r1"), R2: RATE_LIMITED, R3: ok("https://www.reuters.com/r3") });
    expect(await h.decoder.decodeLinks([GN("R1"), GN("R2"), GN("R3")])).toEqual({
      links: 3,
      decoded: { [GN("R1")]: "https://www.reuters.com/r1" },
      attempted: 2,
      outcome: "rate_limited",
    });
    expect(h.seen.map((s) => s.token)).toEqual(["R1", "R2"]);
  });

  it.each(["http", "network", "timeout", "parse"] as const)("a %s failure keeps the raw link and goes on to the next", async (reason) => {
    const h = harness({ R1: failed(reason), R2: ok("https://www.reuters.com/r2") });
    const out = await h.decoder.decodeLinks([GN("R1"), GN("R2")]);
    expect(out).toEqual({ links: 2, decoded: { [GN("R2")]: "https://www.reuters.com/r2" }, attempted: 2, outcome: "completed" });
  });

  it("the deadline is checked between links", async () => {
    const h = harness({});
    const decoder = linkDecoder({
      ...h.deps,
      decode: (url) => {
        h.seen.push({ token: tokenOf(url), timeoutMs: 15_000 });
        h.tick(119_000); // with the 2 s pace, the second link starts past 120 s
        return Promise.resolve(ok(`https://www.reuters.com/${tokenOf(url).toLowerCase()}`));
      },
    });
    expect(await decoder.decodeLinks([GN("R1"), GN("R2")])).toEqual({ links: 2, decoded: { [GN("R1")]: "https://www.reuters.com/r1" }, attempted: 1, outcome: "deadline" });
  });

  it("a token is decoded once per pass and afresh on the next", async () => {
    const h = harness({ R1: failed() });
    const first = await h.decoder.decodeLinks([GN("R1"), "https://news.google.com/rss/articles/R1?hl=en-US"]);
    const second = await h.decoder.decodeLinks([GN("R1")]);
    expect([first.attempted, second.attempted]).toEqual([1, 1]);
    expect(h.seen).toHaveLength(2); // the worker outlives a run: a failure must not be cached into the next
  });

  it("a link the decoder cannot read as Google News spends nothing and is not counted", async () => {
    const h = harness({}, { decode: () => Promise.resolve({ ok: false, reason: "not_google_news", message: "no" }) });
    expect(await h.decoder.decodeLinks(["https://news.google.com/articles/"])).toEqual({ links: 1, decoded: {}, attempted: 0, outcome: "completed" });
    expect(h.sleeps).toEqual([]);
  });

  it("heartbeats before each link", async () => {
    const h = harness({ R1: failed(), R2: failed() });
    await h.decoder.decodeLinks([GN("R1"), GN("R2")]);
    expect(h.beats.length).toBeGreaterThanOrEqual(2);
  });

  it("a cancelled pass stops before the next link, and hands the signal to the decode in flight", async () => {
    const h = harness({});
    const signals: (AbortSignal | undefined)[] = [];
    const decoder = linkDecoder({
      ...h.deps,
      decode: (url, opts) => {
        signals.push(opts.signal);
        h.seen.push({ token: tokenOf(url), timeoutMs: opts.timeoutMs });
        h.controller.abort();
        return Promise.resolve(ok("https://www.reuters.com/r1"));
      },
    });
    const out = await decoder.decodeLinks([GN("R1"), GN("R2")]);
    expect(h.seen.map((s) => s.token)).toEqual(["R1"]);
    expect(signals[0]).toBe(h.controller.signal);
    expect(out.outcome).toBe("cancelled");
  });

  it("a pass cancelled before its first link decodes nothing", async () => {
    const h = harness({ R1: ok("https://www.reuters.com/r1") }, { delayMs: 0 });
    h.controller.abort();
    expect(await h.decoder.decodeLinks([GN("R1")])).toEqual({ links: 1, decoded: {}, attempted: 0, outcome: "cancelled" });
    expect(h.seen).toEqual([]);
  });

  it("a decoder that throws costs that link only: counted, kept raw, and the pass goes on", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const h = harness({}, {
      decode: (url) => (tokenOf(url) === "R1" ? Promise.reject(new Error("bug")) : Promise.resolve(ok("https://www.reuters.com/r2"))),
    });
    expect(await h.decoder.decodeLinks([GN("R1"), GN("R2")])).toEqual({ links: 2, decoded: { [GN("R2")]: "https://www.reuters.com/r2" }, attempted: 2, outcome: "completed" });
    vi.restoreAllMocks();
  });

  it("a decode the cancellation aborted is not counted as decoded", async () => {
    const h = harness({ R1: { ok: false, reason: "aborted", message: "aborted by caller" } });
    expect(await h.decoder.decodeLinks([GN("R1"), GN("R2")])).toEqual({ links: 2, decoded: {}, attempted: 1, outcome: "cancelled" });
  });

  it("two passes at once run one after the other", async () => {
    // Google counts requests per IP: two runs decoding at once would double the request rate.
    let now = 0;
    let peak = 0;
    const h = harness({});
    const decoder = linkDecoder({
      ...h.deps,
      delayMs: 0,
      sleep: real,
      decode: async (url) => {
        peak = Math.max(peak, ++now);
        await real(20);
        now--;
        return ok(`https://www.reuters.com/${tokenOf(url)}`);
      },
    });
    const [a, b] = await Promise.all([decoder.decodeLinks([GN("R0"), GN("R1")]), decoder.decodeLinks([GN("R2"), GN("R3")])]);
    expect(peak).toBe(1);
    expect([a.attempted, b.attempted]).toEqual([2, 2]);
    expect(Object.keys(a.decoded)).toEqual([GN("R0"), GN("R1")]);
  });

  it("a pass that cannot get the lock in time spends nothing and says busy", async () => {
    // The wait sits inside the activity's own start-to-close. Unbounded, a pass queued behind a long
    // one could time out and be stored as a settled "failed" having decoded nothing.
    let release!: () => void;
    const h = harness({});
    const calls: string[] = [];
    const decoder = linkDecoder({
      ...h.deps,
      lockWaitMs: 60_000,
      decode: (url) => {
        calls.push(url);
        return new Promise((r) => (release = () => r(ok("https://www.reuters.com/r0"))));
      },
    });
    const first = decoder.decodeLinks([GN("R0")]);
    const second = await decoder.decodeLinks([GN("R1")]);
    expect(second).toEqual({ links: 1, decoded: {}, attempted: 0, outcome: "busy" });
    expect(calls).toEqual([GN("R0")]);
    expect(h.beats.length).toBeGreaterThan(1); // the wait heartbeats
    release();
    expect((await first).outcome).toBe("completed");
  });

  it("a pass cancelled while it waits for the lock spends nothing and says cancelled", async () => {
    let release!: () => void;
    const h = harness({});
    const decoder = linkDecoder({
      ...h.deps,
      decode: () => new Promise((r) => (release = () => r(ok("https://www.reuters.com/r0")))),
      sleep: (ms) => {
        h.tick(ms);
        h.controller.abort();
        return Promise.resolve();
      },
    });
    const first = decoder.decodeLinks([GN("R0")]);
    expect(await decoder.decodeLinks([GN("R1")])).toEqual({ links: 1, decoded: {}, attempted: 0, outcome: "cancelled" });
    release();
    await first;
  });
});
