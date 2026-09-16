# COHERENCE failure kinds: the model labels them reliably, and it costs nothing (2026-09-16)

Follow-up to `docs/2026-09-16-sota-and-competitor-recheck.md` §1.2. VeriGray
([arXiv 2510.21118](https://arxiv.org/abs/2510.21118)) splits unfaithful sentences into those a
source contradicts and those that need outside knowledge to check at all ("out-dependent").
The 2026-08-30 health check wanted that split for `why_it_matters` and could not get it from
`coherence_report.json`, where absence and contradiction are one `pass: false`. The question
here: if COHERENCE is asked to name which way a field failed, does it do so reliably, and does
asking change what it catches?

## TL;DR

**Yes, and no.** Two runs of the shipped `coherence.md` plus two sentences (a `failure_kinds`
object, values `contradicted` or `unsupported`) on the frozen labelled fixture: every failed
field carried a valid kind (13 of 13), zero malformed entries, and recall and false-drops were
exactly the shipped prompt's (5 of 6 hard positives, 0 of 35 clean fields, the same idx-3
miss the prompt has never caught). Against the label-type mapping written BEFORE the runs, the
kind agreed on 4 of 5 hard positives on both runs (`eval.log`: "4 agree, 1 disagree"); the one
disagreement is a fabricated causal link the model called "contradicted" because the cited
source gives a different cause. That is defensible under VeriGray's definition, so the mapping
now abstains on LinkE, which makes the post-hoc score 4 of 4. Both numbers are reported
because the exclusion was decided after seeing the result; it is n = 1. The label is an
instrument, ready to adopt.

## Method

`bin/eval-coherence` machinery (`newsroom/src/eval_coherence.py`, production agent-SDK path,
Sonnet 5 adaptive) against a scratch copy of `newsroom/tests/fixtures/coherence_faithful/`
(6 hard positives, 8 borderline, 35 clean fields), with the agent file replaced by a copy of
`.claude/agents/coherence.md` carrying two additions:

- in the output-schema example: `"failure_kinds": {"summary": "contradicted"}`
- one paragraph after the `failed_fields` rule: when `pass` is false, include `failure_kinds`,
  one entry per failed field, exactly `contradicted` (a cited source states something
  different: wrong number, entity, scope, time window, binding, quote) or `unsupported` (no
  cited source states it at all; only outside knowledge could check it: an absent figure,
  tenure, prior event, statistic, or a causal link no source draws); both means
  `contradicted`; the label changes nothing about pass/fail.

`eval_coherence.score` now tallies `failure_kinds` per flagged field, rejects unknown values as
malformed, and compares each kind with what the label's `type` prefix implies (`OutE-`,
`unsupported-`, `invented-` mean unsupported; `EntE-`, `CircE-`, `quantifier-`, `quote-` mean
contradicted; `LinkE-` and any other prefix are not judged, and the unjudged ones are printed
as `unscored` so a relabelled fixture cannot silently empty the agreement count). The
prefixes are exact, hyphen included. A report without the field scores exactly as before, so
the eval keeps working against the shipped prompt. `score` also refuses a kind on a field the
entry did not flag (it would let a missed hallucination count as an agreement), drops an
unknown value from the count, and `expected_kind` returns None for any type it does not
recognise rather than defaulting to one side. Two runs; cost not recorded by the eval, about $2
by the stage's prod price.

## Result

| | run 0 | run 1 | shipped prompt (2026-09-03 record) |
|---|---|---|---|
| recall on hard positives | 5/6 | 5/6 | 5/6 |
| false-drops on clean fields | 0/35 | 0/35 | 0/35 |
| borderline caught | 0/8 | 1/8 | 0-2/8 |
| failed fields carrying a kind | 6/6 | 7/7 | n/a |
| malformed kinds | 0 | 0 | n/a |
| kind agrees with label type, mapping as written before the run | 4/5 | 4/5 | n/a |
| same, after abstaining on LinkE (post hoc) | 4/4 | 4/4 | n/a |

The kinds themselves, both runs identical where the same field was flagged:

| story:field | label type | model's kind |
|---|---|---|
| 0:summary ("17 killed since its resumption", scope) | CircE-scope + EntE | contradicted |
| 4:summary ("after barely two years in office") | OutE-absence | unsupported |
| 8:headline ("as Iran election looms") | EntE-wrong-entity | contradicted |
| 12:why_it_matters ("armed conflict has triggered at least 12 attacks") | LinkE-fabricated-causal | contradicted (source: "fueled by rumors") |
| 15:summary ("mirror US restrictions on chip exports") | OutE-padding | unsupported |
| 7:why_it_matters ("hours before Wang Yi arrived"), run 1 only | borderline invented-precision | unsupported |
| 2:why_it_matters (attribution), both runs | unlabelled | contradicted |

The idx-12 case is the interesting one. The label calls it a fabricated link; the model calls
it contradicted because the cited source names a different cause ("fueled by rumors"). Under
VeriGray's definition that is a contradiction, not an out-dependent claim, so the model is
right and the label-derived expectation was too coarse. `expected_kind` returns None for
`LinkE` now; a fabricated link is scored on pass/fail only.

## What this changes

Adopt the label as an instrument, which is three small changes and no policy change
(unsupported still fails, as the project rule "don't fabricate details not in the RSS summary
or fetched article text" requires):

1. `.claude/agents/coherence.md`: the two additions above, verbatim from
   `scratch/coherence-kinds/coherence.md`. Rebuild the image before verifying; agents are
   COPY'd at build.
2. `orchestrate.validate_coherence`: when `failure_kinds` is present it must be an object whose
   values are in `{contradicted, unsupported}`; absent is fine (the field is optional, so a
   model that omits it degrades to today's behaviour, never to a stage failure).
3. `run_health` / `bin/eval-stages`: count kinds per run, so the 2026-08-30 question
   ("absence or contradiction?") has a per-run number, and the repair log can be joined to it
   to see whether repair-from-cited-sources keeps unsupported fields at a different rate from
   contradicted ones.

What it does not do, so nobody reads more into it: it does not catch more (recall unchanged,
and the 2026-08-30 lesson that naming an error class in a prompt does not fix it still holds;
this names it in the output). It is the count the health check asked for, and a cleaner
labelling scheme for the planted-error fixtures, nothing else.

## Open

- Two runs on one fixture. The planted fixtures (`docs/proposed/coherence-planted/`) carry
  their own error types; relabelling them with the two-way kind and running once each would
  put the agreement number on a bigger base before it is quoted anywhere.
- Whether repair's keep rate differs by kind is the first question the count can answer, and
  the one that would change something (an unsupported claim regenerated from its own cited
  sources should vanish; a contradicted one should be corrected). Needs the prod count first.
