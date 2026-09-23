// Scoring for the fulltext fork: success (M1), token F1 against trafilatura (M2), boilerplate line
// share (M3). A promptfoo assertion reports them as named scores; nothing here passes or fails an arm.

export const MIN_CHARS = 200;
const BOILERPLATE = /\b(subscribe|newsletter|cookies?|sign[- ]in|advertisement|copyright|related|read more)\b/i; // the pre-registered list, no more

const tokens = (s: string) => s.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];

export function tokenF1(a: string, b: string): number {
  const ta = tokens(a), tb = tokens(b);
  if (!ta.length || !tb.length) return 0;
  const counts = new Map<string, number>();
  for (const t of tb) counts.set(t, (counts.get(t) ?? 0) + 1);
  let overlap = 0;
  for (const t of ta) {
    const n = counts.get(t) ?? 0;
    if (n > 0) {
      overlap++;
      counts.set(t, n - 1);
    }
  }
  if (!overlap) return 0;
  const p = overlap / ta.length, r = overlap / tb.length;
  return (2 * p * r) / (p + r);
}

export function boilerplateShare(text: string): number {
  const lines = text.split("\n").filter((l) => l.trim());
  return lines.length ? lines.filter((l) => BOILERPLATE.test(l)).length / lines.length : 0;
}

export const succeeded = (text: string) => Array.from(text.trim()).length >= MIN_CHARS;

export default function extractAssert(output: string, context: { vars: { reference: string } }) {
  const ok = succeeded(output);
  const both = ok && succeeded(context.vars.reference);
  const f1 = both ? tokenF1(output, context.vars.reference) : -1; // -1: not comparable, excluded from M2
  return {
    pass: true,
    score: ok ? 1 : 0,
    reason: `success ${ok}, f1 ${f1.toFixed(3)}`,
    namedScores: { success: ok ? 1 : 0, f1, boilerplate: boilerplateShare(output), chars: Array.from(output).length },
  };
}
