# Probe 0 — are the pipeline stages pure functions of their archived inputs?

**Date:** 2026-09-17
**Gates:** the orchestration architecture spike (`docs/2026-09-17-orchestration-architecture-spike.md`)
**Status:** static half COMPLETE; re-run half unblocked for COHERENCE, blocked for RECAP/SELECT/WRITE
until runs archived after this change exist.

## Verdict

**The claim is false for 6 of the 7 stages, and it was provable with zero model calls.**

The spike's method was "re-run each stage from its archived inputs and diff". That presupposes
the archive *contains* each stage's inputs. It does not. Auditing the input set statically
falsified the claim more cheaply and more certainly than a diff would have, and the couplings it
found are the architectural result the spike asked for.

PREHEADER is the only stage that is closed on both counts below.

## A. The archive was not closed under stage inputs

Files an agent prompt tells a stage to open, that `db._TRACE_ARTIFACTS` did not archive:

| File | Read by | Producer | Recoverable for a past run? |
| --- | --- | --- | --- |
| `weekly_recap.txt` | SELECT, WRITE | rolling file in `data/`, trimmed to `WEEKLY_RECAP_MAX_WEEKS = 6` (`run.py`) | **No, after ~6 weeks** — later runs append and trim it |
| `recent_rss_titles.csv` | RECAP | `prepare.py` from `get_previous_headlines(days)` | Yes, via a query that does not exist (below) |
| `sources.csv` | CLUSTER, SELECT | `prepare.py` from `sources.json` | Yes, at the run's `git_sha` |

Confirmed empirically across all of prod history, not just one run: those three names appear in
**zero** rows of `run_artifacts`, while recent runs archive 37–42 artifacts each.

    sqlite3 data/digest.db "SELECT COUNT(*) FROM run_artifacts"   # negative control: must be >0
    sqlite3 data/digest.db "SELECT artifact_name, COUNT(*) FROM run_artifacts
      WHERE artifact_name IN ('sources.csv','recent_rss_titles.csv','weekly_recap.txt')
      GROUP BY artifact_name"

Only `weekly_recap.txt` is genuinely unrecoverable, and only past the 6-week window — which
covers every run anyone would want to replay. **`recent_rss_titles.csv` is recoverable and an
earlier draft of this document said it was not.** `shown_narratives` is append-only and every row
carries `shown_at`, so substituting a run's `run_at` for `datetime('now')` in
`db.get_previous_headlines` reproduces the exact set. What is missing is the parameterised query,
not the data. Archiving it is still defensible — exact bytes, no reconstruction code to drift —
but it is a convenience, not a necessity, and it is ~83 KB/run, about 90% of the storage this
change adds (~92 KB/run total, ~16% on top of the current ~574 KB/run).

`repair_requests.json` is *not* a gap: `repair.build_repair_requests` derives it from
`coherence_report.json` + `draft_selections.json`, both archived.

### A2. A second, separate hole: the repair path's own outputs

`repair_resolution.json` is read by `merge.assemble_selections` and is built from
`repaired_fields.json` + `recheck_report.json`. All three are model outputs, none is archived, and
none is derivable — re-deriving one means paying for the model call again against inputs that are
themselves unarchived. No agent prompt names any of them, so a prompt scan cannot find them; they
are listed by hand in `test_archive_closure.UNARCHIVED_REPAIR_OUTPUTS`. **Open: closing this
changes what production records.**

## B. Two inputs are not files

4. **Wall-clock date.** `render_body` substitutes `datetime.now(UTC).date()` into the system prompt
   of every agent carrying `{{CURRENT_DATE}}` — **WRITE, COHERENCE and REPAIR**. Confirmed live:
   `make replay RUN=285` prints `INFO Date: Thursday, September 17, 2026` for a run from
   2026-09-03. This is also a trap for the re-run half itself: a harness that does not pin the date
   measures a date change and reports it as a coupling. Fixed for curation; see "What changed".
5. **A shared run budget.** `orchestrate_selections` computes
   `run_deadline = time.monotonic() + _RUN_RETRY_BUDGET_S` once and threads it into every stage, so
   a stage's retry behaviour depends on how long *earlier* stages took; run in isolation each stage
   gets a fresh 4 h. This is the invariant `retry.py` exists for and has no test. **Unfixed.**

## C. Provenance

6. `models.json` archives **only** `{"select": ...}` (`run.py`). Every other stage's model is
   recoverable from `run_usage.model`, but not from `run_artifacts` — which is the table
   `replay.py` and the eval harnesses read.
7. ~~`digest_runs.git_sha` is empty for runs 286 and 287.~~ **Retracted.** Those were two 1-second
   local verification runs in a stale dev DB, not prod runs. On a freshly cloned prod DB every run
   from 250 to 299 carries a `git_sha` (292–299 all `84288a6`). There is no provenance defect here.

## D. `_STAGES` is not the DAG

The table has five rows (cluster, recap, select, write, coherence) and the pipeline runs seven
stages: **PREHEADER and REPAIR are stages with no row**, so `orchestrate.py`'s own
`curation start: {len(_STAGES)} stages` log says 5 and runs 7. Three further edges have no node:

8. `_run_fulltext_best_effort` after SELECT — a *network* fetch producing `article_fulltext.json`,
   an input to WRITE, COHERENCE and REPAIR. Archived, so replayable.
9. `gnews.prefetch_selected` — fire-and-forget background work started after SELECT, joined at
   render.
10. The cohesion gate, which runs inside the WRITE phase.

## E. Config is an unarchived input

Eleven env-driven values are read by stage modules. Three change a stage's *output* —
`CLUSTER_EXTRACT_MODEL`, `CLUSTER_JOIN_THRESHOLD`, `COHESION_MODEL`. None are archived.

## What this means for the spike

The sequencing *is* already declarative, and the resume predicate (`_stage_output_is_valid`)
already treats stages as artifact-addressable — both support "orchestration is a DAG over
artifacts". What breaks is that the artifact set was not closed, and the two non-file leaks are a
wall clock and a shared deadline.

Those two are precisely what a durable-execution platform forces you to declare — a workflow input
and a timer. That is a real, small point in Temporal's favour, and the first in this spike that is
not also available in-place for free. It belongs in Arm 1's decision rule *before* any port is
written.

## What changed on 2026-09-17

- `db._TRACE_ARTIFACTS` gained the three files in §A, and `newsroom/tests/test_archive_closure.py`
  holds that list against the filenames the agent prompts name, so the next prompt edit cannot
  silently reopen the gap. Artifacts are written going forward only, so **runs archived before this
  change stay unreproducible for RECAP/SELECT/WRITE.**
- The run date is read once per run (`orchestrate._utc_today`) and threaded to every stage, fixing
  the UTC-midnight split *within curation*. It is **not** a whole-run invariant: `digest.py` and
  `render.py` each read the clock again for the digest key and the reader-facing date, so a run
  spanning midnight still files under the later day. Open decision.
- `claude_cli.assert_prompt_fully_rendered` refuses an unrendered `{{TOKEN}}` at the SDK seam, and
  `test_archive_closure` renders every shipped prompt in CI so a token typo is caught by `make ci`
  rather than by a 10:25Z run that then ships no digest. `bin/test-prompt` shells out to the
  `claude` CLI directly and is **not** behind that seam.
- `eval_coherence` and `eval_write_turns` were shipping the literal string `{{CURRENT_DATE}}` to
  the model while production shipped a real date — in the harness whose docstring claims it
  "reproduces the production harness exactly". Both now render; `eval_coherence` and `eval_repair`
  take `--today` so a fixture can be anchored to the date of the run it came from.

## What blocks the re-run half

Arm 1's vertical slice is CLUSTER → SELECT → WRITE, and those stages read `sources.csv` and
`weekly_recap.txt`. Until runs archived *after* this change exist, the slice can only be driven by
reconstruction, and reconstructing `weekly_recap.txt` is impossible for a run older than 6 weeks.
The earliest faithful slice is therefore the first run archived under the new list.

COHERENCE is the exception: its file inputs (`draft_selections.json`, `articles_*.csv`,
`article_fulltext.json`) were already archived, and with `--today` its wall-clock coupling is now
pinnable too. It is the one stage whose self-agreement band can be measured today. Runs 293–299
all share `git_sha` `84288a6`, so a sample drawn from them carries no code drift.

## Negative control on the instrument

`bin/replay 285` left `data/digest.db` byte-identical (md5 unchanged, all five table counts
unchanged), so the replay path is trustworthy for Probe 0's purposes.

The two 1-second runs that prompted §C7 were local verification debris in a stale dev DB; the clone
has since been reset from the verified deploy backup `2026-09-17T183822Z` and they are gone.

## Cost

Static half: zero model calls. A full 7-stage re-run is ~$6.49 API-equivalent per rep per run
(run 285 actual), so the spike's "3 runs × 2 reps" negative-control matrix is ~$39 and ~2 h — an
order of magnitude above the "no model calls beyond re-running stages" the spike budgeted.
