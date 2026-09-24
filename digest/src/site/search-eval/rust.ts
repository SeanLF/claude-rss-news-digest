import { DatabaseSync } from "node:sqlite";
import type { Hit } from "./score.js";

// The Rust site's search (circulation/src/search.rs, search_shown_narratives) on the legacy SQLite
// file: the whole query quoted as one FTS5 string, FTS5's BM25 rank, 50 rows.
export function rustSearch(file: string): (query: string) => Hit[] {
  const db = new DatabaseSync(file, { readOnly: true });
  const stmt = db.prepare(
    `SELECT sn.headline AS headline, d.date AS date
     FROM shown_narratives_fts f
     JOIN shown_narratives sn ON sn.id = f.rowid
     LEFT JOIN digests d ON d.run_id = sn.run_id
     WHERE shown_narratives_fts MATCH ?
     ORDER BY f.rank
     LIMIT 50`,
  );
  return (query) => stmt.all(`"${query.replaceAll('"', '""')}"`).map((r) => ({ headline: String(r["headline"]), date: r["date"] === null ? null : String(r["date"]) }));
}
