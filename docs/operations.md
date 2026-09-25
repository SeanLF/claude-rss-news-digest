# Operations Reference

Command reference and environment notes for running news-digest: the TypeScript worker and site on
Temporal, the Python full-text worker, one Postgres (see `CLAUDE.md` for the map). Operating the box
(units, memory, backups, stranded runs) is in `docs/2026-09-23-temporal-cutover-runbook.md`.

Reusable *lessons* live in [`docs/lessons/`](lessons/); incident narratives live in
[`docs/postmortems/`](postmortems/). This file is the how-to.

## Deploying

```bash
make deploy                                   # HEAD, built on this Mac
$INFRA_DIR/bin/deploy-digest <sha> --force    # inside the run window or during a run, loudly
$INFRA_DIR/bin/deploy-digest --rollback <sha> # to a version the box still holds
```

CI (`.github/workflows/ci.yml`) runs the tests and osv-scanner on every push to main and on
Dependabot's PRs; the pre-commit hook runs the same tests locally. `make deploy`
hands HEAD to seanfloyd-infra's `bin/deploy-digest` (design: seanfloyd-infra
`docs/2026-09-25-news-digest-kamal-design.md`), which checks this checkout is clean, at that SHA and on
origin/main, and runs osv-scanner on its lockfiles; builds the three images here with Kamal and pushes them to the box's
registry; refuses while a `DigestWorkflow` runs and from 12:00 to 13:45 Europe/Paris (the guard runs in
the live worker); dumps Postgres alongside the Python worker's deploy; then deploys the worker and the
site in parallel. The worker applies the product schema's dbmate migrations as it starts and is healthy
only once it polls, so a failed migration fails the deploy with the old worker still running; its build
is then made the current Temporal version, and one that never becomes current is rolled back.
Migrations only add (a rollback runs old code on the new schema).

Kamal keeps the running container and the three before it per service, so a rollback to any of those
needs no registry. Container logs: `kamal app logs -c config/deploy/digest-worker.yml` in seanfloyd-infra.

## Database

The product database is `digest` in the box's Postgres (`news-digest-temporal-postgres`). Its schema
is dbmate migrations in `digest/db/migrations`, named `YYYYMMDDHHMMSS_description.sql`; the worker
applies them at start (`node dist/cli/migrate.js`), and `make schema-types` regenerates
`digest/src/store/schema.gen.ts` from them (CI fails while it is stale).

### Reading production

`bin/ops` runs the query on the box and prints JSON -- nothing is copied, nothing
is stale:

```bash
bin/ops run|usage|health|artifacts [ID]   # ID defaults to the latest run
bin/ops artifact ID NAME                  # one archived artifact to stdout
bin/ops journal [--since 6h] [--lines 200] [--grep PAT]
bin/ops <any> --print-command             # show what would run, run nothing
```

Read-only twice over: psql logs in as `digest_ro` (SELECT only: the role in `digest/db/ops/digest_ro.sql`, its
reads in the migration `20260925120000_digest_ro_grants.sql`) in a
read-only session; `digest/src/ops/ops-payloads.test.ts` shows each refusing a write without the
other. `docs/2026-09-03-ops-access-review.md` records why this is a CLI over SSH and not a
Tailscale-only route on the site.

Clone only when you need the whole database offline -- analysis across many runs, or a dev stack on
real data. `bin/db-clone` restores the newest verified `digest.pg.dump` backup (so it is **stale**
until the next deploy or nightly dump; `--live` takes a `pg_dump` as `digest_ro` over the wire) into
the local Postgres clone (`digest_clone` in the dev stack's `digest-pg`; `DIGEST_CLONE_URL` and
`DIGEST_CLONE_NETWORK` point it elsewhere), building it as `digest_clone_new` and renaming it over
the old one only once it verifies. `bin/usage`, `bin/trace` and `bin/analytics` read the clone
through `bin/psql`, read-only; `bin/psql` alone opens it.

The last SQLite database (`digest.db`, the Python pipeline's, frozen at the cut-over) is in
`~/Backups/news-digest-python-era/`; its importer is in git history (`bin/import-legacy`, deleted
after the cut-over).

## The dev stack (pipeline, site, mail)

One compose project, `docker-compose.yml`, driven by `make`: Temporal and the worker, the TypeScript
site, and `resend-fake`, over one product database (`digest` in `digest-pg`, on a volume). The worker
writes it as `postgres`; the site reads it as `digest_ro` and runs no migrations, as on the box. Tests
and harnesses use `ci-pg`, a scratch server with no volume; a band copies `digest` with
`CREATE DATABASE ... TEMPLATE`.

```bash
make dev-import                   # a copy of the prod clone (make db-clone first) becomes `digest`; starts the stack
make dev-up                       # start or rebuild the stack; keeps its data
make digest-start                 # today's run (UTC): sends at once if its pre-send checks pass, else holds 15 min
make digest-start ARGS=--force    # today again: a new revision of the issue on the site, never a second send
make digest-start DATE=2026-09-18 ARGS="--resume 300"   # resume a run; only a resume may name another day
make digest-approve               # or digest-reject, while a flagged run holds (DATE defaults to today; a resume's is its DATE); unsignalled, it sends after 15 min
make dev-mail-clear               # empty resend-fake: caught mail and the dev audience
make dev-urls                     # where each part answers
make dev-down                     # stop; keeps the volumes
```

A run's issue is dated the UTC day it starts (`runs.started_at`), whatever `DATE` says: `DATE` only
names the workflow (`digest-DATE`, which approve and reject signal). So `digest-start` refuses a day
other than today unless it is a `--resume`, and the one-run-per-day guard asks about the day the run
will be dated. A resume must name its day, so it never takes today's workflow id. To rehearse another day's news, import a clone taken that day; to run today again, use
`--force`. The production schedule passes no date and is unchanged.

Where it answers (OrbStack domains, `<project>` being the compose project, `news-digest` in the main
checkout):

- site: `http://digest-site.<project>.orb.local:8080`, and `https://digest-site.<project>.orb.local`,
  which is how mailed links (confirm, view in browser) spell it: `DIGEST_DOMAIN` is that host
  (`DEV_SITE_DOMAIN` overrides it). Also `http://127.0.0.1:8080`.
- resend-fake: `http://resend-fake.<project>.orb.local:8025`, every email and broadcast caught, with
  `/api/messages` as JSON. Its contacts (the dev audience) and caught mail are kept on the
  `resend-fake-data` volume, so a restart, `make dev-up` or `make dev-import` keeps them (`dev-import`
  does not restart it at all); `make dev-mail-clear` empties them.
- Temporal UI: `http://temporal.<project>.orb.local:8233`, also `127.0.0.1:8233`.

**Mail never leaves the machine.** Every dev service gets `RESEND_BASE_URL=http://resend-fake:8025`
and a dummy key, and the Resend client (worker and site) refuses real Resend unless
`RESEND_LIVE=true`, and refuses `RESEND_LIVE=true` beside a `RESEND_BASE_URL`
(`digest/src/resend/destination.ts`). The worker's startup line names a refused destination; the site
refuses to start. Production sets `RESEND_LIVE=true` and no base URL. Broadcasting is on in dev
(`BROADCAST_ENABLED`, default `true`), so a dev run goes through the pre-send checks, the hold and its
notification when one fails, the approve or reject, and the broadcast, all into the fake. To hold a
clean run too, as the first days after the cut-over do, start the worker with
`HOLD_ALWAYS_THROUGH=$(date -u +%F) make dev-up`; unset, it is off. Subscribe and confirm on the dev site land
there too, and a confirmed reader is a recipient of the next dev broadcast. No maintained Resend
fake covers broadcasts and segments (resend-box fakes `POST /emails` only), so `resend-fake` is ours
(`digest/src/devmail/fake.ts`), held to the SDK calls the code makes by its tests.

Several projects at once (worktrees): give each its own name and host ports, e.g.
`COMPOSE="docker compose -p mine"` with `DIGEST_SITE_PORT`, `TEMPORAL_PORT`, `TEMPORAL_UI_PORT`,
`DIGEST_PG_PORT` and `RESEND_FAKE_PORT` exported in the shell (`make band` reads the ports from the
shell, not `.env`). The OrbStack names follow the project. `make band` recreates the worker on the
band's copy with broadcasting off, so do not run it while a dev run is in flight.

## Environment notes

- Claude Code intentionally has no temperature/determinism setting
  ([claude-code#3370](https://github.com/anthropics/claude-code/issues/3370)).
  Use the API directly if a pipeline needs determinism.
