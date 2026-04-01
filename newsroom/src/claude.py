"""Claude invocations for the news digest pipeline."""

import logging

from claude_cli import run_sync, stream_sync

logger = logging.getLogger(__name__)

_PERMISSION_MODE = "acceptEdits"
_MCP_CONFIG = ".mcp.json"
_MCP_TOOL = "mcp__news-digest__write_selections"


def generate_selections(model: str | None = None) -> None:
    """Run the dispatcher; streams progress to the log as subagents complete."""
    _model = model or "sonnet"
    logger.info("Selecting stories... (model=%s)", _model)
    pending: dict[str, str] = {}  # Agent tool_use_id -> description

    for event in stream_sync(
        "/news-digest-select",
        model=_model,
        permission_mode=_PERMISSION_MODE,
        allowed_tools=_MCP_TOOL,
        mcp_config=_MCP_CONFIG,
    ):
        etype = event.get("type")

        if etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name == "Agent":
                    description = block.get("input", {}).get("description", "agent")
                    pending[block["id"]] = description
                    logger.info("[%s started]", description)
                elif name == _MCP_TOOL:
                    logger.info("[write_selections called]")

        elif etype == "user" and not event.get("parent_tool_use_id"):
            # Root-level tool_result = Agent returning to dispatcher
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
    return run_sync(prompt, model="haiku", max_turns=1, timeout=120)


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
