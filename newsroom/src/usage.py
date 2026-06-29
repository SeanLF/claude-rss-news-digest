"""Build per-subagent ``run_usage`` rows from the Agent SDK's result events.

Each curation stage runs as a subagent via ``claude_cli.run_agent``, which
distils the SDK's terminal ``ResultMessage`` into a ``StageResult`` carrying the
token ``usage`` dict and the SDK's own ``total_cost_usd``. ``orchestrate.py``
turns each into a ``run_usage`` row via ``_usage_row`` below.

Cost is the SDK's reported ``total_cost_usd`` -- an API-equivalent figure the SDK
computes from token usage at API rates (the actual cost on a Claude subscription
is $0). We no longer hand-roll a pricing table: the SDK is the single source of
truth for cost, so it stays correct as model pricing changes without us tracking
it.
"""

import logging

logger = logging.getLogger(__name__)

# Model IDs the pipeline pins (see config.py / claude_cli.py / .claude/agents/*.md).
# A resolved model matching none of these means an alias drifted to a different
# model (the "prod with no Gemfile.lock" failure) -- log loudly so it can't pass
# silently.
_PINNED_MODEL_IDS = ("claude-sonnet-4-6", "claude-haiku-4-5")


def _usage_row(subagent: str, model: str, usage: dict, api_cost_usd: float) -> dict:
    """Build a ``run_usage`` row from a subagent's token counts and SDK cost.

    ``usage`` holds the SDK token counts (keys: input/output/cache_write/
    cache_read); ``api_cost_usd`` is the SDK's ``total_cost_usd`` for the stage.
    """
    if model != "unknown" and not model.startswith(_PINNED_MODEL_IDS):
        logger.warning(
            "Model drift: subagent %r resolved to %r, which does not match any "
            "pinned ID %s -- an alias may have drifted to a different model.",
            subagent,
            model,
            _PINNED_MODEL_IDS,
        )
    return {
        "subagent": subagent,
        "model": model,
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "cache_write_tokens": usage["cache_write"],
        "cache_read_tokens": usage["cache_read"],
        "api_cost_usd": api_cost_usd,
    }


def usage_row_from_sdk(subagent: str, model: str, sdk_usage: dict, api_cost_usd: float) -> dict:
    """Build a run_usage row from the RAW SDK ResultMessage.usage (keys input_tokens /
    output_tokens / cache_creation_input_tokens / cache_read_input_tokens). Centralizes the
    SDK->row key mapping so callers (orchestrate stages, thread synthesis) can't drift apart."""
    return _usage_row(
        subagent,
        model,
        {
            "input": sdk_usage.get("input_tokens", 0),
            "output": sdk_usage.get("output_tokens", 0),
            "cache_write": sdk_usage.get("cache_creation_input_tokens", 0),
            "cache_read": sdk_usage.get("cache_read_input_tokens", 0),
        },
        api_cost_usd,
    )
