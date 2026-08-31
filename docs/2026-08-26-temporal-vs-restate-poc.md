# Temporal vs Restate for the curation pipeline — PoC findings

*2026-08-26. Both ports built and run. Code in `scratch/temporal-vs-restate/`.*

## What was built

CLUSTER → SELECT → WRITE ported to both runtimes, sharing `common.py`: the **real
validators lifted verbatim** from `orchestrate.py`, plus stage bodies that stand in for
the LLM calls and can be told to fail. Both ports implement the same four behaviours the
production loop has:

1. three stages in order
2. validate each output; retry the stage once from a clean slate on invalid output
3. retry transient errors with backoff, bounded by wall clock
4. on resume, skip any stage whose valid output survives on disk

Both were run with identical fault injection — `FAIL_TRANSIENT=cluster:2
FAIL_INVALID=select:1` — and both produced the same result: `{"stages": 3, "cost": 2.04}`
with all three artifacts written.

## What the code actually costs

| | lines |
|---|---|
| `retry.py` today | 196 |
| `run_stage` (the retry/validate wrapper) | 62 |
| `_stage_output_is_valid` | 18 |
| `orchestrate_selections` loop | 89 |
| **Temporal port** — workflow body / whole file (code only) | **38 / 86** |
| **Restate port** — handler body / whole file (code only) | **34 / 63** |

Both replace `retry.py` outright. Restate's port is smaller mostly because activities
don't have to be declared, named and registered separately — `ctx.run("name", fn)` takes
a closure inline, where Temporal needs a module-level `@activity.defn` plus worker
registration. Over five stages plus repair that difference compounds.

## The finding that actually matters: both have the same footgun, in the same place

**Both runtimes retry a failing step forever by default, and I hit it in both ports on the
first run — on the validation step.**

- Temporal: activity `retry_policy` defaults to unlimited attempts. `validate_out` raised
  `ValueError`, retried forever, workflow hung. Fix: `RetryPolicy(maximum_attempts=1)`.
- Restate: everything except `TerminalError` is retried per the invocation policy
  (default ~70 attempts / ~60 min, then **pause**, not fail). Fix: `max_attempts=1` on
  that `ctx.run`.

A validation verdict is not a transient fault, and neither runtime knows that. This is the
same class as `strict-types-on-model-output-turn-drift-into-silent-loss`: the failure is
silent and looks like slowness.

## The finding that separates them: Restate has no sandbox

The first Restate run failed with `selected.json missing` rather than the real validation
error. Cause: I wrote `Path(out_dir, filename).unlink(missing_ok=True)` in handler code,
outside `ctx.run`. **Restate re-executes handler code on replay**, so the unlink ran again
and deleted the artifact the previous attempt had just written.

Temporal's sandbox makes that impossible — the workflow bundle physically cannot reach
`Path.unlink`, and it fails at import time, not at 3am. Restate trades that guard for
ergonomics: no sandbox, no import restrictions, ordinary Python — and the discipline is
entirely yours.

Restate's diagnostics were better in exchange: the error named the failing step
(`Related command: run [validate-select-1]`) and `restate invocations list` showed the
stuck invocation with its error inline. Temporal's equivalent needed reading worker logs.

## Restate specifics that bear on this pipeline

Verified against `docs.restate.dev` (full docs corpus) and by running server v1.7.7,
Python SDK 1.0.4.

- **The 1-minute inactivity timeout will break this pipeline on day one.** If Restate sees
  no journal entry from a service for 60s it suspends and retries the invocation. A
  3–5 minute LLM stage inside one `ctx.run` trips it; the 10-minute abort timeout then
  kills it. One-line fix (`inactivity_timeout` / `abort_timeout` on the service), but it
  is not the default and it is not optional here.
- **Default memory pools sum to ~4.75 GiB** (RocksDB 2 GiB, query engine 1 GiB, invoker
  1.5 GiB, record cache 250 MiB) on a 4 GB box. Restate warns about cgroup limits; it does
  not shrink itself. Must be hand-tuned before first start, along with
  `default-num-partitions` (defaults to 24, **fixed at first provisioning**).
- **Versioning is immutable deployments, not code branches.** Register a new endpoint; new
  invocations route there, in-flight ones drain on the old one. No `patched()` equivalent
  exists or is needed. For a pipeline whose stage structure gets edited often, this is the
  biggest ergonomic difference from Temporal.
- **Workflow state is discarded 24h after completion.** Story threads must be
  **Virtual Objects** (state retained indefinitely), not Workflows.
- **A blocked exclusive handler queues every other exclusive call to that key.** Shared
  (`kind="shared"`, read-only) handlers still run. A six-month human-in-the-loop wait must
  therefore be a delayed message into a Virtual Object, not a long-blocked handler — which
  also avoids pinning a six-month-old deployment (error RT0014: no service-protocol
  upgrade for in-flight invocations).
- **No native cron.** The existing systemd timer stays.
- **BSL 1.1, not open source** — converts to Apache 2.0 four years after each release. The
  Additional Use Grant covers this usage.
- **Python SDK went 1.0 on 2026-06-23** — two months old, 2–9 commits/month, second-tier
  behind TypeScript and Java. The server is mature; the Python binding is not yet.

## Correction to an earlier claim

I previously said Temporal's SQLite path "silently runs on a stale schema" after a server
upgrade, based on measuring 45 tables on an upgraded DB versus 48 on a fresh one. That
measurement stands. But the stronger and simpler argument is the documented one:
**SQLite is not a production persistence path for Temporal at all**, so single-box
Temporal means running Postgres beside it. Don't lean on the upgrade-drift claim when the
flatter one is available.

## Verdict

**Neither is justified by the daily pipeline alone.** Both delete `retry.py` and the
resume predicate — roughly 226 lines — and both add a supervised long-lived process where
there is currently a one-shot container. Three incomplete runs in 262 does not buy that.

**If the thread-as-entity model is adopted, Restate is the better fit**, and the reason is
deployment shape rather than features: one binary with embedded storage versus
server + Postgres. Virtual Objects are the exact shape of a story thread — keyed, single
writer, state retained indefinitely — and the immutable-deployment versioning model
removes the ceremony that a frequently-edited pipeline would otherwise pay forever.

**The counter-argument is maturity, and it is real**: a two-month-old Python SDK and a
non-open-source licence, against Temporal's years-GA Python SDK and MIT.

## Next, if pursued

Model a story thread as a Virtual Object woken by delayed messages, keyed by a
**content-derived** thread id — and check whether workflow-ID-as-identity makes the run-235
duplicate-card bug unrepresentable. That is the one benefit here that is not a refactor in
disguise, and it is untested.
