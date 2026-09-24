// Scoring for the headline-search evaluation (docs/proposed/2026-09-23-search-tuning): pure functions
// over ranked hits and pooled relevance labels. A result's identity is the story, (headline, date);
// labels are per (query, headline), as the judge saw them.

export interface Hit {
  headline: string;
  date: string | null;
}
export type Labels = Map<string, boolean>;
export interface QueryScore {
  q: string;
  rel: number | null;
  overlap: number | null;
  medianAgeDays: number | null;
  dupSlots: number;
  unjudged: number;
  empty: boolean;
}
export interface Summary {
  rel: number;
  overlap: number;
  medianAgeDays: number;
  dupSlots: number;
  unjudged: number;
  zero: number;
}

export const TOP = 10;
export const labelKey = (q: string, headline: string): string => `${q}\u0000${headline}`;
const storyKey = (h: Hit): string => `${h.headline}\u0000${h.date ?? ""}`;
const DAY_MS = 86_400_000;

// The first ten distinct stories, in first-appearance order: ranking is scored apart from dedup.
function distinctStories(hits: Hit[]): Hit[] {
  const seen = new Set<string>();
  return hits.filter((h) => !seen.has(storyKey(h)) && seen.add(storyKey(h))).slice(0, TOP);
}

function median(xs: number[]): number | null {
  if (xs.length === 0) return null;
  const s = xs.toSorted((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

// `r` is R: distinct relevant stories in the judged pool for this query.
export function scoreQuery(hits: Hit[], ref: Hit[], labels: Labels, q: string, r: number, now: string): QueryScore {
  const stories = distinctStories(hits);
  const unjudged = stories.filter((h) => !labels.has(labelKey(q, h.headline))).length;
  const relevant = stories.filter((h) => labels.get(labelKey(q, h.headline)) === true).length;
  const refHeadlines = new Set(distinctStories(ref).map((h) => h.headline));
  const mine = new Set(stories.map((h) => h.headline));
  const ages = stories.filter((h) => h.date !== null).map((h) => (Date.parse(now) - Date.parse(h.date!)) / DAY_MS);
  return {
    q,
    rel: r === 0 ? null : relevant / Math.min(TOP, r),
    overlap: refHeadlines.size === 0 ? null : [...refHeadlines].filter((x) => mine.has(x)).length / refHeadlines.size,
    medianAgeDays: median(ages),
    dupSlots: rowsShownTwice(hits),
    unjudged,
    empty: hits.length === 0,
  };
}

// Rows among the first ten shown that repeat a story already shown.
function rowsShownTwice(hits: Hit[]): number {
  const top = hits.slice(0, TOP);
  return top.length - new Set(top.map(storyKey)).size;
}

// R for one query: distinct relevant stories across every system's first ten distinct stories.
export function relevantInPool(systems: Hit[][], labels: Labels, q: string): number {
  const rel = new Set<string>();
  for (const hits of systems) for (const h of distinctStories(hits)) if (labels.get(labelKey(q, h.headline)) === true) rel.add(storyKey(h));
  return rel.size;
}

const mean = (xs: (number | null)[]): number => {
  const v = xs.filter((x): x is number => x !== null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : Number.NaN;
};

export function summarise(scores: QueryScore[]): Summary {
  return {
    rel: mean(scores.map((s) => s.rel)),
    overlap: mean(scores.map((s) => s.overlap)),
    medianAgeDays: mean(scores.map((s) => s.medianAgeDays)),
    dupSlots: scores.reduce((a, s) => a + s.dupSlots, 0),
    unjudged: scores.reduce((a, s) => a + s.unjudged, 0),
    zero: scores.filter((s) => s.empty).length,
  };
}

// mulberry32: a seeded shuffle, so the pool and the label permutation are reproducible.
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function shuffle<T>(xs: T[], seed: number): T[] {
  const out = [...xs];
  const r = rng(seed);
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(r() * (i + 1));
    [out[i], out[j]] = [out[j]!, out[i]!];
  }
  return out;
}

export interface PoolItem {
  id: number;
  query: string;
  headline: string;
}
// Every distinct (query, headline) in any system's first ten distinct stories, shuffled, numbered, and stripped of
// which system returned it: what the judge sees.
export function blindPool(systems: Record<string, Record<string, Hit[]>>, seed: number): PoolItem[] {
  const pairs = new Map<string, { query: string; headline: string }>();
  for (const perQuery of Object.values(systems))
    for (const [query, hits] of Object.entries(perQuery)) for (const h of distinctStories(hits)) pairs.set(labelKey(query, h.headline), { query, headline: h.headline });
  const sorted = [...pairs.entries()].toSorted(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([, v]) => v);
  return shuffle(sorted, seed).map((p, i) => ({ id: i + 1, ...p }));
}

// The shuffled-labels control: each query's label values dealt out again among its own headlines.
export function permuteLabels(labels: Labels, seed: number): Labels {
  const byQuery = new Map<string, string[]>();
  for (const k of labels.keys()) {
    const q = k.slice(0, k.indexOf("\u0000"));
    byQuery.set(q, [...(byQuery.get(q) ?? []), k]);
  }
  const out: Labels = new Map();
  let s = seed;
  for (const keys of byQuery.values()) {
    const values = shuffle(keys.map((k) => labels.get(k)!), s++);
    keys.forEach((k, i) => out.set(k, values[i]!));
  }
  return out;
}
