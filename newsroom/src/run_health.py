"""Post-run invariants: assert a finished run is not silently wrong.

This pipeline does not usually fail loudly. The linker returning quoted ids cost a
full day of thread continuity and exited 0; the duplicate cluster cards shipped to
subscribers and exited 0; the alerting misconfiguration no-op'd for months and
exited 0. An exception tracker would have caught none of them, because none of
them raised.

`analytics/queries/run-reliability.sql` already encodes most of these conditions
as its `flags` column -- but only a human running that query ever saw them. This
module evaluates the same conditions at the end of a run so the existing alert
path can carry them.

Each rule is a decision rule with a citable trigger, not a weighted score: it
fires on a count being zero, never on a threshold someone tuned to taste.
"""

from collections.abc import Callable

# (code, predicate, message). A rule fires when its predicate returns True.
# Keep predicates total -- a missing key means the caller built the health dict
# wrong, and .get with a safe default is preferable to an alert path that raises.
_RULES: list[tuple[str, Callable[[dict], bool], str]] = [
    (
        "ZERO_STORIES",
        lambda h: h.get("shipped", 0) == 0,
        "the run completed but shipped no stories",
    ),
    (
        "ZERO_RECIPIENTS",
        # Only meaningful when the run was actually broadcasting: --no-email and
        # --dry-run reach zero recipients by design, and alerting on those would
        # train the reader to ignore the alert.
        lambda h: bool(h.get("broadcasting")) and h.get("recipients", 0) == 0,
        "a digest was built but sent to nobody",
    ),
    (
        "NO_USAGE_RECORDED",
        lambda h: h.get("stages", 0) == 0,
        "no subagent stage recorded usage, so the curation phase left no trace",
    ),
    (
        "NO_ARTIFACTS",
        lambda h: h.get("artifacts", 0) == 0,
        "no intermediate artifacts were archived, so this run cannot be replayed",
    ),
    (
        "NO_THREAD_CONTINUATIONS",
        # Zero is normal in two cases, both excluded by construction rather than by
        # a tuned floor. (1) Nothing to continue: the seeding run (205) is the only
        # such case in 39 thread-enabled runs. (2) The thread layer is switched
        # off -- THREADS_ENABLED is a kill switch that DEFAULTS TO FALSE, and live
        # threads stay in the table when it flips, so without this guard the rule
        # would fire every run forever the moment the switch is used.
        lambda h: (
            h.get("threads_enabled", False)
            and h.get("threads_available", 0) > 0
            and h.get("thread_continuations", 0) == 0
        ),
        "no shipped story continued an existing thread, though live threads existed",
    ),
]


REQUIRED_KEYS = frozenset(
    {
        "shipped",
        "stages",
        "artifacts",
        "recipients",
        "broadcasting",
        "thread_continuations",
        "threads_available",
        "threads_enabled",
    }
)


def violations(health: dict) -> list[str]:
    """Return one readable line per violated invariant; empty means healthy."""
    # Checked here rather than per-rule because the two GUARDED rules fail
    # silent-off on a missing key: their guard defaults to "do not fire", so a
    # rename would quietly disable the very rule this module was built for and
    # leave every test green. A malformed dict is itself the alertable condition.
    if missing := REQUIRED_KEYS - health.keys():
        return [f"MALFORMED_HEALTH: run health is missing {sorted(missing)}; invariants NOT evaluated"]
    return [f"{code}: {message}" for code, predicate, message in _RULES if predicate(health)]
