# Post-mortem: 2026-06-16 digest outage

**Status:** resolved — fixed, deployed, and verified live (2026-06-16)
**Severity:** high — one daily digest missed its scheduled send; silent for ~11h

## Summary

The 2026-06-16 07:00 digest failed to send, and stayed silent for ~11 hours
because the dedicated dead-man's switch *and* its alert both also failed. It
surfaced only when the missing digest was noticed by hand.

There were really **two nested incidents**, and the second is the more useful
lesson:

1. **The outage** — four independent failures in the curation, detection, and
   delivery paths.
2. **The remediation** — the fix itself shipped two bugs that passed every check
   I had (tests green, code committed, "deployed") and were caught only by
   inspecting live production behaviour.

The thread connecting both: **at every layer, a green light lied.** "Tests pass,"
"committed," "applied," "deployed," "syntax OK" each stood in for "it works," and
each was wrong. The only thing that ever told the truth was running the real
thing and observing it.

## Impact

- One daily digest delayed ~11 hours (scheduled 06:15 UTC, delivered 18:15 UTC).
- No content loss after recovery, but the run's **stats metadata was lost**: the
  18:21 backfill restored `shown_narratives` + dedup only, leaving the aggregate
  counters and `run_usage`/`source_health` empty. `/stats` showed the 16th as
  0 kept / 0 emailed / no cost until it was backfilled from the logs on
  2026-06-19 (see Addendum).
- During the incident the old failure path *deleted* two runs' records,
  destroying their forensics (since fixed — runs are now marked, not deleted).

## Timeline (UTC, 2026-06-16)

- **06:15:27** — scheduled run starts CLUSTER over 460 articles.
- **06:54:20** — fails at the output-token ceiling; `abort_run` deletes the run.
- **09:00:14** — dead-man's switch fires but crashes (`OSError: read-only file
  system: /app/data/digest.log`) before checking. Its `OnFailure` alert fires
  and *also* crashes (`/opt/news-digest/.env: line 4: unexpected EOF`). **No
  alert sent — the outage is now fully silent.**
- **~09:47–11:07** — root cause found; curation + detector fixes committed.
- **17:21** — fixed image deployed; **17:47** manual re-trigger (598 articles,
  heavier than the failing run).
- **18:02:41** — CLUSTER completes in 905s — the fix holds.
- **18:11:50** — broadcast send response read-times-out → `abort_run` deletes the
  run **even though Resend had accepted the send** (delivered 18:15:10).
- **18:21** — manual backfill rebuilds `shown_narratives` + dedup without
  re-sending. Digest confirmed delivered. (The run's aggregate counters and
  `run_usage`/`source_health` stayed empty — not noticed until 2026-06-19; see
  Addendum.)
- **(later same day)** — remediation deployed; the alert fix required two further
  corrections (see Incident 2) before it actually worked on the server.

## Incident 1 — root causes (four independent failures)

1. **Digest — extended-thinking budget.** The Agent-SDK migration runs each
   curation stage as a top-level `query()` rather than a Task subagent. Top-level
   Sonnet defaults extended thinking **on**; subagents had it **off**. On CLUSTER
   that burned the 32k output budget over ~460 articles and tripped the ceiling.
   *Fix: disable thinking; restrict each stage to its declared tools.*
2. **Detector — read-only mount.** The dead-man's switch ran with the data volume
   read-only (correct) but its logging setup tried to write `digest.log` and
   crashed before checking. *Fix: survive a read-only data mount.*
3. **Alerting — `.env` apostrophe.** `bin/digest-alert` sourced `.env`, which is
   written for `docker --env-file` (bare, unquoted). `DIGEST_NAME` contains an
   apostrophe, which aborts `. .env` — even though the alert never reads
   `DIGEST_NAME`. *Fix: extract only the needed keys via `sed`, never source.*
4. **Broadcast — non-idempotent send + abort-deletes-everything.** A read-timeout
   on an already-accepted send raised; the run's `except: abort_run()` then
   *deleted* the run and its `shown_narratives` even though the email had gone
   out. *Fix: verify the broadcast's real status before failing; mark runs failed
   instead of deleting; persist the broadcast id and make delivery idempotent.*

## Incident 2 — the fix had the same disease

The alert fix (#3) was correct in the repo, reviewed, and "deployed" — yet the
broken script stayed on the server. Two bugs, each hidden behind a green light:

- **Terraform never re-ran it.** The `null_resource` trigger was a static
  `version = "2"`. I changed the script's content but not the trigger, so
  Terraform saw no change and skipped the provisioner. Even a full deploy would
  not have updated it. *Caught by reading the live file.*
- **The shell was broken on the server.** I wrote `$$1` / `$$(...)` for shell
  vars, but Terraform only escapes `$${` (a literal `${`); a bare `$$` passes
  through literally. The server got `$$1` (= shell PID), so the vars came out
  empty. `bash -n` reported "syntax OK" because `$$1` *is* valid syntax — just
  wrong behaviour. *Caught by running the extraction and checking the values.*

Both were fixed and then **verified by observing live behaviour**: the deployed
script now extracts a non-empty API key, from-address, and alert email, and no
longer crashes on the apostrophe.

## Why it was silent (the deeper root cause)

The observability layer — dead-man's switch *and* alert — was first deployed the
day before. 2026-06-16 09:00 was its **first real activation, and every layer
failed.** A safety net that has never been watched succeed is not a safety net;
defense-in-depth gave false confidence because no layer had run under failure.

All six bugs (four in Incident 1, two in Incident 2) share a shape: they live on
the **unhappy path** or in **infra/deploy glue** — exactly the code that the
happy path, unit tests, and dry runs never exercise.

## What went well

- The curation pipeline rolled back cleanly (no corrupt half-state).
- Root-causing was fast once the logs were read.
- The manual recovery delivered the digest without a double-send.
- Live verification caught the remediation's own bugs before they were trusted.

## What went wrong

- A migration silently changed runtime defaults with no test asserting them.
- The observability layer shipped without one successful end-to-end run.
- The failure path *deleted* forensic data, making diagnosis log-only.
- The remediation trusted "tests pass / deployed" instead of observed behaviour.

## Action items

**Done (this incident):**
- [x] All six root-cause fixes, each driven by a failing test where applicable.
- [x] Runs marked `failed`, never deleted (forensics preserved).
- [x] Idempotent delivery: broadcast id/status/recipients persisted per date; a
      retry re-probes Resend instead of blind-sending; `--resume` finishes a
      failed run from surviving artifacts, refusing stale/missing ones and
      refusing to send without recording.
- [x] Curation stage checkpointing so a re-run skips completed (expensive) stages.
- [x] Alert fix deployed and **verified live** (extraction yields non-empty vars).

**Recommended (open):**
- [ ] Make the alert's Terraform trigger a content hash (`sha1(script)`) so any
      script edit forces re-provision — removes the "forgot to bump the trigger"
      footgun that caused Incident 2.
- [ ] Deploy-time self-test of the deadman + alert path (deliberately trip it,
      confirm a real alert email arrives). This single check would have caught
      both the original silent failure (#3) and the remediation's bugs.
- [ ] Pre-deploy smoke run on a realistic article volume (~500+) against the real
      SDK / mount / `.env`.

## Lessons

- **"X passed" is not "X works."** Verify at the level that matters — run the real
  thing and observe it — for anything outward-facing or deployed.
- Test the failure-handling and the detectors, not the happy path. That's where
  all six bugs lived.
- A safety net is unproven until you've watched it fire successfully once.
- On the unhappy path, never destroy the evidence (mark, don't delete).
- Migrations that change an implicit default need a test that asserts the default.
- The remediation deserves the same rigor as the original system — it can fail the
  same way.

## Addendum — stats backfill (2026-06-19)

Three days later the `/stats` dashboard still showed the 16th as 0 kept / 0
emailed / no cost: the 18:21 recovery never restored the run's aggregates or
usage. The successful 17:47 run was fully reconstructable from the prod
`digest.log` (the `Fetched X/Y` line and per-stage cost lines), cross-validated
against neighbouring runs (the recorded count matches the log's kept figure
exactly, and the 35 per-feed `kept` values sum to 823). Backfilled directly into
the prod DB after a dry run on a clone:

- `digest_runs` #203: `articles_fetched` 823, `articles_emailed` 11.
- `run_usage`: 5 stage rows, $2.83 API-equivalent total (token counts were
  unrecoverable — the live SDK events were gone — so they are 0; not shown on
  `/stats`, only in `make usage`).
- `source_health`: 35 per-feed rows (all `success=1`, sum kept 823 / fetched 2775).
- `digests` 2026-06-16: `broadcast_id`, `broadcast_status='sent'`, `recipients=11`.

Lesson reinforced: the recovery path itself needs to restore *all* of a run's
records, not just the user-visible content — and "recovered" should be verified
against the dashboard, not just the inbox.
