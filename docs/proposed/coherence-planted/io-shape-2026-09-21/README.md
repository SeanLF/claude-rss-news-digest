# COHERENCE I/O-shape arms, planted278, 2026-09-21

Same fixture, same anchored date (`--today 2026-08-28`), same model and thinking (Sonnet 5,
adaptive) as the 2026-09-18 self-agreement band (`../band-2026-09-18/`). Harness:
`bin/eval-io-shape --arm ARM --runs N` (`newsroom/src/eval_coherence.py`). Each arm ran in its own
container against its own copy of the fixture; reps within an arm are sequential in one process
and share the prompt cache, so per-rep cost is order-dependent (see "Cost").

| Arm | What changes | Reps | Recall of 8 planted | False drops of 24 clean | Story 9 `why_it_matters` dropped | Retries |
|---|---|---|---|---|---|---|
| tools (shipped: Read + Write) | nothing | 1 today (+5 in the band) | 8 | 1 (band: 3,1,2,2,3) | 1/1 (band 5/5) | 0 |
| read-text | Write removed; report is the final message, parsed in code | 3 | 8, 8, **7** | 3, 3, 1 | 3/3 | 0 |
| read-schema | as read-text, final message constrained by `COHERENCE_REPORT_SCHEMA` (shape only) | 3 | 8, 8, 8 | 1, 3, 2 | 3/3 | 0 |
| inline-grep, no step 2 (first cut, prompt confounded) | corpus inline + Grep/Read; the cited-sources-only rule was missing | 3 | 8, 8, 8 | 1, 0, 1 | 2/3 | 0 |
| **inline-grep** (corrected prompt) | corpus inline in the user turn + Grep/Read to re-check before a FAIL; step 2 intact | 3 | 8, 8, 8 | **0, 0, 0** | **0/3** | 0 |

## What the data carries

- **No delivery needed a retry**: 13 of 13 reps parsed on the first attempt, and every read-schema
  rep returned its report as `structured_output`. On this fixture the "two structured retries"
  cost is zero attempts, so cost per accepted report equals cost per call.
- **The shipped loop writes once.** Tool calls were never captured before today: the control rep
  made 9 Reads and 1 Write. There is no amend loop to price against.
- **Dropping Write costs nothing in shape and a little in precision.** read-text and read-schema
  sit at the top of the band on false drops and read-text lost one planted defect once. Not a
  win.
- **The corrected inline-grep arm is the only one at zero false drops, three of three**, with
  recall flat at 8/8. The band never went below 1, and the two fields the band dropped in 4-5 of
  5 reps (stories 9 and 4, `why_it_matters`) were dropped by every read arm today and by none of
  the inline-grep reps. That is a systematic difference on the adjudication fields, not
  sampling noise; whether it is *correct* is Sean's ruling on those two labels (pre-registered in
  the band README). Recall on the planted eight is the number no label dispute can move.
- **The grep rule is followed for some fails, not all.** `unbacked_fails` (fails whose reason
  contains no Grep pattern of ≥4 chars; approximate, reported not gated) was 4, 5, 7 of 9 fails
  across the three corrected reps, with 12, 4, 2 Greps. The uncorrected reps made zero tool calls
  in 2 of 3. A prompt rule alone does not make the model grep; the transcript check is what
  makes the number honest.

## Cost

Cold-cache reps are level across arms: tools $1.02, read-text $1.14/$0.97/$0.86, read-schema
$0.93/$0.94/$0.79, inline-grep $1.07 (rep 0, cache_write 143k). The inline arm's later reps cost
$0.39-$0.43 because they reuse the cached corpus (cache_write ~17k); the tool-loop arms rewrite
124-147k of cache on every rep. In production the corpus is new every day, so read the cold
number: **cost per accepted report is ~$1 for every shape**. Output tokens: inline-grep
16-22k vs read arms 23-36k.

## What it does not carry

- Baseline n=1 today; the band is the real control and it is three days old.
- Every read-arm false drop is inside the band; no precision difference between the read arms
  and the shipped loop is supported.
- `read-schema` cannot produce the `malformed` signal the other arms can (the grammar forbids
  unknown field names), so a malformed-count difference would be the grammar, not the model.
- The fix-it-turn question (tell the agent it got the shape wrong vs a fresh attempt) is moot
  on this fixture: nothing failed to parse.

## Decision it informs

For the rewrite spec (2026-09-21 brief): the four tool-using stages return their result as the
final message (schema-constrained where code parses it), Write goes, and the checker's default
delivery is the corpus inline plus Grep/Read with the grep-per-negative rule checked in the
transcript. To be re-measured on the new runner before it ships; no band transfers.
