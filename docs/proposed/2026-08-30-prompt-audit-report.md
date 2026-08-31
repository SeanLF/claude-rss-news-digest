# Prompt audit — news-digest curation surface

**Date:** 2026-08-30 · **Method:** `claude-api/shared/prompt-audit.md` Steps 0–6, with
`model-migration.md` § Claude Sonnet 5 / § Claude Opus 5 and `prompt-caching.md` § Silent invalidators.
**Deliverables:** this report (Step 5) and `scratch/prompt-audit/proposed.diff` (Step 6).
**Nothing has been applied.** Every hunk is a proposal.

---

## Step 0 — assumptions

**Scope** (given): `.claude/agents/*.md` (6 files, 316 lines); `newsroom/src/orchestrate.py` request
config; `newsroom/src/cluster_extractjoin.py` extraction prompt; `newsroom/src/threads.py` linker.
Extended by Step 1 to `newsroom/src/thread_synthesis.py`, which carries two prompts the brief
attributed to `threads.py` (see *Corrections* below), and to `newsroom/src/eval_coherence.py` +
`newsroom/tests/test_orchestrate.py` because Step 6 requires a removal to take its references with it.

**Target model — per stage, as pinned today:**

| Prompt | Model | Audited against |
|---|---|---|
| `coherence.md` (also the repair re-check) | `claude-sonnet-5` | Sonnet 5, plus an explicit Opus 5 read-through (switch under evaluation) |
| `select.md`, `write.md`, `repair.md` | `claude-sonnet-4-6` | Sonnet 4.6 |
| `recap.md` | `claude-haiku-4-5` | Haiku 4.5 |
| `cluster.md` | `claude-sonnet-4-6` | **never invoked** — see F4 |
| `EXTRACT_SYSTEM` (`cluster_extractjoin.py`) | `config.CLUSTER_EXTRACT_MODEL`, default `claude-sonnet-4-6` | 4.6, plus the next-gen swap the code supports |
| `LINK_SYSTEM` (`threads.py`) | `claude-haiku-4-5-20251001` | Haiku 4.5 |
| `EVOLVE_SYSTEM`, `AUDIT_SYSTEM` (`thread_synthesis.py`) | `config.DEFAULT_MODEL`, default `claude-sonnet-4-6`, **env-overridable** | 4.6, plus the override path |

---

## Step 1 — inventory

Everything that reaches a model as text:

1. Six agent bodies under `.claude/agents/` — full system prompts, `render_body`-substituted.
2. Three inline prompt constants: `cluster_extractjoin.EXTRACT_SYSTEM:80`, `threads.LINK_SYSTEM:41`,
   `thread_synthesis.EVOLVE_SYSTEM:63` / `AUDIT_SYSTEM:79` / `_AUDIT_REASK:88`.
3. Request-building code: `orchestrate.py` (`_PROMPT`, `_PERMISSION_MODE`, `_THINKING`,
   `render_body`, `_CURRENT_DATE_TOKEN`, `_tool_list`, per-stage model resolution),
   `claude_cli._build_options`, `cluster_extractjoin._thinking_for`.
4. Eval prompts (`eval_why_judge`, `eval_recap_judge`, `eval_recap_ab`) — out of scope, noted only.
5. **Tool definitions: none.** The pipeline defines no custom tools; `mcp_server.py` and
   `SIGNALS_SCHEMA` are gone. The only tool surface is the SDK's built-in Read/Write, restricted per
   stage at `orchestrate.py:96–111`. **Group 3 is therefore N/A for this codebase** — nothing to
   under-describe, over-steer, or trim.

---

## Summary

**10 findings plus one requested analysis.** Findings carry more than one pattern each, so the group
tallies below overlap by design:

| Group | Count | Findings |
|---|---:|---|
| 1a — pressure language / trait claims | 2 | F7, F9 |
| 1b — scaffolds replaced by API features | 1 | F8 |
| 1c — over-specification / repetition | 3 | F2, F7, F9 |
| 1d — fossils | 4 | F1, F4, F5, F6 |
| 1e — prohibition clusters | 0 | — |
| 1f — output-shaping ceilings | 1 | F2 |
| 2 — brittle skill files | 2 | F4, F6 |
| 3 — tool descriptions | 0 | **N/A — this codebase defines no custom tools.** F3 is Group 3's *`add`* direction applied to a prompt. |
| 4 — request config and architecture | 4 | F1, F5, F10, F11 |

By confidence: **High 3 (F1, F2, F3) · Medium 5 (F4–F8) · Low 1 (F9) · Flag-only 1 (F10)**, plus the
separately-requested linker analysis (F11). Nine findings carry a hunk; F10 and F11 deliberately do not.

### The three that matter

**1. `_THINKING = {"type": "disabled"}` is a global setting whose stated cause no longer exists, and
it now contradicts the project's own written policy on the one stage where it changes behaviour.**
The comment at `orchestrate.py:60–66` blames the holistic CLUSTER agent tripping the 32k output
ceiling on ~460 articles. **That stage no longer runs**: `orchestrate.py:747–760` routes
`label == "cluster"` to `cluster_extractjoin.run_extractjoin_stage`, and `cluster.md`'s body is never
sent to a model. On the 4.x stages the constant is a no-op that merely pins the old default. It is
behaviour-*changing* on exactly two: COHERENCE (`claude-sonnet-5`) and the repair re-check that reuses
its prompt — and there it directly contradicts `cluster_extractjoin.py:50–58`, which says never send
`disabled` to a next-gen model. Prod has been doing precisely that, daily, for ~93 COHERENCE runs.
One of the two positions is wrong. The evidence says it is the comment: the "rewrite pathology" it
cites is an n=1 anecdote from a WRITE config sweep with no preserved artifact, contradicted by every
uneventful COHERENCE run since.

**2. The COHERENCE prompt caps its own coverage at one specific per field, and that is the mechanism
behind a measured 0/27 always-miss.** `coherence.md:9` says "REFUTE **the least-supported claim** in
each field"; `:21` says "Find **the single** least-supported specific"; `:48` says "extract **every
specific** from all three fields". Three sentences, two of them saying one, one saying all. Across
`scratch/coherence-models/out/{run245,lat245}__*.jsonl` — 27 runs, four models — run-245 idx 3
("50% tariffs on **most** Canadian goods" against sources saying "some" / ~5%) was caught **0/27**,
*even though probe 1 already names that exact failure with a near-verbatim example*. Naming it did
not fix it. That is direct evidence the binding constraint is the enumeration cap, not the vocabulary
— and it means the deferred "add a quantifier probe" roadmap item from `5856f35` would not have worked
either.

**3. No COHERENCE probe covers a specific that *is* in the sources but bound to the wrong scope, time
window, or event.** Probe 2 enumerates exactly three relation types (causal, comparative, attributive);
probe 3 scopes entity-binding to the **headline**. Run-245 idx 0 carries two errors squarely in that
gap — "17 killed since its resumption" (17 is the cumulative war toll; only 3 since resumption) and
"Iran struck … Bahrain and **Jordan**" (that night was Bahrain and Kuwait; Jordan was a separate
Friday strike) — and it is caught **0/27**. The irony is that probe 2 already states the right
principle ("not merely that X and Y each appear somewhere") and then scopes it away with a three-item
list. Under Sonnet 5, which "does not silently generalize an instruction from one item to another",
an enumerated list reads as exhaustive.

---

# Findings

Ordered by confidence, highest first.

---

## F1 — HIGH — global `thinking: disabled` outlived its cause and contradicts in-repo policy

| | |
|---|---|
| **Location** | `newsroom/src/orchestrate.py:60-66`, applied at `:370` |
| **Pattern** | Group 1d (fossils: "model-version workarounds… each mitigation names the model it patched; if that model is retired, remove and re-test") + Group 4 (request config) |
| **Confidence** | **High** |
| **Action** | `rewrite` — resolve per model via a shared `config.thinking_for(model)`; 4.x stages stay byte-identical |

**Evidence** (`orchestrate.py:60-66`):

> `# Extended thinking is OFF for every stage. … On the CLUSTER stage that made the model reason over ~460 articles until it tripped the 32k output-token ceiling and the run aborted. … Per-stage thinking budgets are a future tuning knob (e.g. the WRITE/SELECT judgment stages).`
> `_THINKING: ThinkingConfig = {"type": "disabled"}`

**Why obsolete — four independent reasons, in ascending order of force:**

1. **The incident's stage is gone.** `orchestrate.py:747-760` short-circuits `label == "cluster"` to
   `cluster_extractjoin.run_extractjoin_stage`. `cluster.md`'s 29-line body has not been sent to a
   model since 2026-07-01 (`cluster_extractjoin.py:14-15`: "This REPLACES the holistic cluster.md
   agent outright"). The 460-article / 32k-ceiling failure cannot recur through this constant.
2. **The later 400-rationale is measured obsolete.** `bin/sdk-canary` + `docs/2026-07-01-sdk-pinning-canary-finding.md:26-47`:
   on SDK 0.2.110 next-gen models no longer 400 on `thinking=disabled`, and Haiku 4.5 no longer 400s
   on `effort`. Both premises stale, both re-verified live.
3. **It contradicts the repo's own policy.** `cluster_extractjoin.py:50-58` retains a per-model split
   *as policy, not as a 400 dodge* — "adaptive is the validated config for next-gen" — and
   `docs/2026-07-01-sdk-pinning-canary-finding.md:44-47` says outright: *"Do NOT 'simplify' these away
   because the 400 is gone — sending `disabled` to next-gen risks the documented rewrite pathology."*
   Meanwhile `orchestrate.py` sends `disabled` to `claude-sonnet-5` on every production run. **The two
   cannot both be right.** The backing for the pathology claim is thin: it survives only as a
   secondary reference in three places (`docs/2026-06-30-sonnet5-eval-and-forward-plan.md:20`,
   `docs/2026-07-01-sonnet5-judgment-stages-preregistration.md:33-35`, a memory note), the primary
   observation is not preserved anywhere in the repo, it was on WRITE and confounded with
   `effort:high`, and it has never reproduced on ~93 COHERENCE runs at exactly that configuration.
4. **On the Sonnet 5 stages it is behaviour-*changing*, not behaviour-preserving.** Sonnet 4.6
   defaulted thinking off; Sonnet 5 defaults to adaptive. § Claude Sonnet 5 → *Adaptive vs. disabled
   thinking*: *"If the caller was running Sonnet 4.6 with thinking off, **try adaptive + `effort: "low"`
   first** rather than `thinking: {type: "disabled"}`."* And § *Tool use triggering*: *"**With thinking
   disabled**, the model is less likely to reach for tools" — for a stage whose entire output is one
   `Write` call, that is not a neutral setting.

**Counter-evidence I am not hiding.** COHERENCE thinking-off has an *independent* measurement:
`docs/2026-07-21-coherence-reframe-design.md:32-33` — *"Extended thinking ON: measured ~zero recall
benefit on Sonnet 5, slower and costlier. Not worth it."* The table at `:44-53` shows off → 4, 3
(union 5/6) and ON → 4, 4 (union 5/6). **So this is not "nobody checked."** But that measurement is
n=2 per arm, on run 245 only — the very set the same project later showed Sonnet 5 is over-fitted to
(`docs/2026-08-30-health-check-and-clustering-sota.md:557`: *"The ranking inverts between the two sets,
and that is the finding"*) — and it predates both held-out planted sets. It does not cover the decision
being made now.

**What Opus 5 changes specifically** (asked for; § Migrating to Claude Opus 5):

- **[BLOCKS, latent]** `thinking: {type: "disabled"}` with `effort` of `xhigh`/`max` returns a 400,
  validated per request. Today `effort` is unset on every stage so nothing 400s — but the frontmatter
  plumbing exists (`orchestrate.py:143-149, 187`), so the first person to write `effort: xhigh` in
  `coherence.md` gets a hard failure with no obvious link to the thinking constant.
- **Failure mode 1 — tool calls as plain text.** *"The model occasionally writes a tool call into its
  user-facing text rather than emitting a structured `tool_use` block. The turn completes normally and
  the call never runs."* COHERENCE's whole output is a `Write` call. Here it fails **loudly** —
  `validate_coherence` raises, `run_stage` retries once, then the run aborts — so this is an
  availability risk, not a correctness one. In the repair phase it is quieter: `_run_repair_phase`
  catches it and every repair drops (fail-closed, but it looks like a repair regression, not a config
  bug). Neither exists with thinking on.
- **Failure mode 2 — `<thinking>` tags leaking into visible output.** Low impact here (the report is
  written to a file, not parsed from the response), but note the two counterintuitive rules that come
  with it: *"Delete any instruction telling the model not to think or not to reason"* (see F8) and
  *"Do not name thinking tags in the prompt."*
- **The one that matters most.** I verified every record in `scratch/coherence-models/out/*.jsonl`:
  **all 49 sweep runs, all four models, all four sets, ran `"thinking": "disabled"`.** The mechanism is
  `eval_coherence.py:53-54` mirroring the prod default. So the Opus 5 arm of the decision — 9.4±1.3 vs
  Sonnet 5's 7.4±1.1, n=5, t≈2.6, described in the doc as *"marginal"* — was measured in the exact
  configuration the Opus 5 migration guide names as a documented failure mode, and for which its
  primary recommendation is *"turn thinking back on and use a lower `effort`."* **Before that t≈2.6
  decides anything, add an `opus-5 / adaptive / effort:low|medium` arm.** It costs one more sweep and
  it could move the whole comparison.
- Non-issues, checked: Opus 5's prompt-cache minimum drops to 512 tokens; `coherence.md` is ~1.7k
  (4.6 tokenizer) / ~2.2k (new tokenizer), already above both minima. No change.

**Interaction with the measured narration correlation.** Your `pearson(visible output_tokens per story,
fail%) = 0.662` (n=29, p<0.001) is the Group 1b row playing out live: with thinking off,
`coherence.md:48`'s "extract every specific … and run the three probes on each" is executed as *visible
narration*, and more narration catches more. That is evidence the scaffold **works**, which is exactly
why Group 1b says *replace*, don't rewrite. **Do not delete line 48 as part of this change.** Enable
adaptive thinking first, keep the scaffold, and only then A/B removing it — deleting it while thinking
is off would predictably regress, and 0.662 says so in advance.

A corroborating cost signal, from `docs/2026-08-30-…:577-585`: Sonnet 5 burns **3.19M cache-read
tokens/run** against Opus 5's 0.67M — ~5× — which is why Opus costs 2.5× per token yet only ~16% more
per run. Many short narrating turns, each re-reading the cached prefix, is what thinking-off buys you.
A testable prediction of this hunk: moving that reasoning into thinking blocks should collapse Sonnet
5's cache-read multiplier.

**How to test.** `bin/eval-coherence --model claude-sonnet-5 --runs 5` before and after (the harness
reads `thinking:` from frontmatter, so the change is exercised end-to-end). Success = recall on the
run-245 set no worse and false-drops still 0/35; then `scratch/coherence-models/run_matrix.py` over
planted274 + planted278 for the held-out check. Watch `run_usage.cache_read_tokens` and `duration_ms`
— those are the numbers this hunk predicts will move. One change at a time: land F1 before F2/F3.

---

## F2 — HIGH — the COHERENCE check caps itself at one specific per field

| | |
|---|---|
| **Location** | `.claude/agents/coherence.md:9` and `:21`, contradicted by `:48` |
| **Pattern** | Group 1f (numeric output ceilings / over-constraint — "removed together") + Group 1c (near-duplicate rules that disagree) |
| **Confidence** | **High** |
| **Action** | `rewrite` — see hunk F2 |

**Evidence**, three sentences, verbatim:

- `:9` — "actively try to REFUTE **the least-supported claim** in each field."
- `:21` — "Find **the single** least-supported specific (number, date, name, place, quote, statistic,
  event, duration, quantifier)."
- `:48` — "For each story, extract **every specific** from all three fields and run the three probes on
  each before writing that story's result."

**Why obsolete.** Two of three say one specific; one says all. Sonnet 5 "interprets prompts literally
and explicitly", and "does not silently generalize an instruction from one item to another" — a prompt
that says *single* twice and *every* once is resolved in favour of the operative, in-probe wording,
not the trailing rule. This is the same shape as the documented code-review-harness recall loss: an
instruction that scopes the search *down* costs recall while precision looks fine.

**The measurement that makes this a finding rather than a style note.** Over 27 runs
(`run245__*` + `lat245__*`, models haiku-4-5 / sonnet-4-6 / sonnet-5 / opus-5):

| labelled positive | class | caught |
|---|---|---:|
| idx 8 headline — "as Iran election looms" | entity-binding — **named verbatim in probe 3** | **27/27** |
| idx 12 why_it_matters — "armed conflict triggered" | fabricated causal — **named verbatim in probe 2** | 17/27 |
| idx 4 summary — "barely two years in office" | uncited duration — **named in probe 1** | 15/27 |
| idx 15 summary — bare-headline padding | no probe | 9/27 |
| idx 0 summary — scope + event-participant | **no probe** (F3) | **0/27** |
| idx 3 headline — "most" vs "some" | **named in probe 1, still 0** | **0/27** |

idx 3 is the load-bearing row. Probe 1 already carries `if sources say "some" or "many" and the story
says "most", that FAILS` — a near-verbatim description of run 245's actual error, added by `5856f35`
specifically to catch it — and it is still 0/27. The vocabulary is present; the enumeration is not.
`labels.json:_doc` records the same conclusion from the other side: *"MISSED by all 6 reframe runs
(only blind audit caught it) → known reframe blind spot."*

**How to test.** `bin/eval-coherence --model claude-sonnet-5 --runs 5`. Success = idx 3 caught in ≥1/5
with false-drops still 0/35. **The risk to watch is the opposite one**: this hunk widens the number of
specifics examined, so the intact 0-false-drop record across ~250 clean-field checks is the thing that
can regress. Then `run_matrix.py` on planted274/planted278 for the held-out confirmation.

---

## F3 — HIGH — no probe covers scope, time-window, or event-participant binding

| | |
|---|---|
| **Location** | `.claude/agents/coherence.md:23` (probe 2), `:25` (probe 3) |
| **Pattern** | Under-description (Group 3's "add" direction, applied to a prompt) + keep-list #11 ("re-baselining adds text too") + Sonnet 5 literalism |
| **Confidence** | **High** |
| **Action** | `add` — the fix is *more* text; see hunk F3 |

**Evidence.** Probe 2: `For every CAUSAL ("X triggered/forced/led to Y"), COMPARATIVE ("X rather than
Y"), or ATTRIBUTIVE ("X, per Z") relation, verify the RELATION ITSELF is stated in a cited source -- not
merely that X and Y each appear somewhere.` Probe 3: `Verify every named entity in the **HEADLINE** is
bound to the correct predicate…`

**Why obsolete.** Probe 1 tests *presence*. Probe 2 tests binding, but only for three enumerated
relation types. Probe 3 tests entity binding, but only in the headline. Run-245 idx 0 (`labels.json`,
type `CircE-scope + EntE`) is two errors that fall exactly between them:

- *"17 killed since its resumption"* — 17 **is** in the cited sources (A28/A457) as the cumulative war
  toll; only 3 are since the resumption (A574/A241). A **time-window** binding. Probe 1 finds the token
  and passes it. Probe 2's three relation types do not include it.
- *"Iran struck … Bahrain and **Jordan**"* — Jordan **is** in the cited sources (A33), as a separate
  Friday strike; that night was Bahrain and Kuwait (A5). An **event-participant** binding, in a
  *summary*, so probe 3's headline scope excludes it.

Caught 0/27. Probe 2 already states the correct general principle — *"not merely that X and Y each
appear somewhere"* — and then scopes it away with a closed list. Sonnet 5's documented literalism reads
a closed list as closed; the guide's own remedy is *"state the scope explicitly."*

**How to test.** Same harness as F2. Success = idx 0 caught in ≥1/5. Same false-drop caveat, more
acutely: "any binding the field asserts" is a wider net than "three relation types", so watch the 35
clean fields and the ~250-check false-drop record. If false-drops appear, the cheap re-baseline is to
keep the added SCOPE/TIME-WINDOW/EVENT-PARTICIPANT clauses and drop the "for every binding the field
asserts" generalisation, rather than reverting the hunk.

---

## F4 — MEDIUM — `cluster.md` still advertises itself as the live CLUSTER stage

| | |
|---|---|
| **Location** | `.claude/agents/cluster.md:3` (description) and the body |
| **Pattern** | Group 1d (fossils) + Group 2 (history narratives / volatile specifics) |
| **Confidence** | **Medium** |
| **Action** | `rewrite` — correct the description, add one status line. **Do not delete the file.** |

**Evidence.** `description: Groups news articles covering the same story into clusters. Run at the start
of the curation pipeline before SELECT.` It is not. `orchestrate.py:747-760` bypasses it;
`cluster_extractjoin.py:14-15` says it was replaced outright; `config.py:80` says the same.

**Why it still matters despite being unreachable from the pipeline.** These files have a *second*
consumer: Claude Code's own agent loader reads `.claude/agents/*.md`, which is why `cluster`,
`coherence`, `select`, `write`, `recap` and `repair` appear as dispatchable agent types in this repo.
A developer or agent invoking `cluster` here gets a prompt that produces a partition with different
semantics from production (a 25-article cap, "be aggressive about clustering", no temporal kernel).
That is Group 3's contract/behaviour mismatch, on the one piece of text — the `description` — whose job
is routing.

Not deleted because two things depend on it: `newsroom/tests/test_orchestrate.py:26` parses it as the
frontmatter fixture (7 call sites), and `config.py:80` names a code/image revert as the documented
rollback, for which this file is the definition.

**Explicitly NOT flagged, having checked:** `initialPrompt` and `description` in all six files.
`parse_agent_spec` ignores both, and no code in `newsroom/` or `bin/` reads `initialPrompt` — but the
Claude Code agent loader does. Dual consumer, not dead frontmatter. I nearly filed this as a fossil.

**How to test.** `make test` (the parse fixture must still parse). No behavioural change to the pipeline.

---

## F5 — MEDIUM — three model call sites hardcode `thinking: disabled` on an env-overridable model

| | |
|---|---|
| **Location** | `newsroom/src/threads.py:336`, `newsroom/src/thread_synthesis.py:289` (serves both `EVOLVE_SYSTEM` and `AUDIT_SYSTEM`) |
| **Pattern** | Group 1d (patch accretion — the same mitigation copied to four sites, three of which never got the guard) + Group 4 (request config) |
| **Confidence** | **Medium** |
| **Action** | `rewrite` — call the shared `config.thinking_for(model)` |

**Evidence.** `threads.py:336` and `thread_synthesis.py:289` pass `thinking={"type": "disabled"}`
unconditionally. `thread_synthesis` runs on `config.DEFAULT_MODEL` (`config.py:76`), which is
`os.environ.get("DEFAULT_MODEL", …)` — so a deploy-time env change silently sends `disabled` to whatever
model it names, including a next-gen one. `cluster_extractjoin._thinking_for` exists precisely to prevent
that and is not reachable from here.

**Why obsolete.** On the pinned defaults (Sonnet 4.6, Haiku 4.5) `disabled` is behaviour-preserving —
this is a latent, not an active, defect. It becomes active the moment `DEFAULT_MODEL` or `--model`
names a next-gen model, which is a one-line terraform change. The fix costs nothing and makes the
project's stated policy true everywhere instead of in one module.

**Layering note.** `CLAUDE.md`'s module order makes `config` a leaf, so the helper belongs there and
every consumer can import it downward. `cluster_extractjoin._thinking_for` becomes a thin typed wrapper
so nothing else has to change.

**How to test.** `make test`; the existing thread tests cover both call sites' kwargs. Behaviour on the
default models is unchanged by construction.

---

## F6 — MEDIUM — `select.md` anchors against a state that has not existed for 11+ runs

| | |
|---|---|
| **Location** | `.claude/agents/select.md:24` |
| **Pattern** | Group 1d (migration-relative phrasing: "a diff against a previous prompt version the model never saw; relative phrasing implies phantom alternatives") + Group 2 (history narratives) |
| **Confidence** | **Medium** |
| **Action** | `rewrite` — drop the clause, keep the caps and the tie-break |

**Evidence.** `This tier was historically bloated (~23 stories); cut hard.`

**Why obsolete — measured.** Distinct headlines per tier, last 11 production runs (`shown_narratives`,
runs 270–280): `must_know` 3–5 against a target of 3–5 (hard max 6); `should_know` 10–12 against a
target of 8–12 (hard max 14). Not one breach. The "~23" state the sentence argues against has not
occurred in the observable window, and a concrete number in a prompt is an anchor the model reads.

**What I checked and did not flag.** I expected to find "hard max 6 / 14" as an unenforced instruction
(`schema.py` has no `maxItems`; `merge.py` does no truncation) and to propose schema enforcement. The
measurement above kills that: the prose alone hits target on every run. Per the keep list, a working
rule stays and an audit that finds nothing changes nothing. Likewise the three overlapping
drop-when-in-doubt statements at `:24` and `:42` — with `should_know` sitting at the *top* of its range
(10–12 of 8–12) there is no evidence they are over-cutting, so they stay.

**How to test.** A SELECT-only A/B on a fixed `clusters.json` (`bin/eval-stages`), n≥3/arm. Success =
tier counts and `not_covered_blurb` quality unchanged.

---

## F7 — MEDIUM — two positional trait claims in `write.md`

| | |
|---|---|
| **Location** | `.claude/agents/write.md:63` and `:100` |
| **Pattern** | Group 1a (`you (tend to\|often\|sometimes)` trait claims → "State the desired behavior") + Group 1c (near-duplicate sentences across sections) |
| **Confidence** | **Medium** |
| **Action** | `rewrite` — keep both self-checks, replace the trait sentence with the scope statement it was reaching for |

**Evidence**, near-identical, ~40 lines apart:

- `:63` — "Writing many stories at once makes it easy to settle for an importance-sounding restatement
  on the lines you write last -- give every why_it_matters the same scrutiny as your first."
- `:100` — "Writing many stories at once makes it easy to under-cite the ones you write last -- give
  every story the same citation scrutiny as your first."

**Provenance — why this is a rewrite and not a keep.** I traced both self-checks before touching them,
and the *checks* stay. What differs is the trait half. `dbc0fe3`'s own commit message diagnosed filler
as *"an ATTENTION artifact of the full WRITE pass … under-attending to the whys written last"* and then
said plainly: *"At-scale filler-rate reduction is UNMEASURED here."* The later SELECT/WRITE arms measure
filler at 0/17 and call it a *"floor-hug — non-discriminating"*
(`docs/2026-07-01-sonnet5-judgment-stages-preregistration.md:129-160`). So the mechanism that shipped —
the strip-test — is intact and adjacent to measurement; the trait sentence is the unmeasured half of the
same commit, describing a degradation the current data does not show.

**Not touched, deliberately:** the strip-test itself, the FILLER example, all seven anti-overstatement
rules (`675848d`, eval-validated −25% on a clean same-input A/B), the citation self-check body
(`da2bbbf`, COHERENCE drops ~4 → ~1 across runs 204/205), and `recent_digest_headlines.txt`
(`8beeefe`, verified counterfactually against the production clone). Every one of those is a
prohibition against a demonstrated, measured failure — keep-list #5.

**How to test.** `bin/eval-write-arms` / `eval_why_judge` on a fixed selected set, n≥3/arm. Success =
filler rate stays at floor and citation coverage unchanged. Low stakes either way; take it or leave it.

---

## F8 — MEDIUM — "Respond IMMEDIATELY" fights the configuration the code chooses

| | |
|---|---|
| **Location** | `newsroom/src/cluster_extractjoin.py:87` |
| **Pattern** | Group 1b (latency/don't-deliberate incantation superseded by configuration) |
| **Confidence** | **Medium** |
| **Action** | `rewrite` — drop `IMMEDIATELY`, keep the output contract |

**Evidence.** `Respond IMMEDIATELY with ONLY a JSON object, no prose, no markdown fence, one item per
input article in input order:`

**Why obsolete.** It was written when extraction ran thinking-off. `_thinking_for` deliberately returns
`None` (adaptive) for next-gen models — so on a next-gen `CLUSTER_EXTRACT_MODEL` the prompt instructs
against the configuration the code just chose. Group 1b: *"On thinking models the incantation is
redundant at best; control depth via configuration, not prose."* And § Opus 5 → *Two failure modes when
thinking is disabled* is blunter: *"**Delete any instruction telling the model not to think or not to
reason.** That kind of rule *increases* tag leakage rather than suppressing it."*

Only `IMMEDIATELY` goes. "ONLY a JSON object, no prose, no markdown fence, one item per input article
in input order" is the parse contract; `parse_extract_items` is tolerant of fences and prose, not immune.

**How to test.** Re-run the extraction over an archived run's `articles_*.csv` at
`CLUSTER_EXTRACT_MODEL=claude-sonnet-4-6`, n=3. Success = `cluster_health.json` `title_only_fallback`
stays ~0 (the gate runs saw 0/498) and the cluster count is within the usual band.

---

## F9 — LOW *(hypothesis, labelled as such)* — one rule stated three times in `coherence.md`

| | |
|---|---|
| **Location** | `.claude/agents/coherence.md:21`, `:49`, `:50` |
| **Pattern** | Group 1c (padding: "repetition as reinforcement… duplicated rules make the model spend effort reconciling wordings") + Group 1a (register) |
| **Confidence** | **Low** |
| **Action** | `rewrite` — state it once, at the probe where it is operative |

**Evidence.** "Uncertainty about whether a source supports a specific is a FAIL, not a pass." appears
**verbatim** at `:21` and `:49`, plus a third variant at `:50`: "For a concrete specific, uncertainty is
a FAIL."

**What I am and am not claiming.** You asked whether emphasis density plausibly contributes to the
strictness variance. I will not present that as a finding, because the evidence does not support it:

- The adversarial register was introduced *deliberately* by `5856f35` and moved run-245 recall from 0/6
  to 3.43/6 with 0 false-drops. It is a measured win, not cruft, and Group 1a's own carve-out covers it
  ("a tested, scoped fix for one demonstrably underweighted instruction").
- The variance is real and well characterised — run 278 (a *healthy* run) replays 5.9%–23.5%; run 280
  replays 2–8 failures over 16 stories — but `docs/2026-08-30-health-check-and-clustering-sota.md:508-510`
  already labels the "strictness mood" reading **INFERRED, not measured**: observed sd 1.75 against an
  independent-Bernoulli prediction of 1.15 at n=9, needing ~25 replays to resolve.
- At least one *better-evidenced* mechanism competes for the same variance: visible-narration length,
  where you have r=0.662 at n=29, p<0.001. Emphasis density has no such number.

So the defensible, bounded version is the redundancy alone: one rule, three statements, is a documented
Group 1c pattern independent of any variance claim, and the keep list permits exactly one end-of-prompt
restatement (#10) — which this hunk preserves, at `:21`, where the rule is operative. **The register is
untouched.**

**How to test.** Not the recall eval — the band harness. `scratch/coherence-band/` at n≥9 on run 280,
before and after; success = mean recall unchanged with sd moving toward the Bernoulli prediction. Be
honest that n=9 cannot settle it; the right resolution is the ~25-replay run the doc already scoped.
The hunk is cheap to take and cheap to revert.

---

## F10 — FLAG — cache-hostile ordering in the repair re-check

| | |
|---|---|
| **Location** | `newsroom/src/orchestrate.py:556-558` (`_recheck_spec`) |
| **Pattern** | Group 4 (cache-hostile ordering) |
| **Confidence** | Medium on the pattern; the *fix* is out of scope |
| **Action** | `flag` — no hunk proposed; reasoning below |

`_recheck_spec` rewrites `draft_selections.json` → `recheck_draft.json` at `coherence.md:16`, near the
**top** of the system prompt, so the re-check shares no cached prefix with the COHERENCE call minutes
earlier in the same run. That is the documented pattern.

**Measured before proposing anything.** The divergence is bounded by the system prompt itself — ~1.7k
tokens (4.6 tokenizer) / ~2.2k (new tokenizer) — against a mean 105k cache-write and 635k cache-read per
`repair_recheck` row over 20 archived runs. So it is ~2% of the re-check's cache write, on the order of
$0.01/run. Prod caching is otherwise healthy (COHERENCE: mean 1.24M cache-read / 94k cache-write).

**Why no hunk.** The obvious fix — append a trailing path-override block instead of substituting —
places two contradictory file paths in one prompt, and `_recheck_spec`'s marker assertion exists
*specifically* to make path drift fail loudly. The clean fix is to move file paths out of all six agent
bodies and into the user turn (`_PROMPT`, currently the constant `"Begin."`), which is a design change
larger than this audit should propose and would touch `eval_coherence.load_agent_for_eval` and
`eval_repair` as well. Recording the finding and its measurement, and leaving the design call to you.

---

## F11 — separately requested: does `output_config.format` fit the *linker*?

**Yes — and the project's own write-up already carves it out.**
`docs/2026-08-21-sota-and-competitor-recheck.md:58-63`: *"The deferred todo item 'schema-constrained
decoding for the linker' is a different case and may still be worth it — the linker's failure was a
quoted ID, a pure encoding error with no judgment content, which is exactly what a grammar is good at.
Judge it on its own terms; do not carry this section's availability finding over as an endorsement."*
Still open at `:115`. I am not re-proposing structured outputs for COHERENCE or the thread audit, and
I have not.

**The asymmetry, precisely.** The audit's rejection turned on *correspondence*: `minItems: 12` on a
six-claim prompt made the model invent six verdicts for claims that did not exist, and an invented
verdict is indistinguishable from a real one — a loud failure became silent, at 2.26–2.68× cost. The
linker's failure is *encoding*: run 244 lost total continuity because the model quoted every thread id
(`"3"` instead of `3`). Those are not the same hazard.

**What to constrain, and what not to.**

- **Do constrain the element type.** `thread` typed as `integer | null` with an **`enum` of the live
  `valid_ids`** makes the run-244 failure unrepresentable. It cannot manufacture a silent failure: a
  hallucinated id is excluded by the enum, and a wrong-but-valid link is the *same* failure class the
  prompt already risks, already validated at `threads.py:359-361` and already counted and logged by the
  proposed-vs-linked detector at `:364-381`.
- **Do NOT constrain the array length.** `minItems`/`maxItems = len(today_labels)` reintroduces the
  audit's hazard in miniature — a forced entry for a story the model would have skipped — and buys
  nothing, because `link_stories` already defaults every unlinked story to `None` at `:357`. A short
  array is already handled correctly and loudly today.
- **Blocker.** `docs/2026-08-21-…:26-30`: SDK 0.2.143 carries `ClaudeAgentOptions.output_format` but
  *"`run_agent` cannot pass it through today."* So this is a `claude_cli.py` change plus a schema, not
  a prompt change — outside this audit's scope, which is why there is no hunk.
- **If it lands, finish the removal.** Per Step 6, `_parse_links`' quote-tolerance and `_as_index` exist
  only to serve the old mechanism and must go with it, along with the tests asserting the quoted-id
  shape. Otherwise the capability survives on a reachable path.

---

# Checked and left alone

An audit that finds nothing should change nothing. These matched a grep or a first instinct and are
**deliberately not findings**:

| Surface | Why it stays |
|---|---|
| `coherence.md:31-34` "Do NOT fail on" | Each entry cites a real over-drop incident. Keep-list #5; also excluded by the brief. |
| `coherence.md:16` cited-vs-non-cited distinction; `:33` analysis-vs-fact | Context only the author knows. Keep-list #1. |
| `write.md:47-54` anti-overstatement (7 rules) | `675848d`: clean same-input A/B on run 213, overstatements 1.41 → 1.06/item (−25%) at no cost. Demonstrated failure, measured fix. |
| `write.md:100-102` citation self-check body | `da2bbbf`: COHERENCE drops ~4 → ~1 across runs 204/205; the underlying under-citation hit ~25% of stories. |
| `write.md:37-43` continuing-story block | `8beeefe`: verified counterfactually against the production clone — the restated headline was in context on all four in-window re-ship days. |
| `write.md:67` "Max 150 characters" preheader | The number *is* the contract: `schema.py:72` `maxLength: 157`, with the run-229 abort behind the tolerance. Keep-list #7, format-sensitive. |
| `write.md:59-61` three why_it_matters examples | Several, varied, labelled illustrative — that is the *fix* Group 1c prescribes, not the anti-pattern. |
| `write.md:26-33` Economist/AP style spec | The product's voice and quality bar. Keep-list #1, not a tic list written against an older model. |
| `recap.md` (whole file) | Clean. 21 lines, all contract or output shape; "2-3 sentences" is a real downstream shape, and it runs on Haiku 4.5 where the Sonnet 5 verbosity re-baselining does not apply. |
| `select.md` tier caps and drop-when-in-doubt | Measured working over 11 prod runs (see F6). Not proposing schema enforcement. |
| `select.md:26-33` interest-priority table | Reference data in table form — exactly what Group 1c says tables are for. |
| `threads.py:47` "Never quote the id" | Run 244: total continuity loss because the model quoted every id. Keep-list #5, a scar with a reason. |
| `threads.py:46` "When unsure, prefer NEW" | Over-merges were the measured failure (2 in the replay); the module docstring records it. |
| `threads.py:43-45` two worked linker examples | Format-pinning on a genuinely hard judgment (arc matching). Keep-list #7. |
| `thread_synthesis.py:65` "CRITICAL GROUNDING RULE" | One scoped emphasis marker with its reason attached — Group 1a's own carve-out. |
| `thread_synthesis.py:67` "WHERE IDs GO" | Run 247 shipped internal ids to subscribers. Demonstrated failure. |
| `thread_synthesis.py:80` "N claims means N verdicts" | Run 271. The *prose* version of the thing structured outputs got wrong; keeping it is what keeps the failure loud. |
| `thread_synthesis.py:88` `_AUDIT_REASK` | A semantic re-ask with documented provenance, not a JSON-forcing prefill stack. |
| `repair.md` anti-overstatement vs `write.md`'s | Overlapping but not disagreeing, different stages. Keep-list #8, working redundancy. |
| `{{CURRENT_DATE}}` injection (`orchestrate.py:69-93`) | Load-bearing; fixes a documented stale-world-state failure. Excluded by the brief and correct. |
| `initialPrompt` / `description` frontmatter, all six files | Ignored by `parse_agent_spec`, but read by Claude Code's agent loader. Dual consumer, not dead. |

**Group 4 sweeps, all clean:**

- **API fossils:** zero hits across `newsroom/src/` for `temperature`, `top_p`, `top_k`,
  `budget_tokens`, `stop_sequences`, `max_tokens`, trailing assistant-turn prefill, or beta headers.
  Nothing to remove for any target model.
- **Caching silent invalidators** (greps run per `prompt-caching.md`): no `uuid4`, no request ids, no
  per-user interpolation, no conditional system sections, no unsorted `json.dumps` in a prompt path.
  `render_body`'s `{{CURRENT_DATE}}` is the only dynamic system-prompt content, it changes once per UTC
  day, and every stage runs once per day — it cannot invalidate a within-run cache. Prod cache reads are
  healthy. One residual pattern is F10.
- **LLM executor for a deterministic plan:** counted all 10 model call sites (N extraction batches,
  recap, select, write, coherence, repair, repair re-check, linker, evolve, audit). Each carries genuine
  judgment. The project has already run this exact removal once — the holistic CLUSTER agent → a
  deterministic TF-IDF join — and the deterministic linker was built, measured (caught 1 of ~9 obvious
  multi-day threads) and correctly rejected. Nothing left to de-LLM.
- **Redundant specialist sub-agents:** the roster is six agents with six distinct jobs; `coherence.md`
  is *reused* for the re-check rather than duplicated (`_recheck_spec`), which is the right shape.
- **Token accounting:** present and good (`run_usage`, per-subagent, SDK `total_cost_usd`). The
  prerequisite for measuring any of this is already in place.

---

# Corrections to the brief

1. **The thread audit prompt is not in `threads.py`.** `AUDIT_SYSTEM` and `_AUDIT_REASK` are at
   `newsroom/src/thread_synthesis.py:79` and `:88`; `threads.py` carries `LINK_SYSTEM` only. I audited
   both.

2. **Your prime suspect is right, and stronger than you put it.** The CLUSTER incident did not merely
   generalise from one stage — **that stage no longer runs an LLM prompt at all**
   (`orchestrate.py:747-760`). The premise is not stale, it is void.

3. **But COHERENCE's thinking-off is not purely inherited.** It has its own measurement:
   `docs/2026-07-21-coherence-reframe-design.md:32-33`, *"Extended thinking ON: measured ~zero recall
   benefit on Sonnet 5, slower and costlier."* The right framing is not "nobody checked" — it is that
   the check was n=2/arm, on the run-245 training set only, and predates both held-out sets and the
   Opus 5 question. Presenting F1 as "an unexamined global" would overstate it.

4. **The codebase holds two contradictory thinking policies, and prod is running the one its own docs
   forbid.** `cluster_extractjoin.py:50-58` and `docs/2026-07-01-sdk-pinning-canary-finding.md:44-47`
   say never send `disabled` to next-gen; `orchestrate.py:66` sends it to `claude-sonnet-5` every day.
   The "rewrite pathology" backing the prohibition has **no preserved primary artifact** anywhere in the
   repo, was observed on WRITE confounded with `effort:high`, and has not reproduced on ~93 COHERENCE
   runs at exactly that configuration.

5. **Finding 4 is already recorded — what is new is the cause.** The existence of the two
   never-caught errors is in `docs/2026-08-30-health-check-and-clustering-sota.md:597-604`. This audit
   adds: the exact rate (**0/27 each**, verified from the JSONL), which probe covers which error class
   and why the coverage gap falls exactly there, and — the useful part — that the deferred *"add a
   quantifier probe"* roadmap item from `5856f35` **would not have worked**, because probe 1 already
   contains a near-verbatim quantifier example and still misses 0/27. The binding constraint is the
   one-specific-per-field cap, not the vocabulary.

6. **All 49 sweep runs deciding Opus 5 vs Sonnet 5 ran `thinking: disabled`** — verified across every
   record in `scratch/coherence-models/out/*.jsonl`, all four models, all four sets. That includes the
   Opus 5 arm, which the migration guide says is the configuration to avoid on that model. This is the
   single most actionable thing in the report.

---

# What to do first

**Timing note.** The working tree carries an uncommitted SDK bump, `claude-agent-sdk` 0.2.143 →
0.2.148 (`newsroom/constraints-prod.txt`), and `newsroom/tests/test_sdk_pin.py` forces `bin/sdk-canary`
on exactly that event. Both of the canary's premises are already stale on 0.2.110 and both are load-
bearing for F1 — so the canary run you already owe for this bump is also the re-verification F1 needs.
Do it once, for both.

1. **Add an `opus-5 / adaptive` arm to the sweep before the Opus 5 decision.** One more pass through
   `run_matrix.py`. It is cheap, and the current marginal t≈2.6 was measured with the Opus arm in a
   documented-degraded configuration.
2. **Land F1 alone**, re-run `bin/eval-coherence --model claude-sonnet-5 --runs 5`, and watch
   `cache_read_tokens` and `duration_ms` — this hunk makes a falsifiable prediction about both.
3. **Then F2 and F3, one at a time**, on both the run-245 set and the held-out planted sets. These two
   target a 0/27 blind spot and are the only changes here with real upside on recall — and the only two
   with real downside risk on the intact 0-false-drop record.
4. F4–F9 are low-stakes hygiene. Take or leave individually.

Removal is a hypothesis, not a conclusion (Step 7). Every hunk above names the probe that would refute it.

## Not done, deliberately

`docs/2026-06-25-cluster-thinking-config-ab-design.md`, `docs/2026-06-30-sonnet5-eval-and-forward-plan.md`
and `docs/2026-07-01-sdk-pinning-canary-finding.md` all reference `_THINKING` by name. Step 6 says a
removal takes its references with it, but these are **dated design docs recording past decisions**, and
`CLAUDE.md` marks `docs/` dated files as stale by default. Rewriting them would falsify the record.
If F1 lands, the right move is a new dated doc superseding them, not an edit to them.
