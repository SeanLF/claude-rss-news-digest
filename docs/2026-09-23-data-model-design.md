# Data model: design before more of the rewrite lands (2026-09-23)

**Status:** proposal, no code changed. Decisions marked *default* hold until Sean rules (section 6).
**Evidence base:** prod clone `data/prod-20260923b.db` (the brief named `prod-20260923.db`; only the `b` copy
exists, runs 1-305, opened read-only), the TypeScript A/B copy `data/ab305-20260923.db`, the box itself
(read-only `df`/`ls`), and a local Postgres harness (Appendix D). `$P` below is
`sqlite3 -readonly data/prod-20260923b.db`.

## 0. The answer in six lines

1. **Keep the product database in SQLite.** Postgres RAM is not the reason (measured: ~10 MiB per 4
   connections, the rest reclaimable cache). The reason is that Temporal's Postgres belongs to the pipeline
   stack, which `python` mode tears down, and readers must survive the pipeline.
2. **Make runs, attempts and model calls first-class**, so a forced or resumed run stops overwriting its own
   record, and the self-improvement loop can ask "every WRITE call under prompt X, with its inputs, output
   and verdict" without parsing artifact names.
3. **Publication gates visibility.** A thread installment, question or issue is public only if its run was
   published. That deletes the retract and sweep machinery instead of turning it into an event log.
4. **Split `digests`** into the public issue and the send record, and stop packing the send claim into a
   status string.
5. **Experiments stay off the box.** Production traces live in `digest.db`; experiment runs, scores and judge
   output live in promptfoo's store on the Mac; human labels and planted keys live in git.
6. **Migration tool follows the model:** plain SQL on SQLite, so dbmate when yoyo's image retires; yoyo until
   then. Nothing destructive before the rollback-to-Python path is retired.

## 1. Scope

**In:** every table in `digest.db`; the Temporal persistence boundary; where eval and training data live.
**Non-goals:** subscriber data (Resend Marketing holds contacts; no table has an email or IP column:
`$P "SELECT m.name,p.name FROM sqlite_master m, pragma_table_info(m.name) p WHERE p.name LIKE '%email%' OR p.name LIKE '%ip%'"`
returns only the count columns `articles_emailed`, `broadcast_recipients`); the source catalogue
(`newsroom/sources.json` stays a file); multi-tenancy; object storage (largest artifact 283 KB).

### 1.1 Who reads and writes what today

R = reads, W = writes, `-` = neither. Circulation opens every production connection `SQLITE_OPEN_READ_ONLY`
(`rg -n "Connection::open" circulation/src`: every non-test open is read-only).

| table | rows | MiB | Python pipeline | TS pipeline | circulation | analytics, bin/ |
|---|---|---|---|---|---|---|
| `digest_runs` | 294 | <0.1 | W R | W R (guard, lifecycle) | R (stats) | R (14 files, `bin/ops`) |
| `digests` | 282 | 14.5 | W R | W R (upsert, send claim) | R (archive, issue, MCP, search join) | R |
| `shown_narratives` (+fts) | 30,063 | 6.5 +3.1 | W R (dedup) | W R (prepare context) | R (archive counts, stats, FTS search) | R (8) |
| `fetched_articles` | 136,143 | 81.6 | W | W R (prepare replays it) | - | R (2) |
| `run_artifacts` | 1,981 | 57.5 | W R | W R (the record) | - | R (6, `bin/trace`, `bin/replay`, evals) |
| `run_usage` | 2,205 | 0.2 | W | W R (`runCost`) | R (/stats cost) | R (5, `bin/usage`) |
| `source_health` | 9,645 | 0.5 | W R | W R (fetch idempotency, alerts) | R | R (4) |
| `dedup_log` | 24,856 | 5.3 | W | W R (count only) | R | R (2) |
| `selections` | 227 | 6.3 | W | W | - | **no reader** |
| `cluster_runs` | 99 | 3.8 | W | W | - | R (3 queries) |
| `threads` | 951 | 0.2 | W R | W R | R | R |
| `thread_installments` | 1,597 | 4.6 | W R | W R | R | R |
| `thread_questions` | 3,116 | 0.9 | W R | W R | R | R |
| `thread_runs` | 98 | <0.1 | W | W, R existence only | - | **counts read only by the parity oracle** |
| `story_feedback` | 37 | <0.1 | - | - | - (route removed) | - |

Sizes: `$P "SELECT name, round(sum(pgsize)/1048576.0,2) FROM dbstat GROUP BY name ORDER BY 2 DESC"`. The
file is 211,456,000 bytes on the box and in the clone (51,625 pages of 4 KiB).

## 2. Requirements

### 2.1 Functional

| # | Need | Served today by | Gap |
|---|---|---|---|
| F1 | Publish one issue per UTC day; archive, issue page, MCP, feed | `digests` | web copy, send state and "last saver" in one row |
| F2 | Threads across days, public pages | `threads`, `thread_installments`, `thread_questions` | unsent runs' installments are public; undo is DELETE/UPDATE across other runs' rows |
| F3 | Search | FTS5 external-content over `shown_narratives` | works |
| F4 | Next-day dedup and context | `shown_narratives`, `fetched_articles` | works |
| F5 | Cost, latency, config per stage | `run_usage` | grain differs between pipelines; TS drops `effort`; attempts share a run |
| F6 | Source health | `source_health` | works |
| F7 | Replay and resume a run from its record | `run_artifacts`, `fetched_articles` | quarantine by renaming; force replaces and loses the prior sample |
| F8 | Self-improvement: prompts, inputs, outputs, verdicts, labels | artifacts + promptfoo + git, joined by hand | no prompt version per call; no call-to-artifact link |
| F9 | Introspection: why a run did what it did | Temporal history (30 days), artifacts, logs | history expires; nothing keeps it |

### 2.2 Non-functional

- **Durability and restore.** `digest.db` is snapshotted to the Mac on every deploy and daily by
  `$INFRA_DIR/bin/backup-volumes` through the SQLite online-backup API, gunzipped and `integrity_check`ed
  (`rg -n "online-backup|integrity_check" ../seanfloyd.dev/bin/backup-volumes`), 14 kept, 63.9 MB each
  gzipped. RPO one day. Temporal's two databases get a nightly `pg_dump`, 14 kept, on the box.
- **RAM.** 2825 MiB free on the box with no digest running; the caps already sum to 2752 (runbook). Any new
  resident process must fit in the remaining 73 MiB or displace a cap.
- **Disk.** Not a constraint: 28 GB free of 38 (`bin/ssh 'df -h /'`). Growth is ~1.9 MiB per TS run
  (artifacts 1.42 on the run-305 A/B, fetch 0.33, the rest under 0.2), about 0.7 GiB a year.
  `sqlite3 -readonly data/ab305-20260923.db "SELECT run_id, count(*), round(sum(length(content))/1048576.0,2) FROM run_artifacts WHERE run_id>=304 GROUP BY 1"`.
- **Concurrency.** One run a day. Inside it, the WRITE fan-out (4) and per-feed fetches write from one
  worker process; circulation opens a connection per request. Both sides already wait 5 s on a lock:
  the TS store sets `busy_timeout`, and rusqlite 0.40 sets 5000 ms on every open
  (`rg -n sqlite3_busy_timeout ~/.cargo/registry/src/*/rusqlite-0.40.2/src/inner_connection.rs`, line 118).
  Spec §5's "the reader has nothing" is wrong.
- **Retention.** Grows by decision (spec §2). Temporal history: 30 days. Everything else: forever.
- **Schema evolution.** Forward-only, applied at deploy after a snapshot. Until Python is deleted, every
  schema change must keep the Python writer working, because the rollback runbook flips back to it.
- **Privacy.** No PII in the database (spec §3). Article text is publisher content.
- **Testability.** TS tests build a temp database from `migrations/` (`digest/src/store/migrated-db.ts`);
  20 non-test TS files run SQL (`rg -l '\.prepare\(' digest/src -g '!*.test.ts' | wc -l`).

### 2.3 Where requirements conflict, and what I'd do

1. **Self-improvement volume vs a 4 GB box.** The conflict is real only if the optimiser runs on the box.
   GEPA-style optimisation is hundreds to thousands of model rollouts; the box has no room for them (the
   worker's 1280 MiB cap is the WRITE fan-out, and 73 MiB are left). Storage is not the problem: a model
   call's metadata is a few hundred bytes, and its inputs and outputs are already artifacts. So the box
   records production traces, and experiments run on the Mac against a clone. Pushing experiment data into
   the prod DB would add backup weight and a write path to prod for no reader there.
2. **Append-only thread history vs "retract must remove public content".** Retraction exists because the
   threads phase writes before the hold (`digest.workflow.ts` runs `threadsPhase` before the send) and
   circulation's thread page shows every installment (`thread.rs`: `LEFT JOIN digests d ON d.run_id = ti.run_id`,
   no filter). Gate visibility on publication and there is nothing public to retract. Measured cost of the
   gate on today's data: zero rows hidden
   (`$P "SELECT count(*) FROM thread_installments i JOIN digest_runs r ON r.id=i.run_id WHERE r.completed_at IS NULL"` → 0).
   A full event log is not needed either: `threads.label` and `threads.last_run_id` equal the latest
   installment for 951 of 951 threads, so the header is a cache of the installments.
3. **One Postgres for everything vs "readers survive any pipeline failure".** Temporal's Postgres is torn
   down in `python` mode, restarted by a pin bump, and capped at 384 MiB beside Temporal. Putting the
   product there makes the public site depend on the pipeline's infrastructure.
4. **Idempotency on output vs force vs "the artifacts are the record".** `replace` is
   `INSERT OR REPLACE`, which destroys the prior sample, and quarantine renames rows to
   `<name>.corrupt.<n>`. The run-305 A/B reran run 305 in place: its `run_usage` holds Python's $5.77 and
   TypeScript's $5.09 under one `run_id`, separable only by `recorded_at`
   (`sqlite3 -readonly data/ab305-20260923.db "SELECT min(recorded_at), count(*), round(sum(api_cost_usd),2) FROM run_usage WHERE run_id=305 GROUP BY effort IS NULL"`),
   which is why `runCost` takes a `since` argument. An attempt dimension fixes this; replacing rows does not.
5. **Web copy vs emailed copy.** `saveDigest` upserts `html` and `run_id` before the send claim. A forced
   re-run of a day already sent replaces the archived issue, then fails at the claim (`SendClaimed`), so the
   web archive shows a version nobody was emailed. Question 2 below.
6. **A stale premise, not a conflict.** Spec §5 keeps WAL off "unless the backup becomes WAL-aware". It is:
   `backup-volumes` and the staged refresh both use the online-backup API. Two things still break under
   WAL: `bin/db-clone --live` copies the file with `cat`, and the dead-man mounts the volume `:ro`
   (`news-digest.tf:414`), where a WAL reader may be unable to create `-shm` (unverified; test it).

## 3. Options

| | A. SQLite, cleaned up | B. Product DB in the shared Postgres | C. A, plus eval data off the box |
|---|---|---|---|
| Extra RAM on the box | 0 (in-process; circulation 3.8 MiB RSS, spec §3) | measured +~10 MiB anon for 4 connections, shmem grows toward the 128 MiB `shared_buffers` shared with Temporal, page cache reclaimable (Appendix D) | same as A |
| Disk | 202 MiB, +~0.7 GiB/yr | 165 MiB loaded (TOAST compresses artifacts 57.5 → 28 MiB) | same as A |
| Readers during a pipeline outage | unaffected (a file) | down whenever the Temporal stack is (teardown, pin bump, OOM at 384 MiB) | unaffected |
| Rewrite cost | additive migrations, some table rebuilds | circulation from rusqlite to a pooled PG client, FTS5 to `tsvector` (ranking changes); 60 SQLite date/FTS calls in circulation outside `util.rs`, 39 of them in `stats.rs`, inline tests included (`rg -c 'datetime\(|strftime\(|date\(|MATCH' circulation/src`); 8 of 16 analytics queries (`rg -l 'datetime\(|julianday|json_each' analytics/queries`); TS tests need a PG | A |
| When it can happen | now, additively | only after Python stops writing, and it breaks the rollback to Python | now |
| Backups | exists and is verified | add `digest` to the nightly `pg_dump`; the Mac-side clone and analytics workflow move to `pg_restore` | A |
| What it buys | fixes the smells | MVCC, `jsonb`, `ALTER ... ADD CONSTRAINT` | A, plus a clean line between record and experiment |

Postgres wins on nothing this pipeline measures: one writer a day, no lock contention observed, 5 s busy
timeouts on both sides. Split variants (product in SQLite, eval data in Postgres on the box) put experiment
data on the machine that cannot run the experiments. **Recommendation: C.**

## 4. Recommendation: the model

Principles, each tied to a smell:
- **Run → attempt → call → artifact.** A run is a day's issue attempt lineage; an attempt is one execution
  (Python process or Temporal workflow run); a call is one model request; artifacts belong to the attempt
  that produced them.
- **Rows are appended; state is derived.** Where today's code UPDATEs another run's row (thread labels,
  question resolution) the new model inserts, and a view derives the current state.
- **Published is one predicate, written once.** `published_runs` is the only definition of "readers got
  this run"; thread pages, the linker's context, dedup and stats all read it.
- **The workflow owns lifecycle transitions; the database refuses illegal ones** (triggers), and Temporal is
  asked, not guessed, whether a `running` row is alive.

### 4.1 Runs and attempts

```sql
-- digest_runs keeps its name and ids (14 analytics files, circulation, bin/ops read it).
ALTER TABLE digest_runs ADD COLUMN outcome TEXT;  -- sent|disabled|rejected|held_out|no_stories; NULL unless status='completed'
-- completed_at keeps its current meaning, "published", and is renamed published_at with the web rewrite.

CREATE TABLE run_attempts (
  id              INTEGER PRIMARY KEY,
  run_id          INTEGER NOT NULL REFERENCES digest_runs(id),
  pipeline        TEXT NOT NULL CHECK (pipeline IN ('python','temporal')),
  workflow_id     TEXT,                       -- digest-<date>
  workflow_run_id TEXT UNIQUE,                -- replaces digest_runs.workflow_run_id (unshipped)
  git_sha         TEXT,
  forced          INTEGER NOT NULL DEFAULT 0,
  started_at      TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at        TEXT,
  state           TEXT NOT NULL DEFAULT 'running' CHECK (state IN ('running','finished','failed','closed_by_temporal')),
  error           TEXT
) STRICT;
```

**Status state machine** (`digest_runs.status`, enforced by BEFORE UPDATE triggers that `RAISE(ABORT)`):

```
 INSERT ──▶ running ──finishRun(outcome)──▶ completed{sent | disabled | rejected | held_out | no_stories}
              │  ▲                                 │ outcome ≠ sent
     abortRun │  │ resume (new attempt)            └──── resume ──▶ running
              ▼  │
            failed ◀── reconciler: row running, Temporal says the execution closed
```

- Legal: `running→completed` (outcome required), `running→failed`, `failed→running`,
  `completed→running` only when `outcome<>'sent'`. `completed` with `outcome='sent'` is terminal: a forced
  re-run of a sent day is a new run, never a rewrite of this one.
- TS's `disabled`, `rejected`, `skipped`, `held-out` status values move to `outcome` (they have never reached
  prod: `$P "SELECT status, count(*) FROM digest_runs GROUP BY 1"` → running 2, completed 290, failed 2).
- Owners: `startRun`, `finishRun`, `abortRun` activities, and one reconciler at `startRun` that asks Temporal
  about `running` rows with a `workflow_run_id`. That replaces the 4 h liveness guess in `startRun`, once
  Python no longer writes. The two Python orphans (runs 123, 281) are backfilled to `failed`.

### 4.2 Artifacts

```sql
ALTER TABLE run_artifacts ADD COLUMN attempt_id INTEGER REFERENCES run_attempts(id);
ALTER TABLE run_artifacts ADD COLUMN state  TEXT NOT NULL DEFAULT 'current'; -- current|quarantined|replaced
ALTER TABLE run_artifacts ADD COLUMN sha256 TEXT;
ALTER TABLE run_artifacts ADD COLUMN stage  TEXT;   -- write, cluster-extract, thread_synthesis ...
ALTER TABLE run_artifacts ADD COLUMN kind   TEXT;   -- input|output|thinking|health|trace
ALTER TABLE run_artifacts ADD COLUMN branch TEXT;   -- s01, b12, t634 (today encoded in the name)
DROP INDEX idx_run_artifacts_run_name;
CREATE UNIQUE INDEX idx_run_artifacts_current ON run_artifacts(run_id, artifact_name) WHERE state = 'current';
```

- The pointer contract `(run_id, artifact_name, sha256)` is unchanged; `find`/`get` read `state='current'`.
- Quarantine becomes `UPDATE ... SET state='quarantined'`; force becomes "mark `replaced`, insert new". The
  prior sample survives and is attributed to its attempt.
- Content stays inline. Measured 1.42 MiB per TS run, max 283 KB (`article_index.json`).
- `stage/kind/branch` are backfilled from the name patterns in Appendix C and written by the TS store.
- Retire the duplicates: `selections` equals `selections.json` and `cluster_runs` equals `clusters.json`
  for 99 of 99 overlapping runs
  (`$P "SELECT count(*) FROM selections s JOIN run_artifacts a ON a.run_id=s.run_id AND a.artifact_name='selections.json' WHERE a.content=s.selections_json"`).
  128 older runs exist only in `selections`; copy them into `run_artifacts` first.
- **Temporal history is archived** as a `temporal_history.json` artifact of the previous execution, by the
  next `startRun`: 30-day retention otherwise erases the only record of retries, signal payloads and hold
  timings. Fixture histories are 71-175 KB for 101-241 events (`ls -la digest/src/workflow/histories/`);
  a real run's size is unverified.

### 4.3 Model calls and prompts (what self-improvement needs from prod)

```sql
CREATE TABLE prompts (sha256 TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, first_run_id INTEGER) STRICT;

ALTER TABLE run_usage ADD COLUMN attempt_id      INTEGER REFERENCES run_attempts(id);
ALTER TABLE run_usage ADD COLUMN branch          TEXT;
ALTER TABLE run_usage ADD COLUMN prompt_sha      TEXT REFERENCES prompts(sha256); -- system prompt + tool config as sent
ALTER TABLE run_usage ADD COLUMN input_manifest  TEXT;  -- JSON [{name, sha256}] of artifacts materialised for the call
ALTER TABLE run_usage ADD COLUMN output_artifact TEXT;
ALTER TABLE run_usage ADD COLUMN result          TEXT;  -- ok|invalid|error|timeout
```

- `run_usage` keeps its name because circulation's /stats reads it; it becomes the call log. TS already
  writes one row per call (18 `cluster-extract`, 15 `write` on run 305); Python wrote one per stage. Queries
  that count rows per stage must count calls from the cut-over on.
- Fix in the same change: TS never writes `effort`, so `analytics/queries/config-drift.sql` will report
  "(not recorded)" for every TS row (`rg -n effort digest/src/store/usage.ts` → no match).
- Prompts are versioned by content hash, not by `git_sha`: local, A/B and replay runs have no trustworthy
  image SHA. Ten prompts at ~10 KB per version cost nothing.
- With these columns the GEPA training set is a query: calls by `stage` and `prompt_sha`, inputs by
  manifest, output by artifact, verdict from `coherence_report.json` of the same attempt. Parsing the
  verdict stays inside the four allowed reasons (control flow on verdicts).
- **Not in prod:** promptfoo runs, judge transcripts and scores (promptfoo's own SQLite, `~/.promptfoo/promptfoo.db`:
  115 evals, 2,853 results, 60 MB, Drizzle-migrated), Sean's adjudications and planted keys (git,
  `docs/proposed/gate-fixtures/`, `docs/proposed/coherence-planted/`). Labels are small, need review and
  want diffs; git gives all three. The one experiment-side table that might earn a place later is a
  `labels` table joining human verdicts to `(run, story)`; not until a harness needs the join.

### 4.4 Issues and broadcasts (replace `digests`)

```sql
CREATE TABLE issues (
  date         TEXT PRIMARY KEY,
  run_id       INTEGER NOT NULL REFERENCES digest_runs(id),  -- the run readers got
  html         TEXT NOT NULL,
  preheader    TEXT NOT NULL DEFAULT '',
  published_at TEXT NOT NULL
) STRICT;

CREATE TABLE broadcasts (
  date          TEXT PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES digest_runs(id), -- the claiming run (today's broadcast_run_id)
  claim_token   TEXT,            -- today packed into broadcast_status as 'claimed <iso> <uuid>'
  claimed_at    TEXT NOT NULL,
  resend_id     TEXT UNIQUE,
  status        TEXT NOT NULL CHECK (status IN ('claimed','draft','queued','sending','sent','failed')),
  recipients    INTEGER,
  email_artifact TEXT NOT NULL DEFAULT 'email.html'          -- (run_id, name) in run_artifacts
) STRICT;

CREATE VIEW published_runs AS
  SELECT id AS run_id FROM digest_runs WHERE completed_at IS NOT NULL                 -- Python-era and sent
  UNION SELECT run_id FROM broadcasts WHERE status IN ('queued','sending','sent');    -- landed, record step failed

CREATE VIEW digests AS  -- compatibility for circulation until the web rewrite
  SELECT i.date, i.html, i.preheader, i.run_id, b.resend_id AS broadcast_id, b.status AS broadcast_status,
         b.recipients AS broadcast_recipients, b.run_id AS broadcast_run_id
  FROM issues i LEFT JOIN broadcasts b USING (date);
```

- `published_runs` encodes the rule `retract()` already uses (`mayHaveGone`: accepted states or a claim), once.
- `digest_runs.articles_emailed` duplicates `broadcasts.recipients`; it stays until the web rewrite
  because /stats reads it.
- Whether a forced re-run may replace `issues.html` is question 2; the default is that it may not, and the
  re-run's html stays an artifact.

### 4.5 Threads

```sql
CREATE TABLE threads (id INTEGER PRIMARY KEY, created_run_id INTEGER NOT NULL, merged_into INTEGER REFERENCES threads(id));
CREATE TABLE thread_installments (
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id),
  run_id INTEGER NOT NULL REFERENCES digest_runs(id), cluster_story TEXT NOT NULL,
  continued INTEGER NOT NULL,   -- today's matched_score: NULL or 1.0, read only as IS NOT NULL
  content TEXT, UNIQUE (thread_id, run_id));
CREATE TABLE thread_questions (id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, question TEXT NOT NULL, raised_run_id INTEGER NOT NULL);
CREATE TABLE thread_question_resolutions (question_id INTEGER NOT NULL REFERENCES thread_questions(id),
  run_id INTEGER NOT NULL, how TEXT NOT NULL, PRIMARY KEY (question_id, run_id));
CREATE VIEW thread_state AS ...  -- label, first/last run, active|dormant|merged, from published installments
                                 -- (dormancy: last N published runs, today's `dormant_after`)
```

- Every row belongs to the run that wrote it. A forced re-run of run N deletes run N's rows and nothing
  else, inside one transaction; the "later runs build on it" refusal (`dependentRuns`) stays.
- Readers (thread pages, the linker's context, synthesis history) see rows whose run is in
  `published_runs`, plus the current run's own. `retractAbandoned` and the abandoned-run sweep are deleted.
- `thread_runs` goes: outside the parity oracle its counts have no reader (`rg -n "FROM thread_runs" digest/src newsroom circulation/src analytics bin -g '!*test*'`
  → one existence check, plus `newsroom/tools/threads_oracle.py`) and `thread_health.json` holds the same
  numbers. `threads.slug` goes: written, and read only by that oracle. The oracle compares the two pipelines,
  so it retires with Python; that is why both drops wait for step 3.
- **Takedown.** If a publisher or person asks for content to come down, the path is a manual redaction
  (`content` replaced by a tombstone, logged), not the run-undo machinery. Not built; noted.

### 4.6 Everything else

- `shown_narratives` stays, including FTS5. It is one row per (story, source): 30,063 rows for 12,013
  stories, 2.5×. Normalising it saves 6.5 MiB and some `COUNT(DISTINCT headline)`; not worth doing before
  the web rewrite.
- `fetched_articles`, `source_health`, `dedup_log` stay. Drop `dedup_log.action` (24,856 of 24,856 are
  `filtered`). Keep `threshold`: it records a parameter that will change.
- PRAGMAs: WAL on (after the two fixes in 2.3 item 6); `foreign_keys=ON` in the TS `openDb`, which Python
  sets and TS does not (`rg -n foreign_keys digest/src` → no match; the clone has 0 violations,
  `$P "PRAGMA foreign_key_check"` returns nothing).
- Seven redundant indexes, ~4 MiB: three on `shown_narratives(shown_at)`, pairs on `run_id` for
  `fetched_articles`, `shown_narratives`, `source_health`, `selections`, and `idx_digests_date` on a primary key
  (`$P ".indexes shown_narratives"`).

## 5. Migration path

The constraint: until `newsroom/` is deleted, the rollback runbook can flip back to Python, so every step
before that must keep the Python writer working. Staged mode refreshes its scratch DB from the migrated
`digest.db`, so the three gate days exercise the new columns under TypeScript while Python proves backwards
compatibility on prod. Every step runs after the deploy's verified snapshot, and each has a stated inverse.

| step | when | change | Python still works because | inverse |
|---|---|---|---|---|
| 0 | before the next deploy | fold the **unshipped** `20260923120000` (`digest_runs.workflow_run_id`) into `run_attempts`; keep `20260923180000` (`broadcast_run_id`) as the future `broadcasts.run_id`. Both are unapplied on prod (`$P "SELECT migration_id FROM _yoyo_migration ORDER BY 1 DESC LIMIT 1"` → `20260916190000`) | nothing shipped | edit the files |
| 1a | with plan A | `run_attempts`, `prompts`, new `run_usage` and `run_artifacts` columns, partial unique index; TS writes them | Python leaves NULLs; its `INSERT OR REPLACE` hits the partial index for `state='current'` rows (unverified: needs a test) | drop columns/tables, restore the full index |
| 1b | with plan A | `digest_runs.outcome` + transition triggers; an AFTER trigger sets `outcome='sent'` when a write sets `completed` without one (Python only completes on send) | Python's three writes are all legal | drop triggers and column |
| 1c | with plan A | `published_runs` view; circulation's thread queries and the TS linker filter on it | read-side only | revert the queries |
| 1d | with plan A | WAL, after `db-clone --live` uses the backup API and the `:ro` dead-man is tested | Python does not care about journal mode | `PRAGMA journal_mode=DELETE` |
| 1e | anytime | drop the seven redundant indexes; TS `foreign_keys=ON`; TS records `effort` | | recreate |
| 2 | cut-over day | **no schema change.** The flip and its rollback stay a config change | | |
| 3 | after the rollback path is retired (question 4) | `issues` + `broadcasts` + `digests` view; threads restructure; drop `selections` (after backfilling 128 runs), `cluster_runs` (after repointing 3 analytics queries), `thread_runs`, `story_feedback`, `threads.slug`, `dedup_log.action`; delete the retract and sweep code | Python is gone | restore from the pre-migration snapshot (the deploy's rollback path today) |
| 4 | with plan B, web rewrite | rename `completed_at`→`published_at`, drop compatibility views, optionally normalise stories | | |

**Tests the path needs:** transition triggers (every legal and illegal edge); the `digests` view returns
the old table's rows for every date on a prod clone; circulation's suite against a migrated clone; one
Python run in CI against the step-1 schema.

**What the model implies for the migration tool.** Plain SQL on SQLite with triggers, partial indexes,
views, table rebuilds for constraints, and FTS5. That rules out an ORM as schema owner: Drizzle Kit or
Kysely would make TypeScript the source of truth for a schema the Rust tier reads until plan B, and
neither models FTS5 triggers (unverified for both; they would sit in raw SQL anyway). node-pg-migrate is
Postgres-only. **dbmate** fits: one binary, plain SQL with up/down blocks, SQLite and Postgres, a
`schema_migrations(version)` table, maintained (pushed 2026-09-23, 7.4k stars:
`gh repo view amacneil/dbmate --json pushedAt,isArchived`). The switch is cheap because yoyo keys applied
migrations on `sha256(migration_id)`, not content (`printf %s 20260916190000_add_threads_merged_into | shasum -a 256`
equals the stored hash), so the 27 files can gain `-- migrate:up` markers while yoyo still runs them; at
step 3, seed `schema_migrations` with the 27 versions and swap the runner. `migrated-db.ts` must then apply
only the up block. Until step 3, keep yoyo: it runs in the newsroom image, which the rollback path needs
anyway.

## 6. Questions for Sean, most costly-if-wrong first

1. **SQLite for the product database, Postgres for Temporal only?** Default yes. If wrong the other way, the
   cost is a circulation port and tying readers to the pipeline stack.
2. **May a forced re-run replace a sent day's public issue?** Today it does, then fails at the send claim,
   so the archive shows an issue nobody got. Default: no; the issue is what was sent, and replacing it is an
   explicit operator step.
3. **Are an unsent run's thread installments private until it is published?** Default yes (0 rows affected
   today). This is what lets the retract and sweep code go.
4. **When is the rollback to Python retired?** Destructive steps wait for it. Default: when `newsroom/` is
   deleted per spec §7.5, not at cut-over.
5. **Retention: keep everything?** Default yes: ~0.7 GiB a year against 28 GB free; the thinking traces
   are the only candidates for pruning and are ~0.3 MiB per run.
6. **Experiments and labels off the box (promptfoo store on the Mac, labels in git)?** Default yes.
7. **`story_feedback`'s 37 rows (last vote 2026-07-05):** export to a CSV under `docs/` and drop?
   Default yes. The compose comment calling the circulation mount rw "for /feedback" is stale too.

## Appendix A: reproducers

```sh
P='sqlite3 -readonly data/prod-20260923b.db'
$P "SELECT artifact_name, count(*), round(avg(length(content))/1024.0,1), round(sum(length(content))/1048576.0,2) FROM run_artifacts GROUP BY 1 ORDER BY 4 DESC"
$P "SELECT count(*), (SELECT count(*) FROM (SELECT DISTINCT run_id, headline FROM shown_narratives)) FROM shown_narratives"   # 30063 / 12013
$P "SELECT count(*), sum(t.label=(SELECT cluster_story FROM thread_installments i WHERE i.thread_id=t.id ORDER BY run_id DESC, id DESC LIMIT 1)), sum(t.last_run_id=(SELECT max(run_id) FROM thread_installments i WHERE i.thread_id=t.id)) FROM threads t"   # 951 951 951
$P "SELECT count(*) FROM selections s WHERE NOT EXISTS (SELECT 1 FROM run_artifacts a WHERE a.run_id=s.run_id AND a.artifact_name='selections.json')"   # 128
$P "SELECT action, count(*) FROM dedup_log GROUP BY 1"                   # filtered 24856
$P "SELECT count(*), max(created_at) FROM story_feedback"                # 37, 2026-07-05
rg -n "(FROM|JOIN)\s+selections\b" newsroom/src digest/src circulation/src analytics bin -g '!*test*'   # docstrings only
```

## Appendix B: columns nothing reads, or that mislead

Grep scope for every row: `digest/src circulation/src newsroom/src bin analytics`, tests excluded.

| column | evidence | action |
|---|---|---|
| `selections.*` (table) | no `FROM/JOIN selections` outside docstrings | drop after backfill (step 3) |
| `cluster_runs.*` (table) | read by 3 analytics queries only; equals `clusters.json` 99/99 | repoint, drop |
| `thread_runs.threads_synthesized`, `audit_failures` | read only by `threads_oracle.py` (parity); Python alerts from an in-memory count | drop with Python |
| `threads.slug` | written by both pipelines, read only by `threads_oracle.py` (parity) | drop with Python |
| `threads.label`, `last_run_id`, `status`, `first_run_id` | derivable, 951/951 | become `thread_state` |
| `thread_installments.matched_score` | read only as `IS NOT NULL` (`run-health.ts:148`, `db.py:527`) | becomes `continued` |
| `shown_narratives.cluster_id` | written, never read in SQL; 10,855 of 30,063 set | keep for now; drop with normalisation |
| `shown_narratives.shown_at` | duplicates `digest_runs.run_at` via `run_id`; read by /stats | keep until web rewrite |
| `digests.created_at` | no reader (`rg -n created_at circulation/src`: two test-schema lines) | drop with the split |
| `digests.broadcast_status` | also carries the claim token as `'claimed <iso> <uuid>'` | `broadcasts.claim_token` |
| `digests.run_id` | "last saver", not publisher; circulation joins on it for search and threads | `issues.run_id` = publisher |
| `digest_runs.articles_emailed` | is the recipient count (the TS code says the column is misnamed); duplicates `broadcast_recipients` | keep for /stats; rename with web rewrite |
| `digest_runs.git_sha` | read by `bin/ops` only; not a prompt version for local runs | keep; `prompts` does the versioning |
| `run_usage.effort` | read by `config-drift.sql`; never written by TS | write it |
| `dedup_log.action` | constant `filtered` | drop |
| `story_feedback.*` | no writer or reader since the route was removed | export, drop |
| redundant indexes | `idx_shown_at`, `idx_shown_narratives_date`, `idx_shown_narratives_shown_at`; `_run` vs `_run_id` pairs; `idx_digests_date` | drop |

## Appendix C: `run_artifacts` naming conventions (the data hidden in names)

From both DBs (`$P "SELECT DISTINCT artifact_name FROM run_artifacts"` and the same on `ab305`):

| pattern | stage | kind | branch |
|---|---|---|---|
| `articles_<n>.csv`, `sources.csv`, `article_index.json`, `recent_rss_titles.csv`, `recent_digest_headlines.txt`, `yesterday_headlines.txt`, `thread_context.json` | prepare | input | chunk n |
| `cluster_tags_b<n>.json` | cluster-extract | output | b n |
| `cluster_tags.json`, `clusters.json`, `cluster_health.json`, `cluster_cohesion.json` | cluster | output, health | |
| `recap.txt`, `weekly_recap.txt` | recap | output | |
| `selected.json` | select | output | |
| `draft_s<nn>.json`, `thinking_write_s<nn>.txt`, `write_branches.json` | write | output, thinking, health | s nn |
| `draft_selections.json`, `preheader.json`, `selections.json` | assemble, preheader | output | |
| `coherence_report.json`, `thinking_coherence.txt` | coherence | output, thinking | |
| `repair_requests.json`, `repair_resolution.json`, `thinking_repair_recheck.txt` | repair | output, thinking | |
| `thread_links.json`, `thread_assignments.json`, `thread_synthesis_t<id>.json`, `thread_installments.json`, `thread_health.json` | threads | output, health | t id |
| `article_fulltext.json`, `fulltext_health.json`, `gnews_links.json`, `gnews_health.json` | fulltext, gnews | output, health | |
| `digest.html`, `email.html`, `render_context.json` | render | output | |
| `models.json`, `force_undo_threads.json` | run | trace | |
| `<any>.corrupt.<n>` | as base | quarantined | becomes `state` |

## Appendix D: Postgres RAM harness

Question: what does a `digest` database cost inside the Postgres that Temporal already runs? Prod image
and cap (`postgres:18.6-alpine3.24`, `--memory 384m`, default `shared_buffers` 128 MB), eleven prod-clone
tables loaded by `\copy`, indexes including a GIN `tsvector` for search, then 4 concurrent connections
running the archive, search, issue, stats, prepare-replay and thread queries 40 times each. Measured with
`docker stats` and the container's `memory.stat`. Local arm64 under OrbStack, no Temporal load on the same
instance; not measured on the box.

| state | docker stats | anon | shmem | file (reclaimable) |
|---|---|---|---|---|
| empty cluster, idle | 22 MiB | 5 | 15 | 57 |
| digest loaded, restarted, idle | 30 MiB | 5 | 15 | 24 |
| 4 idle connections (control) | 41 MiB | 15 | 16 | 26 |
| 4 × 40 query files | 36-41 MiB | 5-7 | 27-29 | 40 → 250 |

Negative controls: the idle-connection row moves `anon` by 10 MiB, and the workload moves `file` by
~225 MiB, so the instrument sees both kinds of memory. Reading: resident cost is ~10 MiB of backends while
connected; `shmem` grows toward `shared_buffers`, which is shared with Temporal and capped; the rest is
page cache the kernel reclaims under the 384 MiB cap. The database is 165 MiB (`pg_database_size`), with
`run_artifacts` at 28 MiB against 57.5 in SQLite because TOAST compresses the blobs.

Harness (inputs exported with `sqlite3 -readonly -csv data/prod-20260923b.db "SELECT * FROM <table>"`; the
schema mirrors the SQLite column order with `text`/`bigint` types):

```sh
C=dm-pg; docker run -d --name $C --memory 384m -e POSTGRES_PASSWORD=x -v "$PWD":/w:ro postgres:18.6-alpine3.24
q() { docker exec -i $C psql -q -U postgres -d "$1" -v ON_ERROR_STOP=1 "${@:2}"; }
m() { docker exec $C cat /sys/fs/cgroup/memory.stat | awk '$1~/^(anon|file|shmem)$/{printf "%s=%.0fMiB ",$1,$2/1048576}'; docker stats --no-stream --format '{{.MemUsage}}' $C; }
q postgres -c "CREATE DATABASE digest"; q digest -f /w/schema.sql
for t in digest_runs digests shown_narratives fetched_articles run_artifacts run_usage source_health dedup_log threads thread_installments thread_questions; do q digest -c "\\copy $t FROM '/w/$t.csv' CSV"; done
q digest -f /w/post.sql; docker restart $C; sleep 8; m
for i in 1 2 3 4; do q digest -c "SELECT pg_sleep(10)" >/dev/null & done; sleep 5; m; wait
for i in 1 2 3 4; do { for k in $(seq 40); do q digest -f /w/queries.sql >/dev/null; done; } & done; sleep 12; m; wait; m
docker rm -fv $C
```
