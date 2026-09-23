import type { Sql } from "../store/db.js";
import { COHERENCE_FIELDS, FAILURE_KINDS } from "../contracts/coherence.js";

// newsroom/src/run_health.py and db.get_run_health, ported over the same tables. The keys stay
// snake_case and the messages stay byte-identical so the archived-run parity check can diff the two
// systems' output directly. The rationale for each rule lives in run_health.py.
export interface RunHealth {
  run_id: number;
  shipped: number;
  stages: number;
  artifacts: number;
  recipients: number | null;
  broadcasting: boolean;
  thread_continuations: number;
  threads_available: number;
  threads_enabled: boolean;
  batches_lost: number | null;
  title_only_fallback: number | null;
  fulltext_tasks: number | null;
  fulltext_extracted: number | null;
  fulltext_outcome: string | null;
  blanked_why: number | null;
  must_know_shipped: number | null;
  dropped_continuations: number | null;
  linker_ok: boolean | null;
  repair_outcome: string | null;
  repair_detail: string | null;
  stories_dropped_at_write: number | null;
  usage_rows_dropped: number | null;
}

// Python's str() of a value interpolated into a message.
const py = (v: string | number | boolean | null): string => (v === null ? "None" : v === true ? "True" : v === false ? "False" : String(v));
// Python's `:.0f`: round half to even.
function fixed0(x: number): string {
  const floor = Math.floor(x);
  const r = x - floor === 0.5 ? (floor % 2 === 0 ? floor : floor + 1) : Math.round(x);
  return String(r);
}

type Rule = [code: string, fires: (h: RunHealth) => boolean, message: string | ((h: RunHealth) => string)];
const RULES: Rule[] = [
  ["ZERO_STORIES", (h) => h.shipped === 0, "the run completed but shipped no stories"],
  ["ZERO_RECIPIENTS", (h) => h.broadcasting && h.recipients === 0, "a digest was built but sent to nobody"],
  ["NO_USAGE_RECORDED", (h) => h.stages === 0, "no subagent stage recorded usage, so the curation phase left no trace"],
  [
    "USAGE_ROWS_LOST",
    (h) => (h.usage_rows_dropped ?? 0) > 0,
    (h) => `${py(h.usage_rows_dropped)} usage row(s) failed to persist; this run's per-stage cost/config picture is incomplete`,
  ],
  [
    "DEGRADED_CLUSTERING",
    (h) => (h.batches_lost ?? 0) > 0,
    (h) => `${py(h.batches_lost)} extraction batch(es) returned nothing usable; ${py(h.title_only_fallback)} articles lost their entity tags`,
  ],
  [
    "STORIES_DROPPED_AT_WRITE",
    (h) => (h.stories_dropped_at_write ?? 0) > 0,
    (h) => `${py(h.stories_dropped_at_write)} story(ies) SELECT chose never reached WRITE; the digest shipped shorter than it was curated to be`,
  ],
  [
    "BLANKED_WHY_IT_MATTERS",
    (h) => (h.blanked_why ?? 0) >= 2,
    (h) =>
      `${py(h.blanked_why)} of ${py(h.must_know_shipped)} must_know stories` +
      (h.must_know_shipped ? ` (${fixed0((100 * (h.blanked_why ?? 0)) / h.must_know_shipped)}%)` : "") +
      " went out with no why_it_matters",
  ],
  [
    "FULLTEXT_TOTAL_LOSS",
    (h) => (h.fulltext_tasks ?? 0) > 0 && (h.fulltext_extracted ?? 0) === 0,
    (h) => `fulltext extracted 0 of ${py(h.fulltext_tasks)} candidate articles (worker ${h.fulltext_outcome || "unknown"}); stories fell back to CSV summaries`,
  ],
  [
    "REPAIR_SPEC_ERROR",
    (h) => h.repair_outcome === "spec_error",
    (h) => `the repair path was disabled by a prompt/config error, so any coherence-flagged story would drop: ${h.repair_detail || "no detail recorded"}`,
  ],
  ["NO_ARTIFACTS", (h) => h.artifacts === 0, "no intermediate artifacts were archived, so this run cannot be replayed"],
  [
    "NO_THREAD_CONTINUATIONS",
    (h) => h.threads_enabled && h.threads_available > 0 && h.thread_continuations === 0,
    (h) => "no shipped story continued an existing thread, though live threads existed" + (h.linker_ok === false ? " -- the linker call itself failed" : ""),
  ],
];

export const REQUIRED_KEYS: ReadonlySet<string> = new Set([
  "shipped", "stages", "artifacts", "recipients", "broadcasting", "thread_continuations", "threads_available", "threads_enabled",
  "batches_lost", "stories_dropped_at_write", "usage_rows_dropped", "repair_outcome", "repair_detail", "title_only_fallback",
  "dropped_continuations", "linker_ok", "blanked_why", "must_know_shipped",
]);

// One readable line per violated invariant; empty means healthy.
export function violations(health: RunHealth): string[] {
  const missing = [...REQUIRED_KEYS].filter((k) => !(k in health)).toSorted();
  if (missing.length) return [`MALFORMED_HEALTH: run health is missing [${missing.map((k) => `'${k}'`).join(", ")}]; invariants NOT evaluated`];
  return RULES.filter(([, fires]) => fires(health)).map(([code, , message]) => `${code}: ${typeof message === "function" ? message(health) : message}`);
}

export interface KindCounts { contradicted: number; unsupported: number; unlabelled: number }
const isKind = (k: unknown): k is keyof KindCounts => (FAILURE_KINDS as readonly unknown[]).includes(k);
const isField = (f: string): boolean => (COHERENCE_FIELDS as readonly string[]).includes(f);

// Per-field counts of the checker's failure kinds over the fields it named; a failed story naming
// none counts once as unlabelled. Null when there is no readable report. Not a rule.
export function coherenceKindCounts(reportText: string | null | undefined): KindCounts | null {
  if (!reportText) return null;
  let doc: unknown;
  try {
    doc = JSON.parse(reportText);
  } catch {
    return null;
  }
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) return null;
  const results = (doc as Record<string, unknown>)["results"];
  if (!Array.isArray(results)) return null;
  const counts: KindCounts = { contradicted: 0, unsupported: 0, unlabelled: 0 };
  for (const r of results as unknown[]) {
    if (!r || typeof r !== "object" || Array.isArray(r)) continue;
    const rec = r as Record<string, unknown>;
    if (rec["pass"] !== false) continue;
    const failed = rec["failed_fields"];
    const fields = Array.isArray(failed) ? failed.filter((f): f is string => typeof f === "string") : [];
    const raw = rec["failure_kinds"];
    const kinds = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
    const norm = new Map<string, string>([...fields, ...Object.keys(kinds)].map((f) => [f, f.trim().toLowerCase()]));
    const named = [...new Set(norm.values())].filter(isField);
    const byField = new Map<string, unknown>();
    for (const [k, v] of Object.entries(kinds)) if (norm.has(k)) byField.set(norm.get(k)!, v);
    for (const f of named) {
      const kind = byField.get(f);
      counts[isKind(kind) ? kind : "unlabelled"] += 1;
    }
    if (!named.length) counts.unlabelled += 1;
  }
  return counts;
}

// db.get_run_health, over the product schema. The artifact fields are read as SQLite's json_extract
// read them in the Python's query, so a malformed artifact blanks only the field it feeds: a number
// or string as itself, a boolean as 1 or 0, null or absent as null, an object or array as its text.
type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
const parsed = (text: string | undefined): Json | undefined => {
  if (text === undefined) return undefined;
  try {
    return JSON.parse(text) as Json;
  } catch {
    return undefined;
  }
};
const isObj = (v: Json | undefined): v is { [k: string]: Json } => typeof v === "object" && v !== null && !Array.isArray(v);
function extract(doc: Json | undefined, key: string): number | string | null {
  if (!isObj(doc)) return null;
  const v = doc[key];
  if (v === undefined || v === null) return null;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number" || typeof v === "string") return v;
  return JSON.stringify(v);
}
// Passed through as json_extract gives it: the rules read a wrong-typed value as the Python does.
const num = (v: number | string | null): number | null => v as number | null;
const str = (v: number | string | null): string | null => v as string | null;
// SQLite's `value ->> '$.k'` on one element: text of a scalar, NULL off an object.
const textOf = (v: Json | undefined, key: string): string | null => {
  if (!isObj(v)) return null;
  const x = v[key];
  if (x === undefined || x === null) return null;
  if (typeof x === "boolean") return x ? "1" : "0";
  return typeof x === "string" ? x : typeof x === "number" ? String(x) : JSON.stringify(x);
};

// `broadcasting`, `threadsEnabled` and `dormantAfter` are the run's own configuration, not DB state;
// `usageRowsDropped` is process state (rows that never reached the table cannot be counted from it).
// threads_available is what the run's linker could have been offered: published threads last seen
// within `dormantAfter` completed runs before this one, as ThreadStore.activeThreads counts them.
export async function getRunHealth(db: Sql, runId: number, opts: { broadcasting: boolean; threadsEnabled: boolean; usageRowsDropped: number; dormantAfter?: number }): Promise<RunHealth> {
  const counts = (await db.one<{ shipped: number; stages: number; artifacts: number; recipients: number | null; thread_continuations: number; threads_available: number }>(
    `SELECT
       (SELECT COUNT(DISTINCT headline) FROM shown_narratives WHERE run_id = $1) AS shipped,
       (SELECT COUNT(DISTINCT subagent) FROM run_usage WHERE run_id = $1) AS stages,
       (SELECT COUNT(*) FROM run_artifacts WHERE run_id = $1 AND state = 'current') AS artifacts,
       (SELECT SUM(recipients) FROM broadcasts WHERE run_id = $1) AS recipients,
       (SELECT COUNT(*) FROM thread_installments WHERE run_id = $1 AND continued) AS thread_continuations,
       (SELECT COUNT(*) FROM threads t
          JOIN (SELECT thread_id, max(run_id) AS last_run_id FROM thread_installments
                WHERE run_id < $1 AND run_id IN (SELECT run_id FROM published_runs) GROUP BY thread_id) l ON l.thread_id = t.id
          WHERE t.merged_into IS NULL
            AND (SELECT COUNT(*) FROM digest_runs r WHERE r.id > l.last_run_id AND r.id < $1 AND r.completed_at IS NOT NULL) <= $2) AS threads_available`,
    [runId, opts.dormantAfter ?? 3],
  ))!;
  const names = ["cluster_health.json", "fulltext_health.json", "selections.json", "thread_links.json", "repair_health.json", "write_branches.json"];
  const docs = new Map<string, Json | undefined>();
  for (const r of await db.all<{ n: string; c: string }>("SELECT artifact_name AS n, content AS c FROM run_artifacts WHERE run_id = $1 AND state = 'current' AND artifact_name = ANY($2::text[])", [runId, names]))
    docs.set(r.n, parsed(r.c));
  const cluster = docs.get("cluster_health.json");
  const fulltext = docs.get("fulltext_health.json");
  const selections = docs.get("selections.json");
  const links = docs.get("thread_links.json");
  const repair = docs.get("repair_health.json");
  const branches = docs.get("write_branches.json");
  const mustKnow = isObj(selections) && Array.isArray(selections["must_know"]) ? selections["must_know"] : null;
  const stories = isObj(links) && Array.isArray(links["stories"]) ? links["stories"] : null;
  const dropped = isObj(branches) && Array.isArray(branches["dropped"]) ? branches["dropped"] : null;
  const linkerOk = extract(links, "linker_ok");
  return {
    run_id: runId,
    shipped: counts.shipped,
    stages: counts.stages,
    artifacts: counts.artifacts,
    recipients: counts.recipients,
    thread_continuations: counts.thread_continuations,
    threads_available: counts.threads_available,
    broadcasting: opts.broadcasting,
    usage_rows_dropped: opts.usageRowsDropped,
    threads_enabled: opts.threadsEnabled,
    batches_lost: num(extract(cluster, "batches_lost")),
    title_only_fallback: num(extract(cluster, "title_only_fallback")),
    fulltext_tasks: num(extract(fulltext, "tasks")),
    fulltext_extracted: num(extract(fulltext, "extracted")),
    fulltext_outcome: str(extract(fulltext, "outcome")),
    blanked_why: mustKnow === null ? null : mustKnow.filter((s) => (textOf(s, "why_it_matters") ?? "").replace(/^ +| +$/g, "") === "").length,
    must_know_shipped: mustKnow === null ? null : mustKnow.length,
    dropped_continuations: stories === null ? null : stories.some((s) => !isObj(s)) ? null : stories.filter((s) => isObj(s) && s["refused"] === "already_claimed").length,
    // Only 1 and 0 are answers; anything else in that slot is a malformed trace, never "healthy".
    linker_ok: linkerOk === 1 ? true : linkerOk === 0 ? false : null,
    repair_outcome: str(extract(repair, "outcome")),
    repair_detail: str(extract(repair, "detail")),
    stories_dropped_at_write: dropped === null ? null : dropped.length,
  };
}

// The config flag the Python reads (config.THREADS_ENABLED), parsed the same way.
export const threadsEnabled = (env: NodeJS.ProcessEnv = process.env): boolean => ["1", "true", "yes"].includes((env["THREADS_ENABLED"] ?? "false").toLowerCase());
