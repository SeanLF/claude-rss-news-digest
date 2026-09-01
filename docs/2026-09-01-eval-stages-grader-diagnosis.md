# `bin/eval-stages`: which side drifted?

*2026-09-01. Diagnosis only — read-only investigation, nothing in `newsroom/src/` or
`.claude/agents/` was touched. HEAD `2e14c02`.*

## Verdict up front

**Neither side drifted for RECAP or WRITE. The graders were never calibrated against
production output in the first place.** Every numeric bound they assert
(`RECAP_MAX_CHARS = 600`, `summary_max_words = 80`, `why_it_matters_max_words = 60`,
`headline_max_words = 18`) was authored in one commit — `cddf5fd`, 2026-06-15 — against a
**hand-authored 285-character fixture** and a **24-cluster toy**, and no corresponding number
appears in `.claude/agents/recap.md` or `.claude/agents/write.md`. The prompts state
*sentence* constraints; the graders assert *word and character* constraints they invented.

The one check that genuinely tracks a moving target — SELECT's
`select_article_ids_in_cluster` — is grading a field the pipeline **deliberately abandoned**
on 2026-07-28 (`b114c6a`) precisely because it is unreliable. There the grader is right about
the data and wrong about whether it matters.

Reproduce everything below with:

```
cd newsroom && uv run python /path/to/grade_all.py   # script listed at the end
bin/eval-stages                                       # fixture gate: PASS, exit 0
```

## Measured failure rates (75 recorded runs, 204–280, `data/digest.db`)

| stage | check | fail/total | rate |
|---|---|---|---|
| RECAP | `recap_length` | 71/75 | **94.7%** |
| WRITE | `write_why_words` | 66/75 | **88.0%** |
| WRITE | `write_summary_words` | 60/75 | **80.0%** |
| SELECT | `select_article_ids_in_cluster` | 39/75 | **52.0%** |
| WRITE | `write_headline_words` | 13/75 | 17.3% |
| WRITE | `write_preheader_length` | 2/75 | 2.7% |
| *(all other 20 checks)* | — | 0/75 | **0.0%** |

Restricted to runs 241–280 (the healthy window): **RECAP 38/40 fail, SELECT 18/40 fail.**
That is close to, but not identical with, the recorded finding of "16 of 16 recaps, 15 of 31
selections" — I could not reproduce that exact denominator and do not know which run set it
used, so treat my numbers as the ones with a re-runnable command behind them.

The `bin/eval-stages` default (fixture) mode is **green, exit 0, 26/26 checks**. A tool that
passes its own gate and fails 94.7% of shipped output is the negative-control failure from
[[the-metric-was-the-problem]]: the instrument was never pointed at the population it grades.

---

## Per-check diagnosis

### 1. `recap_length` — 94.7% fail — **FIX THE CHECK**

| | |
|---|---|
| **Grader asserts** | `len(recap.strip()) <= 600` — `newsroom/src/eval_stages.py:41` (`RECAP_MAX_CHARS = 600`), enforced at `:212-217` |
| **Prompt says** | "Summarise the major themes in 2-3 sentences" / "plain text, 2-3 sentences maximum" — `.claude/agents/recap.md:14,19`. **No character bound anywhere in the prompt.** |
| **Which side moved** | *Neither.* `RECAP_MAX_CHARS` was born at its current value in `cddf5fd` (2026-06-15) and has never been edited (`git log -S RECAP_MAX_CHARS -- newsroom/src/eval_stages.py` returns exactly one commit). `recap.md` has said "2-3 sentences" since `1033839` (2026-04-01) and the only later edits were the Haiku model swap (`d2bf19f`, 2026-06-24) and effort/thinking frontmatter (`b577a4a`, 2026-06-30) — neither touched the body text. |
| **Is the output wrong?** | **No.** Production recap sentence counts across all 75 runs: `{2: 22, 3: 53}`. **100% of recorded recaps are 2 or 3 sentences** — perfect compliance with the only constraint the prompt states. `recap_sentence_count` passes 75/75. Character length: min 515, median 843, mean 848, p95 1210, max 1283. |

**Why the cap is wrong.** The 600 figure was calibrated on
`newsroom/tests/fixtures/stages/recap_good.txt` — a **286-byte, hand-written** two-sentence
recap that Sean (or the authoring agent) wrote by hand, not model output. The docstring at
`eval_stages.py:566-572` admits the real run-195 `recap.txt` fixture is the missing-input
*stub*, so a healthy baseline had to be fabricated. **The cap is a bound on a human's writing
sampled once, applied to Haiku's.** Haiku writes long, dense, semicolon-joined sentences —
run 280's three sentences run 830 characters and read fine.

The code comment at `eval_stages.py:39-40` says the cap is "Generous so it does not fail
spuriously." It fails 94.7% of the time. That comment is falsified.

**Recommendation: FIX THE CHECK.** Delete `recap_length` entirely, or raise it to ~1500 as a
runaway-dump tripwire only. `recap_sentence_count` (cap 4, `:44-46`) already enforces the
prompt's actual contract, at 100% pass, and is the check worth keeping. If a length bound is
wanted at all it must be justified against the downstream consumer (SELECT reads `recap.txt`
as context) — not against a hand-written sample of one.

---

### 2. `select_article_ids_in_cluster` — 52.0% fail — **DELETE THE CHECK**

| | |
|---|---|
| **Grader asserts** | `set(item["article_ids"]) ⊆ set(clusters[item["cluster_index"]]["article_ids"])` — `newsroom/src/eval_stages.py:308-331` |
| **Prompt says** | "`cluster_index` is the 0-based position of the cluster in the `clusters` array" (`.claude/agents/select.md:44`) and "include ALL relevant article_ids from the cluster" (`:62`). The grader reads the prompt correctly. |
| **Which side moved** | **The pipeline moved, and the grader didn't follow.** `b114c6a` (2026-07-28, *"fix(threads): derive the thread label from article ids, not a counted position"*) removed `cluster_index` as a load-bearing field. `0c5c106` the same day did the same for merge. |
| **Is the output wrong?** | **Partly — but not in a way anything downstream can see.** |

**What is actually happening.** Across 1,227 selections in 75 runs there are **735 stray
article ids. Every single one (735/735) resolves in `article_index.json`, and every single one
belongs to some other cluster in the same run.** No id is fabricated. Decomposing the 192
failing selections:

- **170 (88.5%)** have `article_ids` that all live in **exactly one** cluster — the ids are
  internally consistent and the *only* wrong field is `cluster_index`.
- **22 (11.5%)** genuinely span 2+ clusters — SELECT acting on its own "If two stories cover
  the same situation, merge or drop the weaker one" instruction (`select.md:26`).

So this is a **counting error, not a curation error**: the model mis-reports its 0-based
position into an array that in production holds **189–319 clusters** (runs 241–250). Of 80
mis-indexed entries in runs 241–280, **74 undercount and 6 overcount**, mean offset **+8.93**.

**The repo already knows this and already fixed it — everywhere except here.** Three
independent places document it:

- `newsroom/src/threads.py:202-208`: *"Keyed on the entry's `article_ids`, NOT its
  `cluster_index`. The index is a 0-based position into a several-hundred-element array that a
  model counts by eye, and it is wrong often: in run 247, 7 of 12 should_know entries indexed a
  cluster containing NONE of their own articles."*
- `newsroom/src/utils.py:38-42`: `cluster_for_articles()` exists as "the one derivation",
  explicitly because *"a positional `cluster_index`"* made merge and threads disagree.
- `newsroom/src/relabel_installments.py:4-7`: *"It is wrong ~16% of the time, so 110 of 668
  archived installments are published … under a label describing a different story."*

`grep -rn cluster_index newsroom/src/` shows **no production consumer**. Every remaining
reference is a comment explaining why it is not used, a one-off backfill, an eval, or
`threads.py:238`'s dead fallback for an entry with no ids at all (which
`threads.py:210-211` records as never having fired in 487 archived entries).

**One refinement to the prior art:** `threads.py:206` claims the offset is *"always
undercounting, the offset growing with position."* The undercount direction replicates
strongly (74:6). **The "growing with position" half does not** — Pearson r(reported_index,
offset) = **−0.118** over runs 241–280, i.e. no relationship. That claim was drawn from run
247 alone and should not be relied on.

**Recommendation: DELETE THE CHECK.** It grades a field that `b114c6a` retired, using an
assertion the rest of the codebase has three separate comments explaining why it must not
make. Keeping it means the eval fails 52% of the time on healthy runs for a reason that has
been deliberately engineered to be irrelevant. If SELECT's article-id integrity is worth
checking, the correct assertion is the one the pipeline actually depends on: **every
`article_id` in a selection resolves in `article_index.json`** (735/735 today — it would pass),
and optionally that `utils.cluster_for_articles()` returns non-`None` for every entry. Note
`select_cluster_index_resolves` (`:322-326`) passes 75/75 — the index is always *in range*,
just pointing at the wrong story, which is exactly the failure mode a range check cannot see.

---

### 3. `write_summary_words` (80.0%) and `write_why_words` (88.0%) — **FIX THE CHECK**

Outside the brief's RECAP/SELECT scope, but they fail harder than SELECT and the same root
cause applies, so they are in scope for "is this tool salvageable".

| | |
|---|---|
| **Grader asserts** | `summary <= 80 words`, `why_it_matters <= 60 words` — `newsroom/src/eval_graders.py:52-53`, applied in `eval_stages.py:404-421` |
| **Prompt says** | Summaries: *"2-3 sentences max"* (`.claude/agents/write.md:45`). Why it matters: *"One sentence"* (`:56`). **No word count appears anywhere in `write.md`.** |
| **Which side moved** | Neither. Both caps date to `219e3b0` / `cddf5fd` and the `GraderLimits` docstring (`eval_graders.py:45-47`) says outright they are *"set generously around current observed volume so they don't fail spuriously."* They were not. |
| **Is the output wrong?** | **Overwhelmingly no.** Across 1,210 stories: summary sentence counts `{1:77, 2:642, 3:435, 4:51, 5:5}` — **95.4% obey "2-3 sentences"**. `why_it_matters` sentence counts `{1:1185, 2:24, 3:1}` — **97.9% obey "one sentence"**. Meanwhile the word caps reject 31.7% of summaries and 23.1% of whys. |

**A prompt-faithful check would fail ~4.6% and ~2.1%, not 31.7% and 23.1%.** The word caps are
measuring a constraint nobody was given.

`write_headline_words` (18w) is defensible — 1.3% of headlines exceed it and `write.md:35` sets
no length rule at all, so it is a soft style tripwire rather than a spec check. It is the one
numeric cap in the file whose failure rate is low enough to be informative.

**Recommendation: FIX THE CHECK.** Replace the summary/why word caps with sentence counts
matching the prompt, or delete them. Keep `write_headline_words`.

---

### 4. `write_preheader_length` — 2.7% fail — **FIX THE CHECK (one-line, unambiguous drift)**

| | |
|---|---|
| **Grader asserts** | `len(preheader) <= 150`, `eval_graders.py:56` — comment: *"matches SELECTIONS_SCHEMA maxLength"* |
| **Schema says** | `"preheader": {"type": "string", "maxLength": 157}` — `newsroom/src/schema.py:72` |
| **Which side moved** | **The schema.** `497a05b` (2026-07-11, *"fix(newsroom): tolerate small preheader overshoot instead of aborting the digest"*) raised the schema cap 150 → 157 after the run-229 hard-fail, and declared itself *"the single source of truth for the cap"* (`schema.py:66-70`). `GraderLimits.preheader_max_chars` was not updated. **The code comment claiming the two match is now false.** |
| **Is the output wrong?** | **No.** The two failures are run 230 (152 chars) and run 272 (157) — both inside the tolerance the schema deliberately grants, both shipped, both validated. |

**Recommendation: FIX THE CHECK.** Read the cap from `SELECTIONS_SCHEMA` instead of restating
it, so the next change to one cannot silently diverge from the other. This is the smallest and
least arguable fix in the file.

---

## Why the fixture gate never caught any of this

`bin/eval-stages` with no arguments grades `newsroom/tests/fixtures/stages/` and passes 26/26.
The fixtures are not a run:

- `recap.txt` is the **missing-input stub**, so a **hand-authored** `recap_good.txt` (286 bytes,
  two sentences) is substituted by a special case in the loader (`eval_stages.py:566-579`). The
  recap check is therefore validated against text no model wrote.
- `clusters.json` holds **24 clusters covering 30 article ids — 2.6% of its own 1,176-entry
  `article_index.json`**. Production runs cluster 189–319. `select_article_ids_in_cluster`
  passes on the fixture for the mechanical reason that a model can count to 24 by eye and
  cannot count to 250. **The gate is green because the fixture is below the scale at which the
  failure exists.**

That is the whole story: the fixture was trimmed until the graders passed, then the graders
were shipped as if the fixture were representative.

`bin/eval-stages` is **not** in CI — `bin/ci` does not reference it; only `Makefile:24-25`
exposes a `make eval-stages` target. Good. It must stay out until at least `recap_length`,
`select_article_ids_in_cluster` and the two WRITE word caps are settled, or it will fail every
build on healthy output and be disabled within a day.

## Is `bin/eval-stages` salvageable?

Yes, but only the half that isn't numbers. **Twenty of its twenty-six checks pass 75/75 across
every recorded run**, and they are the checks that assert *structure*: clusters non-empty, ids
resolve in the index, no article assigned twice, tiers present and typed, counts in range,
every draft field non-empty, every source resolves, every headline has a coherence verdict,
every `pass` is a real bool. Those are cheap, load-bearing invariants with a true zero-failure
baseline on healthy data, and they would catch a real stage regression. That is a working
instrument and deleting it would throw it away. The six failing checks are all one mistake made
six times — a numeric bound invented at the grader, calibrated on a hand-written sample or a
2.6%-scale toy, that no prompt ever asked for — plus one genuine grader/pipeline divergence
(`cluster_index`) where the pipeline moved on 2026-07-28 and the grader was never told. So: do
not delete the tool, **delete the numbers**. Strip `recap_length`, `select_article_ids_in_cluster`,
`write_summary_words` and `write_why_words`; bind `preheader_max_chars` to `schema.py` rather
than restating it; replace the fixture with a real recorded run so the gate can never again be
green on a population it doesn't grade. Until that lands, `bin/eval-stages` is a broken
instrument that exits 0 on its own fixture — the exact failure this repo already wrote down in
`docs/lessons/the-metric-was-the-problem.md` — and it must not be wired into CI.

## Summary table

| check | asserts | prompt says | who drifted (sha) | rate | recommendation |
|---|---|---|---|---|---|
| `recap_length` | ≤600 chars (`eval_stages.py:41,212`) | "2-3 sentences", no char bound (`recap.md:14,19`) | neither — cap born wrong in `cddf5fd` (2026-06-15), calibrated on a hand-written 286B fixture | 94.7% | **FIX THE CHECK** (delete, or ≥1500) |
| `recap_sentence_count` | ≤4 (`:44,224`) | "2-3 sentences max" | aligned | 0% | keep — this is the real contract |
| `select_article_ids_in_cluster` | ids ⊆ `clusters[cluster_index]` (`:308-331`) | same (`select.md:44,62`) | **pipeline** — `b114c6a` (2026-07-28) retired `cluster_index`; grader not told | 52.0% | **DELETE THE CHECK** (735/735 stray ids are valid; 88.5% are index-only errors) |
| `select_cluster_index_resolves` | index in range (`:322`) | — | aligned but blind | 0% | keep (weak; cannot see a wrong-but-in-range index) |
| `write_summary_words` | ≤80w (`eval_graders.py:52`) | "2-3 sentences max" (`write.md:45`) | neither — word cap invented, no prompt counterpart | 80.0% | **FIX THE CHECK** → sentence count (95.4% comply) |
| `write_why_words` | ≤60w (`:53`) | "One sentence" (`write.md:56`) | neither — same | 88.0% | **FIX THE CHECK** → sentence count (97.9% comply) |
| `write_headline_words` | ≤18w (`:51`) | no length rule (`write.md:35`) | neither | 17.3% run / 1.3% story | keep as a style tripwire |
| `write_preheader_length` | ≤150 chars (`:56`) | "Max 150 characters" (`write.md:67`) | **schema** — `497a05b` (2026-07-11) raised `schema.py:72` to 157; grader stayed | 2.7% | **FIX THE CHECK** — read the cap from `SELECTIONS_SCHEMA` |
| 18 other checks | structural invariants | — | aligned | 0% | keep, all of them |

## Re-run script

Saved outside the repo at
`/private/tmp/claude-501/-Users-sean-Developer-news-digest/dee8a956-00c5-44c8-8b46-c7abf28ea0e8/scratchpad/`
(`grade_all.py`, `select_stray.py`, `select_offset.py`, `write_stats.py`, `recap_stats.py`,
`final.py`). Each reads `data/digest.db` read-only and runs under
`cd newsroom && uv run python <script>`. `grade_all.py` reproduces the failure-rate table;
`select_offset.py` reproduces the 170-vs-22 decomposition.

## Caveats

- All measurements are on the **local DB clone** `data/digest.db` (runs 204–280, 75 runs with a
  full artifact set). `make db-clone` was **not** run.
- I did **not** reproduce the recorded "16 of 16 / 15 of 31" figures; my equivalents on runs
  241–280 are 38/40 and 18/40. The discrepancy is unexplained — likely a different run window
  — and every number in this document comes from the scripts above, not from that finding.
- "The output is fine" for RECAP and WRITE rests on **compliance with the prompt's stated
  constraint** (sentence counts) plus reading three full recaps (runs 268, 275, 280). It is not
  an editorial-quality judgment; no claim here is that the recaps are *good*, only that they
  are not what the failing checks accuse them of being.
- The 22 genuine multi-cluster SELECT entries are consistent with `select.md:26`'s merge
  instruction, but I did not hand-verify that each merge was editorially correct.
