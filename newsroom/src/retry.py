"""Transient-failure retry with exponential backoff.

Shared by claude.py (weekly recap) and orchestrate.py (per-stage curation
invocations) so both get the same overload/rate-limit resilience. Kept in its
own module to avoid a circular import between claude.py and orchestrate.py.

Retry is wall-clock budgeted by default (``max_elapsed``): a real upstream
outage (status.claude.com data: median ~1h, worst observed ~3h) is ridden out
by retrying until a 4h budget is exhausted, not after a fixed attempt count.
``max_attempts`` is still honoured when a caller passes it (count-based, legacy)
so call sites that want a hard cap keep working. A caller can also pass a shared
absolute ``deadline`` so several with_retry() calls (e.g. both attempts of a
stage) share one budget rather than each starting a fresh one.

Long backoff waits are safe to sit in: hang detection is in-process (the SDK
event-idle timeout in claude_cli), and the only external backstop is a generous
systemd start-timeout sized above this budget -- so there is no file-activity
watchdog to placate during a legitimate wait.
"""

import logging
import random
import time
from collections.abc import Callable

from claude_agent_sdk import ClaudeSDKError

logger = logging.getLogger(__name__)

# Retry tuning for transient API failures (529 overloaded, 502/503, rate limits, timeouts).
# "idle timeout"/"timeout" cover the SDK event-idle hang detector in claude_cli.
_RETRYABLE_PATTERNS = (
    "529",
    "503",
    "502",
    "overloaded",
    "rate_limit",
    "rate-limit",
    "idle timeout",
    "timeout",
)
_BASE_DELAY = 30.0
_MAX_DELAY = 300.0
_JITTER = 0.3

# Default wall-clock retry budget: 4h. Real outages run ~1h median, ~3h worst
# observed; 4h leaves headroom without retrying forever.
_MAX_ELAPSED = 14400.0


def is_retryable(err: BaseException) -> bool:
    msg = str(err).lower()
    return any(p in msg for p in _RETRYABLE_PATTERNS)


def with_retry[T](
    fn: Callable[[], T],
    *,
    label: str,
    max_elapsed: float = _MAX_ELAPSED,
    max_attempts: int | None = None,
    deadline: float | None = None,
    on_retry: Callable[[], None] | None = None,
) -> T:
    """Run fn() with exponential backoff on overload/rate-limit/timeout errors.

    Termination is wall-clock budgeted by ``max_elapsed`` (default 4h): retry
    transient errors until the next sleep would cross the budget, then re-raise
    the last error. If ``max_attempts`` is given (legacy, count-based), stop
    after that many attempts instead. ``deadline`` (absolute monotonic time)
    overrides ``max_elapsed`` and lets callers share one budget across calls.

    Backoff is exponential ``_BASE_DELAY * 2**(n-1)`` capped at ``_MAX_DELAY``
    (300s) with jitter; past the cap it keeps polling at ~300s.

    Non-retryable errors propagate immediately. Each retry is a full restart of
    fn(); there is no built-in resume.
    """
    start = time.monotonic()
    # Absolute monotonic budget end. A caller-supplied deadline (shared across
    # several with_retry calls) wins; otherwise derive it from max_elapsed.
    budget_deadline = deadline if deadline is not None else start + max_elapsed
    attempt = 0
    while True:
        attempt += 1
        try:
            result = fn()
            if attempt > 1:
                logger.info("%s: recovered after %d attempts", label, attempt)
            return result
        except (RuntimeError, ClaudeSDKError) as e:
            if not is_retryable(e):
                raise
            if max_attempts is not None and attempt >= max_attempts:
                raise

            delay = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
            delay *= 1 + random.uniform(-_JITTER, _JITTER)

            # Wall-clock budget: if this sleep would push us past the deadline,
            # give up now rather than start a sleep we cannot afford.
            if max_attempts is None and time.monotonic() + delay >= budget_deadline:
                raise

            if max_attempts is not None:
                logger.warning(
                    "%s: retryable error on attempt %d/%d, sleeping %.1fs: %s",
                    label,
                    attempt,
                    max_attempts,
                    delay,
                    str(e)[:200],
                )
            else:
                elapsed = time.monotonic() - start
                logger.warning(
                    "%s: retryable error on attempt %d (%.0fs/%.0fs budget), sleeping %.1fs: %s",
                    label,
                    attempt,
                    elapsed,
                    budget_deadline - start,
                    delay,
                    str(e)[:200],
                )

            time.sleep(delay)
            if on_retry is not None:
                on_retry()
