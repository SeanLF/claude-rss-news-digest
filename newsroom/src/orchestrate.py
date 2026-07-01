"""Deterministic Python orchestration of the curation subagents.

This replaces the LLM "thin dispatcher" (`/news-digest-select`). The dispatch
sequence was always fixed -- CLUSTER, RECAP, SELECT, WRITE, COHERENCE -- so an
LLM deciding to run them in that order each time was wasted cost plus a source
of nondeterminism. We invoke each agent directly, in order, from Python.

Each agent under `.claude/agents/<name>.md` is self-contained: YAML frontmatter
(name, tools, model, ...) plus a markdown body that is a complete system prompt.
We parse the spec, then drive the agent through the SDK wrapper
(`await claude_cli.run_agent`) with:

  prompt          = "Begin."
  system_prompt   = the agent's markdown body
  model           = the agent's frontmatter model (or a caller override)
  allowed_tools   = "Read Write" (from frontmatter `tools: Read, Write`)
  permission_mode = "acceptEdits"

The wrapper returns a `claude_cli.StageResult` (the SDK's terminal ResultMessage
distilled to subtype/text/usage/cost/duration); we read it directly.

File handoff is unchanged: each agent reads/writes JSON files under
`/app/data/claude_input` exactly as before. After COHERENCE, the existing Python
(`merge.assemble_selections`) takes over -- this module does NOT touch that.

Per-stage usage is captured directly from the StageResult: token counts from its
`usage` dict and cost from the SDK's `total_cost_usd`, assembled into a
`run_usage` row via `usage.usage_row_from_sdk`.
"""

from __future__ import annotations

import datetime
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import claude_cli
import cluster_extractjoin
import config
from claude_agent_sdk import ThinkingConfig
from retry import with_retry_async
from usage import usage_row_from_sdk

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


# Runtime-context token substituted into an agent's system prompt at invocation.
# The models have a training cutoff, so without an explicit "today" anchor a stage
# reasoning about world state (WRITE) silently falls back to a stale prior -- e.g.
# naming "the Biden administration" as current when the day's stories are all about
# the Trump administration. Injecting the real run date (and the paired write.md
# rule to ground office-holders in the articles, not prior knowledge) is the fix.
_CURRENT_DATE_TOKEN = "{{CURRENT_DATE}}"


def render_body(body: str, *, today: datetime.date | None = None) -> str:
    """Resolve runtime-context tokens in an agent system prompt.

    Currently substitutes ``{{CURRENT_DATE}}`` with the run date formatted as
    e.g. ``Wednesday, 1 July 2026``. A body without the token is returned
    unchanged, so this is a no-op for every stage that does not opt in.

    The default date is UTC, matching the pipeline's canonical clock (the digest
    is filed under a UTC date -- see ``run.py``/``db.py``/``digest.py``). Using
    local time here would let the WRITE "today" disagree with the digest date by
    a full day near the UTC-midnight boundary, reintroducing a date mismatch.
    """
    today = today or datetime.datetime.now(datetime.UTC).date()
    # Explicit field formatting avoids the non-portable %-d (no leading zero) flag.
    formatted = f"{today:%A}, {today.day} {today:%B} {today.year}"
    return body.replace(_CURRENT_DATE_TOKEN, formatted)


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
# TODO: cluster and recap are independent -- now that the phase runs under one
# event loop they can be `asyncio.gather`ed once we confirm the SDK is safe to
# drive concurrently (two live `claude` subprocesses). Kept sequential for now to
# keep the control flow trivially correct.


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
    # Optional per-stage SDK tuning, parsed from frontmatter. `effort` is opt-in
    # (low|medium|high|max) -- omitted means the SDK default. Haiku 4.5 used to
    # 400 on effort (no longer on SDK 0.2.110 per bin/sdk-canary), so Haiku stages
    # still leave it unset. `thinking` overrides the module default (_THINKING,
    # disabled); None falls back to that default.
    effort: str | None = None
    thinking: ThinkingConfig | None = None


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

    # Optional tuning keys. `thinking: adaptive` -> {"type": "adaptive"}; absent
    # keys stay None (effort omitted = SDK default; thinking None = _THINKING).
    effort = fields.get("effort") or None
    thinking_val = fields.get("thinking")
    # cast: the frontmatter value is a runtime str, so the {"type": ...} literal
    # can't be statically narrowed to a ThinkingConfig variant; an invalid value
    # surfaces loudly as a 400 at invocation, not silently.
    thinking = cast(ThinkingConfig, {"type": thinking_val}) if thinking_val else None

    return AgentSpec(
        name=name,
        model=model,
        tools_str=tools_str,
        body=body.strip(),
        effort=effort,
        thinking=thinking,
    )


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


async def _invoke_agent(
    spec: AgentSpec,
    *,
    model: str,
    cwd: str | Path | None,
    idle_timeout: float = _IDLE_TIMEOUT_S,
) -> claude_cli.StageResult:
    """Drive one agent to completion and return its :class:`StageResult`.

    Raises RuntimeError if the run did not end successfully (the wrapper itself
    raises on a missing result or an idle hang; a non-success subtype -- e.g. the
    budget cap -- comes back as ``not result.ok`` and we raise here, attaching the
    stage name). ``idle_timeout`` bounds the gap between SDK events; a stall
    raises a retryable RuntimeError so a hang is recovered, not waited on forever.
    """
    # Two shapes of the same declared tool set: a space-joined string for the
    # auto-approval list, and a list for the availability restriction.
    tool_list = _tool_list(spec)
    result = await claude_cli.run_agent(
        _PROMPT,
        model=model,
        system_prompt=render_body(spec.body),
        permission_mode=_PERMISSION_MODE,
        allowed_tools=" ".join(tool_list),
        tools=tool_list,
        cwd=cwd,
        idle_timeout=idle_timeout,
        thinking=spec.thinking or _THINKING,
        effort=spec.effort,
    )
    if not result.ok:
        # error_summary() carries api_error_status when set, so a transient API
        # failure that the SDK mislabelled subtype="success" still reads as
        # retryable to with_retry_async (rather than failing the stage outright).
        raise RuntimeError(f"{spec.name}: {result.error_summary()}")
    return result


async def run_stage(
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

    The returned dict is a ``run_usage`` row built via ``usage.usage_row_from_sdk`` from
    the result event's ``usage`` block.

    Two layers of retry: transient overload/rate-limit errors during the agent
    invocation get exponential-backoff retries (via ``with_retry_async``); a stage
    that runs but produces missing/invalid output (or a non-transient error) is
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
            result = await with_retry_async(
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

        row = usage_row_from_sdk(label, model, result.usage, result.total_cost_usd)
        duration = result.duration_ms / 1000
        logger.info("[%s complete] %.1fs $%.4f", label.capitalize(), duration, row["api_cost_usd"])
        return row

    # Unreachable: the loop either returns or raises on attempt 2.
    raise RuntimeError(f"{label} stage failed: {last_err}")


def _stage_output_is_valid(claude_input_dir: Path, output_filename: str, validate: Callable[[Path], None]) -> bool:
    """True if this stage's output already exists on disk and passes its validator.

    Lets a resumed run skip a stage it already completed. A present-but-invalid file
    (e.g. a crash mid-write) returns False so the stage re-runs from a clean slate.
    (CLUSTER is now the cheap extract-join, not the old ~$6.74 holistic call, but
    skipping any completed stage on resume still saves its cost + latency.)
    """
    if not (claude_input_dir / output_filename).exists():
        return False
    try:
        validate(claude_input_dir)
        return True
    except ValueError:
        return False


async def orchestrate_selections(
    *,
    claude_input_dir: Path,
    model_override: str | None = None,
    cwd: str | Path | None = None,
    resume: bool = False,
) -> list[dict[str, Any]]:
    """Run the five curation stages in order; return their usage rows.

    Stages: CLUSTER, RECAP, SELECT, WRITE, COHERENCE. Each verifies its output
    and retries once on failure. Raises RuntimeError if any stage cannot
    produce valid output after its retry -- downstream (merge.assemble_selections)
    cannot run without these input files.

    Async so the whole curation phase runs under one event loop (opened once by
    ``claude.generate_selections`` via ``asyncio.run``), awaiting the SDK directly
    instead of crossing the sync/async boundary per stage. Stages stay sequential
    for now -- CLUSTER and RECAP are independent and could be ``asyncio.gather``ed
    once we have a safe concurrency story (see the _STAGES note).

    ``resume=True`` skips any stage whose valid output already survives on disk,
    so a re-run after a mid-pipeline failure picks up where it stopped instead of
    re-running (and re-paying for) completed stages. Skipped stages contribute no
    usage row, since they cost nothing this run.
    """
    logger.info("Selecting stories... (model=%s)", model_override or "per-agent default")
    usage_rows: list[dict[str, Any]] = []

    for label, spec_filename, output_filename, validate in _STAGES:
        if resume and _stage_output_is_valid(claude_input_dir, output_filename, validate):
            logger.info("[%s] resuming: valid output present, skipping", label.capitalize())
            continue
        # CLUSTER is the deterministic extract→join path (replaces the holistic LLM agent); it
        # writes clusters.json and validates identically, so the rest of the pipeline is
        # untouched. See cluster_extractjoin.py + the gate doc. Rollback = revert the image.
        if label == "cluster":
            logger.info(
                "[Cluster] extract-join (extract=%s, thr=%.2f)",
                config.CLUSTER_EXTRACT_MODEL,
                config.CLUSTER_JOIN_THRESHOLD,
            )
            row = await cluster_extractjoin.run_extractjoin_stage(
                claude_input_dir,
                model=config.CLUSTER_EXTRACT_MODEL,
                cwd=cwd,
                threshold=config.CLUSTER_JOIN_THRESHOLD,
            )
            validate(claude_input_dir)
            usage_rows.append(row)
            continue
        spec = parse_agent_spec(_AGENTS_DIR / spec_filename)
        row = await run_stage(
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
