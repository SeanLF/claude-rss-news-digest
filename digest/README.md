# digest

The TypeScript-on-Temporal rewrite of the pipeline (spec: `docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md`).
Node 26, Postgres through node-postgres (schema: `db/migrations`, applied by dbmate; tests on in-process PGlite),
the Agent SDK for model stages, Temporal for sequencing, signals and the run budget.

## Layout

- `src/contracts` — the frozen contracts (spec §1) as zod: article ids, selections, coherence verdicts.
- `src/store` — the artifact store: a pointer is `(run, name, sha256)`; `put` conflicts, never replaces.
- `src/runner` — one model stage over the Agent SDK: tools scoped to Read and Grep, result as the final message.
- `src/workflow` — `DigestWorkflow`, the three signals, identity per day, one 4 h budget, the 2 h hold.
- `src/activities` — the activity interface plan A2 fills; stubs today.
- `src/cli` — `start` (one workflow, waits for the result), `schedule` (create or update the daily 10:25Z schedule).

## Commands

```
cd digest && npm install && npm test && npm run typecheck && npm run lint   # bin/ci runs these in the ci-ts container
make temporal-up                       # Temporal dev server (Server 1.32.0) + UI (http://127.0.0.1:8233) + the worker
make digest-start DATE=2026-09-21      # start one DigestWorkflow; it holds before broadcast for 2 h or a signal
docker compose -f digest/compose.temporal.yml exec temporal \
  temporal workflow signal -w digest-2026-09-21 --name approve --input '{"decision":"approve"}'
make digest-schedule                   # create or update the daily schedule
make temporal-down                     # stop; keeps the SQLite volume
bash scripts/check-api-names.sh        # every library name used is declared in the installed types
npm run sbom && still_active --sbom=sbom.cdx.json --fail-if-critical   # the library gate
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
docker compose --env-file .env -f digest/compose.temporal.yml run --rm --build --no-deps digest-worker node dist/cli/smoke-stage.js
```

2026-09-22: `{"structured":{"ok":true},"costUsd":0.0017,"numTurns":2}`. The worker authenticates like the newsroom
container does, with `CLAUDE_CODE_OAUTH_TOKEN` from the repo-root `.env`; a nested Claude Code session cannot run
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
workflow on a scratch copy of the database (`--resume 300`), and `replay.py` rendered the result with the
scratch DB mounted read-only over `/app/data/digest.db`:

```
docker compose run --rm --build -v "$(pwd)/newsroom/src:/app/src:ro" -v "$(pwd)/data/digest-a2.db:/app/data/digest.db:ro" \
  -e PYTHONPATH=/app/src -e THREADS_ENABLED=true --entrypoint /app/.venv/bin/python3 digest-newsroom /app/src/replay.py 300 --out /app/data/replay/run300-ts-<stamp>
```

16 stories assembled (5 must_know, 11 should_know), 0 dropped, 0 repaired, run-health clean, rendered by the
Python renderer. Per stage: CLUSTER $1.27, SELECT $0.55-0.68, WRITE $1.18-1.49 for 16-17 stories, COHERENCE
$0.67-0.70, PREHEADER $0.003, RECAP $0.05: about $3.8 for the span against the old band of $4.36-6.08, one
sample, not yet a band. COHERENCE failed nothing with one Grep; the planted-defect band is what can say
whether that is recall or satisficing.
