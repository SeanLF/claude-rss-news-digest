# SQL / SQLite standards (mid-2026)

> As of 2026-07, verify before relying: SQLite versions and feature availability move — confirm the shipped version in each consumer (`sqlite3.sqlite_version` in Python; `rusqlite::version()` / bundled `libsqlite3-sys` in Rust) and check release notes before acting on a specific number.

This repo: one SQLite file (`data/digest.db`), 22 forward-only migrations under `migrations/` (yoyo). Two concurrent consumers — the **newsroom** Python batch pipeline (writer, `sqlite3` stdlib) and the **circulation** Rust/Axum server (mostly read-only, rusqlite 0.39 / bundled SQLite ~3.53). FTS5 backs `/search`. Concurrent web-read + batch-write is exactly the workload SQLite defaults get wrong.

## 0. Version landscape

- **Current stable: 3.53.x** (3.53.3, 2026-06-26). Python 3.14 stdlib ships ~3.53.1; rusqlite 0.39 bundles its own — the two consumers do **not** share a library, so a feature must be confirmed in *both*.
- `STRICT` needs 3.37+ (2021); FTS5 is compiled in by default in both consumers here. Both far exceed those floors — availability is a non-issue; correct *usage* is the risk.

## 1. Connection pragmas — set explicitly, every connection

SQLite's defaults (rollback journal, no busy timeout, FK off) are wrong for concurrent web+batch. Pragmas are **per-connection**, not persisted (except `journal_mode`, which is per-database and sticks) — set them right after opening, on *every* connection, in both consumers.

- **`journal_mode=WAL`** — readers don't block the writer and vice-versa. Set once; it persists on the DB file. Creates `-wal`/`-shm` sidecar files (keep them; don't ship the DB without them). **Gap in this repo:** `db.py` opens plain `sqlite3.connect()` with no pragmas; circulation opens read-only + one `READ_WRITE` path that sets only `busy_timeout`. Establishing WAL + the pragmas below on connect is the correct hardening.
- **`busy_timeout=5000`** (ms) — without it, any lock contention is an instant `SQLITE_BUSY`/"database is locked". With WAL + a 5s timeout, spurious lock errors mostly vanish. Note: a busy_timeout does **not** save you from a writer-vs-writer deadlock when a transaction upgrades read→write — use `BEGIN IMMEDIATE` for transactions that will write (takes the write lock up front).
- **`foreign_keys=ON`** — FK enforcement is **OFF by default** and per-connection. Every connection that writes must set it or FK `CHECK`s silently do nothing.
- **`synchronous=NORMAL`** — safe *under WAL* (durability boundary is the checkpoint, not each commit); ~faster than `FULL`, no corruption risk. Only valid paired with WAL.
- Reasonable extras: `cache_size=-20000` (~20MB), `temp_store=MEMORY`, `mmap_size` for read-heavy. `wal_autocheckpoint` defaults to 1000 pages; checkpoint after big batch writes so the `-wal` file doesn't grow unbounded.

## 2. Schema & typing

- **`STRICT` tables** (append `) STRICT;`) — real type enforcement; without it SQLite's dynamic typing lets any value into any column. Allowed types: `INT`/`INTEGER`, `REAL`, `TEXT`, `BLOB`, `ANY`. Prefer STRICT for new tables. No `VARCHAR(n)`/`DATETIME`/`BOOL` — those aren't STRICT types; store timestamps as `TEXT` (ISO-8601) or `INTEGER` (epoch), booleans as `INTEGER` 0/1.
- **Generated columns** (`... GENERATED ALWAYS AS (expr) [STORED|VIRTUAL]`) for derived values — `VIRTUAL` (default) costs storage nothing, `STORED` trades disk for read speed; both can be indexed.
- **Constraints do work — use them.** `CHECK` (e.g. `tier IN ('must_know','should_know')`), `NOT NULL`, `UNIQUE`, `FOREIGN KEY ... REFERENCES` (enforced only when `foreign_keys=ON`). Cheaper and more reliable than app-layer validation.
- **Indexes:** index columns in `WHERE`/`JOIN`/`ORDER BY`. Composite index column order = equality cols first, then range/sort. Confirm an index is actually used with **`EXPLAIN QUERY PLAN <query>`** — look for `USING INDEX`, not `SCAN TABLE`. Don't over-index a write-heavy table; each index is write amplification.
- `INTEGER PRIMARY KEY` aliases the rowid (no extra storage/index) — the right PK for most tables here. FTS5 external-content tables link by this rowid.

## 3. FTS5 (backs `/search`)

- **External-content tables** (`content='shown_narratives', content_rowid='id'`) index text without a second copy of the data — avoids storage doubling. This repo already uses this pattern for `shown_narratives_fts` (migration `20260701210002`).
- **Sync triggers are mandatory** with external content — `AFTER INSERT` mirrors the row in; `AFTER DELETE`/`AFTER UPDATE` use the special-insert delete idiom (`INSERT INTO fts(fts, rowid, ...) VALUES('delete', old.id, ...)`). On UPDATE, delete-then-insert. Add all three even if a write path doesn't exist yet — a future path silently desyncs the index forever otherwise (this repo does exactly that, deliberately).
- **Backfill** pre-existing rows in the same migration that creates the vtable (a `SELECT` insert), or the index misses everything already stored.
- **Tokenizers:** `unicode61` (default; diacritic-folding, good for mixed-language wire copy) is the sane default; add `porter` (`tokenize='porter unicode61'`) for English stemming (search "running" matches "run"). Pick deliberately — changing it later means a full rebuild.
- **Ranking:** `ORDER BY bm25(fts_table)` — bm25 returns a **negative** score, most-relevant = most-negative, so ascending order puts best matches first. Optional per-column weights: `bm25(fts, 10.0, 1.0)`.
- **Maintenance:** `INSERT INTO fts(fts) VALUES('optimize')` merges b-tree segments — run occasionally (e.g. after big backfills), not every write; each optimize rewrites the whole index. `'rebuild'` regenerates from content when triggers have drifted (optimize does **not** repair inconsistency, only compacts). `columnsize=0` saves space if you never need per-column bm25 weighting.
- **Query-syntax injection:** FTS5 `MATCH` has its own query grammar (`"`, `*`, `AND`/`OR`/`NOT`, `NEAR`, column filters). A raw user string passed as the match argument is both an injection and a syntax-error surface — parameterize the value *and* sanitize/quote FTS operators (wrap user terms in double-quotes to treat them as literal phrases) before it reaches `MATCH`.

## 4. Migrations (yoyo, forward-only)

- **Forward-only, deterministic order.** Filenames `YYYYMMDDHHMMSS_description.sql` sort lexically = apply order; the timestamp prefix is the ordering contract — don't collide or backdate.
- **Never edit an applied migration.** yoyo tracks applied migrations by hash/id; editing one already run in prod means the change never applies (and can trip hash checks). New change = new migration file.
- **Additive-then-backfill** for column changes: add the column (nullable / with default) in one migration, backfill data, then tighten constraints in a later one. SQLite `ALTER TABLE` is limited (add column / rename; no drop-column before 3.35, no arbitrary constraint changes) — bigger changes use the **12-step table rebuild** (create new table → copy → drop → rename, inside a transaction with FKs deferred).
- Applied automatically on each run via `db.init()` and in prod. Inspect with `make migrate-status`, apply with `make migrate`. If applied outside yoyo, mark it applied manually so yoyo's ledger stays truthful.
- **Test rollback intent** even though the pipeline is forward-only: a migration that can't be reasoned about in reverse is a deploy risk. Wrap DDL in a transaction where the engine allows so a failure doesn't leave a half-migrated schema.

## 5. Query hygiene

- **Parameterized queries only.** `?` placeholders (both `sqlite3` and rusqlite) — never f-strings/`format!`/concatenation into SQL, including for `LIMIT`, `IN (...)` lists, and identifiers. String interpolation is the injection vector and also defeats statement caching.
- **Wrap multi-statement writes in a transaction** — the whole batch record (per-run headline inserts) commits atomically or not at all. `BEGIN IMMEDIATE` when the transaction will write, to grab the write lock before doing read work (avoids the upgrade-deadlock busy_timeout can't fix).
- **Avoid N+1** — one `JOIN` or `WHERE id IN (?, ?, ...)` beats a query-per-row loop. For the archive/index pages, fetch the page's rows in a single query.
- Read paths in circulation should open **read-only** (`SQLITE_OPEN_READ_ONLY`, as they already do) — cheaper, and can't accidentally take a write lock.

## 6. Integrity & ops

- **Verify clones.** This repo has been bitten by truncated raw-`cat` DB clones (`make db-clone` streams the file over SSH and can silently short-read). After any clone: run **`PRAGMA integrity_check`** (expect `ok`) and confirm **`page_count × page_size == filesize`** (`PRAGMA page_count;` × `PRAGMA page_size;`). A short file passes neither — re-clone if it does. Don't trust a cloned DB until both checks pass.
- **`PRAGMA integrity_check`** (full) / **`quick_check`** (fast) also validates STRICT-table column types.
- **`ANALYZE`** refreshes `sqlite_stat` so the planner picks good indexes — cheap, run after large data changes. **`VACUUM`** reclaims space / defragments — but it rewrites the whole DB, needs a lock and ~2x disk, and **breaks with an open transaction**; do it in a maintenance window, not mid-serving. WAL + vacuum interactions: vacuum resets the WAL, so checkpoint first.
- Back up with the **backup API** or `VACUUM INTO 'file'` for a consistent snapshot — *not* a raw `cp`/`cat` of a live WAL DB (that's the truncation trap above and can catch an inconsistent WAL state).

## 7. Pitfalls / anti-patterns

- Assuming pragmas persist — only `journal_mode` does; `foreign_keys`, `busy_timeout`, `synchronous` reset on every new connection.
- `foreign_keys` OFF by default → FK constraints silently no-op. The single most common "why isn't my FK enforced" bug.
- Relying on dynamic typing: a non-STRICT column accepts `"12"` as text and `12` as int in different rows; comparisons and indexes then surprise you.
- `synchronous=NORMAL` **without** WAL — that's a durability downgrade with none of the WAL safety; only pair it with WAL.
- Shipping/copying a WAL-mode DB without its `-wal`/`-shm` files, or `cp`-ing it live → lost recent writes / corruption. Use the backup API.
- Long-held write transactions (batch pipeline) blocking web reads — keep write transactions short; WAL lets reads proceed but a stalled writer still holds the single write slot.
- Never editing an applied migration is a *hard* rule — a "quick fix" to a run migration diverges dev from prod invisibly.
- `SELECT *` in code that resolves columns positionally — a later `ALTER TABLE ADD COLUMN` shifts indices. Name columns.
- Trusting "tests pass" for a clone/migration — inspect the actual row counts and `integrity_check` output, not just the exit code.
