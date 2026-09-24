# Temporal cut-over and rollback runbook (2026-09-23)

The TypeScript pipeline runs on Temporal on the production box. One terraform variable decides whether
it is on the box at all, and which pipeline sends the digest. This runbook covers staging it, the flip,
the flip back, and running the Temporal side day to day.

Terraform: `$INFRA_DIR/infrastructure/terraform/news-digest-temporal.tf`, branch `digest-temporal` of
seanfloyd.dev. Spec: §2.1 and §5 of `docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md`.

## The switch

`news_digest_pipeline` in `terraform.tfvars`:

| | `python` (default) | `staged` | `temporal` |
|---|---|---|---|
| Temporal stack (server, Postgres, UI, workers, dumps) | not on the box | running | running |
| `news-digest.timer` (12:25 Europe/Paris) | enabled | enabled | disabled; the service stays installed |
| schedule `digest-daily` (10:25Z) | none | paused | unpaused |
| worker's database (`DIGEST_DATABASE_URL`, Postgres) | | `digest_staged`, re-imported from a snapshot of `digest.db` at every worker start | `digest`, imported once at the cut-over |
| worker broadcasts (`BROADCAST_ENABLED`) | | `false` | `true` |
| healthchecks.io success ping | the Python run | the Python run | `news-digest-deadman`, after `--verify-today` |
| what `bin/deploy` builds and applies | circulation, newsroom | + both workers, the stack, pause and restore; warns (never refuses) when a run is live or Temporal cannot say | same as staged, but refuses those without `--force`, and 10:00-11:45Z too |

- **`python`** costs nothing. A Python deploy never builds, gates, targets or pauses anything Temporal,
  so a problem in `digest/` cannot block it.
- **`staged`** is for verifying prod before the flip. It is not meant to stay on: it holds about
  560 MiB beside a Python run that is capped at 2 GiB.
- **`temporal`** is the cut-over. It is Sean's call, after three passed gate days (spec §7.5).

Two guards stop both pipelines from sending the same day:
- **The TypeScript `startRun`** refuses a day that already has a sent run (view `sent_runs`), or a run
  still `running` that started within 4 h. `force` overrides it. The two pipelines no longer share a
  database: Python writes `digest.db`, the worker Postgres. In `temporal` the guard sees Python's runs
  only through the cut-over's import, so `import-digest-db` refuses while `news-digest.service` is
  active. In `staged` nothing the worker writes reaches `digest.db`.
- **Terraform ordering**: the bootstrap that unpauses the schedule depends on the timer resource, so
  going to `temporal` disables the timer first.

`bin/deploy` reads the mode with `tf console` on every run. An unreadable mode stops the deploy.

## What is on the box (staged and temporal)

| unit | what | memory cap |
|---|---|---|
| `news-digest-temporal-postgres` | `postgres:18.6-alpine3.24`, volume `news-digest-temporal-pg`; `bin/pg-roles` after every start | 384 MiB |
| `news-digest-temporal-schema` | one-shot: `temporal-sql-tool` create/setup/update-schema (admin-tools 1.32.0) | |
| `news-digest-temporal` | `temporalio/server:1.32.0`, no published port | 512 MiB |
| `news-digest-temporal-ui` | `temporalio/ui:2.54.1` on 127.0.0.1:8233, and on the tailnet via `tailscale serve` | 128 MiB |
| `news-digest-temporal-bootstrap` | one-shot: namespace `news-digest` (30-day retention), `ensureSchedule`, pause state, missed-slot start | |
| `news-digest-worker` | the TypeScript worker, queue `digest`; env `.env` then `worker.env`. Before it starts: the import (`refresh-staged-db` in staged, `import-digest-db` in temporal), then `node dist/cli/migrate.js` | 1280 MiB |
| `news-digest-python` | the Python worker (fulltext), queue `python` | 448 MiB |
| `news-digest-temporal-backup.timer` | nightly `pg_dump` of `temporal`, `temporal_visibility` and `digest` at 03:15 UTC, kept 14 days | |

- Everything sits on the docker network `news-digest-temporal`. The workers also join `digest-v6`:
  they fetch the feeds, and france24 answers only over IPv6.
- Long-running units restart on failure. Five failures in ten minutes stop the restarts and email
  through `news-digest-alert@`.
- Postgres roles (`bin/pg-roles`, idempotent, run after every Postgres start):
  - `postgres`: the superuser, used only over the container's local socket (pg-roles, the dumps, the
    import's database swaps). Password in `pg-admin.env` (0600), generated on the box, read by
    Postgres only when the volume is first initialised.
  - `temporal`: owns `temporal` and `temporal_visibility`. Password in `temporal-db.env` (0600),
    generated on the box; never in tfvars, state or 1Password.
  - `digest`: owns `digest` and `digest_staged`. Password from 1Password
    (`op://Private/seanfloyd.dev/NEWS_DIGEST_DB_PASSWORD`, through `bin/tf`) into `digest-db.env` (0600).
  - Both are `NOSUPERUSER NOCREATEDB NOCREATEROLE`, and `PUBLIC` has no `CONNECT` on any of the
    databases, so neither role can open the other's.
  - A volume initialised when `temporal` was the superuser cannot be demoted: pg-roles fails and says
    so. Dump `temporal` and `temporal_visibility`, remove the volume, start again, restore.
- `worker.env` (0600) holds `BROADCAST_ENABLED`, `DIGEST_DATABASE_URL`
  (`postgres://digest:<pw>@news-digest-temporal-postgres:5432/<digest or digest_staged>?sslmode=disable`:
  the server has no TLS, and dbmate insists on it otherwise), `TEMPORAL_UI_URL`
  (`https://seanfloyd-hetzner.tail739266.ts.net:8233`) and `HEALTH_ALERT_EMAIL`.
- The worker refuses to start without `DIGEST_DATABASE_URL`, and `bin/deploy` refuses a staged or
  temporal deploy whose terraform does not write one with a password.
- `bin/deploy` passes `-var news_digest_import_legacy_path=<checkout>/bin/import-legacy`, so the
  importer terraform ships to the box comes from the checkout that built the worker image. An apply
  run by hand from seanfloyd.dev reads the sibling `../news-digest` checkout instead, and reinstalls
  the importer if that checkout's copy differs.

### Memory budget (temporal)

Read on the box on 2026-09-23 (`free -m`, `docker stats`): 3819 MiB total, no swap, 994 MiB used with
no digest running. That leaves 2825 MiB.

The caps sum to 2752 MiB: Postgres 384, server 512, UI 128, Python worker 448, worker 1280. Every container
can sit at its cap at once and 73 MiB are still free.

The worker's 1280 MiB covers the WRITE fan-out: 4 Claude Code processes at about 245 MiB each is
980 MiB. The node worker itself measured 137-198 MiB idle, so the total is about 1180 MiB, plus
headroom. The worker was OOM-killed at 512 MiB. If the fan-out width (Semaphore 4) changes, this cap
changes with it.

Idle, measured locally: server + Postgres 213-277 MiB (262 on prod on 2026-09-21), worker 137-208,
Python worker 67-79, UI 7.

## Staged verification

1. `news_digest_pipeline = "staged"`, then `bin/deploy`.
2. Before every start the worker unit runs `refresh-staged-db`: it drops and recreates `digest_staged`,
   snapshots `digest.db` (SQLite's online backup API on the host, then `integrity_check`; never the
   live file) and imports the snapshot with `bin/import-legacy` and the worker image. Then
   `migrate.js`, then the worker. Every start rehearses the import itself (not `import-digest-db`'s
   rename, its skip once imported, or its refusal while Python runs). The TypeScript run
   writes only `digest_staged`, so Python's fetch window and its duplicate-run guard never see it.
   Broadcast is off.
3. Health, all read-only:
   ```
   bin/ssh 'systemctl is-active news-digest-temporal-postgres news-digest-temporal news-digest-temporal-ui news-digest-worker news-digest-python'
   bin/ssh 'systemctl is-active news-digest-temporal-schema news-digest-temporal-bootstrap'   # one-shots: active (exited)
   T='docker run --rm --network news-digest-temporal -e TEMPORAL_ADDRESS=news-digest-temporal:7233 -e TEMPORAL_NAMESPACE=news-digest temporalio/admin-tools:1.32.0 temporal'
   bin/ssh "$T schedule describe -s digest-daily -o json" | jq .schedule.state     # paused, "mode staged"
   bin/ssh 'journalctl -u news-digest-temporal-backup --since -2d --no-pager | tail -3'
   bin/ssh 'docker stats --no-stream'
   ```
4. A prod run against the copy, after the Python run has finished for the day. Refresh the copy
   first. The run needs `force`, because the copy holds today's completed Python run:
   ```
   bin/ssh systemctl restart news-digest-worker        # re-imports digest_staged, migrates, then starts
   bin/ssh "$T workflow start -t digest --type DigestWorkflow -w digest-staged-$(date -u +%F) -i '{\"runDate\":\"$(date -u +%F)\",\"force\":true}'"
   ```
   Watch it in the UI. It holds before broadcast (2 h, or a signal), and with broadcast off it sends
   nothing either way.

## Before the cut-over

1. Three passed gate days (spec §7).
2. `broadcast` is a real activity. Run 305 still had broadcast, gnews and threads stubbed.
3. A clean staged run on prod (above).
4. healthchecks.io: the success ping will arrive at the dead-man time (15:00 Europe/Paris), not at run
   end. Widen the check's schedule or grace first, or the first Temporal day alerts falsely.
5. **The TypeScript circulation site is live and reads Postgres `digest`.** The Rust circulation reads
   `digest.db`, which stops changing at the cut-over: from the first Temporal day it would serve no
   new issue, and the email's "View in browser" link would 404.
6. The scripts in "Scripts still on SQLite" below are ported, or knowingly left broken.
7. The schedule is fixed at 10:25Z. The Python timer is 12:25 Europe/Paris, which is 10:25Z in summer
   and 11:25Z in winter (CET from 2026-10-25), so in winter the digest lands an hour earlier.

## Cut-over

Outside the run window. `bin/ssh systemctl is-active news-digest.service` must say `inactive`.

1. `news_digest_pipeline = "temporal"`, then `bin/deploy`. The apply disables the timer, then the
   bootstrap unpauses the schedule. The worker is re-pointed at `digest` with broadcast on.
   - The worker's first start runs `import-digest-db`, once. It imports a snapshot of `digest.db`
     (never the live file) into `digest_import`, and renames that over `digest` only when the import
     has verified. Once `digest` has tables it is a no-op. It refuses while `news-digest.service` is
     active. A failed import leaves `digest` empty and the worker down, with its `OnFailure` alert;
     fix it, then `bin/ssh systemctl restart news-digest-worker`.
   - Then `migrate.js`, then the worker.
   - If it is past 10:25Z and today has no `DigestWorkflow`, the bootstrap starts today's run once. A
     paused schedule drops its missed slot rather than catching it up.
   - If Python already sent today, `startRun` refuses that run with `AlreadyRan`: a failed workflow,
     no email.
2. Verify:
   ```
   bin/ssh systemctl is-enabled news-digest.timer                    # disabled
   bin/ssh "$T schedule describe -s digest-daily -o json" | jq .schedule.state   # no "paused"
   bin/ssh 'grep -E "BROADCAST|DIGEST_DATABASE_URL" /opt/news-digest/worker.env | sed "s/:[^:@]*@/:***@/"'  # true, .../digest?sslmode=disable
   bin/ssh 'journalctl -u news-digest-worker --since -1h --no-pager | grep -E "import-|migrat|^ *ok "'   # "digest imported", every check "ok"
   ```
3. The next day: the run completed and sent, the dead-man passed, and healthchecks.io got its ping.
   Until `bin/ops` is ported (below):
   ```
   bin/ssh 'docker exec news-digest-temporal-postgres psql -U postgres -d digest -c "SELECT id, started_at, status, outcome FROM runs ORDER BY id DESC LIMIT 3"'
   ```

## Rollback (temporal -> python or staged)

The order matters. `news-digest.timer` has `Persistent=true`, so `enable --now` fires at once if 12:25
Paris has passed. The Temporal side must be quiet before the apply re-enables it.

1. Stop the Temporal side first:
   ```
   bin/ssh /opt/news-digest/bin/digest-schedule pause "rollback"
   bin/ssh "$T workflow list -q 'ExecutionStatus=\"Running\"'"
   bin/ssh "$T workflow terminate -w <id> --reason rollback"      # each running one
   ```
2. Set `news_digest_pipeline = "staged"` (keeps the stack, for forensics) and run
   `bin/deploy --skip-build -y`. The timer comes back, the schedule stays paused, and the worker moves
   to the copy with broadcast off.
3. If the day has no digest: `bin/ssh systemctl start --no-block news-digest.service`.
4. To remove the stack as well, set `"python"` and run the teardown below.

**Python cannot see what Temporal sent.** Its duplicate-run guard reads `digest.db`, and Temporal's
runs exist only in Postgres `digest`; nothing exports them back. So:
- Check before step 2 whether Temporal sent today:
  ```
  bin/ssh "docker exec news-digest-temporal-postgres psql -U postgres -d digest -tAc \"SELECT id FROM runs WHERE outcome = 'sent' AND (started_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date\""
  ```
- If it did, the timer re-enabled in step 2 can send the day again: with `Persistent=true` it fires
  at once for the runs it missed while disabled, and at 12:25 Paris, which in winter is after
  10:25Z. Roll back on a day Temporal has not sent, or run the apply only after 12:25 Paris and check
  `bin/ssh systemctl list-timers news-digest.timer` shows the next run tomorrow before Python
  starts (unverified: whether the catch-up fires on enable after days disabled has not been tested).
- After a rollback, Python's fetch window, dedup and threads start from its own last run.

**Cutting over again after a rollback** does not re-import: `import-digest-db` is a no-op once
`digest` has tables, and `digest` then lacks every run Python made since. Before the second cut-over,
with the worker stopped, move it aside so the import runs again (the Temporal-era rows stay in
`digest_before_reimport`; nothing merges them back):
```
bin/ssh 'docker exec news-digest-temporal-postgres psql -U postgres -c "ALTER DATABASE digest RENAME TO digest_before_reimport"'
```

## Teardown (staged -> python)

`bin/deploy` in `python` mode never touches the Temporal resources. If it finds the stack still on the
box (`digest-schedule` or the server unit present), it refuses and points here. Tear down deliberately,
with `news_digest_pipeline = "python"` already set:
```
cd "$INFRA_DIR" && bin/tf apply --fresh -target=null_resource.news_digest_temporal_teardown \
  -replace='null_resource.news_digest_temporal_teardown[0]' \
  -target=null_resource.news_digest_workers -target=null_resource.news_digest_temporal_bootstrap \
  -target=null_resource.news_digest_temporal_backup -target=null_resource.news_digest_temporal_server \
  -target=null_resource.news_digest_temporal_db
```
Keep the `-replace`. If an untargeted apply once created the teardown in python mode, it is still in state,
because later targeted staged or temporal deploys never remove it. A plain `-target` then plans no
change and nothing is torn down. With `-replace` it runs whether or not it is in state (checked with
a scratch config).
The five stack resources leave state with no provisioner run. Destroy provisioners would also run on
every replacement: a pin bump would stop Postgres, and everything that `Requires=` it would stop with
it. `news_digest_temporal_teardown`, which exists only in `python`, then does the work:
- pauses the schedule
- stops, disables and removes every unit
- turns off `tailscale serve` on :8233
- removes the scripts and `worker.env`

It keeps the Postgres volume, `temporal-db.env` and the dumps, so a later `staged` resumes the same
history. Rehearsed under systemd in a container: no unit files and no active units were left afterwards.

## Day to day

- **UI and the three signals.** Open `https://seanfloyd-hetzner.tail739266.ts.net:8233` on the tailnet;
  it has no login and is not on any public interface. From the CLI (`$T` as above):
  `bin/ssh "$T workflow signal -w <id> --name approve --input '{\"decision\":\"approve\"}'"`.
- **Pause by hand:** `bin/ssh /opt/news-digest/bin/digest-schedule pause "reason"`. Restore with
  `bin/ssh systemctl restart news-digest-temporal-bootstrap`, which sets the state the mode calls for.
- **Deploys** in staged or temporal:
  - The schedule is paused before the snapshot and the migrations, and restored after the apply and
    the tag. The exit trap restores it too if the deploy dies partway.
  - In temporal mode, a deploy between 10:00Z and 11:45Z needs `--force`.
  - In temporal mode a deploy refuses, without `--force`, while a digest workflow is running. It
    lists them (`workflow list --query 'WorkflowType="DigestWorkflow" AND ExecutionStatus="Running"'`)
    before the builds and again after the pause. An unreachable Temporal, a failed pause, or an
    answer that is not a complete list refuses too; `--force` is for the deploy that repairs
    Temporal. In staged mode each of these warns and the deploy goes on: the TypeScript runs there
    are rehearsals on a scratch database, and a Temporal problem must not block Python's deploy.
  - Clearing a live run so a temporal deploy can go ahead:
    - in its hold (up to 14:25Z): approve or reject it;
    - parked on the retry signal: answer it (retry or abort);
    - **stuck**: its workflow task keeps failing, as on a nondeterminism error. The refusal marks
      it "stuck". It cannot take a signal, so approving or rejecting does nothing, and it blocks
      every deploy until its 4 h run timeout. Terminate it
      (`bin/ssh "$T workflow terminate -w <id> --reason <why>"`), or deploy with `--force`.
  - **Worker Versioning, pinned** (from 2026-09-23; replaced `patched()`). The worker registers as
    version `digest:<GIT_SHA>` of the deployment `digest` (`digest/src/deployment.ts`), and every
    run stays on the build that started it. A new build gets no run until it is made current.
    `bin/deploy` does it in two steps:
    - after the pause and before the apply, it points current at the build it ships, which has no
      worker yet (`set-current-version --allow-no-pollers --ignore-missing-task-queues`). A run the
      apply's bootstrap starts then waits for the new worker instead of pinning to the old build.
      Skipped under `--skip-build`, where the shipped build is unknown before the apply.
    - after the apply, before the schedule is restored, it runs `digest/src/cli/set-current.ts`
      inside the running worker container. That waits until the worker polls, makes its build
      current, and lists stranded runs. If it fails, the deploy fails and says so.

    If the deploy dies after the first step, the exit trap points current at whichever worker is
    running. A run the apply's bootstrap started meanwhile is pinned to nothing yet, so it follows
    current and starts on that build (tested in `deployment.test.ts`). If no worker's build can be
    made current, the trap leaves the schedule paused, and such a run sits: `set-current` names it
    as `waiting:` and `bin/deploy` logs it. It is not "stranded" (pinned to nothing, so the by-hand
    step below starts it). By hand: `bin/ssh docker exec news-digest-worker node dist/cli/set-current.js`,
    then `bin/ssh systemctl restart news-digest-temporal-bootstrap`. Check with
    `bin/ssh "$T worker deployment describe --name digest"`.
  - **The first versioned deploy** replaces an unversioned worker. A run still in flight from it
    has no version; the new build picks it up and replays it on the new code, and nothing reports
    it. The guard refuses such a run in temporal mode. In staged mode, let it finish first, or ship
    no workflow-command change in that deploy.
  - What versioning adds over the guard above is small on this box. With one worker, a deploy stops
    the only worker of the old build, so a run it overlaps cannot finish on its own build either:
    before, it went on under the new code (or failed replay); now it sits. The guard still has to
    refuse live runs. What versioning removes is the `patched()` discipline: a workflow change no
    longer has to replay old histories, because no run crosses builds unless moved by hand.
  - What a worker restart within one build does to a live run is measured in
    `digest/src/workflow/deploy-safety.test.ts`: a model call, render and assemble are retried by
    their policies. A one-attempt step (COHERENCE, the send, and the other `once` steps) fails the run
    or parks it for an operator.
  - **Stranded runs.** A run pinned to a build with no worker. Nothing polls for it, so it
    does nothing, takes signals without acting on them, and sends no alert until its 4 h run timeout.
    Only the dead-man's switch notices. Two paths leave one:
    - a deploy that goes ahead under a live run: staged mode (which only warns) or `--force`;
    - the bootstrap gap, when `bin/deploy` could not point current at the new build before the
      apply (`--skip-build`, or that step failed). The apply restarts the bootstrap
      (`digest-schedule sync`) before the workers. On a day with no run yet after 10:25Z, its
      missed-day trigger starts one on the old build, and the worker restart then strands it.
      Terraform cannot order the bootstrap after the workers, because the workers depend on it (it
      creates the namespace).

    `set-current.ts` lists every running `DigestWorkflow` pinned to another build, and `bin/deploy`
    prints them with the command to move each. In temporal mode that fails the deploy, after the
    apply and the tag; in staged it warns. The way out is to move the run onto the new build:
    ```
    bin/ssh "$T workflow update-options -w <id> --versioning-override-behavior pinned \
      --versioning-override-deployment-name digest --versioning-override-build-id <new build>"
    ```
    It then replays its history on the new code. If the workflow's commands changed between the two
    builds on the path the run took, the replay fails with a nondeterminism error and the run shows as
    stuck. Terminate it and start the day again, with `--force`: `startRun` refuses a day whose run
    is still marked running from the last 4 h
    (`bin/ssh docker exec -d news-digest-worker node dist/cli/start.js <YYYY-MM-DD> --force`).
    `digest/src/deployment.test.ts` covers the move on a dev server.
  - `replay.test.ts` replays recorded histories of every path (`digest/src/workflow/histories/`)
    against the current code in CI: what a worker restart within one build does to a run. A change to
    the workflow's commands fails it, so re-record the fixtures in the same commit:
    `temporal server start-dev` on a spare port, then
    `cd digest && npm run build && TEMPORAL_ADDRESS=localhost:<port> node dist/cli/record-histories.js`.
  - Old versions pile up, one per deploy. At the server's per-deployment limit
    (`matching.maxVersionsInDeployment`) the server deletes the oldest version that has drained and
    has no pollers. A version that is still draining blocks the new build from registering, and then
    `set-current` fails the deploy. Seen with the limit set to 2 on a dev server. The default limit
    on 1.32.0 is unverified.

## What still needs the Python tree after the cut-over

The cut-over retires the Python pipeline, not `newsroom/`. Deleting it breaks these:

| what | reads from the Python tree | how |
|---|---|---|
| the Python worker image (`digest/python/Dockerfile`) | `newsroom/src/fulltext.py`, `newsroom/src/config.py` | copied into `/app/src/`; trafilatura keeps the worker in Python |
| the TypeScript worker image (`digest/Dockerfile`) | `newsroom/sources.json`, `newsroom/templates/digest-template.html`, `newsroom/templates/digest.css` | copied; the feed catalogue and the render's template |
| the ci-ts image (`digest/Dockerfile.ci`) | `migrations/`, the two templates above, `newsroom/tests/fixtures/kitchensink_selections.json` | copied for the store, render and parity tests |
| SQLite migrations | the newsroom image | `bin/migrate` runs yoyo on `digest.db` in `digest-newsroom`; `bin/deploy` migrates through it. The Postgres schema is dbmate (`migrate.js`) and needs neither |

A deleted file in the first three rows fails its image build. The last row is not a Dockerfile copy,
so nothing fails when it breaks: take yoyo out of `bin/deploy` in the change that deletes it.

## Scripts still on SQLite

Each reads `digest.db` (or a clone) under the old table names. After the cut-over `digest.db` stops
changing, so each reads a frozen copy and says nothing about Temporal days. Port or retire at the
cut-over. The Naming section of `docs/2026-09-23-data-model-design.md` has the renames; the
`completed_at IS NOT NULL` filters become the `sent_runs` view.

Port (Postgres, against `DIGEST_DATABASE_URL` or a restored `digest` dump):
- [ ] `bin/ops` (not `journal`): Python `sqlite3` on the box over SSH, in a container with the data
  volume `:ro` and the file `mode=ro`. `digest_runs`, `run_usage`, `source_health`, `run_artifacts`
  → `runs`/`run_attempts`, `model_calls`, `source_fetches`, `artifacts`, token columns under the
  OTel names. Keep both read-only guarantees: a read-only role (or `default_transaction_read_only`)
  in place of the `:ro` mount, and its test.
- [ ] `bin/usage` (`make usage`, `make usage-daily`): the `sqlite3` CLI. `run_usage` joined to
  `digest_runs.completed_at` → `model_calls` joined to `sent_runs`.
- [ ] `bin/trace`: imports `newsroom/src/db.py` for `run_artifacts`. Rewrite over `artifacts` without
  the Python import.
- [ ] `bin/analytics` and `analytics/queries/*.sql` (`make analytics*`): Python `sqlite3`; all 16
  queries name old tables, and 8 use SQLite date or JSON functions (`datetime(`, `julianday`,
  `json_each`). The runner needs a Postgres driver and parameter binding; each query needs its
  tables renamed and its date functions rewritten.
- [ ] `bin/db-clone` (`make db-clone`): pulls `digest.db` over ssh and checks `integrity_check`.
  Becomes a restore of the newest `digest` dump (`bin/backup-volumes` already streams it to the Mac)
  into a local Postgres.
- [ ] `bin/ask-eval`: plants a row in a copy's `digests` and runs the Rust circulation on it. Moves
  with the TypeScript site: a scratch Postgres and an `issues` row.

Retire (Python-era; they read `newsroom/src` modules or diff against the Python pipeline):
- [ ] `bin/migrate` and `bin/deploy`'s `run_migrations` (yoyo on `digest.db`): dbmate's `migrate.js`
  replaces them.
- [ ] `bin/replay`, `bin/rerun-run`, `bin/rerun-stage`, `bin/eval-stages`: `run_artifacts` through
  `newsroom/src/db.py`.
- [ ] `bin/eval`: reads `digests(date, html)` from a clone.
- [ ] `bin/record-oracle`, `bin/render-oracle`, `bin/threads-oracle`: Python oracles for the
  TypeScript port; nothing is left to diff against.
- [ ] `bin/repair-threads`, `bin/relabel-installments`, `bin/seed-threads`: write the thread tables
  through the Python db module. If one is still needed, it gets a TypeScript CLI over `threads` and
  `thread_updates`.

Python-era too, though they do not read `digest.db`: `bin/eval-regression`, `bin/eval-judge`,
`bin/eval-coherence`, `bin/eval-cohesion`, `bin/eval-io-shape`, `bin/eval-repair`,
`bin/eval-select-order`, `bin/eval-write-arms`, `bin/eval-write-turns` and `bin/test-prompt` run
`newsroom/src`; they go with it.

`bin/import-legacy` stays: it is the cut-over's importer, and it already writes Postgres.

## Backups and the restore drill

Dumps land in `/opt/news-digest/temporal-dumps/` as `<db>-<UTC stamp>.dump` (custom format), for
`temporal`, `temporal_visibility` and `digest`. `digest_staged` is scratch and never dumped.
- A dump counts only after the whole archive has been read back (`pg_restore -f /dev/null`).
  `pg_restore --list` reads only the table of contents: dumps cut off at 100 KB and at 30 MB of 55
  passed it, and the full read fails both (rehearsed 2026-09-23).
- Temporal's stay on the box: its history is for forensics. `digest` is the record, so
  `bin/backup-volumes` also streams a fresh dump of it to the Mac, daily and before every deploy.
- Restoring `digest`: the steps are in the comment above `news_digest_temporal_backup_script` in
  `news-digest-temporal.tf` (worker stopped, restore into `digest_restore`, rename over, `pg-roles`,
  start).

The drill (spec §5 asks for one) was rehearsed locally on 2026-09-23 with `temporal` as the superuser.
The form below, with `postgres` as the superuser, has not been run, locally or on the box.
It restores into a scratch Postgres and server beside the live ones and touches neither:
```
bin/ssh
cd /opt/news-digest
docker run -d --name restore-pg --network news-digest-temporal --env-file pg-admin.env -v restore-pg:/var/lib/postgresql postgres:18.6-alpine3.24
until docker exec restore-pg pg_isready -q -U postgres -d postgres; do sleep 1; done
# The server logs in as temporal, with the live password
{ printf '\\set tpw %s\n' "$(sed -n 's/^SQL_PASSWORD=//p' temporal-db.env)"; echo "CREATE ROLE temporal LOGIN PASSWORD :'tpw';"; } | docker exec -i restore-pg psql -U postgres -v ON_ERROR_STOP=1 -q
for db in temporal temporal_visibility; do
  docker exec restore-pg createdb -U postgres -O temporal $db
  docker exec -i restore-pg pg_restore -U postgres --exit-on-error --no-owner --role=temporal -d $db < "$(ls -t temporal-dumps/$db-*.dump | head -1)"
done
docker run -d --name restore-temporal --network news-digest-temporal --env-file temporal-db.env \
  -e DB=postgres12 -e DB_PORT=5432 -e POSTGRES_SEEDS=restore-pg -e BIND_ON_IP=0.0.0.0 \
  -v /opt/news-digest/temporal/dynamicconfig:/etc/temporal/config/dynamicconfig:ro temporalio/server:1.32.0
R='docker run --rm --network news-digest-temporal -e TEMPORAL_ADDRESS=restore-temporal:7233 -e TEMPORAL_NAMESPACE=news-digest temporalio/admin-tools:1.32.0 temporal'
$R schedule toggle -s digest-daily --pause --reason "restore drill"   # a restored live schedule would fire
$R schedule describe -s digest-daily -o json | jq .schedule.state
$R workflow list --limit 3
docker rm -f restore-temporal restore-pg && docker volume rm restore-pg
```

**Lost password file.** pg-roles sets the `temporal` and `digest` passwords from `temporal-db.env` and
`digest-db.env` on every Postgres start, so a replaced file takes effect with
`bin/ssh systemctl restart news-digest-temporal-postgres` (which restarts everything that `Requires=`
it). A lost `pg-admin.env` stops Postgres from starting (its unit reads it with `--env-file`); rerun the
apply, which writes a new one. Its value does not matter: Postgres read it only when the volume was
first initialised, nothing logs in with it, and local connections inside the container are trusted.

## Unverified until the first apply

- **systemd on the box.** The scripts and units have run only in local rehearsals:
  - Exec lines verbatim against local docker
  - `systemd-analyze verify` on Ubuntu 24.04, and shellcheck
  - the create steps and the teardown under real systemd in a container (docker stubbed), including
    a Postgres pin bump that ended with every unit still up

  They have never run on the box. Boot ordering, `StartLimit*`, `OnFailure` and `docker network prune`
  against live networks are unexercised there.
- **`tailscale serve --bg --https=8233`.** Its syntax was read from the box's `tailscale serve --help`
  (1.102.4), and the registry already uses the same mechanism on :5443. The command itself has not
  been run.
- **Postgres 18 under Temporal 1.32.0**, not the 16 in Temporal's samples. Rehearsed locally on
  arm64; the box is amd64.
- **The worker on the box.** It has never processed a workflow there. The Python container's `.env`
  plus `worker.env` has not been checked against every setting the TypeScript render and broadcast
  read.
- **The 1280 MiB worker cap** rests on the 245 MiB-per-process figure. A four-way fan-out under that
  cap has not been measured.
- **The product database on the box.** `pg-roles`, `refresh-staged-db`, `import-digest-db` and the
  `migrate.js` step have run only in local rehearsals (the import: 15 s, 283 MiB peak on the prod
  clone, the `node:sqlite` importer, measured locally). The import inside the worker unit's
  `TimeoutStartSec=900` on the box is unmeasured.
- **Worker Versioning on the box.** Checked locally on 2026-09-23 against `temporalio/server:1.32.0` on
  Postgres with the empty dynamic config the box uses: the worker image registered `digest:<GIT_SHA>`,
  `set-current.js` made it current, and a run recorded `VERSIONING_BEHAVIOR_PINNED` on it. The
  `docker exec` step in `bin/deploy` has not run on the box.
