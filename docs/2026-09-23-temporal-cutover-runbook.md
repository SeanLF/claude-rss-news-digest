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
| worker's database (`DIGEST_DB_PATH`) | | `digest-staged.db`, a copy | `digest.db` |
| worker broadcasts (`BROADCAST_ENABLED`) | | `false` | `true` |
| healthchecks.io success ping | the Python run | the Python run | `news-digest-deadman`, after `--verify-today` |
| what `bin/deploy` builds and applies | circulation, newsroom | + both workers, the stack, pause and restore | same as staged, and refuses 10:00-11:45Z without `--force` |

- **`python`** costs nothing. A Python deploy never builds, gates, targets or pauses anything Temporal,
  so a problem in `digest/` cannot block it.
- **`staged`** is for verifying prod before the flip. It is not meant to stay on: it holds about
  560 MiB beside a Python run that is capped at 2 GiB.
- **`temporal`** is the cut-over. It is Sean's call, after three passed gate days (spec §7.5).

Two guards stop both pipelines from sending the same day:
- **The TypeScript `startRun`** refuses a day whose `digest_runs` already has a completed run, or a run
  still `running` that started within 4 h. The Python pipeline writes the same table, so this holds
  whichever pipeline started first. `force` overrides it.
- **Terraform ordering**: the bootstrap that unpauses the schedule depends on the timer resource, so
  going to `temporal` disables the timer first.

`bin/deploy` reads the mode with `tf console` on every run. An unreadable mode stops the deploy.

## What is on the box (staged and temporal)

| unit | what | memory cap |
|---|---|---|
| `news-digest-temporal-postgres` | `postgres:18.6-alpine3.24`, volume `news-digest-temporal-pg` | 384 MiB |
| `news-digest-temporal-schema` | one-shot: `temporal-sql-tool` create/setup/update-schema (admin-tools 1.32.0) | |
| `news-digest-temporal` | `temporalio/server:1.32.0`, no published port | 512 MiB |
| `news-digest-temporal-ui` | `temporalio/ui:2.54.1` on 127.0.0.1:8233, and on the tailnet via `tailscale serve` | 128 MiB |
| `news-digest-temporal-bootstrap` | one-shot: namespace `news-digest` (30-day retention), `ensureSchedule`, pause state, missed-slot start | |
| `news-digest-worker` | the TypeScript worker, queue `digest`; env `.env` then `worker.env` | 1280 MiB |
| `news-digest-python` | the Python worker (fulltext, gnews), queue `python` | 448 MiB |
| `news-digest-temporal-backup.timer` | nightly `pg_dump` at 03:15 UTC, kept 14 days | |

- Everything sits on the docker network `news-digest-temporal`. The workers also join `digest-v6`:
  they fetch the feeds, and france24 answers only over IPv6.
- Long-running units restart on failure. Five failures in ten minutes stop the restarts and email
  through `news-digest-alert@`.
- The Postgres password is generated on the box, in `/opt/news-digest/temporal-db.env` (0600). It is not
  in tfvars, state or 1Password.
- `worker.env` (0600) holds `BROADCAST_ENABLED`, `DIGEST_DB_PATH`, `TEMPORAL_UI_URL`
  (`https://seanfloyd-hetzner.tail739266.ts.net:8233`) and `HEALTH_ALERT_EMAIL`.

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
Python worker 67-79 (measured as the fulltext worker, before gnews joined it), UI 7.

## Staged verification

1. `news_digest_pipeline = "staged"`, then `bin/deploy`.
2. The worker unit copies `digest.db` to `digest-staged.db` before every start. It uses SQLite's
   online backup API, run in the newsroom image. The TypeScript run writes only the copy, so Python's
   fetch window (`get_last_run_time`) and its duplicate-run guard never see it. Broadcast is off.
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
   bin/ssh systemctl restart news-digest-worker        # refreshes digest-staged.db, then starts
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
5. The schedule is fixed at 10:25Z. The Python timer is 12:25 Europe/Paris, which is 10:25Z in summer
   and 11:25Z in winter (CET from 2026-10-25), so in winter the digest lands an hour earlier.

## Cut-over

Outside the run window. `bin/ssh systemctl is-active news-digest.service` must say `inactive`.

1. `news_digest_pipeline = "temporal"`, then `bin/deploy`. The apply disables the timer, then the
   bootstrap unpauses the schedule. The worker is re-pointed at `digest.db` with broadcast on.
   - If it is past 10:25Z and today has no `DigestWorkflow`, the bootstrap starts today's run once. A
     paused schedule drops its missed slot rather than catching it up.
   - If Python already sent today, `startRun` refuses that run with `AlreadyRan`: a failed workflow,
     no email.
2. Verify:
   ```
   bin/ssh systemctl is-enabled news-digest.timer                    # disabled
   bin/ssh "$T schedule describe -s digest-daily -o json" | jq .schedule.state   # no "paused"
   bin/ssh 'grep -E "BROADCAST|DIGEST_DB" /opt/news-digest/worker.env'  # true, digest.db
   ```
3. The next day: `bin/ops run` shows the run completed, the dead-man passed, and healthchecks.io got
   its ping.

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
3. If the day has no digest: `bin/ssh systemctl start --no-block news-digest.service`. Python's guard
   refuses a day that already has a completed run.
4. To remove the stack as well, set `"python"` and run the teardown below.

Nothing on the Temporal side needs undoing in `digest.db`: its rows are covered by the pre-migration
snapshot every deploy takes.

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
  - A run already in progress is reported, not blocked: the worker restarts under it and resumes from
    its last completed activity.
  - Known gap: the apply's own bootstrap step restores the schedule a few seconds before the workers
    restart on the new image.

## What still needs the Python tree after the cut-over

The cut-over retires the Python pipeline, not `newsroom/`. Deleting it breaks these:

| what | reads from the Python tree | how |
|---|---|---|
| the Python worker image (`digest/python/Dockerfile`) | `newsroom/src/fulltext.py`, `newsroom/src/gnews.py`, `newsroom/src/config.py` | copied into `/app/src/`; trafilatura keeps the worker in Python whatever happens to gnews |
| the TypeScript worker image (`digest/Dockerfile`) | `newsroom/sources.json`, `newsroom/templates/digest-template.html`, `newsroom/templates/digest.css` | copied; the feed catalogue and the render's template |
| the ci-ts image (`digest/Dockerfile.ci`) | `migrations/`, the two templates above, `newsroom/tests/fixtures/kitchensink_selections.json`, `newsroom/src/` | copied for the store, render and parity tests, and for the guard below |
| migrations | the newsroom image | `bin/migrate` runs yoyo in `digest-newsroom`, locally and on the box; `bin/deploy` migrates through it |
| staged mode's DB refresh | the newsroom image | the worker unit copies `digest.db` to `digest-staged.db` with SQLite's backup API inside it (Staged verification, step 2) |

`digest/src/python-tree.test.ts` fails when a file these Dockerfiles copy from `newsroom/` or
`migrations/` is gone, or is missing from this table. The last two rows are not Dockerfile copies, so
no test guards them: moving yoyo and the staged refresh off the newsroom image comes before
deleting it.

## Backups and the restore drill

Dumps land in `/opt/news-digest/temporal-dumps/` as `<db>-<UTC stamp>.dump` (custom format), for
`temporal` and `temporal_visibility`.
- A dump counts only after `pg_restore --list` has read it back. A truncated archive fails that
  check (tested).
- They stay on the box. Temporal's history is for forensics; the record is `digest.db` (spec §2.1).

The drill (spec §5 asks for one) was rehearsed locally on 2026-09-23. It has not yet been run on the box.
It restores into a scratch Postgres and server beside the live ones and touches neither:
```
bin/ssh
cd /opt/news-digest
docker run -d --name restore-pg --network news-digest-temporal --env-file temporal-db.env -v restore-pg:/var/lib/postgresql postgres:18.6-alpine3.24
until docker exec restore-pg pg_isready -q -U temporal -d postgres; do sleep 1; done
docker exec restore-pg createdb -U temporal temporal_visibility     # "temporal" exists: it is POSTGRES_USER's own
for db in temporal temporal_visibility; do
  docker exec -i restore-pg pg_restore -U temporal --exit-on-error -d $db < "$(ls -t temporal-dumps/$db-*.dump | head -1)"
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

**Lost password file.** Postgres reads `POSTGRES_PASSWORD` only when the volume is first initialised,
so a new `temporal-db.env` over an old volume locks Temporal out. Recover with
`docker exec news-digest-temporal-postgres psql -U temporal -d postgres -c "ALTER ROLE temporal PASSWORD '<value from the new file>'"`.
Local connections inside the container are trusted, so this works without the old password.

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
