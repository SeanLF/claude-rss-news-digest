# First end-to-end run on the Postgres product store (2026-09-24, 00:02-00:20Z)

The first whole-pipeline run since the product database moved to Postgres (44a8dfe). It repeats the
SQLite-era e2e (`../2026-09-23-e2e`) and adds a crash-and-resume.

## Setup

- A `cp -c` copy of `data/prod-20260923b.db` (runs to 305) was loaded by `bin/import-legacy` into a fresh
  Postgres 18.6 in its own compose project (`-p digest-e2e`, no host ports, own images). All 16 §5.1
  checks passed, in 15 s. The original file's sha256 was the same afterwards.
- The run was a fresh day (2026-09-24) through DigestWorkflow on local Temporal, with real model calls.
  The Python fulltext worker and the gnews-decoder library were both in the loop, and THREADS_ENABLED
  and THREAD_LATEBIND were on, as in production.
- Broadcast: BROADCAST_ENABLED=true so the run reaches the pre-send hold, but the worker had no Resend
  env, so no send was possible. The hold ended with an `approve` signal carrying `{"decision":"reject"}`.

## Crash and resume

1. At 00:09:16, after 5 of 16 WRITE branches had landed, the worker was `docker kill`ed.
2. With the worker down, the workflow was cancelled. The worker was restarted, the cancellation ran
   `abortRun`, and run 306 and attempt 306 went to `failed`.
3. `start 2026-09-24 --resume 306` opened attempt 307, and the run went `failed → running`. WRITE ran
   only the 11 missing branches (s04, s06-s15). s00-s03 and s05 were reused.
4. Nothing was duplicated:
   - 16 write calls for 16 branches, no branch twice.
   - No name has two `current` artifacts.
   - Attempt 306 wrote nothing after 00:09:14, so no zombie activity outlived the kill.
5. The trigger enforced every transition. As a negative control, `completed(rejected) → failed` and
   `completed(sent) → running` both raise `illegal run transition`.

## Numbers

| | run 306 (TypeScript on Postgres) | old system, runs 296-305 |
|---|---|---|
| cost (model_calls) | $4.62: attempt 306 $2.08, attempt 307 $2.54 | $4.20-6.40 |
| wall time | 7 m 19 s to the kill, then 9 m 28 s from resume to the hold | 779-1035 s (run 300 band) |
| stories | 16 (4 must-know, 12 should-know) | 16-22 |
| COHERENCE | 2 of 16 flagged (both headlines contradicted), both repaired, 0 dropped | |
| full text (Python worker) | 25 of 46, `completed` | |
| Google News links (gnews-decoder) | 18 of 18 decoded; 0 raw news.google.com links in either HTML | |
| threads | linker ok, 9 of 9 continuations validated, 8 syntheses, 8 audits | |
| outcome | `completed` / `rejected` | |

The cost omits the WRITE calls in flight at the kill. They never returned, so no row was recorded.

Cost by stage: write $1.37 (16 calls), cluster-extract $1.08 (15), coherence $0.63, repair_recheck $0.50,
thread_synthesis $0.35, select $0.31, repair $0.19, thread_audit $0.11, recap, weekly_recap, preheader and
thread_link $0.08.

## Rows for run 306

| table | rows | expected |
|---|---|---|
| runs | 1: `completed`, outcome `rejected`, `articles_kept` NULL (set only on a send) | yes |
| run_attempts | 2: 306 `failed` (the cancellation), 307 `completed` | yes |
| model_calls | 55, all `ok`, all with a prompt_sha256 | yes |
| artifacts | 80: 59 current, 21 quarantined (the thread artifacts, taken back by the reject) | yes |
| issues / sends / story_sources | 0 / 0 / 0 | yes: nothing is published before the hold ends |
| thread_updates | 16 during the hold, 0 after the reject | yes |
| thread_state rows with last_run_id 306 | 0 during the hold and after | yes: not visible |
| published_runs / sent_runs | 0 / 0 | yes |
| articles / source_fetches / dedup_matches | 584 / 36 (0 failed) / 4 | |

## Run health

`run-health 305 306`:
- Run 305 is clean.
- Run 306 fires `ZERO_STORIES` and `NO_THREAD_CONTINUATIONS`. Both read published rows (story_sources, and
  thread updates of published runs), and the workflow judges health on sent runs only for exactly that
  reason. Its thread artifacts show 9 validated continuations, so the second would not fire on a sent
  run. No other invariant fired.

## Render

- Web copy: 51 KB. Email: 130 KB. Issue No. 283.
- 0 internal article ids and 9 thread badges ("Ongoing · day N").
- The repaired headline shipped ("…as Tigray declares 'defensive war'", replacing "both sides declare").
- Screenshots were checked on desktop and in a 390 px iframe. They are not committed.

## Observations, not fixed

- `runs.error` keeps the failed attempt's error ("ActivityFailure: Activity cancelled ()") after the resume
  completes. The Python did the same, nothing reads it, and the attempt row is where the error belongs.
- `thread_link` records `claude-haiku-4-5-20251001` where the other Haiku stages record
  `claude-haiku-4-5`, so a group-by on `request_model` splits one model.

One run is a single sample, not a band.
