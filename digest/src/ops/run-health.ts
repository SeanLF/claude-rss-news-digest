import type { DatabaseSync } from "node:sqlite";
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

// db.get_run_health's query, verbatim. json_valid/json_type guard every extract: json_extract raises on
// malformed input, and one raise here would blank every invariant instead of the one it feeds.
const HEALTH_SQL = `
SELECT
  (SELECT COUNT(DISTINCT headline) FROM shown_narratives WHERE run_id = :r) AS shipped,
  (SELECT COUNT(DISTINCT subagent)  FROM run_usage       WHERE run_id = :r) AS stages,
  (SELECT COUNT(*)                  FROM run_artifacts   WHERE run_id = :r) AS artifacts,
  (SELECT SUM(broadcast_recipients) FROM digests WHERE run_id = :r) AS recipients,
  (SELECT COUNT(*) FROM thread_installments
     WHERE run_id = :r AND matched_score IS NOT NULL) AS thread_continuations,
  (SELECT COUNT(*) FROM threads t
     WHERE t.status = 'active'
       AND EXISTS (SELECT 1 FROM thread_installments p
                    WHERE p.thread_id = t.id AND p.run_id < :r)) AS threads_available,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.batches_lost') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'cluster_health.json') AS batches_lost,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.title_only_fallback') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'cluster_health.json') AS title_only_fallback,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.tasks') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'fulltext_health.json') AS fulltext_tasks,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.extracted') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'fulltext_health.json') AS fulltext_extracted,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.outcome') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'fulltext_health.json') AS fulltext_outcome,
  (SELECT CASE WHEN json_valid(content) AND json_type(content, '$.must_know') = 'array'
               THEN (SELECT COUNT(*) FROM json_each(json_extract(content, '$.must_know'))
                      WHERE TRIM(COALESCE(value ->> '$.why_it_matters', '')) = '') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'selections.json') AS blanked_why,
  (SELECT CASE WHEN json_valid(content) AND json_type(content, '$.stories') = 'array'
               THEN (
     SELECT CASE WHEN COALESCE(SUM(CASE WHEN type = 'object' THEN 0 ELSE 1 END), 0) > 0
                 THEN NULL
                 ELSE COALESCE(SUM(CASE WHEN type = 'object'
                       THEN (CASE WHEN json_extract(value, '$.refused') = 'already_claimed' THEN 1 ELSE 0 END)
                       ELSE 0 END), 0) END
       FROM json_each(run_artifacts.content, '$.stories')) END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'thread_links.json') AS dropped_continuations,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.linker_ok') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'thread_links.json') AS linker_ok,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.outcome') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'repair_health.json') AS repair_outcome,
  (SELECT CASE WHEN json_valid(content) THEN json_extract(content, '$.detail') END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'repair_health.json') AS repair_detail,
  (SELECT CASE WHEN json_valid(content) AND json_type(content, '$.dropped') = 'array'
               THEN json_array_length(json_extract(content, '$.dropped')) END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'write_branches.json') AS stories_dropped_at_write,
  (SELECT CASE WHEN json_valid(content) AND json_type(content, '$.must_know') = 'array'
               THEN json_array_length(json_extract(content, '$.must_know')) END
     FROM run_artifacts WHERE run_id = :r AND artifact_name = 'selections.json') AS must_know_shipped`;

type Row = Omit<RunHealth, "run_id" | "broadcasting" | "threads_enabled" | "usage_rows_dropped" | "linker_ok"> & { linker_ok: unknown };

// `broadcasting` and `threadsEnabled` are the run's own configuration, not DB state;
// `usageRowsDropped` is process state (rows that never reached the table cannot be counted from it).
export function getRunHealth(db: DatabaseSync, runId: number, opts: { broadcasting: boolean; threadsEnabled: boolean; usageRowsDropped: number }): RunHealth {
  const row = db.prepare(HEALTH_SQL).get({ r: runId }) as unknown as Row;
  return {
    run_id: runId,
    shipped: row.shipped,
    stages: row.stages,
    artifacts: row.artifacts,
    recipients: row.recipients,
    thread_continuations: row.thread_continuations,
    threads_available: row.threads_available,
    broadcasting: opts.broadcasting,
    usage_rows_dropped: opts.usageRowsDropped,
    threads_enabled: opts.threadsEnabled,
    batches_lost: row.batches_lost,
    title_only_fallback: row.title_only_fallback,
    fulltext_tasks: row.fulltext_tasks,
    fulltext_extracted: row.fulltext_extracted,
    fulltext_outcome: row.fulltext_outcome,
    blanked_why: row.blanked_why,
    must_know_shipped: row.must_know_shipped,
    dropped_continuations: row.dropped_continuations,
    // Only 1 and 0 are answers; anything else in that slot is a malformed trace, never "healthy".
    linker_ok: row.linker_ok === 1 ? true : row.linker_ok === 0 ? false : null,
    repair_outcome: row.repair_outcome,
    repair_detail: row.repair_detail,
    stories_dropped_at_write: row.stories_dropped_at_write,
  };
}

// The config flag the Python reads (config.THREADS_ENABLED), parsed the same way.
export const threadsEnabled = (env: NodeJS.ProcessEnv = process.env): boolean => ["1", "true", "yes"].includes((env["THREADS_ENABLED"] ?? "false").toLowerCase());
