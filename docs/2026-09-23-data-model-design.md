# Data model: design before more of the rewrite lands (2026-09-23)

**Status:** decided. Sean answered section 6 the same day; sections 0, 3, 4.4, 5 and 6 are revised to
match, and he then chose Postgres (next section), which supersedes the SQLite parts.
**Evidence base:** prod clone `data/prod-20260923b.db` (the brief named `prod-20260923.db`; only the `b` copy
exists, runs 1-305, opened read-only), the TypeScript A/B copy `data/ab305-20260923.db`, the box itself
(read-only `df`/`ls`), and a local Postgres harness (Appendix D). `$P` below is
`sqlite3 -readonly data/prod-20260923b.db`.

## Decided later the same day: Postgres

Sean chose **Postgres** for the product database (2026-09-23), overriding item 1 below. Section 3 had
already called it close: once Python retires and circulation is rewritten anyway, the import costs the
same into either engine, and Postgres can share Temporal's backup and restore path (once `digest` is
added to its nightly `pg_dump`), and brings `jsonb` and readers that need no shared volume mount. What follows from it:
- **Same server, same major.** A `digest` database with its own role in the Postgres that Temporal already
  runs (`postgres:18.6-alpine3.24`, 384 MiB cap). Appendix D measured the resident cost at ~10 MiB for 4
  connections.
- **Circulation is ported to TypeScript before the cut-over**, as a separate unit after the schema. The
  new schema therefore has no compatibility views (`digests`, `threads`, ...) and does not keep old
  names for circulation's sake: the TypeScript web tier reads it directly. The Rust circulation keeps
  reading the legacy SQLite file until the cut-over retires it.
- **Search** is a weighted `tsvector` column with a GIN index instead of FTS5. Ranking changes:
  `ts_rank` weighs term frequency and the A/B weights, not BM25, so result order will differ from today.
- **Tests** run in-process on PGlite (Postgres 18.3 in WebAssembly), which runs everything the schema
  uses: plpgsql triggers, generated `tsvector` columns, GIN, and partial unique indexes. The import runs
  against a real `postgres:18.6` container.
- **Import**: SQLite → Postgres (§5.1 still holds, except the circulation notes).

## 0. The answer in six lines

1. ~~**Keep the product database in SQLite.**~~ Superseded: Postgres, above. (Was: neither RAM (measured:
   ~10 MiB per 4 connections, the rest reclaimable cache) nor downtime (the site is not HA, Sean
   2026-09-23) decides it; Postgres buys nothing this pipeline measures and costs a circulation port.)
2. **Make runs, attempts and model calls first-class**, so a forced or resumed run stops overwriting its own
   record, and the self-improvement loop can ask "every WRITE call under prompt X, with its inputs, output
   and verdict" without parsing artifact names.
3. **Publication gates visibility.** A thread installment, question or issue is public only if its run was
   published. That deletes the retract and sweep machinery instead of turning it into an event log.
4. **Split `digests`** into the public issue and the send record, and stop packing the send claim into a
   status string.
5. **Experiments stay off the box.** Production traces live in `digest.db`; experiment runs, scores and judge
   output live in promptfoo's store on the Mac; human labels and planted keys live in git.
6. **No migration chain into the new shape.** Python retires at the deploy that switches to TypeScript, so
   the new schema is written fresh and today's data is imported once, by a script tested on the prod clone.
   dbmate owns the new schema; yoyo retires with Python.

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

- **Durability and restore.** (For Postgres: the nightly `pg_dump` covers Temporal's two databases only;
  `digest` must be added to it and to the deploy snapshot.) `digest.db` is snapshotted to the Mac on every deploy and daily by
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
- **Schema evolution.** Forward-only, applied at deploy after a snapshot. Python retires at cut-over, so the
  new schema owes it nothing; the rollback is the old file plus the Python tag (section 5, step 4).
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
   product there makes the public site depend on the pipeline's infrastructure. *Resolved by Sean's
   answers:* downtime is acceptable and `python` mode retires, so this no longer decides it (section 3).
4. **Idempotency on output vs force vs "the artifacts are the record".** `replace` is
   `INSERT OR REPLACE`, which destroys the prior sample, and quarantine renames rows to
   `<name>.corrupt.<n>`. The run-305 A/B reran run 305 in place: its `run_usage` holds Python's $5.77 and
   TypeScript's $5.09 under one `run_id`, separable only by `recorded_at`
   (`sqlite3 -readonly data/ab305-20260923.db "SELECT min(recorded_at), count(*), round(sum(api_cost_usd),2) FROM run_usage WHERE run_id=305 GROUP BY effort IS NULL"`),
   which is why `runCost` takes a `since` argument. An attempt dimension fixes this; replacing rows does not.
5. **Web copy vs emailed copy.** `saveDigest` upserts `html` and `run_id` before the send claim. A forced
   re-run of a day already sent replaces the archived issue, then fails at the claim (`SendClaimed`), so the
   web archive shows a version nobody was emailed. Decided: replace, never re-send (section 4.4).
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
| When it can happen | at cut-over, by import | at cut-over, by import (was: only after Python retires) | at cut-over |
| Backups | exists and is verified | add `digest` to the nightly `pg_dump`; the Mac-side clone and analytics workflow move to `pg_restore` | A |
| What it buys | fixes the smells | MVCC, `jsonb`, `ALTER ... ADD CONSTRAINT` | A, plus a clean line between record and experiment |

Postgres wins on nothing this pipeline measures: one writer a day, no lock contention observed, 5 s busy
timeouts on both sides. Split variants (product in SQLite, eval data in Postgres on the box) put experiment
data on the machine that cannot run the experiments. **Recommendation: C.**

**After Sean's answers.** Downtime is acceptable and Python retires at cut-over, so the "readers
unaffected" and "when it can happen" rows no longer separate A from B, and a fresh schema plus a one-shot
import costs the same into either engine. That makes cut-over the cheapest moment Postgres will ever have.
Circulation is being ported to TypeScript anyway (spec, plan B), so its 60 SQLite date/FTS calls get
rewritten whichever engine is chosen; that cost no longer separates the options either. What is left:
SQLite keeps tests in-process on `node:sqlite` with no server, backups as a file, and FTS5 ranking as today;
Postgres gives one backup and restore path shared with Temporal, `jsonb`, and readers that need no shared
volume mount. **A close call; SQLite by a small margin, on test speed and fewer moving parts.** Postgres is
defensible if Sean prefers one engine on the box. The Mac already queries live prod (`bin/ops` over SSH, the
MCP surface) or clones it (`make db-clone`), so remote access argues for neither. Revisit if a second writer
appears (the web tier writing subscriptions or feedback).

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
CREATE TABLE issues (          -- append-only: a forced re-run adds a revision, never overwrites
  date         TEXT NOT NULL,
  revision     INTEGER NOT NULL,                             -- 1 = first publication
  run_id       INTEGER NOT NULL REFERENCES digest_runs(id),  -- the run that produced this revision
  html         TEXT NOT NULL,
  preheader    TEXT NOT NULL DEFAULT '',
  published_at TEXT NOT NULL,
  PRIMARY KEY (date, revision)
) STRICT;
-- the web serves the highest revision; broadcasts.revision says which one subscribers got

CREATE TABLE broadcasts (
  date          TEXT PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES digest_runs(id), -- the claiming run (today's broadcast_run_id)
  claim_token   TEXT,            -- today packed into broadcast_status as 'claimed <iso> <uuid>'
  claimed_at    TEXT NOT NULL,
  resend_id     TEXT UNIQUE,
  revision      INTEGER NOT NULL,                              -- the issues revision that was emailed
  status        TEXT NOT NULL CHECK (status IN ('claimed','draft','queued','sending','sent','failed')),
  recipients    INTEGER,
  email_artifact TEXT NOT NULL DEFAULT 'email.html',         -- (run_id, name) in run_artifacts
  FOREIGN KEY (date, revision) REFERENCES issues(date, revision)
) STRICT;

-- published = on the web or emailed (Sean, 2026-09-23). 'queued' and 'sending' count: once Resend has the
-- broadcast it cannot be recalled, so its threads are about to be public anyway.
CREATE VIEW published_runs AS
  SELECT run_id FROM issues
  UNION SELECT run_id FROM broadcasts WHERE status IN ('queued','sending','sent');

CREATE VIEW digests AS  -- compatibility for circulation until the web rewrite
  SELECT i.date, i.html, i.preheader, i.run_id, b.resend_id AS broadcast_id, b.status AS broadcast_status,
         b.recipients AS broadcast_recipients, b.run_id AS broadcast_run_id
  FROM issues i LEFT JOIN broadcasts b USING (date)
  WHERE i.revision = (SELECT max(revision) FROM issues WHERE date = i.date);
```

- `published_runs` encodes the rule `retract()` already uses (`mayHaveGone`: accepted states or a claim), once.
- `digest_runs.articles_emailed` duplicates `broadcasts.recipients`; it stays until the web rewrite
  because /stats reads it.
- A forced re-run of a sent day publishes a new revision to the web and never re-sends (Sean,
  2026-09-23). The broadcast claim is per date, so the send step already refuses; the change is that the
  run then ends `published`, not `failed`. The page can show "updated since the email" when the served
  revision is newer than `broadcasts.revision`. An operator-approved notice to subscribers is a later
  option, not built.
- An `issues` row is the web publication, so it is inserted after the pre-send hold is approved, never
  before; otherwise `published_runs` would expose a held run's threads. Today `saveDigest` writes the web
  copy before the send claim; whether that is before or after the hold in the TS workflow is unverified.

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

- `shown_narratives` stays (search moves from FTS5 to a `tsvector` column: top of this doc). It is one row per (story, source): 30,063 rows for 12,013
  stories, 2.5×. Normalising it saves 6.5 MiB and some `COUNT(DISTINCT headline)`; not worth doing before
  the web rewrite.
- `fetched_articles`, `source_health`, `dedup_log` stay. Drop `dedup_log.action` (24,856 of 24,856 are
  `filtered`). Keep `threshold`: it records a parameter that will change.
- (SQLite only, superseded by Postgres.) PRAGMAs: WAL on (after the two fixes in 2.3 item 6); `foreign_keys=ON` in the TS `openDb`, which Python
  sets and TS does not (`rg -n foreign_keys digest/src` → no match; the clone has 0 violations,
  `$P "PRAGMA foreign_key_check"` returns nothing).
- Seven redundant indexes, ~4 MiB: three on `shown_narratives(shown_at)`, pairs on `run_id` for
  `fetched_articles`, `shown_narratives`, `source_health`, `selections`, and `idx_digests_date` on a primary key
  (`$P ".indexes shown_narratives"`).

## 5. Migration path

Python retires at the deploy that switches production to TypeScript (Sean, 2026-09-23; it stays in git
history). Nothing has to keep a Python writer working after that, so the model is not reached by a chain
of migrations over today's tables. Instead:

| step | when | change | inverse |
|---|---|---|---|
| 0 | now | drop the two unshipped 2026-09-23 migrations (`workflow_run_id`, `broadcast_run_id`); both are unapplied on prod (`$P "SELECT migration_id FROM _yoyo_migration ORDER BY 1 DESC LIMIT 1"` → `20260916190000`) and their jobs move into `run_attempts` and `broadcasts` | revert the commit |
| 1 | with plan A | write the new schema as dbmate migration 1 (`digest/db/migrations/`); point the TS store and its tests at it; staged mode runs on a scratch DB built from it | revert |
| 2 | with plan A | `bin/import-legacy`: load today's `digest.db` into a `legacy` schema of the new Postgres database and `INSERT ... SELECT` into the new schema (was `ATTACH`, on SQLite). Carries runs, usage, artifacts, issues (as revision 1), broadcasts, threads, installments, questions, source health, shown narratives, `fetched_articles` and `dedup_log` (next-day dedup reads both). Leaves behind `selections` (backfilled into artifacts first for the 128 runs that exist only there), `cluster_runs`, `thread_runs`, `story_feedback` (exported to a CSV under `docs/`), `threads.slug`, `dedup_log.action` | delete the new file |
| 3 | gate days | staged mode refreshes its scratch DB by running the import against the latest prod backup, so all three gate days exercise the import as well as the schema | |
| 4 | cut-over deploy | stop the timer, take the verified snapshot, run the import into the `digest` Postgres database, deploy the TypeScript circulation (ported before this step; there are no compatibility views), start the timer | redeploy the Python tag and the Rust circulation, both still reading the untouched SQLite file. Valid until the first TypeScript run writes; after that, that day exists only in Postgres |
| 5 | after cut-over | delete `newsroom/`, yoyo, the Rust circulation, the retract and sweep code, and the SQLite file | |

**Tests the path needs:** the import on the prod clone with row-count and content checks per table (every
issue's html byte-equal, every thread's derived label equal to today's `threads.label`); the transition
triggers (every legal and illegal edge); circulation's suite against an imported clone.

### 5.1 The import, table by table (measured on the prod clone)

Engine-neutral: this is what `bin/import-legacy` must produce whichever engine holds the new schema.
Counts from `sqlite3 -readonly data/prod-20260923b.db` (runs 1-305, yoyo head `20260916190000`); the
query for every line is `docs/2026-09-23-import-expectations.sql`. "=" means row for row and column for
column, except where a column is named.

| new table | from | expected rows on the clone | content check |
|---|---|---|---|
| `digest_runs` | `digest_runs` | 294 | `completed` (290, all with `completed_at`) splits by evidence, since Python marked a run completed whether or not it emailed (`newsroom/src/db.py:178`): `outcome='sent'` for the 265 with `articles_emailed > 0`; `outcome='unrecorded'` for the 25 with 0 (12 have an issue, 13 are same-day runs whose issue a later run overwrote). `running` → `failed` (runs 123, 281: the orphans of §4.1); `failed` stays (218, 229) |
| `run_attempts` | one per run | 294 | `pipeline='python'`; state from the run's new status; `run_usage.attempt_id` and `run_artifacts.attempt_id` point at it |
| `run_usage` | `run_usage` | 2,205 over 191 runs, $769.74 | = ; `effort` NULL on 1,744 rows stays NULL ("not recorded", never back-filled) |
| `run_artifacts` | `run_artifacts` ∪ `selections` | 1,981 + 128 = 2,109, all `current` | = with `sha256` of `content`, and `stage`/`kind`/`branch` from the name (Appendix C, one function shared with the writer); the 128 are `selections.json` for runs that have no such artifact; the other 99 `selections` rows equal their artifact (99/99) |
| `issues` | `digests` | 282, all revision 1 | `html` byte-equal 282/282; `published_at` = `digests.created_at` (never NULL); `run_id` NULL on 5 (2025-12-26, -30, -31, 2026-01-16, -17: saved before runs were linked), so `issues.run_id` is nullable, for those rows only |
| `broadcasts` | `digests` with a send | 100, all `sent` | `resend_id`, `recipients`, `run_id` = `digests.run_id`; `claim_token`, `claimed_at` unknown, so nullable, for imported rows only. `email_artifact` is dropped: no run has an `email.html` artifact (0 rows), and a TS send's email is its run's `email.html` by name |
| `shown_narratives` (+ full-text index) | same | 30,063 over 277 runs; index 30,063 | = |
| `fetched_articles` | same | 136,143 over 229 runs | = |
| `dedup_log` | same, minus `action` | 24,856 | = (`action` is `filtered` on 24,856 of 24,856) |
| `source_health` | same | 9,645 | = ; `run_id` NULL on 231 (recorded before runs were tracked) |
| `threads_all` | `threads` | 951 | `created_run_id` = `first_run_id`; derived `label` = old `label` 951/951; derived `status` = old `status` 951/951 (52 active, 899 dormant; the clone has no merged thread, so `merged` is exercised only by a test) |
| `thread_installments_all` | `thread_installments` | 1,597 | `continued` = `matched_score IS NOT NULL` (641); `content` 605 set |
| `thread_questions_all` | `thread_questions` | 3,116 | 2,408 open, 708 resolved, every resolving run published |
| `thread_question_resolutions` | resolved questions | 708 | `(question, resolved_run_id, resolved_how)` |
| not carried | `cluster_runs` (99, equal to `clusters.json` 99/99, none without it), `thread_runs` (98), `story_feedback` (37, last 2026-07-05: exported to `docs/2026-09-23-story-feedback.csv`), `threads.slug`, `dedup_log.action`, the yoyo tables | | |

Things the clone showed that §4 did not say:
- The derived `status` matches only when dormancy is counted up to, and excluding, the newest run: the
  stored status is the decay the newest run applied before it finished. Counting every completed run
  gives 943/951 (8 threads that the next run would retire).
- Every installment's run is in `published_runs` (0 outside it), so gating thread pages on publication
  hides nothing today, as §2.3 measured with `completed_at`.
- `threads.updated_at` is not derivable (the decay wrote it): the latest installment's `created_at`
  equals it on 52 of 951 threads, the active ones. The "older threads" page orders by it, so the dormant
  threads' order on that page changes.
- (Moot with Postgres: circulation is ported first and reads the new schema directly.) Circulation
  refuses to start unless `digests` is a *table*, and reads `threads`, `thread_installments` and
  `thread_questions.status` by name (`main.rs` `REQUIRED_TABLES`, `thread.rs:100-449`), which on SQLite
  would have forced compatibility views under those names.
- Outcome spellings: the constraint takes the TypeScript's, `sent`, `disabled`, `rejected`, `held-out`,
  `skipped`, plus `unrecorded` for imported runs; §4.1's `held_out`/`no_stories` are superseded (no
  `no_stories` outcome exists). The TS send writes `created` for a draft; `broadcasts.status` calls it
  `draft`.
- Step 0 cannot land alone: `startRun`'s retry idempotency reads `digest_runs.workflow_run_id` and the
  abandoned-run sweep reads `digests.broadcast_run_id`, so both migrations leave with the port to
  `run_attempts` and `broadcasts`, not before it.
- `archiveRun` stays as an activity that does nothing: the recorded workflow histories schedule it.

**Lifecycle edges, as test cases** (§4.1). States: `running`, `failed`, and `completed` with each of the
five pipeline outcomes. Legal: any state to itself (a write that leaves status and outcome alone, which
§4.1 did not say); `running` → `failed` or any `completed`; `failed` → `running`; `completed(o)` →
`running` for `o` ≠ `sent`. Everything else is refused, including `failed` → `completed`, `completed` →
`failed`, `completed(sent)` → `running`, and `completed(a)` → `completed(b)` for a ≠ b: 7 × 7 = 49
edges, 7 + 6 + 1 + 4 = 18 legal. `completed(unrecorded)` exists only by import and is treated as unsent
(it may resume). Two row checks besides: `completed` needs an outcome, and an outcome needs
`completed`.

**The migration tool.** Plain SQL on SQLite with triggers, partial indexes, views and FTS5 rules out an ORM
as schema owner: Drizzle Kit or Kysely would make TypeScript the source of truth for a schema the Rust tier
also reads, and neither models FTS5 triggers (unverified for both). node-pg-migrate is Postgres-only.
**dbmate** fits: one binary, plain SQL with up/down blocks, SQLite and Postgres, maintained (pushed
2026-09-23: `gh repo view amacneil/dbmate --json pushedAt,isArchived`). With a fresh schema there is no
yoyo history to carry over.

## 6. Decisions and open questions

**Decided by Sean, 2026-09-23:**
1. The site is not HA; short downtime is acceptable.
2. ~~Postgres for the product only if the trade-offs justify it. They don't yet (section 3).~~ Postgres
   (top of this doc).
3. A forced re-run may replace the public issue, and never re-sends (section 4.4).
4. An unsent run's thread installments stay private until the run is published by email or on the web.
5. Python retires at the deploy that switches to TypeScript.
6. Eval and training data live off the box.
7. Today's data is not reshaped by migrations: a fresh schema and a one-shot import instead.

**Still open:**
1. **PostHog.** Sean is open to it for monitoring. Two different jobs, and they should be decided
   separately. For readers, it needs a privacy policy change and a reason to measure; by the
   distribution memo, prod reach is about zero. For the pipeline, its LLM analytics could hold per-call
   traces off the box, which would overlap with section 4.3 and with promptfoo's store. Either way, reader
   events never enter `digest.db`.
2. **Retention: keep everything?** Default yes: ~0.7 GiB a year against 28 GB free.
3. **`story_feedback`'s 37 rows (last vote 2026-07-05):** export to CSV and drop? Default yes.

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
