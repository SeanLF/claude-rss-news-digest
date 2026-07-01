# SDK pinning + canary — Item 5 (2026-07-01)

Phase 5 supply-chain reliability. `claude-agent-sdk` was UNPINNED in `pyproject.toml`, and BOTH
Dockerfiles install via `uv pip install -r pyproject.toml` (the `uv.lock` is not consulted), so a
fresh PROD build floated to whatever PyPI-latest was at build time — a bad SDK release would break
prod with no guard.

## What shipped
- **`newsroom/constraints-prod.txt`** pins `claude-agent-sdk==0.2.110` (the current PyPI-latest,
  live-validated below). Applied ONLY by the prod builder (`newsroom/Dockerfile`:
  `uv pip install -r pyproject.toml -c constraints-prod.txt`).
- **CI keeps floating** (`newsroom/Dockerfile.ci` omits the constraint, as its comment already
  intends) — so a bad release fails the CI build first, not prod.
- **Pin verified to BIND** (not just "latest happens to be 0.2.110"): temporarily pinning to
  `0.2.101` and rebuilding installed `0.2.101` (not the `0.2.110` float); restoring installed
  `0.2.110`. The constraint overrides the float.
- **Pin-consistency canary** (`newsroom/tests/test_sdk_pin.py`, CI-runnable, no network): asserts
  prod pins an EXACT version, that the prod Dockerfile applies the constraint, that CI does NOT,
  and that the pin equals `_WORKAROUNDS_VALIDATED_THROUGH` — so bumping the pin is a deliberate,
  reviewed step that forces re-validating the SDK behaviour-workarounds (`feedback_canary_dep_workarounds`).
- **Live behaviour canary** (`bin/sdk-canary`, runs in the digest-newsroom container with SDK +
  OAuth): probes the `thinking=disabled` behaviour the `_thinking_for` workaround guards. Run it
  after an SDK bump.

## Canary finding — TWO documented SDK-config premises are now stale (it did its job immediately)
`bin/sdk-canary` on the pinned SDK **0.2.110** (each "should-400" probe reachability-gated by a
control call, so the results are attributable — the hardening a silent-failure review demanded):
1. **`thinking={"type":"disabled"}` on claude-sonnet-5 NO LONGER 400s** (returns OK; 4.x still
   accepts it). Stable across runs. `claude_cli` passes `thinking` raw (`claude_cli.py:158`), so
   this is real API behaviour.
2. **`effort="medium"` on Haiku 4.5 NO LONGER 400s** either (control confirms Haiku is reachable,
   so the success is attributable, not a masked outage).

Both **contradict strongly-held priors** stated as fact in memory + handoffs (the same-day commit
`4ea8e6b`: next-gen 400s on `disabled`; the effort plumbing: Haiku 400s on `effort`). Exactly the
"never assert behaviour from memory — verify" case: on the SDK prod now pins, both 400 premises are
stale. (Whether the API now accepts these params or the SDK/CLI silently drops them, the observable
400 the workarounds guarded against no longer occurs.)

**Action taken — corrected the now-false assertions across the code, did NOT change any logic:**
`_thinking_for` (adaptive for next-gen) and the effort-omission on Haiku are both **retained
deliberately as CONFIG POLICY**, not 400 dodges: adaptive is the validated next-gen config (the S5
extraction sweep; and on WRITE forcing `disabled` on S5 induced a self-revision rewrite pathology),
and RECAP/Haiku has no reason to spend on `effort`. The stale "400s on…" comments were corrected
(historical + `bin/sdk-canary` reference) in `cluster_extractjoin.py`, `claude_cli.py`,
`orchestrate.py`, and three test files. **Do NOT "simplify" these away because the 400 is gone** —
sending `disabled` to next-gen risks the documented rewrite pathology.

The SDK #378 generator-teardown-hang workaround (`claude_cli.run_agent`'s `asyncio.wait_for` on
`aclose()`) was NOT live-re-checked this pin (hard to trigger deterministically); it stays as a
defensive guard, listed in the pin canary so a future bump re-examines it.

## Follow-up (not done here — out of MVP scope)
Only the SDK is pinned; other deps (sklearn, premailer, …) still float in prod. A fully
reproducible prod build (`uv sync --frozen` against a maintained `uv.lock`, which is currently
stale at 0.2.101 and unused by either Dockerfile) is the larger, separate hardening step.
