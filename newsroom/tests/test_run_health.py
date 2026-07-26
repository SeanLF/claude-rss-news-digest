"""Tests for run_health.py: post-run invariants on a finished run.

This pipeline's failures are silent-wrong, not loud-crash. Every incident worth
having caught -- the linker returning quoted ids (run 244, all thread continuity
lost), the duplicate cluster cards (run 235), months of no-op alerting -- raised
no exception and exited 0. `run-reliability.sql` already encodes most of these as
its `flags` column, but nothing evaluated them at run time, so they were only
visible when a human went looking. These invariants close that gap: they run at
the end of a successful run and feed the alert path.

Each invariant is a decision rule with a citable trigger, not a tuned score.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import run_health


def _healthy(**overrides):
    """A run with every invariant satisfied; override one field per test."""
    health = {
        "run_id": 245,
        "shipped": 15,
        "stages": 9,
        "artifacts": 13,
        "recipients": 11,
        "broadcasting": True,
        "thread_continuations": 5,
        "threads_available": 55,
        "threads_enabled": True,
    }
    health.update(overrides)
    return health


class TestThreadContinuity:
    def test_zero_continuations_is_flagged_when_threads_were_available(self):
        # Run 244: 16 installments, 0 continuations, ~60 live threads. The linker
        # answered correctly but wrote its ids as strings, and a strict isinstance
        # check dropped all 16. Nothing raised; the run shipped and exited 0. In 39
        # thread-enabled runs the only other zero is run 205, the seeding run.
        found = run_health.violations(_healthy(thread_continuations=0))

        assert any("NO_THREAD_CONTINUATIONS" in v for v in found)

    def test_zero_continuations_is_silent_when_no_threads_existed_yet(self):
        # Run 205 opened the first threads and continued nothing, because there was
        # nothing to continue. That is the ONLY zero in 39 thread-enabled runs, and
        # it is excluded by construction rather than by a tuned floor.
        found = run_health.violations(_healthy(thread_continuations=0, threads_available=0))

        assert found == []

    def test_zero_continuations_is_silent_when_threads_are_switched_off(self):
        # THREADS_ENABLED is a kill switch and DEFAULTS TO FALSE, so this is also
        # the shape of any deploy where the var is unset. Live threads do not
        # disappear when it flips -- they stay in the table -- so without this
        # guard the rule fires on EVERY run, forever, the moment the switch is
        # used. That is alert fatigue, and it is most expensive during an
        # incident, which is exactly when a kill switch gets flipped.
        found = run_health.violations(_healthy(thread_continuations=0, threads_available=387, threads_enabled=False))

        assert found == []

    def test_healthy_run_reports_nothing(self):
        assert run_health.violations(_healthy()) == []


class TestMalformedHealth:
    def test_missing_key_is_itself_a_violation(self):
        # The two guarded rules (ZERO_RECIPIENTS, NO_THREAD_CONTINUATIONS) fail
        # SILENT-OFF on a missing key -- their guard defaults to "don't fire". So a
        # refactor that renamed a key would permanently disable the run-244 rule
        # while every test still passed. For a monitor, the safe default on missing
        # data is to be loud, not quiet.
        health = _healthy()
        del health["threads_available"]

        found = run_health.violations(health)

        assert any("MALFORMED_HEALTH" in v for v in found)
        assert any("threads_available" in v for v in found)


class TestSilentOutageRules:
    """The four conditions run-reliability.sql already flags, evaluated at run time.

    Each is a silent outage: the process exits 0 and nothing raises, but the run
    did not do its job. They were only ever visible to a human running the query.
    """

    @pytest.mark.parametrize(
        ("code", "broken"),
        [
            ("ZERO_STORIES", {"shipped": 0}),
            ("ZERO_RECIPIENTS", {"recipients": 0}),
            ("NO_USAGE_RECORDED", {"stages": 0}),
            ("NO_ARTIFACTS", {"artifacts": 0}),
        ],
    )
    def test_each_rule_fires_on_its_trigger(self, code, broken):
        found = run_health.violations(_healthy(**broken))

        assert any(code in v for v in found), f"{code} did not fire for {broken}"

    def test_zero_recipients_is_silent_when_not_broadcasting(self):
        # --no-email and --dry-run reach zero recipients by design. Alerting on
        # those would train the reader to ignore the alert, which is worse than
        # not sending it.
        found = run_health.violations(_healthy(recipients=0, broadcasting=False))

        assert found == []
