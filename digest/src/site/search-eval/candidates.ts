import type { Sql } from "../../store/db.js";
import type { Hit } from "./score.js";

// The ranking candidates of docs/proposed/2026-09-23-search-tuning, as SQL over story_sources. The
// `simple` vector is computed per query here (the eval's cost, not the site's): only the stored
// `search` column is indexed.

export interface Spec {
  config: "english" | "simple" | "fallback"; // fallback: english, or simple when the English query is empty
  parse: "phrase" | "plain" | "websearch";
  prefix: boolean;
  rank: "ts_rank" | "ts_rank_cd";
  norm: number;
  weights: [number, number, number, number] | null; // {D, C, B, A}
  dedup: boolean;
  order: "id" | "shown_at" | "decay";
}

export const B0: Spec = { config: "english", parse: "phrase", prefix: false, rank: "ts_rank", norm: 0, weights: null, dedup: false, order: "id" };
export const B0P: Spec = { ...B0, config: "fallback" };

export const ROUND1: Record<string, Spec> = {
  B0,
  "B0'": B0P,
  rank_cd: { ...B0P, rank: "ts_rank_cd" },
  norm1: { ...B0P, norm: 1 },
  norm2: { ...B0P, norm: 2 },
  weights_b01: { ...B0P, weights: [0, 0, 0.1, 1] },
  plain: { ...B0P, parse: "plain" },
  websearch: { ...B0P, parse: "websearch" },
  prefix: { ...B0P, prefix: true },
  recency_tie: { ...B0P, order: "shown_at" },
  recency_decay: { ...B0P, order: "decay" },
  dedup: { ...B0P, dedup: true },
  simple: { ...B0P, config: "simple" },
};

const SIMPLE_VEC = "(setweight(to_tsvector('simple', coalesce(s.headline, '')), 'A') || setweight(to_tsvector('simple', coalesce(s.source_title, '')), 'B'))";
const DECAY_DAYS = 180;

export function candidateSql(spec: Spec): string {
  const parser = { phrase: "phraseto_tsquery", plain: "plainto_tsquery", websearch: "websearch_to_tsquery" }[spec.parse];
  // The last lexeme as a prefix: rewrite the query's text form, whose final token is always 'lexeme'.
  const tq = (cfg: string) => {
    const base = `${parser}('${cfg}', $1)`;
    return spec.prefix ? `(CASE WHEN numnode(${base}) = 0 THEN ${base} ELSE regexp_replace(${base}::text, '''([^'']*)''$', '''\\1'':*')::tsquery END)` : base;
  };
  const useEnglish = spec.config === "english" ? "true" : spec.config === "simple" ? "false" : "numnode(q.e) > 0";
  const vec = `(CASE WHEN ${useEnglish} THEN s.search ELSE ${SIMPLE_VEC} END)`;
  const query = `(CASE WHEN ${useEnglish} THEN q.e ELSE q.s END)`;
  const w = spec.weights ? `'{${spec.weights.join(",")}}'::float4[], ` : "";
  const rank = `${spec.rank}(${w}${vec}, ${query}, ${spec.norm})`;
  const order = {
    id: "rank DESC, id DESC",
    shown_at: "rank DESC, shown_at DESC, id DESC",
    decay: `rank / (1 + extract(epoch FROM ($3::timestamptz - shown_at)) / 86400 / ${DECAY_DAYS}) DESC, id DESC`,
  }[spec.order];
  const rows = spec.dedup ? "(SELECT DISTINCT ON (run_id, headline) * FROM m ORDER BY run_id, headline, rank DESC, id DESC)" : "m";
  return `WITH q AS (SELECT ${tq("english")} AS e, ${tq("simple")} AS s),
    m AS (SELECT s.id, s.run_id, s.headline, s.tier, s.shown_at, ${rank} AS rank FROM story_sources s, q WHERE ${vec} @@ ${query})
    SELECT x.headline, (SELECT max(i.issue_date)::text FROM issues i WHERE i.run_id = x.run_id) AS date
    FROM ${rows} x ORDER BY ${order} LIMIT $2`;
}

export async function runCandidate(db: Sql, spec: Spec, query: string, now: string, limit = 50): Promise<Hit[]> {
  const params: unknown[] = [query, limit];
  if (spec.order === "decay") params.push(now);
  return db.all<Hit>(candidateSql(spec), params);
}

// Negative control: rows drawn at random, reproducibly per query.
export async function randomRows(db: Sql, query: string, limit = 50): Promise<Hit[]> {
  return db.all<Hit>(
    `SELECT s.headline, (SELECT max(i.issue_date)::text FROM issues i WHERE i.run_id = s.run_id) AS date
     FROM story_sources s ORDER BY md5(s.id::text || $1) LIMIT $2`,
    [query, limit],
  );
}

// Round 2 of the pre-registration: combinations of the round-1 factors that beat B0'.
export const ROUND2: Record<string, Spec> = {};

// The candidate the site's store.search implements; `make search-eval` holds the two to the same rows.
export const SHIPPED = "dedup";
