# COHERENCE reframe + Sonnet 5 (2026-07-21)

## Problem

A hallucination review of run 245 found **6 genuine hallucinations that production
COHERENCE passed** (verified by hand against source text):

| # | Story | Field | Error | Class |
|---|---|---|---|---|
| 1 | Israel/centrifuges | headline | "as **Iran** election looms" — the election is Israel's | wrong-entity |
| 2 | Congo Ebola | why_it_matters | "**armed conflict** triggered 12 attacks" — sources say "rumors" | fabricated causal link |
| 3 | Canada tariffs | headline | "**most** Canadian goods" — sources say "some" / ~5% | quantifier overstatement |
| 4 | Burnham | summary | "**barely two years** in office" — no source states it | absence |
| 5 | US-Iran strikes | summary | "17 killed **since resumption**" — 17 is cumulative; 3 since resumption | scope conflation |
| 6 | China AI controls | summary | invented context from a 1-line headline | thin-source padding |

Production COHERENCE ran `claude-sonnet-4-6`, extended thinking OFF, a confirmatory
prompt ("extract every specific and verify"), Read/Write only. It caught none of
these (it did catch a separate Zelensky misattribution, which merge dropped).

## What we tried (and killed)

- **MiniCheck** (355M deterministic NLI classifier): over-drops massively (14–18 of
  35 clean fields); a probability-threshold sweep cannot separate errors from clean
  content (distributions overlap); externally validated at 66% balanced accuracy on
  AggreFact (matches the paper). Deterministic but far too imprecise for a
  flag-and-drop gate. **Dead.**
- **Haiku extract → Sonnet reason** (cheap model gets facts, Sonnet reasons): Haiku
  catches total-absence errors but on binding errors mislabels them SUPPORTED or
  **fabricates** a supporting quote. The extraction loses exactly the binding
  detail that matters. **Dead** (and Haiku-COHERENCE was already killed once before).
- **Extended thinking ON**: measured ~zero recall benefit on Sonnet 5, slower and
  costlier. **Not worth it** (prod already defaults thinking off).

## The measurement that matters (harness fidelity)

A first pass measured a rewritten "adversarial reframe" prompt by running it as
Claude Code **subagents** and got 5/6 recall — but that harness (model version,
system prompt, tools, thinking) differs from production. Re-run through the **real
agent-sdk path** (`claude_cli.run_agent`, the actual agent body, Read/Write,
thinking off) in Docker on the run-245 snapshot, the true numbers were much lower.
Subagent proxy over-stated recall ~2.5x. **Only harness-faithful numbers are
trustworthy.** Faithful results (0 false drops in every run, ~250 clean-field checks):

| Config (faithful harness) | per-run recall | 2-run union |
|---|---|---|
| prod prompt / Sonnet 4.6 / off | 0/6 | — (reproduces real prod) |
| reframe / Sonnet 4.6 / off | 3, 2, 2 | 3/6 |
| **reframe / Sonnet 5 / off** | 4, 3 | 5/6 |
| reframe / Sonnet 5 / thinking ON | 4, 4 | 5/6 |

**Sonnet 5 is the lever** (same price, cheaper now): it lifts the union from 3/6 to
5/6 and shrinks the always-missed set from three errors to one. Thinking adds
nothing. One error (#3, the quantifier) is missed by every config in every run —
the three probes don't check quantifier fidelity.

## Decision (this round — minimum viable)

Ship the **detector change only**:

1. Reframe `coherence.md` from a confirmatory check to three **adversarial probes**,
   preserving every existing guardrail (paraphrase-over-drop incident guard,
   stale-office-holder check, `failed_fields` schema, why_it_matters interpretive
   allowance):
   - **specific-refute incl. absence** (try to refute the least-supported specific;
     absence, attribution-upgrade, and stale-world-state fold in here);
   - **relation-binding** (verify the causal/comparative/attributive *link*, not just
     the endpoints);
   - **headline entity-binding + internal consistency** vs the story's own summary.
2. Bump `coherence.md` model `claude-sonnet-4-6` → `claude-sonnet-5`.
3. Single pass. **Union is cut** — it only harvests stochastic misses and is
   mathematically blind to the correlated quantifier miss (per adversarial review).

### Regression gate

`bin/eval-coherence` / `make eval-coherence` runs the real `coherence.md` through the
production agent-sdk path against a committed labelled snapshot
(`newsroom/tests/fixtures/coherence_faithful/`) and scores recall + false-drops.
Makes real model calls on the subscription → **opt-in, never in CI**. Stochastic, so
it reports a per-run scorecard over N runs and exits non-zero only on an *egregious*
regression (false-drops > 2, or recall 0 on every run); a human judges recall
changes. It reads the live prompt, so it always tests what will ship.

Attribution: `MODEL_NAME` (shown to readers) reflects curation; COHERENCE is a
verifier and the WRITE/curation stages stay on 4.6, so attribution is left as-is.
Moving other stages to Sonnet 5 is unvalidated → a separate decision.

## Deferred (next rounds, explicitly)

- **Quantifier/scope-fidelity probe** to close error #3 — fixture-gated against the 8
  borderlines first (the anti-paraphrase guardrail and the probe collide on
  "most" vs "several").
- **WRITE-stage prevention** — errors #3, #4, #6 are WRITE failures with cheap
  prompt-level preventions ("quantifiers no stronger than the source"; "if fulltext
  is missing and the RSS summary is short, one sentence max"). Prevention costs zero
  recall; `write.md` already has anti-overstatement rules that need a look.
- **Multi-day fixtures** — the run-245 set is n=1 and self-labelled (the probes were
  designed from these same 6 errors, so recall is training-set performance and will
  regress out of sample). Accrue ~10 min/day as the held-out set for the *next*
  optimisation round; not a blocker for shipping something strictly better than 0/6.
- **Repair, not drop** — with symmetric drop costs, the eventual win is feeding the
  checker's objection back to a WRITE retry instead of dropping the field. Changes
  what COHERENCE should output (an objection, not a boolean). Later.

Strategy reviewed by Claude Fable 5; the union cut and the prevention-first framing
are its corrections.
