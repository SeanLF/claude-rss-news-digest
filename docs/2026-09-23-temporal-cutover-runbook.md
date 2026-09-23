# Temporal cut-over and rollback runbook (2026-09-23)

The TypeScript pipeline runs on Temporal on the production box beside the Python one. One terraform
variable decides which of the two is live. This runbook covers the flip, the flip back, and running the
Temporal side day to day. Terraform: `$INFRA_DIR/infrastructure/terraform/news-digest-temporal.tf`,
branch `digest-temporal` of seanfloyd.dev. Spec: §2.1 and §5 of
`docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md`.

## The switch

`news_digest_pipeline` in `terraform.tfvars`, `"python"` (default) or `"temporal"`:

| | `python` | `temporal` |
|---|---|---|
| `news-digest.timer` (12:25 Europe/Paris) | enabled | disabled; the service stays installed |
| Temporal schedule `digest-daily` (10:25Z) | paused, note "live pipeline: python" | unpaused |
| healthchecks.io success ping | sent by the Python run | sent by `news-digest-deadman` after `--verify-today` passes |
| Temporal server, Postgres, UI, both workers | running | running |

Both sides are rendered from the one variable, so one apply cannot leave both pipelines armed. The workers
run in either mode, so a worker that cannot start on the box shows up before the cut-over.

The flip is Sean's call, after three passed gate days (spec §7.5).

## What is on the box

| unit | what | memory cap |
|---|---|---|
| `news-digest-temporal-postgres` | `postgres:18.6-alpine3.24`, volume `news-digest-temporal-pg` | 384 MiB |
| `news-digest-temporal-schema` | one-shot: `temporal-sql-tool` create/setup/update-schema (admin-tools 1.32.0) | |
| `news-digest-temporal` | `temporalio/server:1.32.0`, no published port | 512 MiB |
| `news-digest-temporal-ui` | `temporalio/ui:2.54.1` on 127.0.0.1:8233 only | 128 MiB |
| `news-digest-temporal-bootstrap` | one-shot: namespace `news-digest` (30-day retention), `ensureSchedule`, pause state | |
| `news-digest-worker` | the TypeScript worker, queue `digest` | 2 GiB |
| `news-digest-fulltext` | the Python fulltext worker, queue `fulltext` | 512 MiB |
| `news-digest-temporal-backup.timer` | nightly `pg_dump` at 03:15 UTC, kept 14 days | |

Everything sits on the docker network `news-digest-temporal`. The workers also join `digest-v6`, because
they fetch the feeds and france24 only answers over IPv6. Every long-running unit restarts on failure. Five
failures in ten minutes stop the restarts and email an alert through `news-digest-alert@`.

The Postgres password is generated on the box, in `/opt/news-digest/temporal-db.env`. It is not in
tfvars, state or 1Password.

### Memory budget

Read on the box on 2026-09-23 with `free -m` and `docker stats`: 3819 MiB total, no swap, 994 MiB used
with no digest running (seanfloyd.dev web 178, registry 30, kamal-proxy 13, circulation 4, the rest the
system). The Temporal stack adds, idle:

| | measured |
|---|---|
| server + Postgres | 262 MiB on prod (2026-09-21); 277 MiB in the local rehearsal |
| digest worker | 137-198 MiB (local) |
| fulltext worker | 79 MiB (local) |
| UI | 7 MiB (local) |

That is about 1.5 GiB in use with nothing running. A run adds the running pipeline's container, capped at
2 GiB either way. The Python run's peak has never been measured. At the cap, the box is at about 3.5 of
3.8 GiB, with page cache as the only slack. If the box OOMs during a run, first lower the fulltext cap
(its extraction runs in a child process), then the UI's. The digest worker's 2 GiB stays: it was
OOM-killed at 512 MiB running four Claude Code processes at once.

## Before the cut-over

1. Three passed gate days (spec §7).
2. `broadcast` is a real activity, not a stub. Run 305 (`docs/proposed/2026-09-23-e2e/`) still had
   broadcast, gnews and threads stubbed.
3. The stack has been deployed with `python` live and is healthy. All of these are read-only:
   ```
   bin/ssh 'systemctl is-active news-digest-temporal-postgres news-digest-temporal news-digest-temporal-ui news-digest-worker news-digest-fulltext'
   bin/ssh 'systemctl is-active news-digest-temporal-schema news-digest-temporal-bootstrap'   # one-shots: active (exited)
   bin/ssh 'docker run --rm --network news-digest-temporal -e TEMPORAL_ADDRESS=news-digest-temporal:7233 temporalio/admin-tools:1.32.0 temporal schedule describe -n news-digest -s digest-daily -o json' | jq .schedule.state
   bin/ssh 'journalctl -u news-digest-temporal-backup --since -2d --no-pager | tail -3'   # a dump from last night
   bin/ssh 'docker stats --no-stream'
   ```
4. healthchecks.io: once the flip is made, the success ping arrives at the dead-man time (15:00
   Europe/Paris), not when the run ends. Set the check's schedule or grace to cover that before
   flipping, or the first Temporal day alerts falsely.
5. The schedule is fixed at 10:25Z. The Python timer is 12:25 Europe/Paris, which is 10:25Z in summer
   and 11:25Z in winter (CET, from 2026-10-25). After the flip, the digest lands an hour earlier in
   winter than it does now.

## Cut-over

Do the flip outside the run window. Not while a Python run is in progress
(`bin/ssh systemctl is-active news-digest.service` must say `inactive`), and not between 10:20Z and a
digest landing.

1. In `$INFRA_DIR/infrastructure/terraform/terraform.tfvars`: `news_digest_pipeline = "temporal"`.
2. `bin/deploy` (or `bin/deploy --skip-build -y` if the images are already current). The deploy pauses
   the schedule, applies, and then restarts `news-digest-temporal-bootstrap`, which unpauses it because
   `temporal` is now live. The timer resource re-renders and disables `news-digest.timer`.
3. Verify:
   ```
   bin/ssh systemctl is-enabled news-digest.timer            # disabled
   bin/ssh systemctl list-timers --no-pager | grep digest     # no news-digest.timer
   # schedule state: no "paused"; note "live pipeline: temporal"
   ```
4. The next day: the workflow `digest-scheduled` ran (UI, below), `bin/ops run` shows the run completed,
   the dead-man passed, and healthchecks.io got its ping.

## Rollback

1. `news_digest_pipeline = "python"`, then `bin/deploy --skip-build -y`. The timer is re-enabled and the
   schedule paused, in one apply.
2. If a Temporal run is in progress and should not finish (for example, it would broadcast), end it:
   ```
   bin/ssh 'docker run --rm --network news-digest-temporal -e TEMPORAL_ADDRESS=news-digest-temporal:7233 temporalio/admin-tools:1.32.0 temporal workflow terminate -n news-digest -w <workflow id> --reason rollback'
   ```
3. If the day has no digest, run it through Python by hand: `bin/ssh systemctl start --no-block news-digest.service`.
   The Python dup-run guard reads `digest_runs`, which the TypeScript run writes too. A day the TypeScript
   run completed is refused unless forced.
4. Nothing on the Temporal side needs undoing. Its artifacts are rows in `digest.db`, which the
   pre-migration snapshot of every deploy already covers.

A rollback of the Temporal stack itself (bad worker image) is an ordinary deploy of an earlier commit. The
worker digests are in the `deploy/*` tag messages, like the other images.

## Day to day

- **UI, and the three signals**: `ssh -L 8233:localhost:8233 root@seanfloyd-hetzner`, then
  http://localhost:8233. The UI has no login, so it is bound to loopback. From the CLI:
  ```
  T='docker run --rm --network news-digest-temporal -e TEMPORAL_ADDRESS=news-digest-temporal:7233 -e TEMPORAL_NAMESPACE=news-digest temporalio/admin-tools:1.32.0 temporal'
  bin/ssh "$T workflow list --limit 5"
  bin/ssh "$T workflow signal -w <id> --name approve --input '{\"decision\":\"approve\"}'"
  ```
- **Pause by hand**: `bin/ssh /opt/news-digest/bin/digest-schedule pause "reason"`. Restore with
  `bin/ssh systemctl restart news-digest-temporal-bootstrap`. That restores the state the live pipeline
  calls for; it never unpauses while Python is live.
- **Deploys**: `bin/deploy` pauses before the snapshot and migrations. It restores after the apply and the
  tag, and again from its exit trap if it dies partway. A run in progress when a deploy starts is reported,
  not blocked: the worker restarts under it, and it resumes from its last completed activity. One gap: the
  apply's own bootstrap step restores the schedule a few seconds before the workers restart on the new
  image.

## Backups and the restore drill

Dumps are in `/opt/news-digest/temporal-dumps/`, `<db>-<UTC stamp>.dump` (custom format), for
`temporal` and `temporal_visibility`. A dump counts only after `pg_restore --list` has read it back.
A truncated archive fails that step (tested). They are on-box only: Temporal's history is for visibility
and forensics, and the record is `digest.db` (spec §2.1).

The drill (spec §5 asks for one). It was rehearsed locally on 2026-09-23 and has not yet been run on the
box. It restores into a scratch Postgres and server beside the live ones, and touches neither:
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
# pause first: the restored schedule is live, and this server would start a run of its own at 10:25Z
T='docker run --rm --network news-digest-temporal -e TEMPORAL_ADDRESS=restore-temporal:7233 -e TEMPORAL_NAMESPACE=news-digest temporalio/admin-tools:1.32.0 temporal'
$T schedule toggle -s digest-daily --pause --reason "restore drill"
$T schedule describe -s digest-daily -o json | jq .schedule.state
$T workflow list --limit 3
docker rm -f restore-temporal restore-pg && docker volume rm restore-pg
```
No worker polls the scratch server: the workers are pointed at `news-digest-temporal`, so nothing it
holds can run.

**Lost password file.** Postgres reads `POSTGRES_PASSWORD` only when the volume is first initialised.
If `temporal-db.env` is gone and the volume is not, the next provision writes a new password that
Postgres does not know. Recover it with
`docker exec news-digest-temporal-postgres psql -U temporal -d postgres -c "ALTER ROLE temporal PASSWORD '<value from the new file>'"`.
Local connections inside the container are trusted, so this works without the old password.

## Unverified until the first apply

- Every provisioning script and unit ran only in a local rehearsal. Its Exec lines ran verbatim against
  local docker, the units passed `systemd-analyze verify` on Ubuntu 24.04, and the scripts passed
  shellcheck. None of it has run under systemd on the box. Ordering at boot, `StartLimit*`, `OnFailure`
  and `docker network prune` against live networks are all unexercised.
- Postgres 18 runs under Temporal 1.32.0 here, not the 16 in Temporal's own samples. It was rehearsed
  locally (schema, server, namespace, schedule, dump, restore), on arm64 rather than the box's amd64.
- The worker has never processed a workflow on the box. Its `--env-file` is the Python container's
  `.env`, and the TypeScript render and broadcast read their settings from it. Nobody has checked
  whether every name matches.
- The 2 GiB peak with the Temporal stack resident has not been measured.
