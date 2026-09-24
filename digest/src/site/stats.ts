import type { StatsData } from "./data.js";
import { type Bucket, type CatalogueEntry, bucket, parkedIds } from "./sources.js";

// The transparency statistics (circulation's stats.rs): the data as /stats.json and get_stats serve it,
// and the derived balance, concentration and coverage figures /stats renders.

// Ten years is the whole archive several times over; the Rust server clamped here because SQLite's
// date arithmetic turns NULL past its range, and one bound serves both.
export const clampDays = (days: number | undefined): number => Math.min(Math.max(days ?? 30, 1), 3650);

export interface SourceHealth {
  sourceId: string;
  total: number;
  successes: number;
  ratePct: number;
}
export interface Stats {
  periodDays: number;
  health: SourceHealth[];
  usage: StatsData["sourceUsage"];
  recentRuns: StatsData["recentRuns"];
  dedup: StatsData["dedup"];
  neverSelected: string[];
  cost: StatsData["cost"];
}

// Half-away-from-zero, as Rust's f64::round.
const round = (x: number): number => Math.sign(x) * Math.round(Math.abs(x));

// A parked source's failures are a decision already taken, so the present-tense health surfaces
// drop it here, once, so the page and its JSON twin agree.
export function statsFrom(data: StatsData, days: number, cat: CatalogueEntry[]): Stats {
  const parked = parkedIds(cat);
  return {
    periodDays: days,
    health: data.sourceHealth
      .filter((h) => !parked.has(h.sourceId))
      .map((h) => ({ sourceId: h.sourceId, total: h.total, successes: h.successes, ratePct: h.total > 0 ? round((h.successes / h.total) * 100) : 0 })),
    usage: data.sourceUsage,
    recentRuns: data.recentRuns,
    dedup: data.dedup,
    neverSelected: data.neverSelected.filter((id) => !parked.has(id)),
    cost: data.cost,
  };
}

// The JSON /stats.json and get_stats return, keys as the Rust server wrote them.
export function statsValue(s: Stats): unknown {
  return {
    period_days: s.periodDays,
    source_health: s.health.map((h) => ({ source_id: h.sourceId, total_fetches: h.total, successes: h.successes, success_rate_pct: h.ratePct })),
    source_usage: s.usage.map((u) => ({ source_id: u.sourceId, tier: u.tier, count: u.count })),
    recent_runs: s.recentRuns.map((r) => ({ run_at: r.runAt, articles_kept: r.articlesKept, articles_emailed: r.recipients, api_cost_usd: r.apiCostUsd })),
    dedup_stats: s.dedup.count === 0 ? null : { filtered_count: s.dedup.count, avg_similarity: s.dedup.avg, min_similarity: s.dedup.min, max_similarity: s.dedup.max },
    never_selected: s.neverSelected,
  };
}

// The fields the Rust server held as f64: serde_json writes a whole one as 1.0, where JavaScript writes 1.
const FLOAT_FIELDS = new Set(["success_rate_pct", "avg_similarity", "min_similarity", "max_similarity", "api_cost_usd"]);
// A string no source id or number can be, swapped for the bare literal after stringifying.
const FLOAT_MARK = "@@f64@@";

// The stats JSON as the Rust server serialised it: keys sorted (serde_json's map), floats as floats.
// get_stats hands this text to a model verbatim, so its bytes are part of the tool's answer.
export function statsJson(v: unknown, indent?: number): string {
  const walk = (x: unknown, key?: string): unknown => {
    if (Array.isArray(x)) return x.map((e) => walk(e));
    if (x && typeof x === "object") return Object.fromEntries(Object.keys(x).toSorted().map((k) => [k, walk((x as Record<string, unknown>)[k], k)]));
    if (typeof x === "number" && key !== undefined && FLOAT_FIELDS.has(key) && Number.isInteger(x)) return `${FLOAT_MARK}${x}.0`;
    return x;
  };
  return JSON.stringify(walk(v), null, indent).replaceAll(new RegExp(`"${FLOAT_MARK}([^"]*)"`, "g"), "$1");
}

export interface SourceShare {
  name: string;
  sharePct: number;
  barPct: number;
}
export interface Metrics {
  shippedPct: [number, number, number];
  catalogPct: [number, number, number];
  jsd: number;
  factualityHighPct: number;
  bucketsSourced: number;
  // One row per source per story: the denominator for source shares, not a story count.
  totalShipped: number;
  hhi: number;
  effectiveN: number;
  topSources: SourceShare[];
  sourcesUsed: number;
  catalogTotal: number;
  coveragePct: number;
  regions: [string, number][];
  geoHhi: number;
  geoEffective: number;
}

const idx: Record<Bucket, 0 | 1 | 2> = { l: 0, c: 1, r: 2 };
const normalize3 = (v: number[]): number[] => {
  const t = v.reduce((a, b) => a + b, 0);
  return t === 0 ? [0, 0, 0] : v.map((x) => x / t);
};
const kl3 = (a: number[], b: number[]): number => a.reduce((s, ai, i) => (ai > 0 && b[i]! > 0 ? s + ai * Math.log2(ai / b[i]!) : s), 0);
export function jsd3(p: number[], q: number[]): number {
  const [pn, qn] = [normalize3(p), normalize3(q)];
  const m = pn.map((x, i) => (x + qn[i]!) / 2);
  return Math.min(Math.max((kl3(pn, m) + kl3(qn, m)) / 2, 0), 1);
}
// Integer percentages that sum to 100, the rounding drift absorbed by the largest bucket.
export function pct3(v: number[]): [number, number, number] {
  const p = normalize3(v);
  const out = p.map((x) => round(x * 100)) as [number, number, number];
  const sum = out[0] + out[1] + out[2];
  if (sum !== 0 && sum !== 100) {
    let maxI = 0;
    for (let i = 1; i < 3; i++) if (p[i]! >= p[maxI]!) maxI = i;
    out[maxI] = out[maxI]! + 100 - sum;
  }
  return out;
}

// The shipped figures describe history, so a parked source counts; the catalogue figures describe the
// shelf read today, so it does not (else coverage could read 38 / 37).
export function computeMetrics(s: Stats, cat: CatalogueEntry[]): Metrics {
  const meta = new Map(cat.map((c) => [c.id, c]));
  const perSource = new Map<string, number>();
  for (const u of s.usage) perSource.set(u.sourceId, (perSource.get(u.sourceId) ?? 0) + u.count);
  const totalShipped = [...perSource.values()].reduce((a, b) => a + b, 0);

  const shipped: [number, number, number] = [0, 0, 0];
  let factHigh = 0;
  let factKnown = 0;
  const regionCounts = new Map<string, number>();
  for (const [id, count] of perSource) {
    const m = meta.get(id);
    if (!m) continue;
    shipped[idx[bucket(m.bias)]] += count;
    factKnown += count;
    if (m.factuality === "high" || m.factuality === "very-high") factHigh += count;
    const region = m.region || "Global";
    regionCounts.set(region, (regionCounts.get(region) ?? 0) + count);
  }
  const geoTotal = [...regionCounts.values()].reduce((a, b) => a + b, 0);
  const regions = [...regionCounts.entries()].toSorted((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const geoHhi = geoTotal > 0 ? regions.reduce((acc, [, c]) => acc + (c / geoTotal) ** 2, 0) : 0;

  const active = cat.filter((c) => c.active);
  const catalog: [number, number, number] = [0, 0, 0];
  for (const c of active) catalog[idx[bucket(c.bias)]] += 1;

  const hhi = totalShipped > 0 ? [...perSource.values()].reduce((acc, c) => acc + (c / totalShipped) ** 2, 0) : 0;
  const ranked = [...perSource.entries()].toSorted((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const topCount = ranked[0]?.[1] ?? 0;
  const topSources = ranked.slice(0, 5).map(([id, count]) => ({
    name: meta.get(id)?.name ?? id,
    sharePct: totalShipped > 0 ? (count / totalShipped) * 100 : 0,
    barPct: topCount > 0 ? (count / topCount) * 100 : 0,
  }));
  const sourcesUsed = [...perSource.keys()].filter((id) => meta.get(id)?.active).length;
  return {
    shippedPct: pct3(shipped),
    catalogPct: pct3(catalog),
    jsd: jsd3(shipped, catalog),
    factualityHighPct: factKnown > 0 ? round((factHigh / factKnown) * 100) : 0,
    bucketsSourced: shipped.filter((c) => c > 0).length,
    totalShipped,
    hhi,
    effectiveN: hhi > 0 ? 1 / hhi : 0,
    topSources,
    sourcesUsed,
    catalogTotal: active.length,
    coveragePct: active.length > 0 ? round((sourcesUsed / active.length) * 100) : 0,
    regions,
    geoHhi,
    geoEffective: geoHhi > 0 ? 1 / geoHhi : 0,
  };
}
