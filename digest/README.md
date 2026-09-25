# digest

The pipeline and the site, TypeScript on Temporal, in production since 2026-09-25 (spec:
`docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md`, as amended by
`docs/2026-09-24-web-tier-and-ops-decisions.md`).
Node 26, Postgres through node-postgres (schema: `db/migrations`, applied by dbmate; tests on in-process PGlite),
the Agent SDK for model stages, Temporal for sequencing, signals and the run budget.

## Layout

- `src/contracts` — the frozen contracts (spec §1) as zod: article ids, selections, coherence verdicts.
- `src/store` — the artifact store: a pointer is `(run, name, sha256)`; `put` conflicts, never replaces.
- `src/runner` — one model stage over the Agent SDK: tools scoped to Read and Grep, result as the final message.
- `src/workflow` — `DigestWorkflow`, the three signals, identity per day, one 4 h budget, the 15 min hold of a run that fails a pre-send check (every run, through `HOLD_ALWAYS_THROUGH`, the cut-over hold).
- `src/activities` — the activity interface plan A2 fills; stubs today.
- `src/cli` — `start` (one workflow, waits for the result), `schedule` (create or update the daily 10:25Z schedule).

## Commands

```
cd digest && npm install && npm test && npm run typecheck && npm run lint   # bin/ci runs these in the ci-ts container
make dev-up                            # the dev stack: Temporal (UI :8233) + the worker, the site, resend-fake, digest-pg
make digest-start                      # start today's DigestWorkflow (UTC); it sends at once, or holds 15 min if a pre-send check fails
make digest-approve                    # or digest-reject, during a hold; the send lands in resend-fake, never Resend
make digest-schedule                   # create or update the daily schedule
make dev-down                          # stop; keeps the volumes (docs/operations.md, "The dev stack")
bash scripts/check-api-names.sh        # every library name used is declared in the installed types
docker run --rm -v "$PWD":/src ghcr.io/google/osv-scanner:v2.6.0 scan source --lockfile /src/package-lock.json   # the library gate CI runs
```

## Footprint, local, stubs (2026-09-22, one workflow just completed)

```
digest-digest-worker-1   134MiB / 512MiB
digest-temporal-1        181.4MiB / 1GiB
digest-postgres-1        107MiB / 512MiB
digest-temporal-ui-1     18.33MiB
```

First end-to-end run on stubs: `{"runId":1,"stories":3,"broadcast":"sent"}`, 122 history events.

## Live smoke of the runner (one Haiku call, ~$0.002)

```
docker compose run --rm --build --no-deps digest-worker node dist/cli/smoke-stage.js
```

2026-09-22: `{"structured":{"ok":true},"costUsd":0.0017,"numTurns":2}`. The worker authenticates with
`CLAUDE_CODE_OAUTH_TOKEN` from the repo-root `.env`; a nested Claude Code session cannot run
it on the host.

## Port checks on run 300 (scratch DB `data/digest-a2.db`, `make digest-start DATE=2026-09-18 ARGS="--resume 300 --force"`)

| stage | result | archived (Python, run 300) |
|---|---|---|
| RECAP | $0.0465, 2.7 s, 1 turn; plausible, leads differently (known RECAP instability) | $0.042, 7 s |
| CLUSTER | 17 batches, 0 lost, 0 title-only, $1.27, 44 s mean per batch at 4 concurrent; 302 clusters over 659 articles, 205 identical to the archived partition | $0.95, 179 s; 289 clusters |

The join is exact (run 300's archived tags reproduce the archived 289 clusters in the test suite); the
partition differences are extraction sampling. The first CLUSTER run refused every batch holding a Hacker
News article because their summaries carry "Article URL: https://..."; the prompt now scrubs links, and
scrubbing at prepare is owed on the Python side.

## The curation span on run 300, all stages real (2026-09-22)

RECAP, CLUSTER, SELECT, WRITE, PREHEADER, COHERENCE, REPAIR and ASSEMBLE ran as activities through the
workflow on a scratch copy of the database (`--resume 300`), and the Python pipeline's `replay.py` (deleted
since; its tree is at `ae5f03d`) rendered the result with the scratch DB mounted read-only over
`/app/data/digest.db`:

```
docker compose run --rm --build -v "$(pwd)/newsroom/src:/app/src:ro" -v "$(pwd)/data/digest-a2.db:/app/data/digest.db:ro" \
  -e PYTHONPATH=/app/src -e THREADS_ENABLED=true --entrypoint /app/.venv/bin/python3 digest-newsroom /app/src/replay.py 300 --out /app/data/replay/run300-ts-<stamp>
```

16 stories assembled (5 must_know, 11 should_know), 0 dropped, 0 repaired, run-health clean, rendered by the
Python renderer. Per stage: CLUSTER $1.27, SELECT $0.55-0.68, WRITE $1.18-1.49 for 16-17 stories, COHERENCE
$0.67-0.70, PREHEADER $0.003, RECAP $0.05: about $3.8 for the span against the old band of $4.36-6.08, one
sample, not yet a band. COHERENCE failed nothing with one Grep; the planted-defect band is what can say
whether that is recall or satisficing.
