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
        "batches_lost": 0,
        "title_only_fallback": 0,
        "dropped_continuations": 0,
        "linker_ok": True,
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


# --- degraded clustering: the failure every incident this module exists for has in common ---
# One extraction batch returning nothing costs 40 articles their entity tags; they cluster on
# titles alone, which manufactures collision-prone cluster ids and shipped 7 times in 40 runs.
# Every one of those runs exited 0. The count lives nowhere the DB can see it, so the stage now
# archives cluster_health.json and this rule reads it.


def _health(**over):
    """The same run as _healthy, differing only in the counts these rules quote. Derived rather
    than re-typed: this was a third literal of the dict, and every added health key had to be
    hand-copied into all three."""
    return _healthy(**{"shipped": 16, "stages": 5, "artifacts": 15, "threads_available": 40, **over})


def test_degraded_clustering_fires_on_any_title_only_fallback():
    out = run_health.violations(_health(batches_lost=1))
    assert any("DEGRADED_CLUSTERING" in v for v in out)


def test_clean_clustering_is_silent():
    assert not [v for v in run_health.violations(_health()) if "DEGRADED_CLUSTERING" in v]


def test_missing_cluster_health_cannot_judge_and_stays_quiet():
    # Runs archived before the artifact existed must not alert; absence is not evidence.
    out = run_health.violations(_health(batches_lost=None))
    assert not [v for v in out if "DEGRADED_CLUSTERING" in v]


def test_missing_key_entirely_is_malformed_not_silently_skipped():
    # The module's own discipline: a renamed/absent key must be loud, not fail-silent-off.
    h = _health()
    del h["batches_lost"]
    assert any("MALFORMED_HEALTH" in v for v in run_health.violations(h))


class TestDroppedContinuations:
    """Reported by run.py, not by a rule here -- see TestDroppedContinuationReporting in
    test_run_health_alerting.py. It cannot ride NO_THREAD_CONTINUATIONS: a refusal is only
    recorded when another story already claimed that thread, so it entails at least one
    continuation, and that rule requires zero. The two are mutually exclusive by construction.
    """

    def test_it_does_not_alert_on_its_own(self):
        assert run_health.violations(_healthy(dropped_continuations=3)) == []

    def test_a_failed_linker_call_is_named_in_the_message(self):
        """ "Nothing continued" and "the linker never ran" are the same row otherwise."""
        found = run_health.violations(_healthy(thread_continuations=0, linker_ok=False))
        assert any("linker call itself failed" in v for v in found)

    def test_a_healthy_linker_is_not_editorialised(self):
        found = run_health.violations(_healthy(thread_continuations=0, linker_ok=True))
        assert any("NO_THREAD_CONTINUATIONS" in v for v in found)
        assert not any("linker call itself failed" in v for v in found)

    def test_an_unknown_linker_state_is_not_editorialised(self):
        """None is "cannot judge" -- every run archived before the trace existed."""
        found = run_health.violations(_healthy(thread_continuations=0, linker_ok=None))
        assert not any("linker call itself failed" in v for v in found)

    def test_a_missing_key_is_still_a_violation(self):
        """Reported-not-triggered still means a rename must not pass silently."""
        health = _healthy()
        del health["dropped_continuations"]
        assert any("MALFORMED_HEALTH" in v for v in run_health.violations(health))


class TestHealthFixtureOverrides:
    def test_health_can_override_the_counts_it_pins(self):
        """_health derives from _healthy; a naive `_healthy(shipped=16, **over)` raises
        TypeError on exactly the four keys the outage rules trigger on."""
        for key in ("shipped", "stages", "artifacts", "threads_available"):
            assert _health(**{key: 0})[key] == 0
