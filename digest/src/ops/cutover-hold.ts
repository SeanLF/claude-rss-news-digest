// No imports: the workflow reads isCutoverHold, and workflow code may import only deterministic modules.

// HOLD_ALWAYS_THROUGH=YYYY-MM-DD: every run dated on or before that UTC day holds, clean or not, for
// the first days after the cut-over. A date rather than a boolean: it lapses by itself, so nobody
// has to remember to turn it off, and a flag left on would hold every issue for good. A value that
// is not a date holds too: a typo must not quietly drop the hold it was meant to set.
export const CUTOVER_HOLD = "CUTOVER_HOLD";
export const HOLD_ALWAYS_ENV = "HOLD_ALWAYS_THROUGH";
export const isCutoverHold = (line: string): boolean => line.startsWith(`${CUTOVER_HOLD}:`);

export function holdAlwaysInvalid(env: Record<string, string | undefined>): string | null {
  const v = env[HOLD_ALWAYS_ENV]?.trim();
  if (!v) return null;
  const t = new Date(`${v}T00:00:00Z`);
  const valid = /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(t.getTime()) && t.toISOString().startsWith(v);
  return valid ? null : `${HOLD_ALWAYS_ENV}='${v}' is not a YYYY-MM-DD date`;
}

// The line checkPreSend adds for a run the cut-over hold covers; null when it does not.
export function cutoverHold(env: Record<string, string | undefined>, runDate: string): string | null {
  const through = env[HOLD_ALWAYS_ENV]?.trim();
  if (!through) return null;
  const invalid = holdAlwaysInvalid(env);
  if (invalid) return `${CUTOVER_HOLD}: ${invalid}, so every run holds until it is fixed or removed`;
  return runDate <= through ? `${CUTOVER_HOLD}: every run through ${through} holds for the cut-over (${HOLD_ALWAYS_ENV}); no check failed` : null;
}
