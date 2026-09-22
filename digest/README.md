# digest

The TypeScript-on-Temporal rewrite of the pipeline (spec: `docs/superpowers/specs/2026-09-21-four-systems-rewrite-design.md`).
Node 24, `node:sqlite` over the production `run_artifacts` table, the Agent SDK for model stages, Temporal for
sequencing, signals and the run budget.

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
make temporal-up                       # Temporal 1.32.0 + Postgres 16 + UI (http://127.0.0.1:8233) + the worker
make digest-start DATE=2026-09-21      # start one DigestWorkflow; it holds before broadcast for 2 h or a signal
docker run --rm --network digest_default temporalio/admin-tools:1.32.0 \
  temporal workflow signal --address temporal:7233 -w digest-2026-09-21 --name approve --input '{"decision":"approve"}'
make digest-schedule                   # create or update the daily schedule
make temporal-down                     # stop; keeps the Postgres volume
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
