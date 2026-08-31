"""Claude invocations for the news digest pipeline."""

import asyncio
import logging
from collections.abc import Callable

import config  # referenced at call time so model choices stay env/monkeypatch-overridable
from claude_agent_sdk import ClaudeSDKError
from claude_cli import run_sync
from config import CLAUDE_INPUT_DIR
from orchestrate import orchestrate_selections
from retry import with_retry

logger = logging.getLogger(__name__)


def generate_selections(
    model: str | None = None, resume: bool = False, on_usage: Callable[[dict], None] | None = None
) -> list[dict]:
    """Run the fixed curation pipeline; return per-stage usage rows.

    Python orchestrates the five subagents directly, in order (CLUSTER, RECAP,
    SELECT, WRITE, COHERENCE) -- see ``orchestrate.orchestrate_selections``.
    The old LLM "thin dispatcher" is gone: the sequence was always fixed, so an
    LLM choosing it each time was wasted cost plus nondeterminism. Per-stage
    retry now lives in ``orchestrate.run_stage``; this function raises if any
    stage cannot produce valid output after its retry (fail loud).

    Subagents write intermediate JSON files; assembly of selections.json happens
    in Python after this returns (see merge.py). The returned rows are recorded
    into ``run_usage`` by the caller (run.py).

    This is the pipeline's single sync/async boundary: ``orchestrate_selections``
    is async (the curation phase awaits the SDK directly under one event loop), and
    this one ``asyncio.run`` opens that loop and returns to the sync pipeline. The
    surrounding pipeline (db, feeds, render, email) is genuinely blocking, so the
    async island stays scoped to curation rather than going viral up to run.py.
    """
    return asyncio.run(
        orchestrate_selections(
            claude_input_dir=CLAUDE_INPUT_DIR, model_override=model, resume=resume, on_usage=on_usage
        )
    )


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
    # Short, count-based retry: the weekly recap is best-effort and non-fatal, and
    # it runs BEFORE selection. It must fail fast on a transient error rather than
    # burn the 4h wall-clock budget here -- riding out an outage is selection's job.
    return with_retry(
        lambda: run_sync(prompt, model=config.RECAP_MODEL, max_turns=1, timeout=120),
        label="weekly_recap",
        max_attempts=3,
    )


def health_check() -> int:
    """Verify Claude auth is working. Returns 0 on success, 1 on failure."""
    logger.info("Running Claude auth health check...")
    try:
        result = run_sync("respond with 'ok'", model=config.DEFAULT_MODEL, max_turns=1, timeout=60)
        if "ok" in result.lower():
            logger.info("Health check passed")
            return 0
        logger.error("Health check FAILED: unexpected response: %s", result[:100])
        return 1
    except (RuntimeError, ClaudeSDKError) as e:
        logger.error("Health check FAILED: %s", e)
        return 1
