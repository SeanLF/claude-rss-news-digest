// A per-key fixed-window rate limiter, in memory: the site is one process.

export class RateLimiter {
  private readonly windows = new Map<string, { start: number; count: number }>();
  constructor(
    private readonly max: number,
    private readonly windowMs: number,
  ) {}

  // Whether a request keyed by `key` is allowed at `now` (ms), counting it if so.
  check(key: string, now: number): boolean {
    for (const [k, w] of this.windows) if (now - w.start >= this.windowMs) this.windows.delete(k);
    const w = this.windows.get(key) ?? { start: now, count: 0 };
    if (w.count >= this.max) return false;
    w.count++;
    this.windows.set(key, w);
    return true;
  }
}

// The rightmost X-Forwarded-For hop, the one the proxy in front of the site wrote; one shared bucket
// for direct connections.
export function clientKey(forwardedFor: string | undefined | null): string {
  const hop = forwardedFor?.split(",").at(-1)?.trim();
  return hop || "direct";
}
