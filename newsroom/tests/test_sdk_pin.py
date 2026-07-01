"""Canary for the pinned Agent SDK version (supply-chain reliability, Phase 5).

Prod pins ``claude-agent-sdk`` (``newsroom/constraints-prod.txt``) so a bad upstream
release cannot break a fresh prod build with no lockfile guard; CI
(``newsroom/Dockerfile.ci``) floats DELIBERATELY so a bad release fails the CI build
first, not prod. This canary ties the prod PIN to the SDK version the in-repo
SDK WORKAROUNDS were last validated against, so bumping the pin is a deliberate,
reviewed step that forces re-checking them (``feedback_canary_dep_workarounds``):

  - ``claude_cli.run_agent``: the SDK #378 generator-teardown hang, bounded by
    ``asyncio.wait_for(aclose(), 5)`` (not live-re-checked this pin; defensive).
  - ``cluster_extractjoin._thinking_for`` (+ orchestrate): ``disabled`` only for the
    4.x family. Next-gen models used to 400 on ``thinking={"type":"disabled"}``; as of
    0.2.110 that 400 no longer reproduces (``bin/sdk-canary``), but the split is retained
    as config policy (adaptive is the validated next-gen config), not a 400 dodge.

When you bump the pin: re-verify the workarounds still hold on the new SDK (``bin/sdk-canary``
for the thinking behaviour), then
bump ``_WORKAROUNDS_VALIDATED_THROUGH`` to match. The equality assert below makes
that pairing mandatory instead of a silent float.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
NEWSROOM = REPO_ROOT / "newsroom"
CONSTRAINTS = NEWSROOM / "constraints-prod.txt"

# The SDK version the in-repo SDK workarounds (see module docstring) were last
# verified against. MUST equal the prod pin; bump both together, after re-verifying.
_WORKAROUNDS_VALIDATED_THROUGH = "0.2.110"


def _pinned_version() -> str:
    text = CONSTRAINTS.read_text(encoding="utf-8")
    m = re.search(r"^claude-agent-sdk==([0-9][0-9.]*)", text, re.MULTILINE)
    assert m, f"no exact 'claude-agent-sdk==<version>' pin found in {CONSTRAINTS}"
    return m.group(1)


def test_prod_pins_the_sdk_exactly():
    """Prod must pin an EXACT SDK version (==), not float -- the supply-chain guard."""
    assert _pinned_version()  # a bare/range spec would fail the regex above


def test_pin_matches_validated_workarounds():
    """Bumping the prod pin must be paired with re-validating the SDK workarounds."""
    assert _pinned_version() == _WORKAROUNDS_VALIDATED_THROUGH, (
        f"prod pins claude-agent-sdk=={_pinned_version()} but the SDK workarounds were "
        f"validated through {_WORKAROUNDS_VALIDATED_THROUGH}. Re-verify the #378 teardown "
        "timeout and the _thinking_for thinking-400 behaviour on the new SDK, then update "
        "_WORKAROUNDS_VALIDATED_THROUGH to match."
    )


def test_prod_dockerfile_applies_the_constraint():
    """The pin is dead unless the prod Dockerfile installs with -c constraints-prod.txt."""
    df = (NEWSROOM / "Dockerfile").read_text(encoding="utf-8")
    assert "constraints-prod.txt" in df, "prod Dockerfile must install deps with -c constraints-prod.txt"


def test_ci_dockerfile_does_not_pin():
    """CI floats deliberately (no constraint) so a bad SDK release breaks CI, not prod."""
    df = (NEWSROOM / "Dockerfile.ci").read_text(encoding="utf-8")
    assert "constraints-prod.txt" not in df, "CI must float (no prod constraint) to catch bad releases first"
