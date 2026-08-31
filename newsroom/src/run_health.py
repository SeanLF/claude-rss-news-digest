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
# The message is a plain string for rules whose severity is fixed, or a callable when it
# has to quote the run's own numbers (see DEGRADED_CLUSTERING: "a batch was lost" is 1
# article or 40 depending on where the batch fell).
_RULES: list[tuple[str, Callable[[dict], bool], str | Callable[[dict], str]]] = [
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
        "DEGRADED_CLUSTERING",
        # Triggered by a WHOLESALE batch loss, not by the fallback count. Measured
        # over 42 archived runs, the fallback count is BIMODAL: 21 runs at 0, 13 at
        # 1-2 strays, 8 at 24-34. That upper mode IS the batch loss. Triggering on
        # the count would fire on the stray mode too; batches_lost targets the
        # upper mode directly, with no threshold to tune.
        # `or 0` is the "cannot judge" path, not a default: runs archived before
        # cluster_health.json existed carry None, and absence is not evidence of a
        # clean run.
        lambda h: (h.get("batches_lost") or 0) > 0,
        # The count comes from the artifact rather than the batch size: a short
        # final batch makes "a batch was lost" as few as 1 article (3 archived runs
        # end with a 1-article batch), and a rule justified by not crying wolf
        # should not overstate by 40x in its own message.
        lambda h: (
            f"{h.get('batches_lost')} extraction batch(es) returned nothing usable; "
            f"{h.get('title_only_fallback')} articles lost their entity tags"
        ),
    ),
    (
        "BLANKED_WHY_IT_MATTERS",
        # Blanking SHIPS: nothing is dropped, the story count is unchanged, and every
        # other invariant reads clean -- which is why run 280 lost the field on 38% of
        # its digest and nothing fired. None is "not recorded", not a clean run. The
        # floor is a rate, not a count, because one blanked story is the designed
        # fallback working, and a rule that fires on it gets ignored.
        lambda h: (
            h.get("blanked_why") is not None and h.get("shipped", 0) > 0 and h["blanked_why"] / h["shipped"] >= 0.25
        ),
        lambda h: (
            f"{h.get('blanked_why')} of {h.get('shipped')} shipped stories "
            f"({100.0 * h['blanked_why'] / h['shipped']:.0f}%) went out with no why_it_matters"
        ),
    ),
    (
        "FULLTEXT_TOTAL_LOSS",
        # Run 281: the step had 43 candidates and returned nothing after hanging 62
        # minutes. Fulltext is best-effort by design, so the digest shipped and no
        # other invariant could see it. None means the step did not run (disabled --
        # the documented run-281 recovery) and must not alert.
        lambda h: (h.get("fulltext_tasks") or 0) > 0 and (h.get("fulltext_extracted") or 0) == 0,
        lambda h: (
            f"fulltext extracted 0 of {h.get('fulltext_tasks')} candidate articles "
            f"(worker {h.get('fulltext_outcome') or 'unknown'}); stories fell back to CSV summaries"
        ),
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
        # linker_ok is quoted, not triggered on: when this rule fires the first question is
        # "did the linker actually run?", and that answer used to live only in a log line.
        #
        # dropped_continuations is deliberately NOT quoted here. It cannot co-occur with this
        # rule -- a refusal is only recorded when another story already claimed that thread,
        # so it entails at least one continuation, and this rule needs zero. run.py reports it
        # instead.
        lambda h: (
            "no shipped story continued an existing thread, though live threads existed"
            + ("" if h.get("linker_ok") is not False else " -- the linker call itself failed")
        ),
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
        "batches_lost",
        # Not a trigger, but the alert message quotes it -- a message that says
        # "None articles" is its own small lie.
        "title_only_fallback",
        # Quoted by the thread-continuity message, not triggered on.
        #
        # NOT a trigger yet, deliberately. A refusal is usually a dropped continuation (a
        # week-old story rendering "day 1"), but it can also be the guard working correctly
        # against a linker over-merge, and the replay that validated the linker measured ~2
        # over-merges per 94 threads -- so the base rate is not known to be zero and an
        # alert on `> 0` could cry wolf from day one. The measurement is available: give
        # seed_threads a trace and have it write the traces to a FILE (not into run_artifacts
        # -- see the comment there for why), then replay the archived runs and count. Promote
        # this to a rule once there is a number.
        "dropped_continuations",
        "linker_ok",
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
    return [
        f"{code}: {message(health) if callable(message) else message}"
        for code, predicate, message in _RULES
        if predicate(health)
    ]
