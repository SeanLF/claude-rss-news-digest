"""Parse Claude Code JSONL session files for token usage analytics.

Reads session JSONL files from ~/.claude/projects/-app/ to extract
per-subagent token usage. API-equivalent costs are computed for
comparison purposes (actual cost is $0 on Claude subscription).

The JSONL format is Claude Code's internal session log -- not a public API.
If the format changes, this parser fails gracefully (logs warning, returns []).
Raw JSONL files in the Docker volume remain the source of truth.

Why not the Claude Agent SDK session helpers?
----------------------------------------------
The Agent SDK exposes list_sessions()/get_session_messages()/list_subagents()/
get_subagent_messages(), but they do NOT replace this parser:

  * get_session_messages() walks the parentUuid chain and returns only the
    de-duplicated user/assistant messages. This parser instead needs the RAW
    streaming snapshots -- multiple JSONL entries share a message.id and only
    the last has final token counts (see _sum_usage) -- which the chain-walked
    SDK view collapses, losing the per-message usage detail.
  * Per-subagent attribution here is done by matching each subagent file's first
    prompt against the parent session's `Agent` tool_use dispatch *descriptions*
    (CLUSTER/RECAP/SELECT/WRITE/COHERENCE). get_subagent_messages() keys
    subagents by an opaque agent_id, not the dispatch label, so it cannot
    reproduce the human-readable subagent rows stored in `run_usage`.
  * SessionMessage.message is typed Any and does not surface a stable per-message
    `usage` field, so token sums would not be reliably available anyway.

The SDK drives the same `claude` CLI, which still writes these JSONL transcripts,
so this parser stays valid after the SDK migration.

Note: the selection pipeline no longer uses this JSONL parser. Per-stage usage
is now captured directly from each subagent's result event by orchestrate.py
(via `_usage_row`/`_compute_cost` below). `parse_session_usage`/
`_extract_dispatches`/`_match_subagents` are retained for other tooling/tests.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# API-equivalent pricing per million tokens (for comparison only -- subscription = $0)
# Source: https://platform.claude.com/docs/en/about-claude/pricing (2026-03-19)
# Opus = Opus 4.5/4.6, Sonnet = Sonnet 4.x, Haiku = Haiku 4.5
_PRICING = {
    "opus": {"input": 5, "output": 25, "cache_write": 6.25, "cache_read": 0.50},
    "sonnet": {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.30},
    "haiku": {"input": 1, "output": 5, "cache_write": 1.25, "cache_read": 0.10},
}

_PROMPT_PREFIX_LEN = 200
_MAX_AGE_SECONDS = 30 * 60  # 30 minutes -- generous upper bound for a digest run

# Model IDs the pipeline pins (see claude.py / claude_cli.py / .claude/agents/*.md).
# The dispatcher passes these explicitly; the resolved model in the JSONL should
# start with one of them. A mismatch means an alias drifted to a different model
# (the "prod with no Gemfile.lock" failure) -- log loudly so it can't pass silently.
_PINNED_MODEL_IDS = ("claude-sonnet-4-6", "claude-haiku-4-5")


def _extract_dispatches(parent_lines: list[dict]) -> list[dict]:
    """Extract Agent tool_use dispatches from parent session JSONL.

    Returns list of {label, prompt_prefix} in dispatch order.
    Label is the first word of the description, lowercased.
    """
    dispatches = []
    for line in parent_lines:
        msg = line.get("message", {})
        for block in msg.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use" or block.get("name") != "Agent":
                continue
            inp = block.get("input", {})
            description = inp.get("description", "")
            prompt = inp.get("prompt", "")
            label = description.split()[0].lower() if description else "unknown"
            dispatches.append(
                {
                    "label": label,
                    "prompt_prefix": prompt[:_PROMPT_PREFIX_LEN],
                }
            )
    return dispatches


def _match_subagents(dispatches: list[dict], subagent_prompts: dict[str, str]) -> dict[str, str]:
    """Match subagent files to dispatch labels by prompt content.

    Args:
        dispatches: From _extract_dispatches (label + prompt_prefix).
        subagent_prompts: {filename: first_user_message_text}.

    Returns:
        {filename: label} mapping. Unmatched files get "unknown".
    """
    matched = {}
    used_dispatches: set[int] = set()

    for filename, prompt_text in subagent_prompts.items():
        prefix = prompt_text[:_PROMPT_PREFIX_LEN]
        for i, dispatch in enumerate(dispatches):
            if i not in used_dispatches and prefix == dispatch["prompt_prefix"]:
                matched[filename] = dispatch["label"]
                used_dispatches.add(i)
                break
        else:
            matched[filename] = "unknown"

    return matched


def _sum_usage(lines: list[dict]) -> dict[str, dict]:
    """Sum token usage per model from parsed JSONL lines.

    The JSONL records streaming snapshots of each assistant message -- multiple
    entries share the same message.id, with the last having final token counts.
    We keep only the last entry per message.id to avoid overcounting.

    Returns {model: {input, output, cache_write, cache_read}}.
    """
    # Last entry per message.id wins (final streaming state has complete usage)
    last_per_msg: dict[str, dict] = {}
    for line in lines:
        msg = line.get("message", {})
        if msg.get("role") != "assistant" or not msg.get("usage"):
            continue
        mid = msg.get("id", "") or str(id(line))  # fallback for missing ids
        last_per_msg[mid] = msg

    totals: dict[str, dict] = {}
    for msg in last_per_msg.values():
        usage = msg["usage"]
        model = msg.get("model", "unknown")
        if model not in totals:
            totals[model] = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        totals[model]["input"] += usage.get("input_tokens", 0)
        totals[model]["output"] += usage.get("output_tokens", 0)
        totals[model]["cache_write"] += usage.get("cache_creation_input_tokens", 0)
        totals[model]["cache_read"] += usage.get("cache_read_input_tokens", 0)
    return totals


def _compute_cost(model: str, usage: dict) -> float:
    """Compute API-equivalent cost in USD for a model's token usage."""
    model_lower = model.lower()
    if "opus" in model_lower:
        pricing = _PRICING["opus"]
    elif "haiku" in model_lower:
        pricing = _PRICING["haiku"]
    else:
        if "sonnet" not in model_lower:
            logger.debug("Unknown model %r, using Sonnet pricing as fallback", model)
        pricing = _PRICING["sonnet"]
    return round(
        usage["input"] * pricing["input"] / 1_000_000
        + usage["output"] * pricing["output"] / 1_000_000
        + usage["cache_write"] * pricing["cache_write"] / 1_000_000
        + usage["cache_read"] * pricing["cache_read"] / 1_000_000,
        6,
    )


def _usage_row(subagent: str, model: str, usage: dict) -> dict:
    """Build a usage result dict from model name and summed token counts."""
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
        "api_cost_usd": _compute_cost(model, usage),
    }


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, returning parsed lines. Empty list on error."""
    try:
        raw_lines = path.read_text().splitlines()
    except OSError as e:
        logger.warning("Failed to read %s: %s", path, e)
        return []
    lines = []
    for i, raw in enumerate(raw_lines):
        if not raw.strip():
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.debug("Skipping malformed line %d in %s", i + 1, path)
    return lines


def _extract_first_prompt(lines: list[dict]) -> str:
    """Extract the text from the first user message in a subagent session."""
    for line in lines:
        msg = line.get("message", {})
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block["text"]
        elif isinstance(content, str):
            return content
    return ""


def parse_session_usage(projects_dir: Path) -> list[dict]:
    """Parse the most recent Claude session for per-subagent token usage.

    Args:
        projects_dir: Path to ~/.claude/projects/-app/ (or equivalent).

    Returns:
        List of dicts, each with keys: subagent, model, input_tokens,
        output_tokens, cache_write_tokens, cache_read_tokens, api_cost_usd.
        Empty list if no session found or on parse error.
    """
    if not projects_dir.is_dir():
        logger.info("Usage tracking skipped: %s does not exist", projects_dir)
        return []

    jsonl_files = sorted(projects_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not jsonl_files:
        logger.info("Usage tracking skipped: no .jsonl files in %s", projects_dir)
        return []

    session_file = jsonl_files[-1]  # most recent
    session_id = session_file.stem

    age = time.time() - session_file.stat().st_mtime
    if age > _MAX_AGE_SECONDS:
        logger.warning(
            "Usage tracking skipped: most recent session %s is %.0f minutes old (stale)",
            session_id,
            age / 60,
        )
        return []

    logger.info("Parsing usage from session %s", session_id)

    results = []

    # Parse dispatcher (parent session)
    parent_lines = _read_jsonl(session_file)
    if not parent_lines:
        logger.warning("Session file is empty: %s", session_file)
        return []

    for model, usage in _sum_usage(parent_lines).items():
        results.append(_usage_row("dispatcher", model, usage))

    # Match subagent files to dispatch descriptions by prompt content
    dispatches = _extract_dispatches(parent_lines)
    subagents_dir = projects_dir / session_id / "subagents"

    if subagents_dir.is_dir():
        # Read all subagent files and extract their first prompts
        sub_files = sorted(subagents_dir.glob("*.jsonl"))
        sub_data: dict[str, list[dict]] = {}
        sub_prompts: dict[str, str] = {}
        for sub_file in sub_files:
            sub_lines = _read_jsonl(sub_file)
            if not sub_lines:
                continue
            sub_data[sub_file.name] = sub_lines
            sub_prompts[sub_file.name] = _extract_first_prompt(sub_lines)

        # Match by content
        label_map = _match_subagents(dispatches, sub_prompts)

        for filename, lines in sub_data.items():
            role = label_map.get(filename, "unknown")
            for model, usage in _sum_usage(lines).items():
                results.append(_usage_row(role, model, usage))

    total_cost = sum(r["api_cost_usd"] for r in results)
    logger.info("Session %s: %d usage rows, $%.2f API-equivalent", session_id, len(results), total_cost)

    return results
