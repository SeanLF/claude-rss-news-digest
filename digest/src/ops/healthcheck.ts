// healthchecks.io, the off-box dead-man's switch (newsroom/src/healthcheck.py): it alerts when the
// daily success ping does not arrive, which covers what no in-process check can see (the box, the
// worker or Temporal down). Unset HEALTHCHECK_PING_URL makes every call a no-op; no call ever throws.
export const PING_ENV = "HEALTHCHECK_PING_URL";
const TIMEOUT_MS = 10_000;
const MAX_LOG_BYTES = 1000;

export type PingEvent = "start" | "fail";
export interface Healthcheck {
  ping(event?: PingEvent): Promise<void>;
  // /log records an event without changing up/down state: a stage boundary seen from off-box while
  // the run is still going.
  log(message: string): Promise<void>;
}

export function healthcheck(env: Record<string, string | undefined> = process.env, fetchImpl: typeof fetch = fetch): Healthcheck {
  async function post(event: PingEvent | "log" | undefined, body?: string): Promise<void> {
    const base = env[PING_ENV];
    if (!base) return;
    const url = base.replace(/\/+$/, "") + (event ? `/${event}` : "");
    const name = event === "log" ? "log" : (event ?? "success");
    if (!url.startsWith("https://")) {
      console.warn(`${PING_ENV} is not https -- skipping ${name} ping`);
      return;
    }
    try {
      const res = await fetchImpl(url, {
        method: body === undefined ? "GET" : "POST",
        headers: { "User-Agent": "news-digest-healthcheck/1" },
        signal: AbortSignal.timeout(TIMEOUT_MS),
        ...(body !== undefined ? { body } : {}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      console.warn(`healthcheck ${name} ping failed (non-fatal): ${String(e)}`);
    }
  }
  return {
    ping: (event) => post(event),
    log: (message) => post("log", truncateUtf8(message, MAX_LOG_BYTES)),
  };
}

// The line each finished model call posts to /log, as orchestrate.py posts one per stage.
export function stageDoneLine(row: { stage: string; story?: unknown; durationMs: number; costUsd: number }): string {
  const branch = typeof row.story === "number" ? ` s${String(row.story).padStart(2, "0")}` : "";
  return `${row.stage}${branch} done ${Math.floor(row.durationMs / 1000)}s $${row.costUsd.toFixed(4)}`;
}

// At most `max` bytes of UTF-8, never splitting a character.
function truncateUtf8(s: string, max: number): string {
  const bytes = Buffer.from(s, "utf8");
  if (bytes.length <= max) return s;
  return new TextDecoder("utf-8", { fatal: false }).decode(bytes.subarray(0, max)).replace(/�$/, "");
}
