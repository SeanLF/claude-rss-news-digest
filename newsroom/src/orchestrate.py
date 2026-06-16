"""Deterministic Python orchestration of the curation subagents.

This replaces the LLM "thin dispatcher" (`/news-digest-select`). The dispatch
sequence was always fixed -- CLUSTER, RECAP, SELECT, WRITE, COHERENCE -- so an
LLM deciding to run them in that order each time was wasted cost plus a source
of nondeterminism. We invoke each agent directly, in order, from Python.

Each agent under `.claude/agents/<name>.md` is self-contained: YAML frontmatter
(name, tools, model, ...) plus a markdown body that is a complete system prompt.
We parse the spec, then drive the agent through the SDK wrapper
(`claude_cli.stream_sync`) with:

  prompt          = "Begin."
  system_prompt   = the agent's markdown body
  model           = the agent's frontmatter model (or a caller override)
  allowed_tools   = "Read Write" (from frontmatter `tools: Read, Write`)
  permission_mode = "acceptEdits"

File handoff is unchanged: each agent reads/writes JSON files under
`/app/data/claude_input` exactly as before. After COHERENCE, the existing Python
(`merge.assemble_selections`) takes over -- this module does NOT touch that.

Per-stage usage is captured directly from each agent's terminal `result` event
(its `usage` dict) and turned into a `run_usage` row via `usage._usage_row` /
`usage._compute_cost`, so the recorded rows stay identical in shape and metric
to the previous JSONL-parsing path.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import claude_cli
from claude_agent_sdk import ThinkingConfig
from retry import with_retry
from usage import _usage_row

logger = logging.getLogger(__name__)

_PERMISSION_MODE = "acceptEdits"
_PROMPT = "Begin."
_AGENTS_DIR = Path(".claude/agents")

# Extended thinking is OFF for every stage. These agents previously ran as Task
# subagents (thinking off); the Python orchestrator runs them as top-level SDK
# queries, where Sonnet defaults thinking ON. On the CLUSTER stage that made the
# model reason over ~460 articles until it tripped the 32k output-token ceiling
# and the run aborted. Disabling restores the known-good behaviour. Per-stage
# thinking budgets are a future tuning knob (e.g. the WRITE/SELECT judgment stages).
_THINKING: ThinkingConfig = {"type": "disabled"}


def _tool_list(spec: AgentSpec) -> list[str]:
    """The tool set a stage may use, parsed from its ``tools:`` frontmatter.

    Passed to the SDK as BOTH the ``tools`` availability restriction (the stage
    may only call these, not the full Claude Code set) and -- space-joined -- the
    ``allowed_tools`` auto-approval list, the two shapes the SDK wants. Without the
    availability restriction the agents inherit Bash/Edit/Task and improvise,
    spawning subagents that hang on permission (no handler under acceptEdits) and
    trip the idle-timeout. Falls back to Read/Write (every stage is file-based)
    with a warning, since a missing ``tools:`` key is more likely a spec mistake.
    """
    tools = [t for t in spec.tools_str.replace(",", " ").split() if t]
    if not tools:
        logger.warning("%s: no 'tools' in frontmatter; defaulting to Read/Write", spec.name)
        return ["Read", "Write"]
    return tools


# Wall-clock retry budget per stage: ride out an upstream outage. status.claude.com
# shows median ~1h, worst observed ~3h; 4h leaves headroom.
_STAGE_RETRY_BUDGET_S = 14400

# Per-event idle timeout for the SDK stream (in-process hang detection). A stage
# that goes silent longer than this raises a retryable RuntimeError, so a hang is
# caught at the source rather than waited on. This is the primary hang detector;
# the only external backstop is a generous systemd start-timeout (no file watchdog).
_IDLE_TIMEOUT_S = 120.0

# Stage order is fixed. (subagent_label, agent_spec_name, output_filename, validator).
# TODO: cluster and recap are independent -- they can be run in parallel once we
# have a safe concurrency story for stream_sync (separate event loops / threads).
# Kept sequential for v1 to keep the control flow trivially correct.


@dataclass(frozen=True)
class AgentSpec:
    """A parsed `.claude/agents/<name>.md` spec.

    Only the fields the orchestrator needs are parsed: name, model, the raw
    `tools:` string, and the markdown body (the full system prompt).
    """

    name: str
    model: str
    tools_str: str
    body: str


def parse_agent_spec(path: Path) -> AgentSpec:
    """Parse an agent spec file into an :class:`AgentSpec`.

    The file is `---`-delimited YAML frontmatter followed by a markdown body.
    We hand-parse the handful of scalar frontmatter keys we need (name, model,
    tools) rather than pull in a YAML dependency for three flat strings.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing frontmatter (expected leading '---')")

    # Split off the frontmatter block: text is "---\n<frontmatter>\n---\n<body>".
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{path}: malformed frontmatter (expected a closing '---')")
    _, frontmatter, body = parts

    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        fields[key.strip()] = value.strip().strip("\"'")

    name = fields.get("name", "")
    model = fields.get("model", "")
    tools_str = fields.get("tools", "")
    if not name:
        raise ValueError(f"{path}: frontmatter missing 'name'")
    if not model:
        raise ValueError(f"{path}: frontmatter missing 'model'")

    return AgentSpec(name=name, model=model, tools_str=tools_str, body=body.strip())


# --------------------------------------------------------------------------- #
# Per-stage validators. Each takes the claude_input dir and raises ValueError
# (with a short reason) if the stage output is missing or structurally invalid.
# --------------------------------------------------------------------------- #


def _load_json(path: Path) -> Any:
    """Read and parse a JSON file; raise ValueError on missing/invalid."""
    import json

    if not path.exists():
        raise ValueError(f"{path.name} missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name} unreadable/invalid JSON: {e}") from e


def validate_clusters(claude_input_dir: Path) -> None:
    data = _load_json(claude_input_dir / "clusters.json")
    clusters = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(clusters, list) or not clusters:
        raise ValueError("clusters.json: 'clusters' missing or empty")


def validate_recap(claude_input_dir: Path) -> None:
    path = claude_input_dir / "recap.txt"
    if not path.exists():
        raise ValueError("recap.txt missing")
    if not path.read_text(encoding="utf-8").strip():
        raise ValueError("recap.txt empty")


def validate_selected(claude_input_dir: Path) -> None:
    data = _load_json(claude_input_dir / "selected.json")
    if not isinstance(data, dict) or "must_know" not in data or "should_know" not in data:
        raise ValueError("selected.json: must_know/should_know missing")


def validate_draft(claude_input_dir: Path) -> None:
    data = _load_json(claude_input_dir / "draft_selections.json")
    if not isinstance(data, dict) or not all(k in data for k in ("must_know", "should_know", "preheader")):
        raise ValueError("draft_selections.json: must_know/should_know/preheader missing")


def validate_coherence(claude_input_dir: Path) -> None:
    data = _load_json(claude_input_dir / "coherence_report.json")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("coherence_report.json: 'results' array missing")


# (label, spec filename, output filename, validator)
_STAGES: tuple[tuple[str, str, str, Callable[[Path], None]], ...] = (
    ("cluster", "cluster.md", "clusters.json", validate_clusters),
    ("recap", "recap.md", "recap.txt", validate_recap),
    ("select", "select.md", "selected.json", validate_selected),
    ("write", "write.md", "draft_selections.json", validate_draft),
    ("coherence", "coherence.md", "coherence_report.json", validate_coherence),
)


# --------------------------------------------------------------------------- #
# Stage runner.
# --------------------------------------------------------------------------- #


def _invoke_agent(
    spec: AgentSpec,
    *,
    model: str,
    cwd: str | Path | None,
    idle_timeout: float = _IDLE_TIMEOUT_S,
) -> dict[str, Any]:
    """Drive one agent to completion and return its terminal result event.

    Raises RuntimeError (mirroring ``claude_cli._check_result`` semantics) if
    the stream ends without a result event or with a non-success subtype.
    ``idle_timeout`` bounds the gap between SDK events; a stall raises a
    retryable RuntimeError so a hang is recovered, not waited on forever.
    """
    result_event: dict[str, Any] | None = None
    # Two shapes of the same declared tool set: a space-joined string for the
    # auto-approval list, and a list for the availability restriction.
    tool_list = _tool_list(spec)
    for event in claude_cli.stream_sync(
        _PROMPT,
        model=model,
        system_prompt=spec.body,
        permission_mode=_PERMISSION_MODE,
        allowed_tools=" ".join(tool_list),
        tools=tool_list,
        cwd=cwd,
        idle_timeout=idle_timeout,
        thinking=_THINKING,
    ):
        if event.get("type") == "result":
            result_event = event

    if result_event is None:
        raise RuntimeError(f"{spec.name}: stream ended without a result event")
    subtype = result_event.get("subtype")
    if subtype != "success":
        raise RuntimeError(f"{spec.name}: result subtype={subtype!r}")
    return result_event


def run_stage(
    spec: AgentSpec,
    *,
    label: str,
    output_path: Path,
    validate: Callable[[Path], None],
    model_override: str | None,
    cwd: str | Path | None,
    claude_input_dir: Path,
) -> dict[str, Any]:
    """Run one subagent stage with a single retry, returning its usage row.

    Cleans the stage's stale output file, invokes the agent, then validates the
    written output. On any failure (invocation error, missing/invalid output)
    the stage is retried ONCE from a clean slate. Raises if it still fails.

    The returned dict is a ``run_usage`` row built via ``usage._usage_row`` from
    the result event's ``usage`` block.

    Two layers of retry: transient overload/rate-limit errors during the agent
    invocation get exponential-backoff retries (via ``with_retry``); a stage that
    runs but produces missing/invalid output (or a non-transient error) is
    retried ONCE from a clean slate -- mirroring the old dispatcher's "retry the
    subagent once" rule.
    """
    model = model_override or spec.model
    last_err: Exception | None = None
    # One wall-clock budget shared across BOTH attempts, so the invalid-output
    # retry cannot silently double the outage-riding budget (4h, not 8h).
    stage_deadline = time.monotonic() + _STAGE_RETRY_BUDGET_S

    for attempt in (1, 2):
        output_path.unlink(missing_ok=True)
        try:
            logger.info("[%s started]%s", label.capitalize(), " (retry)" if attempt == 2 else "")
            result_event = with_retry(
                lambda: _invoke_agent(spec, model=model, cwd=cwd),
                label=label,
                deadline=stage_deadline,
            )
            validate(claude_input_dir)
        except (RuntimeError, ValueError) as e:
            last_err = e
            if attempt == 1:
                logger.warning("%s failed (attempt 1/2), retrying: %s", label, e)
                continue
            raise RuntimeError(f"{label} stage failed after retry: {e}") from e

        usage = result_event.get("usage") or {}
        row = _usage_row(
            label,
            model,
            {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "cache_write": usage.get("cache_creation_input_tokens", 0),
                "cache_read": usage.get("cache_read_input_tokens", 0),
            },
        )
        cost = result_event.get("total_cost_usd", 0) or 0
        duration = (result_event.get("duration_ms", 0) or 0) / 1000
        logger.info(
            "[%s complete] %.1fs $%.4f (API-equiv $%.4f)", label.capitalize(), duration, cost, row["api_cost_usd"]
        )
        return row

    # Unreachable: the loop either returns or raises on attempt 2.
    raise RuntimeError(f"{label} stage failed: {last_err}")


def orchestrate_selections(
    *,
    claude_input_dir: Path,
    model_override: str | None = None,
    cwd: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Run the five curation stages in order; return their usage rows.

    Stages: CLUSTER, RECAP, SELECT, WRITE, COHERENCE. Each verifies its output
    and retries once on failure. Raises RuntimeError if any stage cannot
    produce valid output after its retry -- downstream (merge.assemble_selections)
    cannot run without these input files.
    """
    logger.info("Selecting stories... (model=%s)", model_override or "per-agent default")
    usage_rows: list[dict[str, Any]] = []

    for label, spec_filename, output_filename, validate in _STAGES:
        spec = parse_agent_spec(_AGENTS_DIR / spec_filename)
        row = run_stage(
            spec,
            label=label,
            output_path=claude_input_dir / output_filename,
            validate=validate,
            model_override=model_override,
            cwd=cwd,
            claude_input_dir=claude_input_dir,
        )
        usage_rows.append(row)

    total = sum(r["api_cost_usd"] for r in usage_rows)
    logger.info("Selection complete: %d stages, $%.4f API-equivalent", len(usage_rows), total)
    return usage_rows
