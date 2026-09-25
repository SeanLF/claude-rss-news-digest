"""bin/deploy's run window follows the schedule's zone, not UTC.

The Temporal schedule fires at 12:25 Europe/Paris (digest/src/client.ts), which is 10:25Z in summer
and 11:25Z in winter; a window fixed in UTC would let a winter deploy land on a live run.
"""

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parent.parent.parent / "bin" / "deploy"


def window(paris_hhmm, mode="temporal", force="false"):
    # `date` answers only when asked for Paris time; a UTC read gets a time outside any window.
    script = f"""
source {DEPLOY}
trap - EXIT
date() {{ [ "$TZ" = Europe/Paris ] && echo {paris_hhmm} || echo 0300; }}
PIPELINE_MODE={mode}
FORCE={force}
check_run_window
"""
    p = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "CLAUDECODE": ""}, timeout=60
    )
    return p.returncode, p.stdout + p.stderr


@pytest.mark.parametrize("hhmm", ["1200", "1225", "1344"])
def test_refuses_inside_the_paris_window(hhmm):
    rc, out = window(hhmm)
    assert rc == 1, out
    assert "Europe/Paris" in out


@pytest.mark.parametrize("hhmm", ["1159", "1345", "0300"])
def test_allows_outside_it(hhmm):
    assert window(hhmm)[0] == 0


def test_force_goes_ahead_inside_it():
    assert window("1225", force="true")[0] == 0


def test_only_temporal_mode_is_guarded():
    assert window("1225", mode="staged")[0] == 0
