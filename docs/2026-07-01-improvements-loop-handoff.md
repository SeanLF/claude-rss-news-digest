# Handoff — five digest improvements, executed in a loop to the high bar (2026-07-01)

For a FRESH session. Execute the five items below in order, each to completion (validated +
reviewed + committed), then move to the next — a loop. Deploy is explicitly HELD (see §Stop line).
Plan in this session, build in a fresh one — this doc is the bridge (the project's ~3× token-
efficiency rule).

## The bar (non-negotiable — this is what "high bar" means here)
Apply to EVERY item before calling it done:
- **Verify with real output, not proxies.** Run it the way prod does (Docker), inspect the actual
  digest/clusters/tags by eye. "Tests pass" ≠ done. A metric reading 0 is a trap, not a pass.
- **Eval discipline** (for anything measured): pre-register the gate/bar in a doc BEFORE looking at
  results; measure the reference's own rep band (n≥6); use task-grounded PRODUCT signals, never
  ARI/BCubed as a gate (smoke only); cross-family + order-swapped judges; add a deflation step
  ("what did I overclaim?"). See `feedback_eval_ill_posed_metric`, `docs/2026-06-26-cluster-eval-
  methodology.md`, `docs/2026-07-01-graph-gate-preregistration.md`.
- **TDD**: failing test first; reproduce a reported bug with a failing test before fixing.
- **Review gate before every commit**: code-reviewer + silent-failure-hunter (on error handling) +
  code-simplifier. Skip ONLY for trivial/build/docs changes via `touch /tmp/claude-commit-force-
  <session_id>` (separate Bash call BEFORE the commit; re-touch after any pre-commit failure).
- **CI green**: `make ci-fix` (Docker; uses the CI image's ruff — host ruff differs and will
  mislead you). Commits: conventional, no emoji, WHY-focused, atomic; trailer
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` + the `Claude-Session:` line.
- **Don't assume — measure/dig.** Never assert a version/behaviour/cost from memory; verify. (This
  session shipped an SDK-config bug caught only by an audit, and mis-stated Sonnet-5 extraction cost
  until it was measured.)
- **Burn the weekly pool freely** (Sean covers overage). Parallelize evals via `scratch/tg_parallel.sh`
  (one OAuth token, container-per-chain, 0 rate-limit failures at 12 chains). Check `checking-usage`
  before bailing a budget-bounded loop; only weekly-near-full is a real stop.

## Critical state (read before touching anything)
- **Branch:** `feat/extractjoin-cluster-stage`, 4 CI-green commits (HEAD `4ea8e6b`): extract→join
  CLUSTER stage + Alpine→Debian image migration + multi-stage image (911MB) + model-aware thinking.
  **HELD from deploy.** Continue items here (or a child branch per item); do NOT merge to main /
  deploy without Sean.
- **Prod code that shipped this session:** `newsroom/src/cluster_extractjoin.py` (the new stage),
  `orchestrate.py` (cluster label special-cased to it), `config.py` (`CLUSTER_EXTRACT_MODEL` default
  `claude-sonnet-4-6`, `CLUSTER_JOIN_THRESHOLD` 0.80), `newsroom/Dockerfile` + `Dockerfile.ci`
  (Debian, multi-stage), `pyproject.toml` (+scikit-learn). Tests: `newsroom/tests/test_cluster_extractjoin.py`.
- **⚠️ DATA STATE:** `data/claude_input/` was refreshed to TODAY's 499-article fetch (A1–A499) during
  dry-run work; the old 498-article snapshot (A1–A498) AND its holistic `sonnet5_ab/cluster_claude-
  sonnet-4-6.json` reference are GONE locally. Eval work that needs a holistic reference must
  regenerate one (run the prod CLUSTER agent once, or use `scratch/scaletest_setup.py` on an archived
  DB run 205–207). Gate evidence is preserved in the committed `docs/2026-07-01-graph-gate-
  preregistration.md`, unaffected.
- **Reusable eval/build assets (scratch, gitignored):** `cluster_taskgrounded_eval.py` (the measuring
  stick: SELECT→WRITE→COHERENCE→assemble on a partition, scores the digest), `tg_judge_product.py`
  (strengthened product signals: order-swapped dedup + input-derived important-story miss + bias/
  coverage), `tg_parallel.sh` (fan-out), `tg_aggregate.py`, `scaletest_setup.py` (materialize an
  archived DB run into the harness layout), `s5_extract_sweep.py` (SDK-config sweep), `extract_join_
  snapshot.py` (regen extract→join), `cluster-replay/{extract_tags,join_eval,adjudicate,judge_digests,
  band_eval}.py`. `adjudicate.py::JUDGES` = cross-family list (GLM→DeepSeek→Nemotron via NIM/Olla on
  `127.0.0.1:40114`; was DOWN this session — check before relying on it).
- **Read first:** memory `project_sonnet5_eval.md` (authoritative running state), `feedback_eval_ill_
  posed_metric`, `feedback_canary_dep_workarounds`; docs `2026-07-01-graph-gate-preregistration.md`,
  `2026-07-01-extractjoin-cluster-stage-plan.md` (untracked), `2026-06-30-phase2-close-and-graph-poc-
  handoff.md`, `.claude/learnings.md`.
- **SDK config facts (hard-won):** `thinking={"type":"disabled"}` restores good behaviour on the 4.x
  family (Sonnet 4.6 / Haiku 4.5) but **400s on Sonnet 5 / Opus 4.8 / Fable 5** (always-on thinking)
  → use `cluster_extractjoin._thinking_for(model)` pattern. `effort` **400s on Haiku 4.5**; omit it
  there. `run_sync` has NO `effort` param (use `run_agent`). SDK can't run nested in Claude Code
  (tests mock `run_agent`). When wiring Sonnet 5 into any stage, add `claude-sonnet-5` to
  `usage.py::_PINNED_MODEL_IDS` or every run logs "model drift".

---

## Item 1 — Fix the stale-world-fact WRITE bug (highest value, cheap, independent)
**Why:** Today's PROD digest referred to "the Biden administration" as current (it is not — the
digest's own stories are about the Trump administration). WRITE injected a political prior NOT in the
articles; COHERENCE didn't catch it because it only checks claims against the CITED source articles,
not world facts (current office-holders, dates). Reader-facing credibility bug.
**Find the real instance first (don't fabricate):** it's in the prod digest, not local data. Pull it —
`make db-clone` then query the `digests`/`shown_narratives` tables for today, or `bin/ssh` to read the
live digest — and confirm the exact sentence + which story.
**Approach (verify, then choose):** WRITE currently gets NO current-date/office context (confirmed:
`.claude/agents/write.md` + `prepare.py` inject none), yet its rules already say "do not fabricate
beyond the article summaries" and "no added precision" — so a political prior is already a rule
violation that slips through. Likely fix = BOTH: (a) inject the current date (+ optionally a minimal
"current heads of government are as reported in today's articles, do not assert office-holders from
prior knowledge") into the WRITE context/prompt; (b) tighten the anti-fabrication rule to name
world-state/political framing explicitly. Consider a lightweight world-fact check in COHERENCE as a
net. Prefer prevention at WRITE over detection.
**Bar:** reproduce with a failing test (a WRITE output / eval case that asserts no un-sourced office-
holder claim), fix, then verify on a REAL run that WRITE no longer emits stale office-holders. Don't
hardcode "Trump is president" in a rot-prone way — inject the date and ground in articles.
**Files:** `.claude/agents/write.md`, `newsroom/src/prepare.py` (context assembly), maybe
`coherence.md`. Don't-redo: the existing filler/citation self-checks are good — extend the pattern,
don't rewrite.

## Item 2 — Sonnet 5 on the JUDGMENT stages (biggest digest-quality lever; the deferred A/Bs)
**Why:** Sonnet 5's effort-tunable frontier pays off on JUDGMENT (SELECT / WRITE / synthesis), not
mechanical extraction (this session measured extraction on S5 = a pricier peer, not worth it). These
A/Bs were deferred pending the clustering redesign, which is now settled.
**Approach:** task-grounded gate per stage, downstream-fixed, n≥6, pre-registered. WRITE has the
strongest net — the validated `why_judge` golden (agreement 0.867) — use it as the primary WRITE
signal. Prior WRITE finding (build on, confirm at n≥6): parity at `thinking:adaptive`+`effort:medium`,
~+80% cost (cheap absolute); `thinking:disabled` is a MISCONFIG on S5 (verbalizes reasoning → rewrite
pathology) and 400s anyway. Synthesis: faithfulness ~96.4% (gold-free cross-family) is the trustworthy
prior; guard it. SELECT: score coverage/dedup/miss on the digest.
**Bar:** pre-registered gate, product signals, n≥6, band, cross-family order-swapped judge, deflation.
Wire a stage to S5 in prod ONLY if it clears; add `claude-sonnet-5` to `usage.py::_PINNED_MODEL_IDS`
when wiring. Use a `_thinking_for`-style model-aware config (never send `disabled` to S5).
**Assets:** `cluster_taskgrounded_eval.py` + `tg_judge_product.py` + `tg_parallel.sh`; `sonnet5_write_
ab.py`, `why_judge_drafts.py`, `eval_why_judge.py`. Don't-redo: single-pass judges w/o order-swap;
ARI/gold; the extraction-on-S5 question (closed — pricier peer).

## Item 3 — Time-decay join (clustering's open gap + coverage-dip recovery)
**Why:** the join uses tag TF-IDF with NO temporal signal. Literature: entity-Jaccard + temporal
proximity ≈ 92 BCubed-F. Hypothesis: Gaussian time-decay (σ≈72h) tightens clusters AND recovers the
durable ~10% citation-coverage dip (the one measured weakness of extract→join).
**Approach:** blend the tag-cosine with a temporal factor (publish-time gap; `published` col is in
`articles_*.csv`) in `cluster_extractjoin.join_tags` (or a variant behind a flag/param). Threshold
0.80 will likely need held-out re-tuning WITH decay (tune on archived runs 204/205, never on the eval
snapshot — that circularity is the discredited move). Temporal signal is weak within one day, strong
across days → test on a large/multi-day snapshot.
**Bar:** re-run the task-grounded gate (pre-registered): time-decay join must be ≥ the current no-decay
join on product signals (esp. recover coverage without regressing dedup/miss). Report the band; a delta
counts only if it clears it. Only ship if it clears.
**Assets:** `extract_join_snapshot.py`, `cluster_taskgrounded_eval.py`, `tg_judge_product.py`,
`scaletest_setup.py`. Don't-redo: the entity-conjunction over-merge gate (FAILED — residual errors are
semantic quasi-identity, not lexical); a full Sonnet re-cluster.

## Item 4 — Pre-deploy hardening (do the validation; STOP before the deploy)
**Why:** two verification steps were skipped this session. Do them so deploy is a one-step go when Sean
says.
**Do:** (a) full dry run on the CURRENT multi-stage image — `docker compose --env-file .env run --rm
digest-newsroom --no-email --no-record --force` — inspect the digest + clusters by eye (over-split?
junk clusters? stale facts? the Item-1 fix holding?). (b) Cross-family judge re-score of the gate: NIM/
Olla was down; when up, re-score the gate digests' dedup/miss with `adjudicate.py::JUDGES` (GLM/
Nemotron) to confirm the opus-only verdict — port from `judge_digests.py` / `tg_judge_product.py`.
**Bar:** dry run yields a clean digest on the shipped image; cross-family re-score confirms (or
honestly challenges) the gate. Record results in the pre-reg doc.
**⚠️ STOP LINE:** do NOT merge to main or run `bin/deploy`. Deploy is Sean's explicit call (held). Leave
everything staged/validated + report readiness. (When he greenlights: `deploy-check` skill → merge →
`bin/deploy` → watch first cron; `-target` list must include the service unit; base-image migration
redeploys the whole newsroom service.)

## Item 5 — SDK supply-chain reliability (Phase 5; last)
**Why:** `claude-agent-sdk` is UNPINNED in `pyproject.toml`; both prod + CI Dockerfiles `uv pip install
-r pyproject.toml` (lockfile unused) → both float to PyPI-latest at build. A bad SDK release breaks a
fresh prod build with no guard. Real supply-chain/reproducibility risk.
**Approach:** pin the SDK for PROD (e.g. `==<version>` or `uv sync --frozen`) while CI floats
deliberately to catch bad releases (the Dockerfile.ci comment already says so). Add canary tests
(`feedback_canary_dep_workarounds`): xfail-strict that trips when a workaround becomes unnecessary or
behaviour changes — e.g. the SDK #378 teardown hang, and the thinking-400-on-next-gen behaviour
(`_thinking_for`) — so the workaround self-expires on upgrade. Prefer battle-tested libs (tenacity,
asyncio.timeout) over hand-rolled.
**Bar:** canary-per-SDK-version discipline; the canary must fail loudly when upstream changes. TDD +
review gate. Memory says this is normally its own session — keep it decoupled from feature work; do it
last so it doesn't entangle items 1–4.
**Files:** `newsroom/pyproject.toml`, `newsroom/Dockerfile`, `Dockerfile.ci`, a new canary test.

---

## Loop protocol
Order: **1 → 2 → 3 → 4 → 5** (1 & 5 independent; 2 & 3 use the eval infra + a regenerated holistic
reference; 4 gates deploy). For each: (brainstorm if the shape is unclear) → pre-register if it's an
eval → TDD implement → validate to the bar on REAL output → review gate → `make ci-fix` green →
atomic commit → update `project_sonnet5_eval.md` memory + this doc's status → next. Between items,
run `checking-usage`; stop only if the weekly pool is near-full. Do NOT cross the §STOP LINE (no
deploy). If an eval does NOT clear its pre-registered bar, that's a real finding — record it, don't
force the change.

---

## STATUS LOG (execution session, 2026-07-01)

**Item 1 — stale-world-fact WRITE bug: DONE + committed (`9e4fbdd`).** Fix = inject
the run date into the WRITE system prompt via a `{{CURRENT_DATE}}` token resolved at
invocation (`orchestrate.render_body`, UTC-anchored to match run.py/db.py/digest.py) +
a "NO STALE WORLD-STATE PRIORS" anti-fabrication rule in `write.md`. TDD: 5 failing
unit tests first (render_body substitution/no-op/UTC-default/live-injection + write.md
guard), then green (481 pass). Review gate: code-reviewer caught a real UTC-vs-local
bug (fixed); simplifier clean. Verified on REAL output: live 499-article WRITE run — token
substitutes to "Wednesday, 1 July 2026" in the actual SDK system prompt, clean 5+14
digest, 0 stale office-holder hits, no regression from the added rules. **HONEST
DEFLATION:** could NOT pull the exact prod instance (SSH/tailscale unreachable this AFK
session; local prod clone reaches 06-30 only and its 12 most-recent Biden mentions are
all correct historical references — bug is intermittent). Adversarial reproduction (two
bait designs incl. a strong Biden-coded student-debt lure, 8 fix-off reps) could NOT
force the failure — the frontier model resists it. So this ships as **verified
prevention**, not a measured before/after. Low-risk (no-op for other stages).

**Item 2 — Sonnet 5 on JUDGMENT stages: DONE (WRITE+SELECT), synthesis scoped out. KEEP 4.6 on
both — clean negative.** Pre-registered `docs/2026-07-01-sonnet5-judgment-stages-preregistration.md`
(gate/signals/rule fixed before results; absolute signals only since NIM/Olla down → no pairwise
preference judge that would show family bias vs the S5-authored arm). n=6 per arm, product-grounded.
- **WRITE:** S5 WORSE on the validated why_judge golden (filler 0.114 vs 4.6's 0.000, clears band),
  −28% citations (but distinct-sources + bias-spread IDENTICAL → bias diversity unchanged), +26%
  cost, one invalid-JSON WRITE output (caught by prod once-retry). KEEP 4.6.
- **SELECT:** filler floor-hug 0/0 both (non-discriminating), coverage/bias/kept parity, +25% cost.
  KEEP 4.6.
- **One honest positive:** S5 has fewer COHERENCE failures in BOTH gates (mild, low-count rare-event
  edge, NOT gated on) → the trigger to revisit S5 IF faithfulness becomes priority or pricing drops.
  No prod change; `usage.py::_PINNED_MODEL_IDS` untouched (S5 not wired). Harnesses in scratch.

**Item 3 — time-decay join: DONE + committed (`b8fb035`). Hypothesis FALSIFIED — NOT shipped active.**
Pre-registered + a deterministic no-LLM structural probe (`docs/2026-07-01-timedecay-join-finding.md`).
σ=72h is INERT on the daily fetch window (~24-32h): matched-granularity partition is 99.9% pair-
identical to no-decay across runs 205+206, coverage moves ±1-2 (noise) — the kernel is near-constant
within a day so it can't reorder distances. Tighter σ (≤12h) HURTS coverage (fragments day-spanning
stories, −10..−21 in top-20). The dip is head-coarseness; the proven lever is Sonnet extraction
(graph-gate A/B). LLM gate skipped (unchanged partition → provably unchanged digest). Time-decay ships
DORMANT/opt-in on `join_tags` (params default off → byte-identical prod, 3 TDD tests, both review
agents clean) for reproducibility + the future multi-day story-graph substrate; NOT wired to config.

**Item 4 — pre-deploy hardening: DONE. Deploy-readiness GREEN, HELD for Sean.** Doc
`docs/2026-07-01-predeploy-validation.md`. (a) Full dry run on the FRESHLY-BUILT multi-stage image
(`--build --no-email --no-record --force`) = clean end-to-end: 751 fetched → 545 to Claude → 240
extract-join clusters (max 26, no junk) → 5 must_know + 10 should_know → valid HTML, $2.74, exit 0.
Item-1 fix HOLDS on real output (0 Biden, correct Trump-admin framing); COHERENCE dropped 1;
only non-fatal flag = 1 why_it_matters 61w vs 60w soft cap (cosmetic). (b) Cross-family re-score
DEFERRED (NIM/Olla down, checked twice; gate already has Anthropic-family robustness). ⚠️ NO merge,
NO deploy — held. Deploy sequence documented (deploy-check → merge → bin/deploy w/ service unit in
-target → watch cron).

**Item 5 — SDK pinning + canaries: DONE + committed (`37566ea`).** `newsroom/constraints-prod.txt`
pins `claude-agent-sdk==0.2.110`; prod `newsroom/Dockerfile` applies it (`-c`), CI floats. Pin
VERIFIED to bind. Canaries: `test_sdk_pin.py` (CI, forces re-validation on bump) + `bin/sdk-canary`
(live, reachability-gated, three-way verdict, hardened per a silent-failure review). **The canary
caught TWO stale priors on 0.2.110: `thinking=disabled` no longer 400s on next-gen AND `effort` no
longer 400s on Haiku 4.5** — both "400s on…" comments corrected across 6 files; the workarounds are
retained as validated config policy, not 400-dodges. (⚠️ the §Critical-state "SDK-config facts" 400
claims are now STALE — verify via bin/sdk-canary.)

---

# LOOP COMPLETE — 2026-07-01 (all 5 shipped to `feat/extractjoin-cluster-stage`, HELD from deploy)
| # | Item | Outcome | Commit |
|---|---|---|---|
| 1 | Stale-world-fact WRITE bug | FIXED (date injection + rule); verified prevention (bug intermittent, couldn't repro) | `9e4fbdd` |
| 2 | Sonnet 5 on judgment stages | KEEP 4.6 both WRITE+SELECT (clean negative, n=6 pre-reg); synthesis scoped out | `e754c63` |
| 3 | Time-decay join | Hypothesis FALSIFIED (inert on daily window); shipped DORMANT/opt-in, not wired | `b8fb035` |
| 4 | Pre-deploy hardening | Full dry run on built image = GREEN; deploy readiness confirmed, HELD | `e04bcb2` |
| 5 | SDK pinning + canaries | Prod pinned, CI floats, 2 canaries; caught 2 stale SDK-config facts | `37566ea` |

3 ships (1, 4-readiness, 5) + 2 rigorously-recorded negatives (2, 3). **Deploy HELD for Sean**
(`deploy-check` → merge → `bin/deploy` w/ newsroom service unit in `-target` → watch first cron).
Deferred nice-to-haves (needed unreachable infra this AFK session): pull the EXACT prod Biden
instance for Item 1 (SSH/tailscale hung); cross-family NIM re-score for Item 4 (Olla down).

---

## (original paste-prompt below)

> Resume news-digest on branch `feat/extractjoin-cluster-stage`. Execute the five improvements in
> `docs/2026-07-01-improvements-loop-handoff.md` IN ORDER, each to the project's high bar (verify on
> real output, TDD, eval pre-registration + band + cross-family judge + deflation, review gate,
> `make ci-fix` green, atomic WHY-commits), looping to the next when one is done + committed. READ
> FIRST: that handoff doc's §"The bar", §"Critical state" (esp. the DATA-STATE warning and the SDK-
> config facts), plus memory `project_sonnet5_eval.md` + `feedback_eval_ill_posed_metric`. Start with
> Item 1 (stale-world-fact WRITE bug) — pull the real Biden instance from the prod digest first.
> HARD STOP before deploy (Item 4's §STOP LINE): validate deploy-readiness but do NOT merge to main or
> run bin/deploy — that's Sean's call. Burn the pool freely; parallelize evals via tg_parallel.sh.
