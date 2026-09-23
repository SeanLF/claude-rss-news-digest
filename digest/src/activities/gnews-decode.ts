// The Google-News decode pass (the `decodeLinks` activity): gnews-decoder does one decode; the pass
// is this pipeline's policy around it, as newsroom's gnews.resolve and the Python worker had it. Serial,
// paced, one per-run deadline checked between links, stop at the first rate limit, and a failed link
// keeps its raw URL. The constraint is Google's per-IP daily budget, so nothing here retries.
import { createDecoder, type DecodeResult } from "gnews-decoder";
import type { GnewsDecode, LinkDecoder } from "./index.js";

export interface LinkDecoderDeps {
  decode: (url: string, opts: { signal?: AbortSignal; timeoutMs: number }) => Promise<DecodeResult>;
  timeoutMs: number; // one whole decode (GNEWS_RESOLVE_TIMEOUT_S)
  delayMs: number; // the pause between network decodes (GNEWS_RESOLVE_DELAY_S)
  deadlineMs: number; // the pass, checked before each link (GNEWS_RESOLVE_DEADLINE_S)
  // How long a pass waits behind another before giving up as "busy". The wait sits inside the
  // activity's start-to-close, so it must end well before that, or a pass that decoded nothing is
  // stored as a settled failure.
  lockWaitMs?: number;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  heartbeat?: () => void;
  signal?: () => AbortSignal | undefined;
}

const LOCK_WAIT_MS = 60_000;
const LOCK_POLL_MS = 5_000;
// gnews._extract_art_id: two links to one article differ only in their query.
const token = (url: string): string => /\/articles\/([^?/]+)/.exec(url)?.[1] ?? url;
const pause = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function linkDecoder(deps: LinkDecoderDeps): LinkDecoder {
  const now = deps.now ?? (() => performance.now());
  const sleep = deps.sleep ?? pause;
  const heartbeat = deps.heartbeat ?? (() => undefined);
  const signal = deps.signal ?? (() => undefined);
  // One pass at a time per worker: Google's budget is per IP, so two runs decoding at once would
  // double the request rate.
  let held: Promise<void> | undefined;

  async function pass(urls: string[]): Promise<GnewsDecode> {
    const decoded: Record<string, string> = {};
    const tried = new Map<string, string | null>(); // per pass: the worker outlives a run
    let attempted = 0;
    let outcome = "completed";
    const end = now() + deps.deadlineMs;
    for (const url of urls) {
      // A timed-out activity learns it at a heartbeat, so the pass stops at the next link.
      heartbeat();
      if (signal()?.aborted) {
        outcome = "cancelled";
        break;
      }
      const key = token(url);
      if (!tried.has(key)) {
        if (attempted > 0 && deps.delayMs > 0) {
          await sleep(deps.delayMs);
          if (signal()?.aborted) {
            outcome = "cancelled";
            break;
          }
        }
        // After the pace, as gnews.resolve slept after each decode: the deadline counts it.
        if (now() > end) {
          outcome = "deadline";
          break;
        }
        const s = signal();
        // The library returns every expected failure; a throw is a bug in it, and costs this link only.
        const result = await deps.decode(url, s ? { signal: s, timeoutMs: deps.timeoutMs } : { timeoutMs: deps.timeoutMs }).catch((e: unknown): DecodeResult => {
          console.warn(JSON.stringify({ stage: "gnews", warning: "decoder threw", error: String(e) }));
          return { ok: false, reason: "parse", message: String(e) };
        });
        if (!result.ok && result.reason === "not_google_news") {
          tried.set(key, null);
          continue; // no request was made
        }
        attempted++;
        if (!result.ok && result.reason === "rate_limited") {
          outcome = "rate_limited";
          break;
        }
        if (!result.ok && result.reason === "aborted") {
          outcome = "cancelled";
          break;
        }
        tried.set(key, result.ok ? result.url : null);
      }
      const to = tried.get(key);
      if (to) decoded[url] = to;
    }
    return { links: urls.length, decoded, attempted, outcome };
  }

  return {
    async decodeLinks(urls: string[]): Promise<GnewsDecode> {
      const giveUp = now() + (deps.lockWaitMs ?? LOCK_WAIT_MS);
      for (let holder = held; holder; holder = held) {
        const spent = { links: urls.length, decoded: {}, attempted: 0 };
        if (signal()?.aborted) return { ...spent, outcome: "cancelled" };
        if (now() >= giveUp) return { ...spent, outcome: "busy" };
        heartbeat();
        await Promise.race([holder, sleep(Math.min(LOCK_POLL_MS, Math.max(0, giveUp - now())))]);
      }
      let release!: () => void;
      held = new Promise<void>((r) => (release = r));
      try {
        return await pass(urls);
      } finally {
        held = undefined;
        release();
      }
    },
  };
}

// The worker's decoder, configured as the Python's was: GNEWS_RESOLVE_TIMEOUT_S (15),
// GNEWS_RESOLVE_DELAY_S (2) and GNEWS_RESOLVE_DEADLINE_S (120).
export function linkDecoderFromEnv(env: NodeJS.ProcessEnv, hooks: Pick<LinkDecoderDeps, "heartbeat" | "signal"> = {}): LinkDecoder {
  const seconds = (name: string, fallback: number, min: "zero" | "positive") => {
    const v = Number(env[name] ?? fallback);
    if (!Number.isFinite(v) || v < 0 || (min === "positive" && v === 0)) throw new Error(`${name} must be a ${min === "positive" ? "positive " : ""}number of seconds, got ${env[name]}`);
    return v * 1000;
  };
  const decoder = createDecoder();
  return linkDecoder({
    decode: (url, opts) => decoder.decode(url, opts),
    timeoutMs: seconds("GNEWS_RESOLVE_TIMEOUT_S", 15, "positive"),
    delayMs: seconds("GNEWS_RESOLVE_DELAY_S", 2, "zero"),
    deadlineMs: seconds("GNEWS_RESOLVE_DEADLINE_S", 120, "zero"),
    ...hooks,
  });
}
