# Post-mortem: 2026-07-11 preheader schema-validation outage

**Status:** resolved — fixed, deployed (`deploy/2026-07-11-131412Z`), and recovered live (2026-07-11)
**Severity:** high — one daily digest missed its scheduled send; down ~2h45m until manual recovery

## Summary

The 2026-07-11 10:25 UTC digest completed all five curation stages (~$2.88 of
model work) and then **died in Python assembly, on the very last gate before
render**: the WRITE stage produced a **152-character preheader**, the selections
schema capped it at a hard **150**, and `validate_selections` raised. The whole
digest was thrown away over **two characters** of inbox-preview text.

The interesting part is not the two characters — it's the **class** of bug. A
purely cosmetic, best-effort field was given a hard, load-bearing invariant, so a
field that only affects an email preview could veto delivery of the entire
product. The sibling field (`not_covered_blurb`) already degraded gracefully;
the preheader didn't.

Running the incident down also surfaced a second, quieter problem that had been
sitting since the 2026-06-16 outage: **`--resume` was a lossy recovery.** It
delivered the digest but silently skipped artifact archival and story-thread
processing — exactly the "recovery restores the inbox but not the records"
failure mode the 2026-06-16 post-mortem's addendum warned about.

## Impact

- One daily digest delayed ~2h45m (scheduled/failed 10:39 UTC, delivered 13:21:54 UTC).
- No content loss: recovery reused run 229's already-paid curation verbatim.
- The external dead-man's switch correctly tripped at 13:00 UTC (after its grace
  window) — this time the observability layer worked as designed.
- No forensic loss: run 229 was **marked failed, not deleted** (a fix from the
  2026-06-16 incident), so every intermediate artifact survived on the volume and
  made a cheap `--resume` possible.

## Timeline (UTC, 2026-07-11)

- **10:25:19** — scheduled run 229 starts; fetches 496 articles to curate.
- **10:27:39 → 10:39:32** — CLUSTER ($0.70), RECAP, SELECT ($0.59), WRITE ($0.80),
  COHERENCE ($0.74) all complete. Selection cost $2.8820. COHERENCE drops one
  headline (Typhoon Bavi) as designed.
- **10:39:32** — `assemble_selections` calls `validate_selections` → raises:
  `preheader: '…' is too long` (152 vs 150). Run 229 marked **failed**;
  `/fail` ping sent.
- **10:39:34** — `news-digest.service` exits 1.
- **13:00:13** — dead-man's switch (`news-digest-deadman.service`) fires: no
  success ping since the failure. Correct behaviour.
- **~11:00–13:14** — root cause found; three fixes written test-first, reviewed
  (silent-failure-hunter ×2, code-reviewer, simplifier), full CI green (703 tests).
- **13:14:12** — fixed image deployed (`deploy/2026-07-11-131412Z`, commit `74c140d`).
- **13:16:49 → 13:21:54** — manual `--resume`: skips all five cached stages (~$0),
  re-runs assembly (now with the fix) → archival + threads → render → send.
- **13:21:54** — broadcast delivered to 11 contacts; run 230 completes; success
  ping clears both the digest-down and deadman alerts.

## Root cause

`SELECTIONS_SCHEMA.preheader` had `maxLength: 150` and no tolerance.
`merge.assemble_selections` runs `validate_selections` and raises on any schema
error, aborting the run. The preheader is model-generated inbox-preview prose;
WRITE is *told* "Max 150 characters" but, like any LLM against a soft target,
routinely lands a few characters over. There was no gap between "editorial
target" and "hard failure": the soft target *was* the hard cap.

Contrast `not_covered_blurb` (a footer garnish): it already had a
truncate-on-word-boundary guard, so a verbose value degraded instead of
aborting. The preheader — the more visible field — lacked the equivalent.

## The design questions this raised

Two decisions were worth more than the fix itself.

**1. Should a cosmetic overshoot truncate, or be tolerated?**
Truncating a 152-char line chops a whole clause and appends an ellipsis, which
reads worse than two extra characters. So the tolerance is asymmetric on purpose:
the editorial target stays 150 (what WRITE is told, what the L1 grader watches),
but the schema/abort ceiling is **157** (150 × 1.05). ≤157 ships **untouched**;
only a *gross* overshoot (a WRITE malfunction, not a nudge) is word-boundary
truncated — the same graceful-degradation stance `not_covered_blurb` uses.

**2. Should trace/analytics archival be able to block delivery?**
While making `--resume` lossless, the archival writes (`archive_selections`,
`archive_clusters`, `archive_run_artifacts`) turned out to be deliberately
fail-soft — "a trace-archival problem must never kill a digest." The instinct to
make them fail hard is tempting (silent data loss is bad), but it's wrong: for a
*systemic* DB failure, archival runs before the delivery-critical writes, which
would abort the send anyway — so fail-hard buys nothing there. The only case
where it differs is an archival-*isolated* failure, where fail-hard would
withhold the digest from subscribers over a bookkeeping problem. That's the wrong
trade. **Delivery is the product; analytics is not.**

But the real flaw wasn't fail-soft — it was fail-*silent*: archival failure was a
`logger.error` nobody watches. The fix is observability, not coupling: keep
delivering, but make the failure **loud** (surfaced via return value → warning →
alert on the unattended cron path). Fail soft, but never quiet.

## Fixes

Two commits, `deploy/2026-07-11-131412Z`:

1. **Preheader tolerance** (`merge.py`, `schema.py`). Schema cap → 157; assembly
   ships ≤157 untouched and word-boundary-truncates a gross overshoot instead of
   raising. A cosmetic field can no longer abort a delivered digest.
2. **`--resume` is now lossless** (`run.py`). Artifact archival + story-thread
   processing lived only in the full-pipeline path; a resumed run skipped them.
   Extracted into a shared `_archive_run_and_threads` helper that *both* `main()`
   and the resume path call, so a recovered run finishes identically to a normal
   one and the two paths can't drift again. (`--write-only` still skips them — they
   ran on the original pipeline.)
3. **Archival fail-soft but loud** (`db.py`, `broadcast.py`). `archive_*` now
   report failure via return value; the pipeline logs it and alerts on the cron
   path. Delivery is still never blocked by archival.

## What went well

- Run 229 was marked failed, not deleted (2026-06-16 fix) — every artifact
  survived, so recovery was a ~$0, seconds-long `--resume` rather than a $2.88
  re-run.
- The dead-man's switch fired correctly — the observability layer told the truth
  this time.
- The fix was verified on the *live delivered surface*: the recovered digest's
  `<meta name="description">` is the full untouched 152-char preheader, and run
  230 shows archival (12 artifacts) + threads (8 installments) actually ran on a
  resume for the first time.

## What went wrong

- A model-generated, cosmetic field carried a hard, delivery-gating invariant with
  zero tolerance — and no test asserted the graceful-degradation the sibling field
  had.
- `--resume` had been quietly lossy since it was introduced, restoring the inbox
  but not the run's records — the exact lesson the 2026-06-16 addendum had already
  written down, not yet acted on.

## Action items

**Done (this incident):**
- [x] Preheader tolerates a small overshoot; gross overshoot truncates; never aborts.
- [x] `--resume` runs the full downstream sequence (archival + threads) via a
      shared helper — recovery is lossless and the paths can't drift.
- [x] Archival is fail-soft **and loud** (returns status → warning → cron alert).
- [x] Each fix driven test-first; verified against live production behaviour.

**Recommended (open):**
- [ ] Audit every `maxLength`/hard invariant in `SELECTIONS_SCHEMA` for the same
      "cosmetic field, hard gate" shape (only `preheader`/`not_covered_blurb`
      carry caps today — both now degrade — but new fields should default to
      degrade-not-abort).
- [ ] A WRITE-stage self-check that trims the preheader to target *before* it
      reaches assembly, so the schema tolerance is a backstop, not the primary
      guard.
- [ ] Route the "fail soft but loud" signals through proper error tracking
      (Sentry) rather than bespoke alert emails, if/when that lands.

## Lessons

The uncomfortable part: **none of the underlying principles were new. We had
already applied or written down two of the three, and repeated the mistake
anyway.**

- *Degrade a cosmetic field, don't abort* was already **implemented** —
  `not_covered_blurb` truncates. The preheader simply never got the same
  treatment.
- *A recovery path must restore everything, not just what users see* wasn't just
  known — it was **written down three weeks earlier**, in the 2026-06-16
  post-mortem's addendum. `--resume` stayed lossy regardless.
- *Fail soft but loud* was already the house pattern (health + thread-audit
  alerts exist); archival was the one path left silent.

So the real lesson is not any of those three. It is:

- **A principle you only write down is a principle you will repeat. Knowledge
  doesn't propagate — structure does.** The durable fixes here are the ones that
  make the mistake impossible to repeat by *forgetting*: the shared
  `_archive_run_and_threads` helper means `main()` and `--resume` can no longer
  drift. The raised preheader cap is the *weaker* fix — it repairs this field but
  leaves the next cosmetic field free to hold the same load-bearing invariant. The
  2026-06-16 addendum should have been a test or a lint, not prose. **This
  post-mortem's own "recommended" items are inert until they become code** — which
  is exactly how the last one failed.
- **Verify at the level that matters** — the delivered `<meta>` tag and the run's
  DB records, not just "tests pass." (This one we did do, and it's why recovery
  was clean.)
