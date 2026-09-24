# Operations Reference

Command reference and environment notes for running news-digest. Migrated from
the untracked `.claude/learnings.md` so it survives outside one machine.

Reusable *lessons* live in [`docs/lessons/`](solutions/); incident narratives
live in [`docs/postmortems/`](postmortems/). This file is the how-to.

## Containers

The newsroom container is **one-shot**: it runs and exits. There is no
persistent container to `exec` into.

```bash
# Run the pipeline (entrypoint passes flags to run.py)
docker compose run --rm digest-newsroom --dry-run

# Query the production DB (no sqlite3 binary on the server)
docker run --rm -v news-digest-data:/app/data <image> python3 -c "..."
```

- Production volume: `news-digest-data`
- Session JSONL volume: `news-digest-claude` (`/home/appuser/.claude/`)
- Systemd unit: `news-digest.service`

## Database

```bash
bin/migrate            # apply pending (local, runs in Docker)
bin/migrate --status   # check status
bin/ssh bin/migrate    # production
```

New migrations: `migrations/YYYYMMDDHHMMSS_description.sql`. The baseline uses
`CREATE TABLE IF NOT EXISTS`, so it is safe on existing databases with no
bootstrap step.

### Reading production

`bin/ops` runs the query on the box and prints JSON -- nothing is copied, nothing
is stale:

```bash
bin/ops run|usage|health|artifacts [ID]   # ID defaults to the latest run
bin/ops artifact ID NAME                  # one archived artifact to stdout
bin/ops journal [--since 6h] [--lines 200] [--grep PAT]
bin/ops <any> --print-command             # show what would run, run nothing
```

Read-only twice over. On SQLite the volume is mounted `:ro` and SQLite opens with
`mode=ro`, both negative-controlled against the live database
(`docs/2026-09-03-ops-access-review.md`). That doc also records why this is a
CLI and not a Tailscale-only route on circulation. On Postgres psql logs in as
`digest_ro` (SELECT only, `digest/db/ops/digest_ro.sql`) in a read-only session;
`digest/src/ops/ops-payloads.test.ts` shows each refusing a write without the
other. `bin/lib/prod-store` names the store production runs on; the cut-over
flips it from `sqlite` to `postgres` (`DIGEST_PROD_STORE` overrides it for one
command).

Clone only when you need the whole database offline -- a replay harness, or
analysis across many runs. `bin/db-clone` prefers the newest verified backup (so
it is **stale** until the next deploy or nightly dump; `--live` forces a wire
copy) and fills the local Postgres clone (`digest_clone` in the dev stack's
`digest-pg`; `DIGEST_CLONE_URL` and `DIGEST_CLONE_NETWORK` point it
elsewhere), building it as `digest_clone_new` and renaming it over the old one
only once it verifies. Before the cut-over it also lands the SQLite file at
`data/digest.db` (checked with `integrity_check` and
`page_count x page_size == file size`) and imports it with `bin/import-legacy`;
after it, it restores the `digest.pg.dump` backup, or a live `pg_dump` as
`digest_ro`. `bin/usage`, `bin/trace` and `bin/analytics` read the clone through
`bin/psql`, read-only; `bin/psql` alone opens it.

## The dev stack (TypeScript pipeline, site, mail)

One compose project, `docker-compose.yml`, driven by `make`: Temporal and the worker, the TypeScript
site, and `resend-fake`, over one product database (`digest` in `digest-pg`, on a volume). The worker
writes it as `postgres`; the site reads it as `digest_ro` and runs no migrations, as on the box. Tests
and harnesses use `ci-pg`, a scratch server with no volume; a band copies `digest` with
`CREATE DATABASE ... TEMPLATE`.

```bash
make dev-import                   # a cp -c copy of data/prod-20260923b.db (SRC=...) becomes `digest`; starts the stack
make dev-up                       # start or rebuild the stack; keeps its data
make digest-start                 # today's run (UTC), through the hold
make digest-start ARGS=--force    # today again: a new revision of the issue on the site, never a second send
make digest-start DATE=2026-09-18 ARGS="--resume 300"   # resume a run; only a resume may name another day
make digest-approve               # or digest-reject (DATE defaults to today; a resume's is its DATE); unsignalled, the hold ends after 2 h and it sends
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
(`BROADCAST_ENABLED`, default `true`), so a dev run goes through the hold, its notification, the
approve or reject, and the broadcast, all into the fake; subscribe and confirm on the dev site land
there too, and a confirmed reader is a recipient of the next dev broadcast. No maintained Resend
fake covers broadcasts and segments (resend-box fakes `POST /emails` only), so `resend-fake` is ours
(`digest/src/devmail/fake.ts`), held to the SDK calls the code makes by its tests.

Several projects at once (worktrees): give each its own name and host ports, e.g.
`COMPOSE="docker compose -p mine"` with `DIGEST_SITE_PORT`, `TEMPORAL_PORT`, `TEMPORAL_UI_PORT`,
`DIGEST_PG_PORT` and `RESEND_FAKE_PORT` exported in the shell (`make band` reads the ports from the
shell, not `.env`). The OrbStack names follow the project. `make band` recreates the worker on the
band's copy with broadcasting off, so do not run it while a dev run is in flight. The legacy
`digest-newsroom` and `digest-circulation` services in the same file still take `.env`'s real
Resend key.

## Local development (the Python pipeline)

```bash
CLAUDE_CODE_OAUTH_TOKEN=$(op item get "seanfloyd.dev" \
  --fields NEWS_DIGEST_CLAUDE_OAUTH_TOKEN --reveal) \
  docker compose run --rm digest-newsroom .venv/bin/python src/run.py --dry-run
```

Requires `.env` (see `.env.example`). For `--dry-run` only display settings are
needed. Output lands in `data/output/digest-*.html`.

Run-mode flags:

- `--dry-run` -- no email, no DB writes; truncates to 20 articles
- `--no-email` -- full pipeline minus broadcast (still writes to the DB)
- `--write-only` -- re-render from existing selections
- `--force` -- override the duplicate-run guard (which fails closed)
- Use `--no-email --no-record --force` for a full-size test run

## Prompt test harness

```bash
bin/test-prompt snapshot                        # save current input
bin/test-prompt run baseline                    # run with the production prompt
bin/test-prompt run baseline --model opus --limit 5
bin/test-prompt diff <run1> <run2>
```

Custom prompts go in `newsroom/prompts/<name>.md`.

Use the `bin/test-prompt` wrapper rather than the `ci` service directly -- `ci`
mounts the host `.venv`, whose macOS symlinks break inside the container.

**The harness overwrites `data/claude_input/selections.json`**, a shared path,
so concurrent test runs clobber each other. To compare against production:

```bash
bin/ssh "sudo cat /var/lib/docker/volumes/news-digest-data/_data/claude_input/selections.json" \
  > /tmp/prod_selections.json
bin/test-prompt run <prompt> --model opus
diff <(jq --sort-keys . /tmp/prod_selections.json) \
     <(jq --sort-keys . data/runs/<run_id>/selections.json)
```

Runs are also copied to `data/runs/<run_id>/`.

### Debugging a hung harness

```bash
docker exec <container> ps aux
```

`0:00` CPU time after several minutes means stuck, most likely on auth/login.
Increasing CPU time means it is genuinely working. Stuck containers block new
`docker compose run` invocations -- kill them first.

## Environment notes

- `CLAUDE_CODE_EAGER_FLUSH=1` is required for usage tracking and is set in
  terraform, not in `docker-compose.yml`. See
  [`solutions/integration-issues/buffered-sdk-logs-need-eager-flush-before-reading.md`](solutions/integration-issues/buffered-sdk-logs-need-eager-flush-before-reading.md).
- `MODEL_NAME` controls model attribution; update via terraform tfvars.
- MCP tool unavailable locally: check `.mcp.json` uses `.venv/bin/python`, not
  `python3`.
- `ModuleNotFoundError` in production: systemd/terraform must not override the
  Docker `CMD` -- dependencies live in the venv, not global python. Do not append
  `python3 run.py` to the docker run command in `news-digest.tf`.
- Claude Code intentionally has no temperature/determinism setting
  ([claude-code#3370](https://github.com/anthropics/claude-code/issues/3370)).
  Use the API directly if a pipeline needs determinism.

---

## Superseded measurements

Kept for history. **Do not act on these** -- they describe the pre-Agent-SDK
dispatcher and have been contradicted by later work.

<details>
<summary><strong>MCP tool reliability by model (2026-02-02) -- SUPERSEDED</strong></summary>

Measured against the old thin-dispatcher architecture, which used an MCP
`write_selections` tool. Reported Opus 100%, Haiku 50-75%, Sonnet 0-25% tool-call
success on large contexts, and recommended Opus for production curation.

**Why it no longer applies:** the pipeline moved to Python-orchestrated
file-based subagents via the Agent SDK (`orchestrate.py`). Stages write files;
there is no large-context MCP tool call to fail. Production runs
CLUSTER/SELECT/WRITE/COHERENCE on `claude-sonnet-4-6` and RECAP on
`claude-haiku-4-5` reliably. The "use Opus for curation" recommendation is
stale and would roughly triple cost for no reliability gain.

The mitigation it proposed (an explicit "CRITICAL INSTRUCTION / you MUST call
the tool" block) is still a valid technique for forcing tool invocation in
small models, if that situation ever recurs.

</details>

<details>
<summary><strong>Clustering PoC results (2026-03-19) -- SUPERSEDED</strong></summary>

Compared TF-IDF, MiniLM (sbert), and model2vec against Claude's clustering over
runs 106-108. Best was MiniLM at ARI 0.497, purity 0.892, coverage 0.836,
642 MB RAM. Concluded that automated clustering could not replace editorial
judgment at 50% agreement.

**Superseded by** `docs/2026-06-26-cluster-eval-methodology.md`,
`docs/2026-06-26-cluster-eval-noground-truth-literature.md`, and the
extract-then-join CLUSTER stage shipped 2026-07-02
(`cluster_extractjoin.py`). Critically, the later work established that
Sonnet-vs-Sonnet ARI self-agreement is only 0.60-0.88, so the 0.497 figure was
being compared against a reference whose own reproducibility was never measured.
See [[a-tuned-composite-score-with-no-ground-truth-is-taste]].

The durable finding that survives: CLUSTER performs **editorial narrative
grouping, not deduplication**, so it cannot be cheaply replaced by a similarity
threshold.

</details>

<details>
<summary><strong>Language and convention notes -- moved</strong></summary>

Python 3.14 restored `except A, B:` syntax (catches both; ruff prefers the comma
form at 3.14 target). Rust unit tests live in a `#[cfg(test)]` module at the
bottom of the file they test; `tests/` is for integration tests only; small Rust
apps can stay in `main.rs` until roughly 1000 lines.

These are general language conventions rather than lessons from this codebase.

</details>

## /ask provider (circulation)

`ASK_ENABLED=true` plus `ASK_API_KEY` switch the question box on; either alone leaves it off
and the page says so. `ASK_OPENROUTER_MODELS` (comma list, walked in order) selects
OpenRouter: the base defaults to `https://openrouter.ai/api/v1`, the key is an OpenRouter key,
every request carries the whole list (`models`) so the gateway fails over inside the call,
sends `provider.data_collection=deny` so no host that trains on prompts is routed to, and
circulation retries from the next leg when a leg fails before any answer text (HTTP 429, 5xx,
an error object inside a 200 stream, or no first token within 30 s). The SSE `model` event
and `/ask.json`'s `model` field name the leg that answered. Change legs with the env, not a
code edit; model ids expire.

At most 3 legs: OpenRouter 400s a longer `models` array, which fails every request rather
than one leg, so circulation truncates and warns. Nothing in code checks price — a paid id
here WILL be billed; the guard is the key's own OpenRouter spend cap. There is no built-in
model list: ids expire, so they live in the deploy env (`news_digest_ask_openrouter_models`)
and an unset list leaves `/ask` off. `ASK_REFERER`/`ASK_TITLE` set the OpenRouter activity-log
attribution independently of `DIGEST_DOMAIN`.

Free-model quota is per ACCOUNT, not per key (1000 req/day), so news-digest and seanfloyd.dev
share one bucket and a burst on either rate-limits the other. Each has its own key for
attribution, revocation and spend caps only.

`make ask-eval` (`bin/ask-eval`) gates a candidate list on the planted-injection archive with
real calls: `ASK_OPENROUTER_MODELS=a,b bin/ask-eval`; the key comes from `OPENROUTER_API_KEY`
or 1Password's "OpenRouter" item.

