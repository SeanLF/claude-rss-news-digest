"""Claude invocations for the news digest pipeline."""

import logging
import random
import time
from collections.abc import Callable

from claude_cli import run_sync, stream_sync
from config import CLAUDE_INPUT_DIR

logger = logging.getLogger(__name__)

_PERMISSION_MODE = "acceptEdits"

# Retry tuning for transient API failures (529 overloaded, 502/503, rate limits, timeouts).
# 5 attempts with exponential backoff = ~10 min total budget before giving up.
_RETRYABLE_PATTERNS = ("529", "503", "502", "overloaded", "rate_limit", "rate-limit", "timeout")
_MAX_ATTEMPTS = 5
_BASE_DELAY = 30.0
_MAX_DELAY = 300.0
_JITTER = 0.3

# Subagent outputs cleared between dispatcher retries so attempt N+1 starts
# from a clean slate instead of inheriting partial files from attempt N.
_DISPATCHER_INTERMEDIATES = (
    "clusters.json",
    "recap.txt",
    "selected.json",
    "draft_selections.json",
    "coherence_report.json",
)


def _is_retryable(err: BaseException) -> bool:
    msg = str(err).lower()
    return any(p in msg for p in _RETRYABLE_PATTERNS)


def _with_retry[T](
    fn: Callable[[], T],
    *,
    label: str,
    max_attempts: int = _MAX_ATTEMPTS,
    on_retry: Callable[[], None] | None = None,
) -> T:
    """Run fn() with exponential backoff on overload/rate-limit errors.

    Non-retryable errors propagate immediately. Each retry is a full restart of
    fn() -- for the dispatcher this means re-running CLUSTER -> COHERENCE from
    scratch at full token cost; no built-in resume.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn()
            if attempt > 1:
                logger.info("%s: recovered after %d attempts", label, attempt)
            return result
        except RuntimeError as e:
            if not _is_retryable(e) or attempt == max_attempts:
                raise
            delay = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
            delay *= 1 + random.uniform(-_JITTER, _JITTER)
            logger.warning(
                "%s: retryable error on attempt %d/%d, sleeping %.1fs: %s",
                label,
                attempt,
                max_attempts,
                delay,
                str(e)[:200],
            )
            time.sleep(delay)
            if on_retry is not None:
                on_retry()
    raise AssertionError("unreachable")


def _cleanup_dispatcher_intermediates() -> None:
    """Remove subagent output files so a retried dispatcher writes clean state."""
    for name in _DISPATCHER_INTERMEDIATES:
        (CLAUDE_INPUT_DIR / name).unlink(missing_ok=True)


def generate_selections(model: str | None = None) -> None:
    """Run the dispatcher; streams progress to the log as subagents complete.

    The dispatcher writes intermediate JSON files via subagents; assembly of
    selections.json happens in Python after this returns (see merge.py).
    """
    _model = model or "sonnet"

    def _run() -> None:
        logger.info("Selecting stories... (model=%s)", _model)
        pending: dict[str, str] = {}  # Agent tool_use_id -> description

        for event in stream_sync(
            "/news-digest-select",
            model=_model,
            permission_mode=_PERMISSION_MODE,
        ):
            etype = event.get("type")

            if etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") == "Agent":
                        description = block.get("input", {}).get("description", "agent")
                        pending[block["id"]] = description
                        logger.info("[%s started]", description)

            # Root-level tool_result (no parent_tool_use_id) = Agent returning to dispatcher.
            elif etype == "user" and not event.get("parent_tool_use_id"):
                for block in event.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        name = pending.pop(block.get("tool_use_id", ""), None)
                        if name:
                            logger.info("[%s complete]", name)

            elif etype == "result":
                cost = event.get("total_cost_usd", 0)
                duration = event.get("duration_ms", 0) / 1000
                subtype = event.get("subtype", "?")
                logger.info("Selection %s: %.1fs $%.4f", subtype, duration, cost)
                if subtype != "success":
                    raise RuntimeError(f"Claude dispatcher ended with subtype={subtype!r}")

    _with_retry(_run, label="dispatcher", on_retry=_cleanup_dispatcher_intermediates)


def generate_weekly_recap(title_lines: str) -> str:
    """Summarise recent RSS titles into a 2-3 sentence thematic recap via Haiku."""
    prompt = (
        "Below are RSS article titles from the past week. "
        "Summarise the major themes in 2-3 sentences. "
        "Note any multi-day themes that developed over the week. "
        "Do NOT reproduce specific headlines or titles. "
        "Write in paragraph format only.\n\n"
        f"{title_lines}"
    )
    return _with_retry(
        lambda: run_sync(prompt, model="haiku", max_turns=1, timeout=120),
        label="weekly_recap",
    )


def health_check() -> int:
    """Verify Claude auth is working. Returns 0 on success, 1 on failure."""
    logger.info("Running Claude auth health check...")
    try:
        result = run_sync("respond with 'ok'", max_turns=1, timeout=60)
        if "ok" in result.lower():
            logger.info("Health check passed")
            return 0
        logger.error("Health check FAILED: unexpected response: %s", result[:100])
        return 1
    except RuntimeError as e:
        logger.error("Health check FAILED: %s", e)
        return 1
