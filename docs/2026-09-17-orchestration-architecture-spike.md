# Orchestration architecture spike

**Date:** 2026-09-17
**Status:** designed, not yet run
**Decision rules below are pre-registered.** They were written before any measurement, because
every prior evaluation here that fixed its threshold afterwards read its own noise as a result
(see the SELECT-order finding, which did not replicate).

## Why

We hand-rolled the four things durable-execution platforms sell: retries with a wall-clock budget
(`retry.py`), resumption (`run.py`, 23 sites), an event archive (`db.run_artifacts`), and
replay-from-history (`replay.py`, 2026-09-17). The last of those took three rounds of adversarial
review to stop it destroying files in its own output directory, which is what prompted this.

`hivebrite-agent` runs self-hosted Temporal 1.31.2 in Python — worker, schedules, interceptors,
workflow patches, and history-replay tests — so both answers now exist side by side in two repos,
each with a file called `test_replay.py`: one bought, one built.

Sean's decision, 2026-09-17: judge **best fit per repo**, not one shared model. The cost of that
choice — owning two orchestration models — is a reported outcome of this spike, not a hidden one.

## What is NOT the problem

Measured before designing, and it kills the obvious framing:

| Function in `orchestrate.py` | Lines | Kind |
| --- | --- | --- |
| `orchestrate_selections` | 117 | sequencing |
| `validate_coherence` | 115 | stage contract |
| `run_write_phase` | 110 | fan-out |
| `_tool_list` | 101 | SDK plumbing |
| `run_stage` | 96 | invocation |
| `_run_repair_phase` | 87 | phase logic |
| `parse_agent_spec` | 66 | SDK plumbing |

Sequencing is already a declarative table (`_STAGES`). The mass is **per-stage validators and SDK
plumbing**. An orchestrator replaces the sequencing and leaves the rest, so "adopt to delete 2900
lines" is false before we start. What a platform can actually delete is `retry.py`, the resume
predicate, and part of the archive — and it re-encodes the validation-is-terminal distinction that
`retry.py` exists for.

## Probe 0 — are the stages actually independent? (gates everything)

**Claim to test:** each stage is a pure function from its archived inputs to its output, gradeable
alone. If true, orchestration is a DAG over artifacts, every candidate below gets cheap, and
replay is just "run a stage against archived inputs."

**Method:** for 3 archived runs, run each stage in isolation from that run's archived inputs and
diff against the archived output. Model nondeterminism means the diff is judged on shape and
citation set, not bytes.

**Outcome:** the list of couplings that break purity (expected candidates: SELECT's `cluster_index`
drift, the WRITE fan-in order, repair's re-check). Those couplings ARE the architecture finding,
whatever the orchestrator turns out to be.

**Cost:** ~2h, no model calls beyond re-running stages.

## Arm 1 — four orchestration candidates

One vertical slice: CLUSTER extract→join → SELECT → WRITE fan-out, driven by archived artifacts.

| Candidate | Why it is in | Operational cost |
| --- | --- | --- |
| Keep hand-rolled | control; must be able to win | none |
| Restructure in place | the table exists; test whether validators+plumbing collapse | none |
| Temporal | reference implementation exists in `hivebrite-agent` | server + Postgres + worker |
| Prefect | Python-native, lighter than Temporal for a daily pipeline | server, optional for simple flows |

Inngest gets a paper pass only. Mastra is out: TypeScript-only costs the Python Agent SDK path on
the subscription, which is the wrong trade for "simpler."

### Decision rule (pre-registered)

- **Restructure wins** if the slice holds `test_orchestrate.py` green while cutting the
  orchestration+durability surface by ≥50%, with no new service.
- **A platform wins** if it does that AND fits the box — measured RSS for server + storage + worker
  against a 4 GB CX23 already running newsroom and circulation — AND nets a deletion *after*
  re-encoding validation-is-terminal.
- **Keep what we have** if neither clears its bar. This must stay reachable or the spike is theatre.

## Arm 2 — decomposition, tested on the cohesion gate

**Claim to test:** stages carrying several instructions at once do them worse than narrower stages
would. Prior art says this is real but not uniform: GPT-4 misses ≥1 constraint on 21% of
multi-constraint instructions (DeCRIM, arXiv:2410.06458); naive sequential decomposition propagates
early errors (arXiv:2506.02683); the 2026 state of the art is *adaptive* — decompose only where it
pays (Select-Then-Decompose, arXiv:2510.17922). Our own evidence splits the same way: per-story
WRITE fan-out shipped and helped; per-story COHERENCE held recall but false-dropped in 4 of 5 runs.

**Test case:** the cohesion gate, because it is the clearest multi-pronged stage and it has a
failure to beat — 67% count agreement, 5 over-splits, all 6 known strays separated, $0.05/run.

The gate today asks for a **partition**: `{"group": N, "events": [["A1","A2"], ["A3"]]}`. A
partition of k articles has Bell(k) answers — 115,975 at k=10. The decomposition under test replaces
it with **pairwise same-event questions** plus the deterministic join we already run in
`cluster_extractjoin`, which is the "cheap extract → deterministic join → thin refine" shape the
clustering prior-art doc found the aggregators use.

## Arm 3 — Jev on the pairwise question (merged with Arm 2)

Jev's primitives are Choice (≤255 options), Score (rubric), and Noul (calibrated 0–1). Partition is
not among them, so Jev cannot do the gate as currently framed — but the pairwise decomposition from
Arm 2 is exactly a Noul. All questions mix in one call, evaluated in parallel against the same
state, with no context rot, so the cluster stays visible globally while each pair is judged alone.

**The shape under test is a hybrid, not a replacement:** Noul returns a calibrated probability, so
Jev answers the pairs it is confident about and the uncertain ones route to Claude.

The 2026-09-16 verdict (1/6 vs Claude's 4/6) stands for what it measured — comprehension and
judgment on COHERENCE, clustering and SELECT. This is a different primitive on a different task and
does not re-open it.

**Risk, stated:** TypeSafe publishes no worked example for deduplication or entity resolution, so
this is a first-principles application of a model in early access.

**Egress:** the payload is `A1: <title> -- <200-char summary snippet>`; `build_judge_prompt` never
assembles a URL, so the no-URLs invariant holds by construction. Sean, 2026-09-17: training on
public article text is acceptable, so no data gate blocks this arm.

### Ground truth — NOT our golden sets

Our goldens are Claude-generated, so scoring a challenger against them measures agreement with
Claude, not correctness; a cheaper model that disagreed and was right would score worse. Instead:

1. **Planted strays** — ground truth by construction, the method that killed Haiku on COHERENCE
   (missed 3 of 4 planted fabrications).
2. **Transitivity violations** — A~B, B~C, A≁C is self-evidently wrong with no label needed, and a
   judge producing many is not reasoning about events. Doubles as the negative control.
3. **Disagreement adjudication** — Sean labels only where the candidates disagree.

### Decision rule (pre-registered)

- A decomposition or a Jev hybrid **ships** only if it beats the current arm by more than the
  reference's own self-agreement band, measured first, on planted strays.
- Transitivity violation rate above 10% of judged triples **fails** the arm regardless of accuracy.

## Negative controls

- Every arm must be able to conclude "keep what we have."
- A deliberately broken port must fail loudly; an arm that cannot fail is not evidence.
- The self-agreement band is measured before any comparison, never after.

## Out of scope

Mastra (TypeScript boundary), multi-tenant, Restate (2026-08-26 verdict stands, and the per-repo
decision removes its shared-model argument).

## Model assignment

Per-task pins, never inherited; cheap models at low effort, never cheap at high.

| Task | Model | Why |
| --- | --- | --- |
| Probe 0 stage re-runs | the stage's own pinned model | fidelity: a re-run on a different model is not a re-run |
| Port scaffolding (Arm 1) | Sonnet | mechanical, high volume |
| Decision-rule scoring | deterministic Python | no model in the scorer |
| Arm 2/3 judging | as under test | the arm's variable |
| Adversarial review of each arm | Opus | the gate that caught three rounds of the same defect today |

## Independent of the outcome

`retry.py` encodes the subtlest invariant in the system — one run budget shared across stages, which
must stay under systemd's `TimeoutStartSec` or a killed run leaves its row `running` forever — and
has no test file. That test gets written whichever way this lands.

## Cost

Probe 0 ~2h. Arm 1 ~half a day for restructure, ~1 day for Temporal, ~half a day for Prefect. Arm
2/3 $5–15 of archived-run model calls plus Jev's early-access pricing. Run the arms in separate
sessions: planning and implementing in one session measured ~3x less token-efficient.
