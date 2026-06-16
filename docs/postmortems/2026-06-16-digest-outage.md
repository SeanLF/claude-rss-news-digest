# Post-mortem: 2026-06-16 digest outage

**Status:** resolved (digest delivered same day)
**Severity:** high — one daily digest missed its scheduled send; silent for ~11h
**Author:** Sean (with Claude)

## Summary

The 2026-06-16 07:00 digest failed to send. The failure was silent: the
dedicated dead-man's switch *and* its alert path both also failed, so nothing
paged. The outage surfaced only when the missing digest was noticed by hand. A
manual re-run on a fixed image delivered the digest the same day (~18:15 UTC).

This was **four independent failures**, not one — and three of them lived in the
failure-handling and observability code, which is exactly the code that normal
runs never exercise.

## Impact

- One daily digest delayed ~11 hours (scheduled 06:15 UTC, delivered 18:15 UTC).
- No data loss after recovery: the run was rebuilt and dedup state restored.
- During the incident, two runs (201, 207-era 202) were *deleted* by the old
  failure path, destroying their forensic record (since fixed).

## Timeline (UTC, 2026-06-16)

- **06:15:27** — scheduled run starts CLUSTER over 460 articles (June-15 image).
- **06:54:20** — run fails at the output-token ceiling; `abort_run` deletes run 201.
- **09:00:14** — dead-man's switch fires, but crashes (`OSError: read-only file
  system: /app/data/digest.log`) before checking. Its `OnFailure` alert fires
  and *also* crashes (`/opt/news-digest/.env: line 4: unexpected EOF`). No alert
  sent. **Outage now fully silent.**
- **~09:47–11:07** — root cause found; fixes committed (thinking, tools, deadman).
- **17:21** — fixed image (`8cbc421`) deployed.
- **17:47:32** — manual re-trigger; CLUSTER over 598 articles (heavier than the
  failing run).
- **18:02:41** — CLUSTER completes in 905s — the fix holds.
- **18:11:20** — broadcast `c094abc5` created; **18:11:50** the send response
  read-times-out → `ResendError` → `abort_run` deletes run 202 — **even though
  Resend had accepted the send** (queued 18:11 → sent 18:15:10).
- **18:21** — `--write-only --no-email` backfill rebuilds the run record + dedup
  without re-sending. Digest confirmed delivered.

## Root causes (four independent failures)

1. **Digest — extended-thinking budget.** The Agent-SDK migration runs each
   curation stage as a top-level `query()` instead of a Task subagent. Top-level
   Sonnet defaults extended thinking **on**; subagents had it **off**. On CLUSTER
   that burned the 32k output budget reasoning over ~460 articles and tripped the
   ceiling. *Fix: `2deced3` — pass `{"type": "disabled"}`.* (Sibling: `831ff68`
   restricted each stage to its declared tools.)

2. **Detector — read-only mount.** The dead-man's switch ran with the data volume
   mounted read-only (correct) but its logging setup tried to write
   `/app/data/digest.log` and crashed before performing its check. *Fix: `4ca5f50`.*

3. **Alerting — `.env` apostrophe.** `bin/digest-alert` sourced `/opt/news-digest/.env`,
   which is written for `docker --env-file` (bare, unquoted values). `DIGEST_NAME`
   / `RESEND_FROM` contain an apostrophe, which aborts `. .env`. *Fix: `d58d698` —
   extract only the needed keys via `sed`, never source.*

4. **Broadcast — non-idempotent send + abort-deletes-everything.** A read-timeout
   on an already-accepted Resend send raised `ResendError`; the run's
   `except: abort_run()` then **deleted** the run (and its `shown_narratives`)
   even though the email had gone out. *Fix: `a0599b8` + follow-ups — verify the
   broadcast's real status before failing; mark runs failed instead of deleting;
   persist the broadcast id and make delivery idempotent.*

## Why it was silent (the real lesson)

The observability layer — dead-man's switch **and** alert — was first deployed
the day before (`7635ced`, 2026-06-15). 2026-06-16 09:00 was its **first real
activation, and every layer of it failed.** A safety net that has never been
watched succeed is not a safety net. Defense-in-depth gave a false sense of
security because no layer had ever actually run under failure conditions.

All four bugs share a shape: they live on the **unhappy path** at integration
seams (SDK defaults, filesystem mode, shell parsing, network timeout). Dry runs
and happy-path tests sail straight past them.

## What went well

- The digest pipeline rolled back cleanly (no corrupt half-state).
- Once found, root-causing was fast and fixes were validated end-to-end locally.
- The manual `--write-only --no-email` recovery worked and avoided a double-send.

## What went wrong

- A migration silently changed runtime defaults with no test asserting them.
- The entire alerting layer shipped without one successful end-to-end run.
- The failure path *deleted* forensic data, making diagnosis log-only.
- Broadcast send was non-idempotent; recovery required a manual Resend status check.

## Action items

**Done (this incident):**
- [x] All four root-cause fixes (above).
- [x] Runs marked `failed`, never deleted (forensics preserved).
- [x] Idempotent delivery: broadcast id/status persisted per date; retry
      re-probes Resend instead of blind-sending; `--resume` finishes a failed run
      from surviving artifacts; refuses stale (prior-day) or missing artifacts;
      refuses to send without recording.
- [x] Curation stage checkpointing so a re-run skips completed (expensive) stages.
- [x] Fault-injection tests for the delivery/idempotency/abort paths (TDD).

**Recommended (follow-up):**
- [ ] Deploy the fixes (the alert fix `d58d698` is unverified until applied).
- [ ] Deploy-time self-test of the deadman + alert path (deliberately trip it,
      assert a real alert email arrives). This is the gap that hid the outage.
- [ ] Pre-deploy smoke run on a realistic article volume (~500+) against the real
      SDK / mount / `.env` — would have caught failures #1 and #2 before prod.
- [ ] Reconcile `queued` broadcasts (the one delivery status we assume-but-don't-verify).
- [ ] Minor: distinguish "skipped send" from `articles_emailed=0` in run metrics.

## Lessons

- Test the failure-handling and the detectors, not just the happy path.
- A safety net is unproven until you've watched it fire successfully once.
- Migrations that change implicit defaults need a test that asserts the default.
- On the unhappy path, never destroy the evidence (mark, don't delete).
