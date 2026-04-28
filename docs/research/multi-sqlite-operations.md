# Multi-SQLite Operations Research

Operational patterns for running ~3-10 SQLite databases (one per vertical/tenant) plus a `control.db` on a single 4 GB Hetzner CX23 host. Focus is on concrete production knowledge from people who run this in production today, not "SQLite is good for small apps" boilerplate.

The headline finding: at the 10-DB scale this project is targeting, almost nothing breaks. The hard ops problems show up at hundreds-to-thousands of DBs. The interesting risks for a 10-DB host are checkpoint behaviour under a long-lived Rust reader and getting backups right when WAL/SHM files are involved.

---

## 1. Backup/restore at N databases

**Recommendation:** use `sqlite3_rsync` (the official tool, ships with SQLite 3.47+) per file, driven by a small shell loop that iterates the tenant list from `control.db`. Skip raw `cp`/`rsync` of the `.db` file -- the WAL/SHM interaction makes it unreliable. Litestream is overkill for a daily-write workload but cheap insurance if a same-day rebuild matters.

**Why not raw rsync of the file:**
The SQLite project's own ["How to Corrupt an SQLite Database"](https://sqlite.org/howtocorrupt.html) document explicitly calls out copying a WAL-mode database while the WAL exists as a corruption vector. If you copy `digest.db` without also copying `digest.db-wal` and `digest.db-shm` consistently, the replica is missing committed transactions; if you copy them all but not at the same instant, the replica sees a torn write. Including all three with a single `cp -a` mostly works but is not safe in the formal sense -- the sqlite-users list has [years of threads](https://sqlite-users.sqlite.narkive.com/aNKAggBg/sqlite-online-hot-backup-of-wal-journalling-mode-database) recommending against it.

**Why `sqlite3_rsync` is the right primitive now:**
The [official tool](https://sqlite.org/rsync.html) takes a snapshot at the moment the copy starts, streams page-by-page (so unchanged pages aren't sent), and is safe against a writer running concurrently. It only handles one DB per invocation -- as Simon Willison [notes](https://simonwillison.net/2024/Oct/4/sqlite-rsync/) -- so a multi-DB shop wraps it in a script. The [SQLite forum has a reference wrapper](https://sqlite.org/forum/info/b29e176bff250bd6a5cfb33bda7fb110be89cfb33103fcce45be9ee108bbdff0) for exactly this. With 10 databases this is a 10-line bash loop.

**Why Litestream if you want continuous backup:**
Litestream's [directory replication](https://litestream.io/guides/directory/) with a glob pattern (`pattern: "*.db"` under `/var/lib/tenants`) was designed for exactly this multi-tenant case, and the [directory watcher](https://litestream.io/guides/directory-watcher/) auto-discovers new tenant DBs. The known gotcha: Litestream takes brief write locks while shipping WAL frames, so your app must use `PRAGMA busy_timeout` ([docs](https://litestream.io/tips/)). And there is a [documented interaction](https://github.com/benbjohnson/litestream/issues/237) where a long snapshot transfer can block writes for the snapshot's duration, plus [issue #521](https://github.com/benbjohnson/litestream/issues/521) where a manual app-side checkpoint can cause Litestream to lose its position and force a full re-snapshot. Under high write load this matters; for once-a-day writes it does not.

**Restore:** with `sqlite3_rsync` restore is just running it the other direction. With Litestream you'd `litestream restore -o /data/digest.db s3://bucket/digest`. Per-DB restore is an underrated win of file-per-tenant: you can roll back one tenant without touching others.

---

## 2. WAL checkpoint orchestration

**Recommendation:** keep the default `wal_autocheckpoint=1000`, set `PRAGMA journal_size_limit = 67108864` (64 MB) on every connection as a hard ceiling, and run a scheduled `PRAGMA wal_checkpoint(TRUNCATE)` from the Python writer at the end of each daily run. The Rust reader does NOT need to do anything special as long as connections are short-lived per request.

**The actual failure mode you're at risk of:**
The [SQLite WAL docs](https://sqlite.org/wal.html) are explicit: "if a database has many concurrent overlapping readers and there is always at least one active reader, then no checkpoints will be able to complete and hence the WAL file will grow without bound." Auto-checkpoints are PASSIVE and never block; a passive checkpoint can only reclaim WAL frames that no reader still holds open. A long-lived Rust reader holding a transaction open across requests becomes a checkpoint floor.

In practice, if you open a fresh `rusqlite::Connection` per HTTP request and let it drop at the end of the request (which is what your `circulation/src/main.rs` is structured for, given there's no connection pool today), the reader window is milliseconds and checkpoints reclaim cleanly. The danger pattern is holding a long-running `prepare`d statement or a `BEGIN`'d transaction across awaits.

There's a [recent OpenAI Codex bug](https://github.com/openai/codex/issues/16270) where `state_5.sqlite` and `logs_1.sqlite` grew unboundedly on long-running installs because of exactly this -- write lock contention from a never-completing checkpoint. Worth reading as the canonical modern example.

**Why scheduled TRUNCATE checkpoints:**
[`PRAGMA wal_checkpoint(TRUNCATE)`](https://sqlite.org/c3ref/wal_checkpoint_v2.html) blocks until all readers are clear and physically truncates the WAL file to zero bytes. The [SQLite docs](https://sqlite.org/wal.html) note this is the safe pattern: "ensure that there are reader gaps... and that checkpoints are attempted during those times." Your daily cron run is your reader gap. Run TRUNCATE at the end of the writer cron.

**Why journal_size_limit is the seatbelt:**
[`PRAGMA journal_size_limit`](https://sqlite.org/pragma.html#pragma_journal_size_limit) is the hard cap; even if checkpointing fails, the WAL file won't grow past the limit -- writes will start blocking instead of filling the disk. The [Litestream WAL truncate threshold guide](https://litestream.io/guides/wal-truncate-threshold/) suggests 64-256 MB depending on workload. For a once-daily writer, 64 MB is more than ample.

**`wal_autocheckpoint` tuning:** the default 1000 pages (~4 MB at default page size) is fine. [phiresky's well-known tuning post](https://phiresky.github.io/blog/2020/sqlite-performance-tuning/) for read-heavy workloads doesn't touch this. Lowering it just makes checkpoints more frequent, which doesn't help if a long reader is blocking them.

---

## 3. Connection pooling across many DBs

**Recommendation:** one connection-per-request in the Rust server (no pool yet -- premature), one connection-per-script-run in the Python writer. If you grow to a pool, use a per-DB pool with small per-DB caps (4-8 connections), not a shared pool with `ATTACH`. At 10 DBs and CX23 RAM (4 GB), this is well inside any reasonable limit.

**Per-DB pool vs `ATTACH`:**
The Rails ecosystem (Shardines, Basecamp's HEY, ActiveRecord::Tenanted) all converged on per-DB pools that are established lazily. Julik Tarkhanov's [shardines post](https://blog.julik.nl/2025/04/a-can-of-shardines) documents the design: dynamic connection pools that avoid declaring thousands of shards at boot, using Rails' `register_db_config_handler` to generate per-tenant configs on demand. The HN [discussion](https://news.ycombinator.com/item?id=43811400) reinforces this is the production pattern, not a clever trick.

37signals' production setup ([Solid Queue post](https://dev.37signals.com/introducing-solid-queue/), [Rails 8 post](https://rubyonrails.org/2024/9/27/rails-8-beta1-no-paas-required)) runs four separate SQLite DBs (primary, cache, queue, cable), each with its own pool, "to allow simultaneous writes across them." That's exactly your model: separate pool per file, never `ATTACH`.

**PocketBase's published numbers** ([discussion #4209](https://github.com/pocketbase/pocketbase/discussions/4209)) show its production-tested defaults: 120 read connections + 1 write connection per database. For your scale that's wildly excessive; 4 read + 1 write per tenant DB on Rust, plus 1 read + 1 write per DB on Python, comfortably fits. 10 DBs * (4 + 1) = 50 connections at peak from Rust, plus a couple from Python. Each open SQLite connection costs maybe a few hundred KB of resident memory plus the page cache; on a 4 GB host this is noise.

**File descriptor headroom:** every open SQLite connection consumes 3 fds (db, wal, shm). 50 connections * 3 = 150 fds. Linux default soft limit is 1024-4096 ([linuxvox](https://linuxvox.com/blog/linux-file-descriptor-limit/)); you don't need to tune anything until you're at hundreds of tenants.

---

## 4. Cross-DB queries: ATTACH vs app-level joins

**Recommendation:** don't ATTACH. Treat each tenant DB as a fully isolated database, do any cross-tenant analytics (e.g. for the `control.db` admin views) by querying each DB sequentially in application code and aggregating. ATTACH has two production-disqualifying properties for a multi-tenant setup.

**Why ATTACH is a trap here:**

1. **Cross-DB transactions are not atomic in WAL mode.** From the [ATTACH docs](https://sqlite.org/lang_attach.html): "If the main database is ':memory:' or if the journal_mode is WAL, then transactions continue to be atomic within each individual database file. But if the host computer crashes in the middle of a COMMIT where two or more database files are updated, some of those files might get the changes where others might not." For a multi-tenant SaaS where a write to `control.db` (e.g. "tenant X just rotated their API key") needs to be paired with a write to `tenant_x.db`, ATTACH gives you a partial-write window on crash. Either keep the writes truly independent (write to `control.db` first, treat it as the source of truth) or move tenant-state into `control.db`.

2. **Hard limit of 10 attached DBs by default**, max 125 ([SQLite limits](https://sqlite.org/limits.html)). Fine at your scale, blows up at the SaaS tier.

3. **Cache fragmentation.** [sqlite-users](https://sqlite-users.sqlite.narkive.com/hJb2nxtW/sqlite-performance-with-large-and-multiple-attached-databases) has multiple threads on this: "Each attached database in each connection has its own cache. With that many data sources you're going to get a very uneven distribution of cache utilization, and very high worst-case usage."

**Where ATTACH is fine:** ad-hoc data exploration in `sqlite3` CLI -- Simon Willison's [cross-database queries post](https://simonwillison.net/2021/Feb/21/cross-database-queries/) is the reference for this, and it's explicitly a `datasette`-style read-only pattern. For one-off "give me a count across all tenants", attach them, query, detach.

Turso recently [added cross-DB ATTACH](https://turso.tech/blog/database-per-tenant-architectures-get-production-friendly-improvements) to their per-tenant offering, but their guidance is the same: read-only analytics, not transactional writes.

---

## 5. What breaks between 10 and 100 tenant DBs on one host

At 10 DBs: nothing meaningful breaks. At 100 DBs on a CX23 you'd start seeing real issues. Specific failure modes from people who have lived it:

**Schema migration time becomes the dominant deploy cost.** Multiple Rails-ecosystem posts ([Codeminer42 on multi-DB tenancy](https://blog.codeminer42.com/rails-multi-databases-and-tenancy-how-we-do-it-in-2025/), the [shardines post](https://blog.julik.nl/2025/04/a-can-of-shardines), [37signals' Rails Multi-Tenancy post](https://dev.37signals.com/rails-multi-tenancy/)) all flag this: "release times can increase substantially because schema migrations must run on individual databases one by one." Your `db.init()` runs yoyo migrations per-DB; at 100 DBs that's 100 sequential migration checks per deploy. Mitigation: parallelise across cores, or keep migrations idempotent and skip already-applied checks via the yoyo log.

**Backup time scales linearly.** `sqlite3_rsync` on a 50 MB DB takes ~seconds. At 100 DBs that's minutes; at 1000 it's an hour. Backups can run in parallel but disk IO is the bottleneck, and on a CX23 (NVMe but 2 vCPU) you'd see real contention. Litestream solves this by streaming continuously instead of in batch ([Fly's "Litestream: Revamped" post](https://fly.io/blog/litestream-revamped/) discusses the rationale), but adds the lock-window risk from §1.

**The LiteFS team's own published guidance** ([community.fly.io](https://community.fly.io/t/litefs-many-tens-to-hundreds-of-thousands-of-sqlite-dbs/9977)): "tens or hundreds of databases on LiteFS without an issue" is the tested envelope; "thousands or hundreds of thousands may pose a problem." This isn't a SQLite limit, it's an operational observation about file-system overhead, FUSE bookkeeping, and replication coordination. Without LiteFS you skip the FUSE part, but the file-system inode/directory-listing pressure is real -- `ls /data/` becomes slow at thousands of files.

**Checkpoint stampede on a shared cron.** If you write to all tenant DBs at the same minute (e.g. 6:00 UTC daily run for everyone), you get N concurrent writers contending for the same disk and CPU. For news-digest specifically, the Claude-curation step is rate-limited by API anyway, so writes are naturally staggered. But a pure cron-everything-at-6am pattern hits a stampede. Mitigation: jitter the cron times by tenant ID hash, or process tenants serially in one cron job.

**File descriptor pressure** (already covered) becomes real around the thousand-tenant mark with default ulimits, not at hundreds.

The [SQLite forum thread on thousands of DBs](https://sqlite.org/forum/forumpost/939c555daeb34818) is short but blunt: "SQLite handles this fine; your operating system might not."

---

## 6. Observability across many SQLite files

**Recommendation:** for now, add a `/stats` endpoint extension that walks all tenant DBs once a minute and emits: file size, WAL size, last successful checkpoint timestamp, count of `SQLITE_BUSY` errors since last scrape. Expose as JSON; if you grow into Prometheus later, the same endpoint becomes the scrape target.

**What to watch (production guidance from people doing it):**

- **WAL file size per DB.** Alert if it exceeds 50-100 MB ([sqliteforum](https://www.sqliteforum.com/p/sqlite-performance-monitoring-and), [oneuptime production guide](https://oneuptime.com/blog/post/2026-02-02-sqlite-production-setup/view)). Caught early, this is "a reader is leaking transactions"; caught late, it's "your disk is full and writes are failing."
- **`SQLITE_BUSY` rate.** [sqliteforum guidance](https://www.sqliteforum.com/p/automating-sqlite-health-monitoring): >10/min is anomalous and indicates either checkpoint contention or a misbehaving long writer. In your Rust server, log every `rusqlite::Error::SqliteFailure` with `code == ErrorCode::DatabaseBusy` -- it's currently being silently propagated to a 500.
- **DB file size growth rate.** Sudden growth without sudden traffic = bug. Sustained growth without `VACUUM` = expected (SQLite doesn't reclaim deleted-row pages without VACUUM, see [oneuptime](https://oneuptime.com/blog/post/2026-02-02-sqlite-production-setup/view)).
- **Per-DB query latency p95.** [Turso's per-user analytics post](https://turso.tech/blog/analytics-for-per-user-database-architecture) describes the canonical pattern: emit per-tenant metrics from each DB, aggregate into a central observability DB (in your case, `control.db`).

**Concrete query-side instrumentation ideas:**

- `PRAGMA wal_checkpoint;` returns `(busy, log_size_pages, checkpointed_pages)`. Run on a schedule, log the result. If `busy=1` consistently you have a stuck reader.
- `PRAGMA page_count * PRAGMA page_size` gives DB file size in bytes without stat'ing the file.
- `PRAGMA freelist_count` shows fragmentation; high values mean you should `VACUUM`.
- `sqlite_stat1` and `sqlite_stat4` (after `ANALYZE`) feed query-planner stats; not really for monitoring, but worth running `ANALYZE` weekly so the planner doesn't degrade.

There's no first-class "Prometheus exporter for SQLite" that's de facto standard the way `postgres_exporter` is. The [sqliteforum thread on Prometheus integration](https://www.sqliteforum.com/p/sqlite-performance-monitoring-and) describes the pattern people actually use: a small custom exporter (often a 50-line Python script) that runs the PRAGMAs above against each DB and emits text. The `/stats.json` endpoint your project already has (`circulation/src/stats.rs`) is the natural place to extend.

---

## Recommendation for this project

Concrete plan for the news-digest multi-tenant rollout on CX23:

1. **Storage layout:** `/data/control.db` for the tenant catalog and any cross-tenant aggregate state. `/data/tenants/<vertical_id>/digest.db` per tenant. This matches Litestream's directory-replication pattern if you ever turn that on, and makes `sqlite3_rsync` loops trivial.

2. **Rust server changes:** introduce a `HashMap<TenantId, Arc<DbState>>` in `AppState`, populated lazily on first request to a tenant's routes. Each `DbState` carries the `db_path` only; connections stay per-request as today. No connection pool yet -- revisit when there's measurable contention.

3. **Python writer:** change `db.init()` to take a tenant id and resolve to the right path. Migrations run per-tenant; at 10 tenants this is fast enough not to matter.

4. **Per-connection PRAGMAs to set everywhere** (Rust and Python both, on every new connection):
   - `PRAGMA journal_mode = WAL;` (already on)
   - `PRAGMA busy_timeout = 5000;` (5 s -- the standard recommendation)
   - `PRAGMA journal_size_limit = 67108864;` (64 MB cap)
   - `PRAGMA synchronous = NORMAL;` (safe in WAL; current code doesn't set it explicitly, defaults to FULL which is slower)

5. **End-of-run checkpoint:** at the end of the daily writer cron, after `complete_run()`, run `PRAGMA wal_checkpoint(TRUNCATE);` against the just-written DB. This is the reader-gap moment and prevents WAL drift during the day.

6. **Backups:** start with a nightly cron loop calling `sqlite3_rsync` per tenant to a second Hetzner Storage Box or S3-compatible. 10 DBs * a few seconds = under a minute. Add Litestream only if RPO needs to drop below "yesterday."

7. **Monitoring v1:** extend `circulation/src/stats.rs` to walk `/data/tenants/*/digest.db`, emit file size, WAL size, last `digest_runs.completed_at` per tenant. Surface in the existing `/stats` page. Alert (out of scope for now) when WAL > 50 MB or last completion > 36 h ago.

8. **What to defer:** ATTACH-based admin queries (use Python loops over tenants); a connection pool (no measured need); LiteFS or any distributed-SQLite layer (single host is the constraint; replication can come if/when you outgrow CX23, and at that point you're considering Postgres anyway).

The 10-DB target is well inside the boring zone. The two things that can quietly bite are the long-lived-reader / WAL-growth interaction (mitigated by per-request connections + scheduled TRUNCATE checkpoint + journal_size_limit) and silent corruption from naive file-copy backups (mitigated by `sqlite3_rsync`). Everything else is operational comfort, not survival.
