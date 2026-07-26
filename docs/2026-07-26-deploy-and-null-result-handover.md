# Handover — the deploy that landed, and the fix that didn't work

**2026-07-26.** Resume in a fresh session. The forward queue is in `.claude/tasks/todo.md`,
which is **gitignored** — this file is the copy that survives the machine.

## State in one paragraph

`main` is **deployed and pushed**: `deploy/2026-07-26-025737Z` at `dba76a1`, 23 commits,
tip now `0715d75`. Production was verified live, not assumed. The thread repair has been
**applied to the production database**. One change shipped in this batch is **measured not
to work** and must not be described as a fix. Nothing is left half-done.

## What went live

| verified | result |
|---|---|
| `HEALTH_ALERT_EMAIL` in prod `/opt/news-digest/.env` | present — alerting alive for the first time in months |
| circulation container | `digest-circulation-dba76a1`, blue-greened, site HTTP 200 |
| analytics queries in the image | 14 files, `bin/analytics run` works against the volume |
| pending migrations | none |

The alerting fix matters most: terraform wrote `DIGEST_ALERT_EMAIL` while the code read
`HEALTH_ALERT_EMAIL`, so alerts silently no-op'd for months. `validate_env` now checks the
recipient at startup, non-fatally — **its ABSENCE from the next run's log is the
confirmation**, not its presence.

### Thread repair — applied in production

Run 244 lost all thread continuity (the linker returned quoted ids; a strict
`isinstance(int)` discarded all 16) and re-opened five live stories as duplicates.
`0a86b45` stopped recurrence; `bin/repair-threads` (new, `0b92473`) repaired the data:

| duplicate | merged into | day count |
|---|---|---|
| 378 Spain/France wildfires | 356 Cap Ferret wildfire | 4 |
| 379 India education minister | 332 India Cockroach protests | 6 |
| 380 Zelensky/Fedorov | 261 Ukraine leadership replacement | 10 |
| 382 Kyiv drone-exhibition strike | 12 Ukraine/Russia strikes | 26 |
| 383 China–Philippines coastguard | 324 China water cannon | 5 |

Thread 12 kept all 145 questions; 0 duplicates remain; active threads 60 → 55. Each target
now carries the *newer* label, which is correct — that is what the linker matches on next.

## Watch the first run after this (10:25 UTC)

1. **No `HEALTH_ALERT_EMAIL` startup ERROR.** If it appears, terraform did not apply and
   alerting is still blind.
2. **Thread continuations > 0.** Run 244 had 16 installments and 0 continuations; normal is
   4–10. **Zero again means the linker bug has a second cause** — that is the thread to pull.
3. **`bin/analytics run run-reliability` shows 9 stages, not 7.** Run 244 dropped to 7
   because zero continuations left `thread_synthesis`/`thread_audit` nothing to do.

Then **delete `digest.db.pre-repair-20260726T025909Z`** (121 MB) from the prod volume — it is
disk on a 4 GB box, and a full restore-tested backup already exists at
`~/Backups/seanfloyd-hetzner/2026-07-26T025527Z` (SQLite backup API, integrity-checked).

## The shipped change that does not work

`8beeefe` gives WRITE the last 7 days of shipped headlines so a continuation can lead with
what changed. **Measured: no effect.** Replaying run 237 against identical archived inputs,
scoring each headline's max TF-IDF similarity to the prior week:

| arm | mean | near-dups ≥0.75 |
|---|---|---|
| archived (production, no file) | 0.390 | 1 |
| **control** (replay, no file) | 0.359 | 2 |
| **treatment** (replay, with file) | 0.385 | 2 |

The treatment sits **between two runs of the same no-file configuration**. On the story it
was built for, the control improved 0.934 → 0.864 *without the file*; the treatment reached
0.803 — still a near-duplicate. With the file the model reworded ("named" → "confirmed")
rather than moving the angle, which the prompt explicitly forbids.

**Why:** the prior headline says what *not* to write, not what *to* write. The facts that
would move the angle exist (Burnham had 7) but sit in `whats_new`, computed *after* WRITE.
Transport was right; the payload was wrong. Delivering the right one needs the ordering
change (link before WRITE) that was deferred as unjustified — it is now known-necessary.

> **SUPERSEDED the same day — do not act on the paragraph above.** The ordering change was
> tested (`bin/eval-write-arms`, 4 reps/arm) and shows **no significant effect**, and its
> premise is false: run 237's shipped *summary* already contained the Iran fact its headline
> omitted, so WRITE HAD the information and buried it. That is a prioritisation failure,
> which supplying the same information earlier cannot fix. **The refactor is dropped.**
> See `docs/2026-07-26-write-delta-poc-findings.md`.

The commit is harmless (fail-open, ~$0.006/run) and is a prerequisite for a real fix. It is
not one. Lesson: `docs/solutions/best-practices/measure-a-prompt-change-against-a-control-run.md`.

## Diagnosis that flipped: synthesis, not the auditor

Six installments recorded zero `whats_new` facts on obviously newsworthy events (a court
upholding a conviction, a rising quake toll). The auditor was the prime suspect. It is not.

`new_questions` and `resolved` come from the same model call but are **never audited**. Across
214 continuation installments: 208 with ≥1 fact, **100% of those have new_questions > 0**; all
6 zero-fact installments have **zero unaudited output too**. Perfect separation — the
generator emits an empty installment and the audit never sees anything to reject.

So the fix belongs in the synthesis prompt. Scale is 6/214 (2.8%) — **do not tune it without a
measurement harness first**. Lesson:
`docs/solutions/best-practices/an-unfiltered-sibling-tells-generator-from-validator.md`.

## Killed — do not re-propose without new data

**Delta-informs-SELECT**, on four independent lines: (a) Gulf/Iran `must_know` continuations
carry the *richest* deltas in the corpus (median 7.0 facts, **0 of 42 thin**) vs 6.0
elsewhere, so a delta signal would rank the crowding *up*; (b) the Le Pen resolution scored
0, so a gate demotes major stories; (c) no production aggregator drops on a novelty verdict —
all rank; (d) TREC Temporal Summarization's precision/recall anti-correlation was never broken
in three years of the track.

## SOTA, with citations (two sweeps, 2026-07-26)

**Cross-day story linking.** The task is closer to 1990s **TDT Topic Tracking/Link Detection**
than to cross-document event coreference — a *looser* task, so CDEC's numbers do not bound it.
Those numbers are also inflated: ACCI reports 88.4 CoNLL F1 on ECB+, but Cattan et al.,
*Realistic Evaluation Principles for CDCR* (\*SEM 2021, arXiv:2106.04192) showed removing the
topic-partition shortcut dropped a competitive model **33 F1 points**. Actionable:
**schema-constrained decoding** (Claude structured outputs, >99.9% schema validity) would have
made the run-244 quoted-id class structurally impossible; and one batched call judging 16
stories against 60 threads is **4–12× past the validated listwise regime (~5–15 items)** —
pre-filter candidates and keep the LLM as semantic arbiter. No published statistic exists for
what fraction of a curated daily digest *should* be continuations.

**LLM fact-verification over-rejection.** Real and measured (60–80% accuracy typical), with
lexical-overlap bias the named mechanism. Most relevant here: **VitaminC** (Schuster et al.,
NAACL 2021, arXiv:2103.08541) is built from 100k+ Wikipedia revisions *precisely because*
models are not robust to facts that change; *Do LLMs Truly Understand When a Precedent Is
Overruled?* (arXiv:2510.20941) finds shallow heuristics on exactly the Le Pen shape; and
**decomposition-granularity mismatch** — atomic claims checked against whole-article full
text — is a documented calibration problem, which is exactly what `thread_synthesis` does.
Note this sweep was commissioned against the wrong suspect; it still applies, because the
generator faces the same update-reasoning problem the auditor was accused of.

**Anticipation → resolution is unpublished.** A sweep of TDT/FSD, TREC Novelty, TAC Update and
TREC Temporal Summarization found **no benchmark isolating it**. Three instances with ground
truth exist here: Le Pen (in `whats_new`), Jimmy Lai's sentencing and Portugal's runoff (in the
`dedup_log` 0.80–0.90 band).

## Deferred deliberately — decisions, not oversights

- ~~**Move thread linking before WRITE.**~~ **DROPPED 2026-07-26 on measurement.** No
  significant effect at 4 reps/arm, and the premise was false — WRITE already had the fact
  and buried it in the summary. Do not re-propose without new data.
  See `docs/2026-07-26-write-delta-poc-findings.md`.
- **Schema-constrained decoding for the linker** — cheapest robustness win available.
- **Pre-filter linker candidates** — see the listwise-regime note above.
- **Verify WRITE actually *reads* its inputs.** `claude_cli.py:235-237` discards every
  non-text block; capturing `ToolUseBlock` file paths onto `StageResult` would prove reads for
  `recent_digest_headlines.txt`, `weekly_recap.txt` and `article_fulltext.json` — all
  unverified today. Touches the wrapper shared by all five stages.
- **`kept N of M` logging in `thread_synthesis`** — would have answered the
  generator-vs-validator question without a query.
- **`story_feedback` retirement** — a dead reader-vote table (shipped `9f26b0a`, reverted
  `66283c7`; 37 rows, all bot traffic). Unrelated to threads despite the `story` column name.
  Was queued to "ride a deploy"; this deploy had no migrations, so it went past. Needs a
  migration written.
- **Consolidate `thread_audit` into COHERENCE** — two overlapping verification passes,
  $0.962/run combined = 28% of spend. (Was gated on the ordering change, which is now
  dropped — so this stands on its own merits, or not at all.)
- **Crowding allocation rule.** Galtung & Ruge call it *continuity*: a running story has
  inertia independent of new developments. Every product that manages this does structural
  allocation at selection time; nobody publishes a diversity formula. Calibration first —
  median 1 Gulf `must_know` card/run, max 3, ≥3 in only 3/38 runs, so a cap would rarely bind.

## Calibration note

Three confident positions were overturned by measurement this session: that a delta signal
would fix the Iran/Gulf crowding (backtest killed it), that the auditor was over-rejecting
(the unfiltered-sibling test flipped it), and that the WRITE fix worked (the control arm
killed it). The PoC-and-prior-art detour produced all three corrections and was worth its
cost. **Anything on the queue that has not been measured should start with a measurement.**
