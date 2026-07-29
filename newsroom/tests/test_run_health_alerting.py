"""Tests for run.py's post-run health check wiring.

The check runs AFTER the digest has been built, sent and recorded. That ordering
is the whole safety argument: it is best-effort instrumentation sitting on the
critical path of a run that has already succeeded, so it must never be able to
turn a delivered digest into a failed run.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import run

# Imported rather than copied: this was a second literal of the same dict, and adding a
# required health key broke it in a way only CI caught. One definition, one place to update.
from tests.test_run_health import _healthy


def _sent(monkeypatch, *, health, alerting=True):
    """Wire the check against a fake DB/alert pair; return the captured sends."""
    sends = []
    monkeypatch.setattr(run.db, "current_run_id", lambda: 244)
    monkeypatch.setattr(run.db, "should_alert", lambda: alerting)
    monkeypatch.setattr(run.db, "get_run_health", lambda _run_id: health)
    monkeypatch.setattr(run, "send_run_health_alert", lambda v, r: sends.append((v, r)))
    run._alert_on_run_health()
    return sends


_HEALTHY = _healthy()


def test_violation_sends_an_alert_naming_the_run(monkeypatch):
    broken = {**_HEALTHY, "thread_continuations": 0}

    sends = _sent(monkeypatch, health=broken)

    assert len(sends) == 1
    violations, run_id = sends[0]
    assert run_id == 244
    assert any("NO_THREAD_CONTINUATIONS" in v for v in violations)


def test_healthy_run_sends_nothing(monkeypatch):
    assert _sent(monkeypatch, health=_HEALTHY) == []


def test_silent_when_alerting_is_disabled(monkeypatch):
    # --dry-run and --no-record runs must not page anyone.
    broken = {**_HEALTHY, "thread_continuations": 0}

    assert _sent(monkeypatch, health=broken, alerting=False) == []


def test_violations_reach_the_log_even_when_no_alert_is_sent(monkeypatch, caplog):
    # broadcast.py already learned this: "a monitor that can't reach anyone is
    # itself an outage", and a WARNING nobody greps for hid 18 days of a dead
    # feed. With alerting off the violations were computed and then dropped with
    # no record at all -- the log is their only surviving copy, so it must be
    # written BEFORE the send is attempted, and at ERROR.
    broken = {**_HEALTHY, "thread_continuations": 0}

    with caplog.at_level("DEBUG"):
        _sent(monkeypatch, health=broken, alerting=False)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, f"violations logged below ERROR: {caplog.text!r}"
    assert "NO_THREAD_CONTINUATIONS" in caplog.text


def test_unjudgeable_run_stays_quiet(monkeypatch):
    # get_run_health returns {} when the DB is unreachable. Absence of data is not
    # evidence of failure -- alerting on it would cry wolf on every DB hiccup.
    assert _sent(monkeypatch, health={}) == []


def test_a_failing_check_never_breaks_the_run(monkeypatch, caplog):
    # The digest is already built, sent and recorded by the time this runs. An
    # exception escaping here would convert a delivered digest into a failed run
    # and trip the healthcheck down-alert, which is strictly worse than not
    # knowing whether an invariant held.
    def _boom(_run_id):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(run.db, "current_run_id", lambda: 244)
    monkeypatch.setattr(run.db, "should_alert", lambda: True)
    monkeypatch.setattr(run.db, "get_run_health", _boom)

    with caplog.at_level("DEBUG"):
        run._alert_on_run_health()

    # ERROR, not WARNING: a monitor that failed to run is an outage in the
    # monitor, and it is the one failure no alert can report.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, f"monitor failure logged below ERROR: {caplog.text!r}"
    assert "db exploded" in caplog.text


class TestDroppedContinuationReporting:
    """The count cannot be carried by a rule (it is mutually exclusive with the only rule that
    could quote it), so run.py logs it directly -- before the no-violations early return, which
    is the path every run that can produce it takes."""

    def test_it_is_logged_on_an_otherwise_healthy_run(self, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING):
            sends = _sent(monkeypatch, health={**_HEALTHY, "dropped_continuations": 2})
        assert sends == [], "reporting must not become an alert"
        assert "2 story/stories lost a proposed thread continuation" in caplog.text

    def test_zero_is_silent(self, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING):
            _sent(monkeypatch, health={**_HEALTHY, "dropped_continuations": 0})
        assert "lost a proposed thread continuation" not in caplog.text

    def test_cannot_judge_is_silent(self, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING):
            _sent(monkeypatch, health={**_HEALTHY, "dropped_continuations": None})
        assert "lost a proposed thread continuation" not in caplog.text
