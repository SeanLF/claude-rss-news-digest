// usage: search-eval pool OUT.json | labels POOL.json A.json B.json HAND.json ADJ.json | score
// The headline-search evaluation pre-registered in docs/proposed/2026-09-23-search-tuning (run by
// bin/search-eval). `pool` writes the blind (query, headline) pairs no judgement covers yet; `labels`
// folds two judge runs, the hand sample and the adjudications into judgements.json; `score` runs every
// system, the negative controls and the decision rule, writes results.json, and exits 1 if a control fails.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { openDb } from "../store/db.js";
import { ROUND1, ROUND2, SHIPPED, B0P, type Spec, randomRows, runCandidate } from "../site/search-eval/candidates.js";
import { rustSearch } from "../site/search-eval/rust.js";
import { type Hit, type Labels, type PoolItem, blindPool, labelKey, permuteLabels, relevantInPool, scoreQuery, summarise, type Summary } from "../site/search-eval/score.js";
import { siteStore } from "../site/store.js";

const env = (k: string): string => {
  const v = process.env[k];
  if (!v) throw new Error(`${k} is unset`);
  return v;
};
const DIR = env("SEARCH_EVAL_DIR");
const NOW = "2026-09-23";
const SEED = 20260923;
const readJson = (f: string): unknown => JSON.parse(readFileSync(f, "utf8"));
const agree = (x: Record<string, number>, y: Record<string, number>) => {
  const ids = Object.keys(y).filter((id) => id in x);
  return `${ids.filter((id) => x[id] === y[id]).length}/${ids.length}`;
};
const sameRows = (hs: Hit[]) => JSON.stringify(hs.map((h) => `${h.date} ${h.headline}`));
const writeJson = (f: string, v: unknown) => writeFileSync(f, `${JSON.stringify(v, null, 1)}\n`);

interface Judgement {
  query: string;
  headline: string;
  a: boolean;
  b: boolean;
  hand?: boolean;
  final: boolean;
}
const JUDGEMENTS = join(DIR, "judgements.json");
const judgements: Judgement[] = existsSync(JUDGEMENTS) ? readJson(JUDGEMENTS) as Judgement[] : [];
const labelsBy = (pick: (j: Judgement) => boolean): Labels => new Map(judgements.map((j) => [labelKey(j.query, j.headline), pick(j)]));

const [step, ...args] = process.argv.slice(2);

if (step === "labels") {
  const [poolFile, aFile, bFile, handFile, adjFile] = args;
  if (!adjFile) throw new Error("usage: search-eval labels POOL A B HAND ADJ");
  const pool = readJson(poolFile!) as PoolItem[];
  const [a, b, hand, adj] = [aFile, bFile, handFile, adjFile].map((f) => readJson(f!) as Record<string, number>);
  console.log(`judge A vs hand ${agree(a!, hand!)}, judge B vs hand ${agree(b!, hand!)}, A vs B ${agree(a!, b!)}`);
  const known = new Set(judgements.map((j) => labelKey(j.query, j.headline)));
  for (const p of pool) {
    if (known.has(labelKey(p.query, p.headline))) continue;
    const id = String(p.id);
    const [va, vb] = [a![id], b![id]];
    if (va === undefined || vb === undefined) throw new Error(`pool item ${id} has no label from judge ${va === undefined ? "A" : "B"}`);
    const final = va === vb ? va : adj![id];
    if (final === undefined) throw new Error(`pool item ${id}: the judges disagree and there is no adjudication`);
    judgements.push({ query: p.query, headline: p.headline, a: va === 1, b: vb === 1, ...(hand![id] === undefined ? {} : { hand: hand![id] === 1 }), final: final === 1 });
  }
  writeJson(JUDGEMENTS, judgements.toSorted((x, y) => (labelKey(x.query, x.headline) < labelKey(y.query, y.headline) ? -1 : 1)));
  console.log(`search-eval: ${judgements.length} judgements in ${JUDGEMENTS}`);
  process.exit(0);
}

if (step !== "pool" && step !== "score") {
  console.error("usage: search-eval pool OUT.json | labels POOL A B HAND ADJ | score");
  process.exit(2);
}

const queries = (readJson(join(DIR, "queries.json")) as { q: string }[]).map((x) => x.q);
const db = openDb(env("SEARCH_EVAL_DATABASE_URL"));
const rust = rustSearch(env("SEARCH_EVAL_SQLITE"));
const store = siteStore(db);
const specs: Record<string, Spec> = { ...ROUND1, ...ROUND2 };

const systems: Record<string, Record<string, Hit[]>> = { rust: {}, random: {}, shipped: {} };
for (const q of queries) {
  systems["rust"]![q] = rust(q);
  systems["random"]![q] = await randomRows(db, q);
  systems["shipped"]![q] = (await store.search(q, 50)).map((h) => ({ headline: h.headline, date: h.date }));
  for (const [name, spec] of Object.entries(specs)) (systems[name] ??= {})[q] = await runCandidate(db, spec, q, NOW);
}

if (step === "pool") {
  const known = new Set(judgements.map((j) => labelKey(j.query, j.headline)));
  const pool = blindPool(systems, SEED).filter((p) => !known.has(labelKey(p.query, p.headline)));
  writeJson(args[0] ?? "pool.json", pool);
  console.log(`search-eval: ${pool.length} unjudged pairs to ${args[0]}`);
  process.exit(0);
}

// ── score ──
const failures: string[] = [];
const check = (ok: boolean, what: string) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures.push(what);
};

const recorded = (readJson(join(DIR, "rust-recorded.json")) as { recorded: { query: string; hits: Hit[] }[] }).recorded;
for (const r of recorded) {
  const mine = rust(r.query);
  check(sameRows(mine) === sameRows(r.hits), `reference reproduced: node:sqlite answers "${r.query}" as the Rust server recorded (${r.hits.length} rows)`);
}
check(queries.every((q) => systems["rust"]![q]!.length > 0), "reference answers every query");

const labelSets: Record<string, Labels> = { final: labelsBy((j) => j.final), a: labelsBy((j) => j.a), b: labelsBy((j) => j.b) };
// The oracle: each query's judged-relevant pooled stories, first.
const oracle: Record<string, Hit[]> = {};
for (const q of queries) {
  const seen = new Set<string>();
  oracle[q] = Object.values(systems)
    .flatMap((s) => s[q]!)
    .filter((h) => labelSets["final"]!.get(labelKey(q, h.headline)) === true && !seen.has(`${h.headline}\u0000${h.date}`) && seen.add(`${h.headline}\u0000${h.date}`));
}

function scoreSystem(perQuery: Record<string, Hit[]>, labels: Labels): Summary {
  return summarise(
    queries.map((q) => {
      const r = relevantInPool(Object.values(systems).map((s) => s[q]!), labels, q);
      return { ...scoreQuery(perQuery[q]!, systems["rust"]![q]!, labels, q, r, NOW), empty: perQuery[q]!.length === 0 && systems["rust"]![q]!.length > 0 };
    }),
  );
}

interface Row extends Summary {
  name: string;
  relA: number;
  relB: number;
}
const rows: Row[] = Object.entries(systems).map(([name, s]) => ({
  name,
  ...scoreSystem(s, labelSets["final"]!),
  relA: scoreSystem(s, labelSets["a"]!).rel,
  relB: scoreSystem(s, labelSets["b"]!).rel,
}));
const row = (name: string) => rows.find((r) => r.name === name)!;
const f = (x: number) => (Number.isNaN(x) ? "  -  " : x.toFixed(3));
console.log(`\n${"system".padEnd(22)} rel@10  (A)    (B)    ovl@10 age_d  zero dup unjudged`);
for (const r of rows) console.log(`${r.name.padEnd(22)} ${f(r.rel)}  ${f(r.relA)}  ${f(r.relB)}  ${f(r.overlap)}  ${r.medianAgeDays.toFixed(0).padStart(4)}  ${String(r.zero).padStart(4)} ${String(r.dupSlots).padStart(3)} ${String(r.unjudged).padStart(8)}`);
console.log();

const base = row("B0'");
check(rows.every((r) => r.name === "random" || r.unjudged === 0), "every scored system's first ten distinct stories are judged");
check(scoreSystem(oracle, labelSets["final"]!).rel === 1, "the oracle scores rel@10 = 1");
check(row("random").rel <= base.rel - 0.2 && row("random").overlap < 0.1, `random rows score rel@10 ${f(row("random").rel)} (B0' ${f(base.rel)} - 0.2) and overlap ${f(row("random").overlap)} (< 0.1)`);

// The decision rule. Changes from B0' count the fields that differ.
const changes = (s: Spec) => (Object.keys(B0P) as (keyof Spec)[]).filter((k) => JSON.stringify(s[k]) !== JSON.stringify(B0P[k])).length;
const eligible = rows.filter((r) => r.name in specs && r.zero === 0 && r.unjudged === 0);
const beats = eligible.filter((r) => r.rel - base.rel >= 0.05 && r.relA > base.relA && r.relB > base.relB);
const best = Math.max(...beats.map((r) => r.rel));
const tied = beats
  .filter((r) => r.rel >= best - 0.02)
  .toSorted((x, y) => y.overlap - x.overlap || x.medianAgeDays - y.medianAgeDays || changes(specs[x.name]!) - changes(specs[y.name]!));
const winner = tied[0]?.name ?? "B0'";
console.log(`beat B0' (rel@10 +0.05, above it under each judge): ${beats.map((r) => r.name).join(", ") || "none"}`);
console.log(`tied within 0.02 of the best, in tie-break order: ${tied.map((r) => r.name).join(", ") || "none"}`);
console.log(`winner: ${winner}${winner === "B0'" ? " (nothing beat the baseline)" : ""}`);
const rustRow = row("rust");
console.log(`rust rel@10 ${f(rustRow.rel)}: ${row(winner).rel < rustRow.rel - 0.05 ? "the winner is below Rust by more than 0.05; report pg_search" : "the winner is within 0.05 of Rust or above it; no pg_search"}`);

// Dedup is decided on its own: adopted when it cuts duplicate slots and keeps rel@10 within 0.02.
const deduped = winner === "B0'" ? "dedup" : undefined;
const adoptDedup = deduped !== undefined && row(deduped).dupSlots < row(winner).dupSlots && row(deduped).rel >= row(winner).rel - 0.02;
const chosen = adoptDedup ? deduped : winner;
console.log(`dedup: ${adoptDedup ? `adopted (duplicate slots ${row(winner).dupSlots} -> ${row(deduped).dupSlots}, rel@10 ${f(row(winner).rel)} -> ${f(row(deduped).rel)})` : "not adopted"}; the rule chooses ${chosen}`);
check(chosen === SHIPPED, `the site ships the rule's choice (${chosen}), not another candidate (${SHIPPED})`);

const shuffled = scoreSystem(systems[winner]!, permuteLabels(labelSets["final"]!, SEED)).rel;
check(row(winner).rel - shuffled >= 0.1, `shuffled labels: ${winner} falls from ${f(row(winner).rel)} to ${f(shuffled)} (>= 0.1)`);

// What ships is the candidate it claims to be, row for row, and fast enough.
check(queries.every((q) => JSON.stringify(systems["shipped"]![q]) === JSON.stringify(systems[SHIPPED]![q])), `the site's search returns exactly what candidate ${SHIPPED} does`);
const times: number[] = [];
for (let pass = 0; pass < 4; pass++)
  for (const q of queries) {
    const t = performance.now();
    await store.search(q, 50);
    if (pass > 0) times.push(performance.now() - t);
  }
const p95 = times.toSorted((a, b) => a - b)[Math.floor(times.length * 0.95)]!;
check(p95 <= 50, `the site's search p95 ${p95.toFixed(1)} ms over ${times.length} queries (<= 50 ms)`);

writeJson(join(DIR, "results.json"), { now: NOW, queries: queries.length, judgements: judgements.length, shipped: SHIPPED, winner, chosen, p95Ms: Math.round(p95 * 10) / 10, rows, failures });
if (failures.length) {
  console.error(`search-eval: ${failures.length} control(s) failed`);
  process.exit(1);
}
