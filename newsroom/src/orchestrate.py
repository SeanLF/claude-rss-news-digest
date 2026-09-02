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

import asyncio
import datetime
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import claude_cli
import cluster_extractjoin
import config
import db
import fulltext
import gnews
import healthcheck
import merge
import repair
import schema
import write_fanout
from claude_agent_sdk import ThinkingConfig
from merge import _item_article_ids
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
# and the run aborted. That stage no longer reaches this default -- orchestrate_selections
# routes cluster to cluster_extractjoin, which sets its own -- so this now governs only
# RECAP/SELECT, and the incident that justifies it happened elsewhere. coherence and write
# override it per-stage; repair inherits this default and is the next candidate.
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

# The same budget for the WHOLE run. Without it each of the seven stages took a FRESH
# 4h, so the pipeline could ask for 28h under a systemd TimeoutStartSec of 5h -- the
# ceiling sat 23h BELOW the budget it was documented as sitting above, and the kill it
# produces has no handler, so digest_runs keeps status='running' forever.
_RUN_RETRY_BUDGET_S = 14400

# Hard cap on ONE stage attempt. with_retry_async's deadline is only consulted after
# fn() raises, and _IDLE_TIMEOUT_S resets on every streamed event, so a stage that
# emits tokens steadily and never terminates was bounded by nothing in this process.
_STAGE_ATTEMPT_TIMEOUT_S = 2700.0

# Spend cap for ONE stage attempt, enforced by the SDK (subtype="error_max_budget_usd", which
# StageResult.ok already rejects). _STAGE_ATTEMPT_TIMEOUT_S bounds the clock; a stage that loops
# cheaply inside it still burns money. A backstop, not a budget: the worst single call in runs
# 241-280 is $3.16 (coherence), so this never fires on a healthy run and still trips before one
# stage can outspend a whole normal run.
_STAGE_BUDGET_USD = 8.0

# Concurrent WRITE branches. Matches cluster_extractjoin._EXTRACT_CONCURRENCY, the only
# other fan-out in the pipeline.
_WRITE_BRANCH_CONCURRENCY = 4

# Hard cap on ONE branch attempt. Far below _STAGE_ATTEMPT_TIMEOUT_S because a branch writes
# ONE story from ~4 KB of CSV, and because the ceiling compounds: at the whole-stage 45 min,
# select.md's hard max of 20 stories over 4 concurrent slots x 2 attempts is 7.5h, past
# _RUN_RETRY_BUDGET_S and past the systemd start-timeout whose kill has no handler.
_WRITE_BRANCH_ATTEMPT_TIMEOUT_S = 900.0

# Spend cap for ONE branch attempt, sized to a healthy branch rather than to a whole stage:
# the run-284 replay on the shipped config bills ~$0.13 per branch and $0.25 at its widest
# cluster, so this is ~4x headroom over the worst branch measured. _run_write_branches bounds
# the PHASE on top of it.
_WRITE_BRANCH_BUDGET_USD = 1.00

_PREHEADER_NAME = "preheader.txt"
_WRITE_BRANCHES_NAME = "write_branches.json"
# The preheader stage reads a ~2 KB draft and writes one line on Haiku. Its own bounds, not
# the whole-stage ones, so the fan-out's worst case stays inside the run budget.
_PREHEADER_ATTEMPT_TIMEOUT_S = 300.0
_PREHEADER_BUDGET_USD = 0.50

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
    # `display` is an optional key ON adaptive/enabled, not a config of its own, and
    # ThinkingConfigDisabled has no such key -- attaching it there is a 400. It is billed
    # identically either way, so the only thing it changes is whether the reasoning is
    # recoverable after a miss.
    display = fields.get("display")
    if display and thinking is not None and thinking_val != "disabled":
        thinking = cast(ThinkingConfig, {**thinking, "display": display})

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


# A CLOSED SET of label prefixes, not a shape. Anything general enough to match "Preheader:"
# also matches a lead clause in this pipeline's own register -- "Colombia quake: 3-year-old
# rescued", "WHO: companies filed 235 lawsuits" -- and decapitates the sentence. Measured
# against the 1,225 headlines in the 79 archived selections.json, which preheader.md tells the
# agent to compose from: a length-bounded `^word+:` rule silently eats 10 of them. A literal
# list cannot, so an unrecognised prefix ships intact instead of taking the subject with it.
_PREHEADER_LABELS = ("preheader", "preheader line", "here is the preheader", "the preheader")
_PREHEADER_LABEL_RE = re.compile(
    r"^\**\s*(?:" + "|".join(re.escape(label) for label in _PREHEADER_LABELS) + r")\s*\**\s*:\**\s*",
    re.IGNORECASE,
)
_PREHEADER_LIST_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def clean_preheader(text: str) -> str:
    """Strip the wrappers a model puts around a one-line answer.

    Handled, in order: code fences, blank lines, a line that is nothing but a label, a list
    marker, a label prefix, and matched wrapping quotes. Whatever survives is truncated to
    ``schema.PREHEADER_MAX_CHARS``. Returns "" when nothing usable is left, which merge
    fills from the first non-empty headline -- the field is reader-facing and has a
    hard-fail history (run 229), so every branch here degrades rather than raises.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line and not line.startswith("```")]
    # A line that is a label and nothing else carries no content, so dropping it cannot lose
    # any. Same closed set, for the same reason.
    while lines and _PREHEADER_LABEL_RE.sub("", lines[0]).strip() == "":
        lines.pop(0)
    if not lines:
        return ""
    first = lines[0]
    if len(lines) > 1:
        logger.warning("preheader.txt has %d usable lines; keeping the first", len(lines))
    first = _PREHEADER_LIST_RE.sub("", first)
    first = _PREHEADER_LABEL_RE.sub("", first).strip()
    for quote in ('"', "'", "“”", "‘’"):
        open_q, close_q = (quote, quote) if len(quote) == 1 else (quote[0], quote[1])
        if len(first) > 1 and first.startswith(open_q) and first.endswith(close_q):
            first = first[1:-1].strip()
    return merge._truncate_on_word_boundary(first, schema.PREHEADER_MAX_CHARS)


def read_preheader(claude_input_dir: Path) -> str:
    """:func:`clean_preheader` applied to the preheader stage's output file."""
    path = claude_input_dir / _PREHEADER_NAME
    if not path.exists():
        return ""
    return clean_preheader(path.read_text(encoding="utf-8"))


def validate_preheader(claude_input_dir: Path) -> None:
    """Retry gate on the preheader stage: it must have written something.

    Only emptiness fails, and only so ``run_stage`` re-attempts once. Shape problems are
    degraded by :func:`read_preheader`, and a stage that fails outright leaves the
    preheader blank for merge to fill -- never an aborted digest."""
    path = claude_input_dir / _PREHEADER_NAME
    if not path.exists():
        raise ValueError(f"{_PREHEADER_NAME} missing")
    if not path.read_text(encoding="utf-8").strip():
        raise ValueError(f"{_PREHEADER_NAME} empty")


def validate_coherence(claude_input_dir: Path) -> None:
    """Fail-closed structure gate on coherence_report.json.

    Raises ValueError (retrying the stage via run_stage) when: (a) any entry in
    ``results`` is not an object, (b) any entry lacks a boolean "pass", or (c)
    any draft story has NO matching result (matched the way merge.py matches:
    by cited-article_ids set, falling back to normalized headline). A truncated
    or drifted report used to only warn downstream (merge.py's coverage-gap
    log) and ship the unchecked story unverified -- COHERENCE is meant to check
    EVERY story, so a coverage gap is a stage failure, not a soft signal. The
    check is identity-based, not count-based: a duplicate result or a
    badly-retyped headline cannot satisfy it while a real story goes unchecked.

    (c) is best-effort: if draft_selections.json is missing or unreadable here,
    the coverage check is skipped rather than raised -- the WRITE-stage
    validator (validate_draft) owns that file's integrity, not this one.
    """
    data = _load_json(claude_input_dir / "coherence_report.json")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("coherence_report.json: 'results' array missing")
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            raise ValueError(f"coherence_report.json: results[{i}] is not an object")
        if not isinstance(r.get("pass"), bool):
            raise ValueError(
                f"coherence_report.json: results[{i}] (headline={r.get('headline')!r}) missing boolean 'pass'"
            )
        # failed_fields is OPTIONAL graceful-degradation metadata (merge.py owns the
        # drop-vs-strip decision). Only enforce the wire shape on a failing entry;
        # absent is fine, but present-and-malformed is a stage failure, not a
        # silent downstream misparse. Unknown field names inside the list are NOT
        # rejected here (forward-compat) -- merge.py treats those conservatively.
        if r.get("pass") is False and "failed_fields" in r:
            failed_fields = r["failed_fields"]
            if not isinstance(failed_fields, list) or not all(isinstance(f, str) for f in failed_fields):
                raise ValueError(
                    f"coherence_report.json: results[{i}] (headline={r.get('headline')!r}) "
                    "'failed_fields' must be a list of strings"
                )

    import json

    draft_path = claude_input_dir / "draft_selections.json"
    if not draft_path.exists():
        logger.warning("draft_selections.json missing -- skipping coherence coverage check")
        return
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("draft_selections.json unreadable (%s) -- skipping coherence coverage check", e)
        return
    if not isinstance(draft, dict):
        logger.warning(
            "draft_selections.json is %s, not an object -- skipping coherence coverage check",
            type(draft).__name__,
        )
        return
    # Identity-based coverage: every draft story must have >=1 matching result,
    # using merge.py's own matching (ids preferred, normalized headline
    # fallback), so validation and assembly agree on what "covered" means.
    import merge

    unmatched = []
    for tier in ("must_know", "should_know"):
        for item in draft.get(tier) or []:
            if not isinstance(item, dict):
                continue
            item_ids = merge._item_article_ids(item)
            item_norm = merge._norm_headline(item.get("headline", ""))
            if not any(merge._result_matches(r, item_ids, item_norm) for r in results):
                unmatched.append(item.get("headline"))
    if unmatched:
        raise ValueError(
            f"coherence_report.json: no result matches {len(unmatched)} draft story(ies): "
            + "; ".join(repr(h) for h in unmatched[:5])
        )


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


# The corpus travels as the USER MESSAGE, never in the system prompt. The SDK ships
# system_prompt as a single argv entry, and Linux caps one argument at MAX_ARG_STRLEN
# (128 KiB); this corpus is ~289 KB, which fails as `[Errno 7] Argument list too long`
# before the model is ever reached. The prompt is streamed over stdin and has no such cap.
_SINGLE_TURN_IO = """**Your input arrives in the next message, inline. There are no files to open and no tools available.**

**Reply with the JSON object and nothing else** -- no preamble, no code fence, no commentary.

"""


def build_coherence_corpus(claude_input_dir: Path) -> str:
    """Inline everything the multi-turn agent opens with the Read tool.

    Same set, same order as `coherence.md`'s instruction list: the draft, every articles_*.csv,
    and article_fulltext.json when it exists (best-effort in production, so its absence is normal).
    """
    parts: list[str] = []
    draft = claude_input_dir / "draft_selections.json"
    parts.append(f"## draft_selections.json\n\n{draft.read_text(encoding='utf-8')}")
    for csv_path in sorted(claude_input_dir.glob("articles_*.csv")):
        parts.append(f"## {csv_path.name}\n\n{csv_path.read_text(encoding='utf-8')}")
    fulltext = claude_input_dir / "article_fulltext.json"
    if fulltext.exists():
        parts.append(f"## article_fulltext.json\n\n{fulltext.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def build_single_turn_body(body: str) -> str:
    """Swap `coherence.md`'s tool-based I/O for an inlined corpus, leaving the probes untouched.

    Derived from the shipped prompt rather than duplicated, so the two deliveries cannot drift
    on the rules -- a test asserts the probe block survives byte for byte. Without that, a cost
    measurement quietly becomes a quality measurement.
    """
    start = body.index("**Instructions:**")
    end = body.index("**For each field, run all three probes")
    out = body[:start] + _SINGLE_TURN_IO + body[end:]
    out = out.replace(
        "3. Use the Write tool to write the result to `/app/data/claude_input/coherence_report.json`\n", ""
    )
    return out.replace("- DO NOT use Bash. Use Read and Write tools only.\n", "")


def parse_coherence_report(text: str) -> dict | None:
    """Pull the report object out of a single-turn reply, tolerant of fences and preamble.

    Same shape as `cluster_extractjoin.parse_extract_items`, which has carried the only
    non-file-handoff stage in this pipeline. None means unparseable -- the caller fails the
    stage rather than writing a partial report, because a missing entry reads downstream as
    "keep unchecked".
    """
    start, end = text.find("{"), text.rfind("}")
    if not (0 <= start < end):
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("results"), list):
        return obj
    return None


def _resolved_thinking(spec: AgentSpec) -> ThinkingConfig:
    """The thinking config actually sent for `spec`.

    Both the send site and the run_usage record site call this. Two copies of the expression
    would let the recorded config drift from the sent one, which is the blind spot the column
    was added to close.
    """
    return spec.thinking or _THINKING


async def _invoke_agent(
    spec: AgentSpec,
    *,
    model: str,
    cwd: str | Path | None,
    idle_timeout: float = _IDLE_TIMEOUT_S,
    max_budget_usd: float | None = None,
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
        thinking=_resolved_thinking(spec),
        effort=spec.effort,
        max_budget_usd=max_budget_usd,
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
    run_deadline: float | None = None,
    attempt_timeout: float | None = None,
    max_budget_usd: float | None = None,
) -> dict[str, Any]:
    """Run one subagent stage with a single retry, returning its usage row.

    ``attempt_timeout`` and ``max_budget_usd`` fall back to the whole-stage bounds; they are
    resolved here rather than as default arguments so the module constants stay the single
    source (and stay monkeypatchable). They are parameters at all because the WRITE fan-out
    runs N stages where there used to be one: left at the whole-stage values, N branches
    multiply both ceilings by N (see ``run_write_phase``).

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
    attempt_timeout = _STAGE_ATTEMPT_TIMEOUT_S if attempt_timeout is None else attempt_timeout
    max_budget_usd = _STAGE_BUDGET_USD if max_budget_usd is None else max_budget_usd
    last_err: Exception | None = None
    # One wall-clock budget shared across BOTH attempts, so the invalid-output
    # retry cannot silently double the outage-riding budget (4h, not 8h) -- and
    # never past the run's own deadline, which the stages share.
    stage_deadline = time.monotonic() + _STAGE_RETRY_BUDGET_S
    if run_deadline is not None:
        stage_deadline = min(stage_deadline, run_deadline)

    for attempt in (1, 2):
        output_path.unlink(missing_ok=True)
        try:
            logger.info("[%s started]%s", label.capitalize(), " (retry)" if attempt == 2 else "")
            result = await with_retry_async(
                lambda: asyncio.wait_for(
                    _invoke_agent(spec, model=model, cwd=cwd, max_budget_usd=max_budget_usd),
                    timeout=attempt_timeout,
                ),
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

        # Summarized reasoning is free (billed either way) but dies with the process unless
        # it is archived. Absent whenever the stage did not ask for display: summarized.
        if result.thinking:
            db.record_run_artifact(f"thinking_{label}.txt", result.thinking)

        row = usage_row_from_sdk(
            label,
            model,
            result.usage,
            result.total_cost_usd,
            duration_ms=result.duration_ms,
            thinking=_resolved_thinking(spec),
            effort=spec.effort,
        )
        duration = result.duration_ms / 1000
        # Basenames only -- every path shares the same container prefix, which is noise
        # on every line. "NOTHING" rather than a bare "read=" so the case worth catching
        # (a stage that wrote valid output without opening its input) reads as a claim.
        read = ", ".join(Path(p).name for p in result.files_read) or "NOTHING"
        logger.info("[%s complete] %.1fs $%.4f read=%s", label.capitalize(), duration, row["api_cost_usd"], read)
        return row

    # Unreachable: the loop either returns or raises on attempt 2.
    raise RuntimeError(f"{label} stage failed: {last_err}")


# --------------------------------------------------------------------------- #
# WRITE phase: one call per selected story, fanned back in by Python.
# --------------------------------------------------------------------------- #


async def _run_write_branches(
    branches: list[write_fanout.Branch],
    spec: AgentSpec,
    *,
    model_override: str | None,
    cwd: str | Path | None,
    run_deadline: float | None,
    rows: dict[str, dict[str, Any]],
) -> None:
    """Drive every branch through ``run_stage``, bounded at ``_WRITE_BRANCH_CONCURRENCY``.

    Each branch is a full stage: transient-error retry, clean-slate re-attempt and usage
    capture, against the branch's own directory. The ceilings do NOT ride along unchanged:
    N branches at the whole-stage cap would let the phase bill N x $8 and run N/4 waves x
    45 min. Each branch gets ``_WRITE_BRANCH_BUDGET_USD`` and
    ``_WRITE_BRANCH_ATTEMPT_TIMEOUT_S``, and a branch that would start after the run
    deadline does not start at all.

    The phase bound is a ceiling on STARTING branches, not on total spend: the running sum
    is read before a branch begins, so at the moment it trips, up to
    ``_WRITE_BRANCH_CONCURRENCY`` branches are already in flight, each of which may bill up
    to its own cap TWICE (run_stage retries once). The true worst case is therefore
    ``_STAGE_BUDGET_USD + concurrency * 2 * _WRITE_BRANCH_BUDGET_USD``. That is the honest
    number, and it is still an order of magnitude under N branches at the stage cap.

    A branch whose draft already validates is skipped and contributes no usage row, the
    same contract stage-level ``--resume`` has: it costs nothing this run.

    Completed rows are written into ``rows`` as they land, so a later branch failing does
    not discard spend already billed. A branch that fails after its retry aborts the phase
    (cancelling its siblings) rather than letting the fan-in ship a digest one story short.
    """
    sem = asyncio.Semaphore(_WRITE_BRANCH_CONCURRENCY)

    async def _one(branch: write_fanout.Branch) -> None:
        if _branch_is_done(branch):
            logger.info("[Write %s] resuming: valid draft present, skipping", branch.name)
            return
        async with sem:
            # Checked here rather than only after a raise: with_retry_async consults the
            # deadline when fn() fails, so a queued wave would otherwise start a fresh
            # 15-minute attempt well past the run's budget.
            if run_deadline is not None and time.monotonic() >= run_deadline:
                raise RuntimeError(f"write_{branch.name}: run deadline passed before the branch started")
            spent = sum(r["api_cost_usd"] for r in rows.values())
            if spent >= _STAGE_BUDGET_USD:
                raise RuntimeError(
                    f"write_{branch.name}: branches have billed ${spent:.2f}, at the ${_STAGE_BUDGET_USD:.2f} "
                    "phase cap -- stopping"
                )
            row = await run_stage(
                replace(spec, body=write_fanout.branch_body(spec.body, branch.dir)),
                label=f"write_{branch.name}",
                output_path=branch.dir / write_fanout.BRANCH_DRAFT_NAME,
                validate=write_fanout.validate_branch_draft,
                model_override=model_override,
                cwd=cwd,
                claude_input_dir=branch.dir,
                run_deadline=run_deadline,
                attempt_timeout=_WRITE_BRANCH_ATTEMPT_TIMEOUT_S,
                max_budget_usd=_WRITE_BRANCH_BUDGET_USD,
            )
            rows[branch.name] = row
            healthcheck.log(f"write {branch.name} done {row.get('duration_ms', 0) // 1000}s ${row['api_cost_usd']:.4f}")

    tasks: dict[str, asyncio.Task] = {}
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = {b.name: tg.create_task(_one(b)) for b in branches}
    except ExceptionGroup as eg:
        failed = sorted(n for n, t in tasks.items() if t.done() and not t.cancelled() and t.exception() is not None)
        raise RuntimeError(f"write stage failed on branch(es) {', '.join(failed)}: {eg.exceptions[0]}") from eg


def _branch_is_done(branch: write_fanout.Branch) -> bool:
    """True if this branch already carries a valid draft (see write_fanout._reusable_draft,
    which only preserves one written for this exact story)."""
    try:
        write_fanout.validate_branch_draft(branch.dir)
    except ValueError:
        return False
    return True


def _aggregate_write_row(
    rows: list[dict[str, Any]], spec: AgentSpec, *, model: str, duration_ms: int
) -> dict[str, Any]:
    """Collapse the branches into the single ``write`` row run_usage expects.

    Duration is the phase's wall clock, not the sum of concurrent branches -- the same
    convention ``cluster_extractjoin`` uses for its batched extraction.
    """
    return usage_row_from_sdk(
        "write",
        model,
        {
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "cache_creation_input_tokens": sum(r["cache_write_tokens"] for r in rows),
            "cache_read_input_tokens": sum(r["cache_read_tokens"] for r in rows),
        },
        sum(r["api_cost_usd"] for r in rows),
        duration_ms=duration_ms,
        thinking=_resolved_thinking(spec),
        effort=spec.effort,
    )


def _archive_write_branches(
    claude_input_dir: Path,
    fanout: write_fanout.FanOut,
    branches: list[write_fanout.Branch],
    rows: list[dict[str, Any]],
) -> None:
    """Archive the per-branch cost breakdown the aggregated row erases, so cost per
    story stays queryable after the fact -- and the counts that say whether the row is a
    whole phase or a partial one, which the aggregate alone cannot distinguish."""
    payload = {
        "branches_total": len(fanout.branches),
        "branches_completed": len(branches),
        "dropped": [
            {
                "branch": d.name,
                "tier": d.tier,
                "cluster_index": d.cluster_index,
                "story_article_ids": list(d.story_article_ids),
                "reason": d.reason,
            }
            for d in fanout.dropped
        ],
        "branches": [
            {
                "branch": b.name,
                "tier": b.tier,
                "cluster_index": b.cluster_index,
                "story_article_ids": list(b.story_article_ids),
                # The IDS, not a count: archive_run_artifacts globs articles_*.csv
                # non-recursively, so the branch CSVs are never archived and this is the
                # only record of what a branch was actually allowed to read.
                "context_article_ids": list(b.context_article_ids),
                **{
                    k: row[k]
                    for k in (
                        "model",
                        "input_tokens",
                        "output_tokens",
                        "cache_write_tokens",
                        "cache_read_tokens",
                        "api_cost_usd",
                        "duration_ms",
                    )
                },
            }
            for b, row in zip(branches, rows, strict=True)
        ],
    }
    body = json.dumps(payload, indent=2)
    # BOTH sinks, because neither covers both paths. record_run_artifact writes immediately
    # but is a no-op until db.start_run() has run, and on --resume start_run happens AFTER
    # curation (run.py: _render_record_deliver) -- so on the one path where a degraded input
    # state is most likely, the row would never exist and STORIES_DROPPED_AT_WRITE would be
    # dark. The file rides archive_run_artifacts' sweep instead, which runs with recording
    # on. INSERT OR REPLACE makes the overlap on the full path harmless.
    try:
        (claude_input_dir / _WRITE_BRANCHES_NAME).write_text(body, encoding="utf-8")
    except OSError as e:
        logger.warning("could not write %s (non-fatal): %s", _WRITE_BRANCHES_NAME, e)
    db.record_run_artifact(_WRITE_BRANCHES_NAME, body)


async def run_write_phase(
    *,
    claude_input_dir: Path,
    model_override: str | None,
    cwd: str | Path | None,
    run_deadline: float | None,
    on_usage: Callable[[dict[str, Any]], None],
) -> None:
    """Run the WRITE stage, handing each usage row to ``on_usage`` as it is produced.

    WRITE runs once per selected story against only that story's cluster, and Python
    assembles the branches back into ``draft_selections.json`` in SELECT's order; the
    preheader -- the one genuinely cross-story field -- is then written by its own stage
    from the assembled headlines.

    Rows go out through the callback rather than a return value because the phase can fail
    after paying for some of its branches, and spend already billed has to reach
    ``run_usage`` regardless (the invariant ``orchestrate_selections._record`` exists for).
    """
    spec = parse_agent_spec(_AGENTS_DIR / "write.md")
    draft_path = claude_input_dir / "draft_selections.json"

    stage_start = time.monotonic()
    model = model_override or spec.model
    branch_rows: dict[str, dict[str, Any]] = {}
    fanout = write_fanout.FanOut(branches=[])
    # Fail-closed: a phase that raises must leave no draft behind for a resumed run to
    # mistake for this run's output.
    draft_path.unlink(missing_ok=True)
    try:
        fanout = write_fanout.build_branches(claude_input_dir)
        for entry in fanout.dropped:
            healthcheck.log(f"write {entry.name} DROPPED ({entry.tier}): {entry.reason}")
        logger.info(
            "[Write started] %d stories, %d dropped, concurrency %d",
            len(fanout.branches),
            len(fanout.dropped),
            _WRITE_BRANCH_CONCURRENCY,
        )
        await _run_write_branches(
            fanout.branches,
            spec,
            model_override=model_override,
            cwd=cwd,
            run_deadline=run_deadline,
            rows=branch_rows,
        )
        draft = write_fanout.assemble_draft(fanout.branches)
    except BaseException:
        draft_path.unlink(missing_ok=True)
        _emit_write_row(
            claude_input_dir, branch_rows, fanout, spec, model=model, stage_start=stage_start, on_usage=on_usage
        )
        raise

    _emit_write_row(
        claude_input_dir, branch_rows, fanout, spec, model=model, stage_start=stage_start, on_usage=on_usage
    )

    # The draft has to be on disk before the preheader agent runs -- it reads it. Nothing
    # about the preheader may abort a delivered digest (run 229), so a stage that fails
    # after its retry leaves the field blank and merge fills it from the top headline.
    draft_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    try:
        on_usage(
            await run_stage(
                parse_agent_spec(_AGENTS_DIR / "preheader.md"),
                label="preheader",
                output_path=claude_input_dir / _PREHEADER_NAME,
                validate=validate_preheader,
                model_override=model_override,
                cwd=cwd,
                claude_input_dir=claude_input_dir,
                run_deadline=run_deadline,
                attempt_timeout=_PREHEADER_ATTEMPT_TIMEOUT_S,
                max_budget_usd=_PREHEADER_BUDGET_USD,
            )
        )
        draft["preheader"] = read_preheader(claude_input_dir)
    except Exception as e:
        # Broad by design, and NOT (RuntimeError, ValueError): run_stage bounds an attempt
        # with asyncio.wait_for, whose expiry is a bare TimeoutError that neither
        # with_retry_async nor run_stage's own handler catches (TestStageAttemptIsBounded
        # pins that it escapes). A hung preheader call would then abort the whole curation
        # run -- the exact outcome "nothing about the preheader may abort a digest" forbids.
        # asyncio.CancelledError is a BaseException, so real cancellation still propagates.
        logger.warning(
            "preheader stage failed (%s: %s) -- merge will fill it from the first headline",
            type(e).__name__,
            e,
        )
        draft["preheader"] = ""

    draft_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    validate_draft(claude_input_dir)

    for a, b, score in write_fanout.repetition_warnings(draft):
        logger.warning("write: two headlines are near-duplicate wording (%.2f): %r / %r", score, a, b)
        # Off-box too, alongside the drop line: run 235 shipped two identical cards, and a
        # log line in a file that rotates within days reaches nobody.
        healthcheck.log(f"write: near-duplicate headlines ({score:.2f}): {a[:60]!r} / {b[:60]!r}")


def _emit_write_row(
    claude_input_dir: Path,
    branch_rows: dict[str, dict[str, Any]],
    fanout: write_fanout.FanOut,
    spec: AgentSpec,
    *,
    model: str,
    stage_start: float,
    on_usage: Callable[[dict[str, Any]], None],
) -> None:
    """Aggregate whatever the branches billed into the one ``write`` row, and archive the
    per-branch breakdown. Called on the success path AND on the failure path, where the
    branches that completed have already been paid for.

    A branch skipped by the resume path billed nothing this run and so has no row; the
    artifact's ``branches_completed`` vs ``branches_total`` is what says whether the write
    row covers the whole phase. The artifact is archived even when NO branch billed -- a
    fully-resumed phase still dropped whatever it dropped, and STORIES_DROPPED_AT_WRITE
    reads that list."""
    if not fanout.branches and not fanout.dropped:
        return  # build_branches itself failed; there is nothing true to say about branches
    done = [b for b in fanout.branches if b.name in branch_rows]
    rows = [branch_rows[b.name] for b in done]
    _archive_write_branches(claude_input_dir, fanout, done, rows)
    if not rows:
        logger.info("[Write complete] 0/%d branches billed (all resumed)", len(fanout.branches))
        return
    row = _aggregate_write_row(rows, spec, model=model, duration_ms=int((time.monotonic() - stage_start) * 1000))
    logger.info(
        "[Write complete] %d/%d branches billed %.1fs $%.4f",
        len(done),
        len(fanout.branches),
        row["duration_ms"] / 1000,
        row["api_cost_usd"],
    )
    on_usage(row)


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


async def _run_fulltext_best_effort(claude_input_dir: Path) -> None:
    """Best-effort full-text fetch for the SELECTED stories, between SELECT and WRITE.

    Network-dependent and strictly additive: WRITE/COHERENCE already work off the CSV summaries
    alone (the floor), so nothing here may touch the run's success. ``fulltext.fetch_for_selected``
    already catches everything internally and returns None on any failure; the broad catch here is
    a second, redundant layer (mirrors ``merge.py``'s best-effort instrumentation) so even a bug
    inside fulltext itself -- not just an expected fetch failure -- can never reach this orchestrator.
    Run via ``asyncio.to_thread`` since it blocks on a child process for up to
    ``config.FULLTEXT_DEADLINE_S`` + ``config.FULLTEXT_KILL_GRACE_S``.
    """
    try:
        path = await asyncio.to_thread(fulltext.fetch_for_selected, claude_input_dir)
    except Exception as e:  # broad by design: this step must never abort the run
        logger.warning(
            "fulltext: unexpected error (non-fatal, falling back to CSV summaries): %s: %s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        return
    if path is None:
        logger.info("fulltext: no full text extracted (WRITE/COHERENCE fall back to CSV summaries)")


# --------------------------------------------------------------------------- #
# Repair-not-drop phase (conditional, between COHERENCE and merge).
# --------------------------------------------------------------------------- #

_RECHECK_DRAFT_NAME = "recheck_draft.json"
_RECHECK_REPORT_NAME = "recheck_report.json"
_REPAIR_LOG_NAME = repair.REPAIR_LOG_NAME
_REPAIR_HEALTH_NAME = "repair_health.json"


class RepairSpecError(RuntimeError):
    """The repair path is unusable because its PROMPTS are wrong -- coherence.md missing,
    or drifted off the filenames the scoped re-check re-points. Distinct from every other
    repair failure: it is deterministic, it recurs every run until a human edits a file,
    and it silently disables repair entirely."""


def _require_results_array(path: Path) -> None:
    """Loose wire-shape gate shared by the repair validators: confirm the file is
    a JSON object carrying a ``results`` array. The real per-entry guards live
    downstream (repair.apply_repairs / build_repair_resolution), so this only has
    to guarantee there is an array to score against."""
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError(f"{path.name}: 'results' array missing")


def validate_repaired_fields(claude_input_dir: Path) -> None:
    """Loose wire-shape gate on repaired_fields.json; repair.apply_repairs owns
    the real per-field guards (missing/empty/id-leak/wrong-field)."""
    _require_results_array(claude_input_dir / "repaired_fields.json")


def validate_recheck_report(claude_input_dir: Path) -> None:
    """Loose wire-shape gate on the scoped re-check report; build_repair_resolution
    treats any malformed/non-passing entry as a fail (drop), so this only needs to
    confirm a results array exists to score against."""
    _require_results_array(claude_input_dir / _RECHECK_REPORT_NAME)


def _build_recheck_draft(draft: dict, ok_entries: list[dict]) -> dict:
    """A draft_selections-shaped doc holding ONLY the successfully-patched stories,
    with the repaired field(s) applied, for the scoped re-check.

    coherence.md checks must_know + should_know against each story's own cited
    sources; tier is irrelevant to the check, so every patched story goes under
    must_know. The original story is matched by its article_ids (drift-proof), so
    the re-check sees the full story object (sources included) with the patch on."""
    by_ids: dict[frozenset[str], dict] = {}
    for tier in ("must_know", "should_know"):
        for item in draft.get(tier) or []:
            if not isinstance(item, dict):
                continue
            # merge._item_article_ids is the single source of truth for a draft
            # item's article_ids identity -- reused here so the recheck-draft match
            # can never drift from merge's own matching guards.
            ids = _item_article_ids(item)
            if ids:
                by_ids[ids] = item
    patched = []
    for entry in ok_entries:
        item = by_ids.get(frozenset(entry.get("article_ids", [])))
        if item is None:
            continue
        story = dict(item)
        story.update(entry.get("patched_fields", {}))
        patched.append(story)
    return {"must_know": patched, "should_know": [], "preheader": "recheck"}


def _recheck_spec(coherence_spec: AgentSpec) -> AgentSpec:
    """coherence.md re-pointed at the recheck files -- reads recheck_draft.json and
    writes recheck_report.json instead of the run's real draft/report -- so the
    scoped re-check reuses the LIVE checker prompt verbatim (the eval_coherence.py
    trick). Asserts the markers were present so a prompt whose filenames drifted
    fails loudly here rather than silently re-checking against the wrong file."""
    body = coherence_spec.body
    for marker in ("draft_selections.json", "coherence_report.json"):
        if marker not in body:
            raise RepairSpecError(
                f"coherence.md: expected {marker!r} to re-point for the repair re-check; prompt drifted"
            )
    body = body.replace("draft_selections.json", _RECHECK_DRAFT_NAME).replace(
        "coherence_report.json", _RECHECK_REPORT_NAME
    )
    return replace(coherence_spec, body=body)


def _load_repair_spec(filename: str) -> AgentSpec:
    """Parse one of the repair path's prompts, translating ANY failure into RepairSpecError.

    Broad on purpose: the whole point of the class is that a prompt this path cannot read is
    never confused with a repair that found nothing, and a parse failure this code did not
    anticipate is exactly the case that would otherwise go back to being silent."""
    try:
        return parse_agent_spec(_AGENTS_DIR / filename)
    except Exception as e:
        raise RepairSpecError(f"{filename} could not be parsed ({type(e).__name__}: {e})") from e


def _clear_repair_health(claude_input_dir: Path) -> None:
    """Drop a previous attempt's fault file, before the phase can decide it has nothing to
    do: same-day --resume reuses claude_input, so a fault left by an earlier attempt would
    otherwise be archived again and alert on a run that hit no fault at all.

    Never raises: a filesystem mutation added for observability must not be the thing that
    kills a delivery."""
    try:
        (claude_input_dir / _REPAIR_HEALTH_NAME).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("repair: could not clear %s (non-fatal): %s", _REPAIR_HEALTH_NAME, e)


def _write_repair_health(claude_input_dir: Path, *, outcome: str, detail: str) -> None:
    """Record a repair-phase fault where the post-run invariants can see it.

    The alert path judges a finished run off run_artifacts (see db._TRACE_ARTIFACTS), and
    this file is archived with the rest -- on the resume path too, where no run row exists
    yet when the phase runs. A log line alone reaches nobody."""
    try:
        (claude_input_dir / _REPAIR_HEALTH_NAME).write_text(
            json.dumps({"outcome": outcome, "detail": detail}, sort_keys=True), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError) as e:
        logger.warning("repair: could not write %s (non-fatal): %s", _REPAIR_HEALTH_NAME, e)


def _log_repair_events(claude_input_dir: Path, requests: dict, applied: dict, resolution: dict) -> None:
    """Append one repair_log.jsonl event per attempted story and log a run-level
    counter -- so a guard-fail or recheck-fail is VISIBLE in run logs instead of
    looking like a generic coherence drop (the accruing log is also the eval's
    ground-truth corpus). Written to the data dir (claude_input's parent), which
    persists across runs, not the per-run claude_input that prepare.py wipes."""
    req_by_ids = {frozenset(r.get("article_ids", [])): r for r in requests.get("requests", [])}
    applied_by_ids = {frozenset(e.get("article_ids", [])): e for e in applied.get("applied", [])}
    log_path = claude_input_dir.parent / _REPAIR_LOG_NAME
    counts = {"repaired": 0, "recheck_failed": 0, "guard_failed": 0}
    for res in resolution.get("results", []):
        ids = frozenset(res.get("article_ids", []))
        req = req_by_ids.get(ids, {})
        ap = applied_by_ids.get(ids, {})
        status = res.get("status", "guard_failed")
        counts[status] = counts.get(status, 0) + 1
        failed_fields = req.get("failed_fields") or []
        try:
            repair.append_repair_log(
                log_path,
                {
                    # The log spans every run, so each event has to say which one it came
                    # from -- otherwise the only way to date an entry is the rotated
                    # digest.log, which ages out within days.
                    "run_id": db.current_run_id(),
                    # Which process wrote it. On --resume the run id does not exist yet, and
                    # this is what lets run.py claim ITS OWN events afterwards by identity.
                    "proc": repair.PROCESS_TOKEN,
                    "ts": datetime.datetime.now(datetime.UTC).isoformat(),
                    "article_ids": sorted(ids),
                    "failed_fields": failed_fields,
                    "reason": req.get("reason"),
                    "original_fields": {f: req.get("fields", {}).get(f) for f in failed_fields},
                    "repaired_fields": ap.get("patched_fields") or None,
                    "action": ap.get("action"),
                    "guard": ap.get("guard"),
                    "status": status,
                    "recheck_pass": res.get("recheck_pass"),
                },
            )
        except OSError as e:
            logger.warning("repair: could not append to %s (%s)", log_path, e)
    logger.info(
        "repair: %d attempted, %d kept, %d recheck-failed, %d guard-failed",
        len(requests.get("requests", [])),
        counts.get("repaired", 0),
        counts.get("recheck_failed", 0),
        counts.get("guard_failed", 0),
    )


async def _run_repair_phase(
    claude_input_dir: Path, *, model_override: str | None, cwd: str | Path | None
) -> list[dict[str, Any]]:
    """Regenerate COHERENCE-flagged repairable fields, re-check, and write
    repair_resolution.json for merge to consume. Returns the phase's usage rows.

    Sequence: build requests from the draft + coherence report; if none, no-op.
    Otherwise run repair.md -> apply guards -> for the stories that passed the
    guards, build a scoped recheck draft and re-run the LIVE coherence.md over
    ONLY those -> assemble the resolution. The re-check is what makes repair safe
    (a story is kept only if the independent checker passes the patched text), so
    a re-check that fails or errors leaves the resolution empty of ``repaired``
    verdicts and the story drops. repair_resolution.json is CLEARED at entry and
    written only LAST, so a skipped OR failed phase leaves no resolution and merge
    drops exactly as today.
    """
    import json

    # Fail-closed invariant: a phase that skips or fails must leave NO resolution,
    # so merge drops. Clear any stale one up front -- same-day `--resume` reuses
    # claude_input (prepare.py wipes it only on a FULL run), so a prior run's
    # `repaired` verdict would otherwise survive a phase that fails THIS run and
    # let merge keep a story this run never confirmed.
    (claude_input_dir / "repair_resolution.json").unlink(missing_ok=True)
    _clear_repair_health(claude_input_dir)

    # Both prompts, before anything is spent and before the no-op early return: a prompt that
    # cannot drive the re-check makes every repair unconfirmable, so paying the repairer first
    # buys a patch nothing can validate. It is also the only way either fault surfaces on a
    # run with no repairable failures.
    repair_spec = _load_repair_spec("repair.md")
    recheck_spec = _recheck_spec(_load_repair_spec("coherence.md"))

    draft = _load_json(claude_input_dir / "draft_selections.json")
    coherence = _load_json(claude_input_dir / "coherence_report.json")
    requests = repair.build_repair_requests(draft, coherence)
    if not requests["requests"]:
        logger.info("repair: no repairable coherence failures this run")
        return []
    logger.info("repair: %d story(ies) with a repairable coherence failure", len(requests["requests"]))
    (claude_input_dir / "repair_requests.json").write_text(json.dumps(requests, indent=2))

    rows: list[dict[str, Any]] = []
    rows.append(
        await run_stage(
            repair_spec,
            label="repair",
            output_path=claude_input_dir / "repaired_fields.json",
            validate=validate_repaired_fields,
            model_override=model_override,
            cwd=cwd,
            claude_input_dir=claude_input_dir,
        )
    )
    repaired = _load_json(claude_input_dir / "repaired_fields.json")
    applied = repair.apply_repairs(requests, repaired)
    ok_entries = [e for e in applied["applied"] if e.get("ok")]

    recheck: dict[str, Any] = {"results": []}
    if ok_entries:
        (claude_input_dir / _RECHECK_DRAFT_NAME).write_text(
            json.dumps(_build_recheck_draft(draft, ok_entries), indent=2)
        )
        try:
            rows.append(
                await run_stage(
                    recheck_spec,
                    label="repair_recheck",
                    output_path=claude_input_dir / _RECHECK_REPORT_NAME,
                    validate=validate_recheck_report,
                    model_override=model_override,
                    cwd=cwd,
                    claude_input_dir=claude_input_dir,
                )
            )
            recheck = json.loads((claude_input_dir / _RECHECK_REPORT_NAME).read_text(encoding="utf-8"))
        except (RuntimeError, ValueError) as e:
            # Fail-closed: a re-check we cannot trust confirms NOTHING, so leave
            # recheck empty -> every patched story resolves recheck_failed -> drop.
            logger.warning("repair: re-check failed (%s) -- all repairs drop (fail-closed)", e)

    resolution = repair.build_repair_resolution(applied, recheck)
    _log_repair_events(claude_input_dir, requests, applied, resolution)
    (claude_input_dir / "repair_resolution.json").write_text(json.dumps(resolution, indent=2))
    return rows


async def _run_repair_phase_best_effort(
    claude_input_dir: Path, *, model_override: str | None, cwd: str | Path | None
) -> list[dict[str, Any]]:
    """Wrap the repair phase so NOTHING it does can abort the run. Any failure
    (repair agent, re-check, I/O) leaves no repair_resolution.json (or an all-fail
    one), so merge.assemble_selections drops the flagged stories exactly as before
    repair existed. Mirrors _run_fulltext_best_effort's additive stance."""
    try:
        return await _run_repair_phase(claude_input_dir, model_override=model_override, cwd=cwd)
    except RepairSpecError as e:
        # NOT the same as a repair that found nothing, and not the same as a model call that
        # failed: this disables the repair path outright, on every run, until a file is
        # edited. ERROR plus a fault artifact the run-health alert reads.
        logger.error("repair: DISABLED by a prompt/config error -- no repair can run: %s", e, exc_info=True)
        _write_repair_health(claude_input_dir, outcome="spec_error", detail=str(e))
        return []
    except Exception as e:  # broad by design: repair is additive and must never break the run
        # Emit the same "kept" token the success-path counter uses, so run-log
        # monitoring sees a repair-stage regression as "0 kept" rather than only a
        # generic phase error (a repairer/coherence prompt that fails every run).
        logger.warning(
            "repair: phase did not complete -- 0 kept (flagged stories drop as today): %s: %s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        return []


async def orchestrate_selections(
    *,
    claude_input_dir: Path,
    model_override: str | None = None,
    cwd: str | Path | None = None,
    resume: bool = False,
    on_usage: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run the five curation stages in order; return their usage rows.

    ``on_usage`` is called with each row as its stage completes, so spend already
    billed survives a later stage raising.

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
    run_deadline = time.monotonic() + _RUN_RETRY_BUDGET_S

    healthcheck.log(f"curation start: {len(_STAGES)} stages")

    def _record(row: dict[str, Any]) -> None:
        # Emitted as each stage completes, not returned in a batch at the end: a later stage
        # raising used to discard every earlier stage's row, so a run that failed at WRITE
        # recorded none of the cluster/recap/select spend it had already been billed for.
        usage_rows.append(row)
        # Off-box progress marker. The only signal that makes a run observable WHILE it runs:
        # run_health judges finished runs only, the deadman fires 2h35m after start, and
        # OnFailure waits for the 5h TimeoutStartSec. Run 281 hung 62 minutes unseen.
        healthcheck.log(f"{row['subagent']} done {row.get('duration_ms', 0) // 1000}s ${row['api_cost_usd']:.4f}")
        if on_usage is not None:
            on_usage(row)

    for label, spec_filename, output_filename, validate in _STAGES:
        if resume and _stage_output_is_valid(claude_input_dir, output_filename, validate):
            logger.info("[%s] resuming: valid output present, skipping", label.capitalize())
        # CLUSTER is the deterministic extract→join path (replaces the holistic LLM agent); it
        # writes clusters.json and validates identically, so the rest of the pipeline is
        # untouched. See cluster_extractjoin.py + the gate doc. Rollback = revert the image.
        elif label == "cluster":
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
            _record(row)
        # WRITE is one call per story against only that story's cluster, fanned back in
        # by Python.
        elif label == "write":
            await run_write_phase(
                claude_input_dir=claude_input_dir,
                model_override=model_override,
                cwd=cwd,
                run_deadline=run_deadline,
                on_usage=_record,
            )
        else:
            spec = parse_agent_spec(_AGENTS_DIR / spec_filename)
            row = await run_stage(
                spec,
                label=label,
                output_path=claude_input_dir / output_filename,
                validate=validate,
                model_override=model_override,
                cwd=cwd,
                claude_input_dir=claude_input_dir,
                run_deadline=run_deadline,
            )
            _record(row)
        # Fetch full text for the SELECTED stories before WRITE runs (whether SELECT just ran or
        # was resumed from a valid prior output), so WRITE/COHERENCE see real article text instead
        # of the ~300-char RSS blurb. Best-effort: guarded by the kill switch here (skip entirely,
        # no thread pool spun up) and internally by fulltext itself.
        if label == "select" and config.FULLTEXT_ENABLED:
            await _run_fulltext_best_effort(claude_input_dir)
        # Kick off Google-News link resolution in the background here (URLs are only needed at the
        # final HTML injection, not by WRITE/COHERENCE), so its ~70-100s overlaps those stages
        # instead of blocking render. Fire-and-forget; render joins the cache.
        if label == "select" and config.GNEWS_RESOLVE_ENABLED:
            gnews.prefetch_selected(
                claude_input_dir,
                timeout=config.GNEWS_RESOLVE_TIMEOUT_S,
                delay=config.GNEWS_RESOLVE_DELAY_S,
                deadline=config.GNEWS_RESOLVE_DEADLINE_S,
            )

    # Repair-not-drop: between COHERENCE and merge, regenerate a flagged repairable
    # field and re-check it rather than let merge drop the whole story. Best-effort so
    # it can never abort the run (merge falls back to dropping the story). Runs whenever
    # the draft/report are on disk, including resume-only runs where COHERENCE was reused.
    for row in await _run_repair_phase_best_effort(claude_input_dir, model_override=model_override, cwd=cwd):
        _record(row)

    total = sum(r["api_cost_usd"] for r in usage_rows)
    logger.info("Selection complete: %d stages, $%.4f API-equivalent", len(usage_rows), total)
    return usage_rows
