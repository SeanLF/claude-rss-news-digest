// Run `fn` over `items` with at most `limit` in flight, results in input order. Deterministic
// (no timers, no I/O), so it is safe inside workflow code; the model fan-outs use it because
// the subscription tolerated 4 concurrent calls with no rate-limit failures and 17 is untested.
export async function mapBounded<T, R>(items: readonly T[], limit: number, fn: (item: T, index: number) => Promise<R>): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = Array.from({ length: items.length }, () => ({ status: "rejected", reason: new Error("not started") }));
  let next = 0;
  async function worker(): Promise<void> {
    while (next < items.length) {
      const i = next++;
      try {
        results[i] = { status: "fulfilled", value: await fn(items[i]!, i) };
      } catch (reason) {
        results[i] = { status: "rejected", reason };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}
export const MODEL_FANOUT_LIMIT = 4;
