import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { artifactKind } from "./artifact-kinds.js";
import type { Db, Sql } from "./db.js";
import { RESET_IDENTITIES } from "./schema.js";

// The legacy SQLite database into the product schema (data-model design §5.1). pgloader loads the file
// into this database's public schema (db/import/legacy.load); moveAside renames that to `legacy`,
// the migrations then build the product schema in public, and transform copies across in one
// transaction. verify holds the copy against the legacy tables before they are dropped.
export const TRANSFORM = new URL("../../db/import/transform.sql", import.meta.url).pathname;

// Refuses a database that already holds anything in public: the import writes a fresh database only.
export async function assertEmpty(db: Sql): Promise<void> {
  const r = await db.one<{ n: number }>("SELECT count(*) AS n FROM pg_tables WHERE schemaname IN ('public', 'legacy')");
  if (r!.n > 0) throw new Error(`the target database already has ${r!.n} table(s) in public or legacy; the import writes only into a fresh database`);
}

export async function moveAside(db: Sql): Promise<void> {
  const r = await db.one<{ n: number }>("SELECT count(*) AS n FROM pg_tables WHERE schemaname = 'public' AND tablename = 'digest_runs'");
  if (r!.n !== 1) throw new Error("no legacy digest_runs in public: load the SQLite file with pgloader first");
  await db.exec("ALTER SCHEMA public RENAME TO legacy; CREATE SCHEMA public");
}

export async function transform(db: Db, sql = readFileSync(TRANSFORM, "utf8")): Promise<void> {
  await db.tx(async (t) => {
    await t.exec(sql);
    for (const { n } of await t.all<{ n: string }>("SELECT DISTINCT artifact_name AS n FROM run_artifacts")) {
      const k = artifactKind(n);
      if (k.stage !== null) await t.run("UPDATE run_artifacts SET stage = $2, kind = $3, branch = $4 WHERE artifact_name = $1", [n, k.stage, k.kind, k.branch]);
    }
    await t.exec(RESET_IDENTITIES);
  });
}

// What pgloader loads, fingerprinted on both sides before anything is copied: per table its row count,
// and per column its non-null count and a sum (bytes of text, the value of a number). pgloader can lose
// data and still exit 0 with no error logged (text after a NUL byte is dropped), and every check below
// compares against its copy, so this is the one comparison with the file itself.
export interface Fingerprint { [table: string]: { rows: number; columns: Record<string, [count: number, sum: number]> } }
const LOADED = (name: string) => !/^(sqlite_|_yoyo|yoyo_)/.test(name) && !name.startsWith("shown_narratives_fts");

export function sqliteFingerprint(path: string): Fingerprint {
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    const mode = (db.prepare("PRAGMA journal_mode").get() as { journal_mode: string }).journal_mode;
    if (mode.toLowerCase() === "wal") throw new Error(`${path} is in WAL mode: commits may sit in its -wal file; import an online-backup snapshot instead`);
    const out: Fingerprint = {};
    for (const { name } of db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all() as { name: string }[]) {
      if (!LOADED(name)) continue;
      const cols = db.prepare(`SELECT name, upper(type) AS type FROM pragma_table_info('${name}')`).all() as { name: string; type: string }[];
      const parts = cols.flatMap((c) => {
        const q = `"${c.name}"`;
        const sum = /INT|REAL|FLOA|DOUB/.test(c.type) ? `total(${q})` : `total(length(CAST(${q} AS BLOB)))`;
        return [`count(${q})`, sum];
      });
      const row = db.prepare(`SELECT count(*) AS n, ${parts.map((p, i) => `${p} AS p${i}`).join(", ")} FROM "${name}"`).get() as Record<string, number>;
      out[name] = { rows: row["n"]!, columns: Object.fromEntries(cols.map((c, i) => [c.name, [row[`p${2 * i}`]!, row[`p${2 * i + 1}`]!]])) };
    }
    return out;
  } finally {
    db.close();
  }
}

// Of the loaded tables in `schema`: public right after pgloader, legacy once moved aside.
export async function legacyFingerprint(db: Sql, like: Fingerprint, schema = "legacy"): Promise<Fingerprint> {
  const out: Fingerprint = {};
  for (const [table, want] of Object.entries(like)) {
    const cols = await db.all<{ name: string; type: string }>("SELECT column_name AS name, data_type AS type FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2", [schema, table]);
    if (!cols.length) {
      out[table] = { rows: -1, columns: {} };
      continue;
    }
    const names = Object.keys(want.columns).filter((c) => cols.some((x) => x.name === c));
    const parts = names.flatMap((c) => {
      const type = cols.find((x) => x.name === c)!.type;
      const q = `"${c}"`;
      return [`count(${q})`, type === "text" ? `COALESCE(sum(octet_length(${q})), 0)` : `COALESCE(sum(${q}), 0)`];
    });
    const row = (await db.one<Record<string, unknown>>(`SELECT count(*) AS n${parts.map((p, i) => `, ${p} AS p${i}`).join("")} FROM "${schema}"."${table}"`))!;
    out[table] = { rows: Number(row["n"]), columns: Object.fromEntries(names.map((c, i) => [c, [Number(row[`p${2 * i}`]), Number(row[`p${2 * i + 1}`])]])) };
  }
  return out;
}

// Every difference between the two, as readable lines; a real sum agrees to 1e-9 relative.
const close = (a: number, b: number) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a));
export function fingerprintDiff(file: Fingerprint, loaded: Fingerprint): string[] {
  const out: string[] = [];
  for (const [table, want] of Object.entries(file)) {
    const got = loaded[table];
    if (!got || got.rows !== want.rows) {
      out.push(`${table}: ${want.rows} rows in the file, ${got?.rows ?? "none"} loaded`);
      continue;
    }
    for (const [col, [count, sum]] of Object.entries(want.columns)) {
      const g = got.columns[col];
      if (!g || g[0] !== count || !close(g[1], sum)) out.push(`${table}.${col}: file ${count} values, sum ${sum}; loaded ${g ? `${g[0]} values, sum ${g[1]}` : "missing"}`);
    }
  }
  return out;
}

// Each check is a query over both schemas that returns the number of rows that break it; all must be 0.
const utc = (col: string) => `to_char(${col} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')`;
export const CHECKS: [name: string, sql: string][] = [
  // Times are compared as the text SQLite held, not through the transform's own conversion.
  ["every legacy run is a run, with its times, counts and error", `SELECT count(*) FROM legacy.digest_runs l FULL JOIN digest_runs r ON r.id = l.id
     WHERE r.id IS NULL OR l.id IS NULL OR ${utc("r.run_at")} IS DISTINCT FROM l.run_at OR ${utc("r.completed_at")} IS DISTINCT FROM l.completed_at
        OR r.articles_kept IS DISTINCT FROM l.articles_kept OR r.articles_emailed IS DISTINCT FROM l.articles_emailed
        OR r.git_sha IS DISTINCT FROM l.git_sha OR r.error IS DISTINCT FROM l.error`],
  ["a sent outcome only where the legacy run emailed someone", `SELECT count(*) FROM digest_runs r JOIN legacy.digest_runs l ON l.id = r.id
     WHERE (r.outcome = 'sent') <> (l.status = 'completed' AND COALESCE(l.articles_emailed, 0) > 0)`],
  ["every model call, column for column", `SELECT count(*) FROM legacy.run_usage l FULL JOIN run_usage u ON u.id = l.id
     WHERE u.id IS NULL OR l.id IS NULL OR u.run_id IS DISTINCT FROM l.run_id OR u.subagent <> l.subagent OR u.model <> l.model
        OR u.input_tokens <> l.input_tokens OR u.output_tokens <> l.output_tokens OR u.cache_write_tokens <> l.cache_write_tokens
        OR u.cache_read_tokens <> l.cache_read_tokens OR u.api_cost_usd <> l.api_cost_usd OR u.duration_ms IS DISTINCT FROM l.duration_ms
        OR u.thinking IS DISTINCT FROM l.thinking OR u.effort IS DISTINCT FROM l.effort OR ${utc("u.recorded_at")} IS DISTINCT FROM l.recorded_at`],
  ["every artifact, content byte for byte", `SELECT count(*) FROM legacy.run_artifacts l LEFT JOIN run_artifacts a ON a.id = l.id
     WHERE a.id IS NULL OR a.content IS DISTINCT FROM l.content OR a.sha256 <> encode(sha256(convert_to(l.content, 'UTF8')), 'hex')`],
  ["a selections.json for every run the retired table held", `SELECT count(*) FROM legacy.selections s
     WHERE NOT EXISTS (SELECT 1 FROM run_artifacts a WHERE a.run_id = s.run_id AND a.artifact_name = 'selections.json' AND a.state = 'current' AND a.content = s.selections_json)`],
  ["every issue, html byte for byte", `SELECT count(*) FROM legacy.digests d LEFT JOIN issues i ON i.date = d.date::date AND i.revision = 1
     WHERE i.date IS NULL OR i.html IS DISTINCT FROM d.html OR i.run_id IS DISTINCT FROM d.run_id`],
  ["no issue the legacy file did not have", `SELECT (SELECT count(*) FROM issues) - (SELECT count(*) FROM legacy.digests)`],
  ["every send, with its broadcast id and recipients", `SELECT count(*) FROM legacy.digests d FULL JOIN broadcasts b ON b.date = d.date::date
     WHERE (d.broadcast_status IS NOT NULL) <> (b.date IS NOT NULL)
        OR (b.date IS NOT NULL AND (b.resend_id IS DISTINCT FROM d.broadcast_id OR b.recipients IS DISTINCT FROM d.broadcast_recipients))`],
  ["every shown headline, column for column", `SELECT count(*) FROM legacy.shown_narratives l FULL JOIN shown_narratives s ON s.id = l.id
     WHERE s.id IS NULL OR l.id IS NULL OR s.headline <> l.headline OR s.tier IS DISTINCT FROM l.tier OR ${utc("s.shown_at")} IS DISTINCT FROM l.shown_at
        OR s.source_id IS DISTINCT FROM l.source_id OR s.run_id IS DISTINCT FROM l.run_id OR s.original_title IS DISTINCT FROM l.original_title OR s.cluster_id IS DISTINCT FROM l.cluster_id`],
  ["every fetched article, column for column", `SELECT count(*) FROM legacy.fetched_articles l FULL JOIN fetched_articles f ON f.id = l.id
     WHERE f.id IS NULL OR l.id IS NULL OR f.run_id IS DISTINCT FROM l.run_id OR f.source_id <> l.source_id OR f.title <> l.title OR f.url <> l.url
        OR f.published IS DISTINCT FROM l.published OR f.summary IS DISTINCT FROM l.summary OR ${utc("f.fetched_at")} IS DISTINCT FROM l.fetched_at`],
  ["every source health row, column for column", `SELECT count(*) FROM legacy.source_health l FULL JOIN source_health h ON h.id = l.id
     WHERE h.id IS NULL OR l.id IS NULL OR h.source_id <> l.source_id OR h.success <> (l.success <> 0) OR h.error_message IS DISTINCT FROM l.error_message
        OR ${utc("h.recorded_at")} IS DISTINCT FROM l.recorded_at OR h.articles_fetched IS DISTINCT FROM l.articles_fetched OR h.articles_kept IS DISTINCT FROM l.articles_kept OR h.run_id IS DISTINCT FROM l.run_id`],
  ["every dedup row, column for column", `SELECT count(*) FROM legacy.dedup_log l FULL JOIN dedup_log d ON d.id = l.id
     WHERE d.id IS NULL OR l.id IS NULL OR ${utc("d.logged_at")} IS DISTINCT FROM l.logged_at OR d.article_title <> l.article_title
        OR d.article_source_id IS DISTINCT FROM l.article_source_id OR d.matched_headline <> l.matched_headline OR d.similarity <> l.similarity
        OR d.threshold <> l.threshold OR d.run_id IS DISTINCT FROM l.run_id`],
  ["every thread's derived label is its stored label", `SELECT count(*) FROM legacy.threads l
     WHERE l.label IS DISTINCT FROM (SELECT cluster_story FROM thread_installments i WHERE i.thread_id = l.id ORDER BY run_id DESC, id DESC LIMIT 1)`],
  ["every thread's derived status is its stored status", `SELECT count(*) FROM legacy.threads l LEFT JOIN thread_state s ON s.id = l.id WHERE s.status IS DISTINCT FROM l.status`],
  ["every installment, continued as the linker decided", `SELECT count(*) FROM legacy.thread_installments l FULL JOIN thread_installments i ON i.id = l.id
     WHERE i.id IS NULL OR l.id IS NULL OR i.thread_id <> l.thread_id OR i.run_id <> l.run_id OR i.cluster_story IS DISTINCT FROM l.cluster_story
        OR i.continued <> (l.matched_score IS NOT NULL) OR i.content IS DISTINCT FROM l.content`],
  ["every question, open or resolved as it was", `SELECT count(*) FROM legacy.thread_questions l LEFT JOIN thread_questions q ON q.id = l.id
     WHERE q.id IS NULL OR (l.status = 'resolved') <> EXISTS (SELECT 1 FROM thread_question_resolutions r WHERE r.question_id = l.id AND r.run_id = l.resolved_run_id)`],
];

export async function verify(db: Sql): Promise<{ name: string; broken: number }[]> {
  const out: { name: string; broken: number }[] = [];
  for (const [name, sql] of CHECKS) out.push({ name, broken: Number(Object.values((await db.one<Record<string, unknown>>(sql))!)[0]) });
  return out;
}

export async function dropLegacy(db: Sql): Promise<void> {
  await db.exec("DROP SCHEMA legacy CASCADE");
}
