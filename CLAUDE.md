# Claude Instructions

## Project

Automated news digest: RSS feeds → Claude curation → HTML email via Resend, and a web archive.
TypeScript on Temporal since the cut-over of 2026-09-25; the Python pipeline (`newsroom/`) and the Rust
web server (`circulation/`) are deleted (last in `ae5f03d`).

Where things were decided: `docs/2026-09-24-web-tier-and-ops-decisions.md` (the decision record, which
wins over the spec it supersedes), `docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md` (the
spec), `docs/2026-09-23-temporal-cutover-runbook.md` (operating the Temporal side on the box),
`docs/2026-09-23-data-model-design.md` (the Postgres schema).

**Architecture** (one box, one Postgres; infra in seanfloyd.dev `news-digest-temporal.tf`):
- `digest/` — the TypeScript worker (`DigestWorkflow` and its activities) and the site (Hono), one
  Dockerfile with `worker`, `site` and `dev` targets. Node 26, npm.
- `digest/python/` — the Python worker: full-text extraction (trafilatura) only, on the `python` task
  queue. A run goes on without full text if it is down.
- Postgres database `digest` — the worker writes it; the site and `bin/ops` read it as `digest_ro`.

**A run** (`digest/src/workflow/digest.workflow.ts`, daily at 12:25 Europe/Paris): fetch → prepare
(dedup, opaque article ids) → weekly recap → recap and cluster (extract, then a deterministic join) →
select → full text (Python) → write, one call per story → preheader → coherence → repair → assemble →
threads and Google News link decoding → render (web and MJML email) → pre-send checks (a failure holds
the run 15 min for approve or reject) → broadcast → record.

Claude never sees URLs: the model works on article ids (`A1`, `A2`, ...) and the workflow resolves
them to URL, source and bias afterwards. Stage prompts are `digest/agents/*.md`.

## Commands

`make help` lists them all.
- **CI**: `make ci` (`bin/ci`: the TypeScript, Python worker and `bin/` scripts suites, each in its
  container, in parallel); the pre-commit hook runs `bin/ci --staged`.
- **Dev stack**: `make dev-up` / `dev-down` / `dev-urls`; `make dev-import` loads a prod clone;
  `make digest-start` runs today; `make digest-approve` / `digest-reject` during a hold. Mail goes
  to resend-fake, never Resend (`docs/operations.md`, "The dev stack").
- **Evals** (model calls, promptfoo in the worker image): `make band`, `make judges`, `make planted`,
  `make fulltext-fork`.
- **Schema**: migrations are dbmate files in `digest/db/migrations`, applied by the worker at start
  (`node dist/cli/migrate.js`); after one, `make schema-types` regenerates the row types.
- **Deploy**: `make deploy` / `make deploy-dry` (`bin/deploy`: builds and audits the three images,
  pauses the schedule, snapshots the database, applies terraform, smokes the site; refuses during a
  run and 12:00-13:45 Europe/Paris). `bin/deploy --rollback=deploy/<stamp>` redeploys a tag's digests.
- **Production reads**: `bin/ops run|usage|health|artifacts|journal` (read-only, over SSH);
  `make db-clone` then `bin/psql`, `make usage`, `make analytics`.
- **Server**: `make ssh`.

## Key paths

- `digest/src/workflow/` — the workflow, its signals and policies (Temporal replays this code).
- `digest/src/activities/` — every stage with I/O; `real.ts` wires them for the worker.
- `digest/src/runner/` — one model stage over the Claude Agent SDK.
- `digest/src/store/` — Postgres access; `schema.gen.ts` is generated.
- `digest/src/site/` — the web tier; `digest/src/render/` — web and email rendering.
- `digest/catalogue/sources.json` — the feed catalogue (served at `/sources`, read by fetch).
- `digest/templates/` — the issue's web template and `digest.css`; `design/tokens.css` — design tokens.
- `digest/db/ops/` — `bin/ops`'s payloads and `digest_ro.sql`, whose path terraform reads: do not move it.
- `bin/` — operator scripts, tested in `bin/tests` (ci-scripts).

## Layering

Workflow code (`digest/src/workflow`) imports activity types and pure helpers only: Temporal replays it,
so anything with I/O or a clock belongs in an activity. A change to a workflow's command sequence needs
a patch or a new worker build (`digest/src/deployment.ts`, the runbook's "Stranded runs").

## Working Docs

- `docs/lessons/` — reusable lessons, one per file, named for the lesson. Write one when closing an
  incident or landing a non-obvious fix, as its own commit. Convention in `docs/lessons/README.md`.
- `docs/operations.md` — command reference and environment notes.
- `docs/postmortems/` — incident narratives.
- `docs/` (dated files) — design docs, evals, handoffs; history, stale by default.

## Persistent TODO

Check `.claude/tasks/todo.md` for tasks that persist across sessions (not tracked by git).

## Don't

- Don't skip article files
- Don't skip deduplication
- Don't hardcode paths or emails
- Don't fabricate details not in the RSS summary or fetched article text
