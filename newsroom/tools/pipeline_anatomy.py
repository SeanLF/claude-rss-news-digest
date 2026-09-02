"""Generate the "Digest Pipeline Anatomy" page and the README flow diagram.

Both outputs are derived, never authored:

  shape    orchestrate._STAGES, run_write_phase's per-story fan-out + preheader
           stage, the repair phase, cluster_extractjoin's extraction batching
  models   .claude/agents/*.md frontmatter via orchestrate.parse_agent_spec, plus
           config.CLUSTER_EXTRACT_MODEL where the model comes from config
  captions each agent's own frontmatter `description`
  figures  source_health / run_usage / run_artifacts / digest_runs for one run

    python3 newsroom/tools/pipeline_anatomy.py --html docs/pipeline-anatomy.html
    python3 newsroom/tools/pipeline_anatomy.py --readme README.md --svg-dir docs
    python3 newsroom/tools/pipeline_anatomy.py --mermaid
    make anatomy [RUN=284] [DB=path]

The data-flow drawing is laid out by the box/arrow helpers below rather than pasted
as a string with numbers substituted, so a stage added to `_STAGES` is drawn
without touching the drawing code. Box widths are computed from label length, and
newsroom/tests/test_pipeline_anatomy.py fails if any label outgrows its box.

One layout, three renderings, chosen by `Palette`: the page embeds it against CSS
tokens, and `--readme` writes the same drawing twice as self-contained files with
literal colours, which is what GitHub's image proxy can render. The `--mermaid`
block covers the same stages for anywhere a picture will not do; it is not what
goes in the README.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import itertools
import json
import math
import os
import re
import sqlite3
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "newsroom" / "src"))

import cluster_extractjoin  # noqa: E402
import config  # noqa: E402
import eval_graders  # noqa: E402
import orchestrate  # noqa: E402

AGENTS_DIR = REPO / ".claude" / "agents"
SOURCES_FILE = REPO / "newsroom" / "sources.json"
DEFAULT_DB = REPO / "data" / "digest.db"

README_BEGIN = "<!-- pipeline-anatomy:begin -->"
README_END = "<!-- pipeline-anatomy:end -->"

INTAKE_LANE = "Python · intake"
CURATION_LANE = "Claude · curation"
ASSEMBLY_LANE = "Python · assembly"


# --------------------------------------------------------------------------- #
# Run figures, read from the database.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UsageRow:
    subagent: str
    model: str
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    duration_ms: int
    thinking: str | None
    calls: int


@dataclass(frozen=True)
class RunFigures:
    run_id: int
    run_at: str
    completed_at: str | None
    git_sha: str | None
    window_hours: float | None
    active_sources: int
    feeds_attempted: int
    feeds_ok: int
    articles_fetched: int
    articles_kept: int
    articles_indexed: int | None
    clusters: int | None
    tier_counts: dict[str, int]
    stories_shipped: int | None
    mean_sources: float | None
    coherence_checked: int | None
    coherence_flagged: int | None
    write_branches: int | None
    write_branches_source: str | None
    grader_checks: int | None
    recipients: int | None
    usage: tuple[UsageRow, ...]

    @property
    def selected_stories(self) -> int | None:
        return sum(self.tier_counts.values()) or None

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.usage)

    @property
    def wall_clock_s(self) -> float | None:
        if not self.completed_at:
            return None
        started = datetime.fromisoformat(self.run_at)
        ended = datetime.fromisoformat(self.completed_at)
        return (ended - started).total_seconds()


# run_usage writes one row per thread call; the page reports the phase, not each call.
_THREAD_PREFIX = "thread_"
_THREAD_LABEL = "threads"


@contextlib.contextmanager
def _connect_ro(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Read-only, and CLOSED on the way out.

    sqlite3's own context manager ends the transaction but leaves the handle open,
    which leaks a file descriptor per call; this tool is also pointed at the live
    production database, so `mode=ro` is what guarantees it cannot write to it.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _active_source_count() -> int:
    sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    return sum(1 for s in sources if s.get("active", True))


def _latest_completed_run(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM digest_runs WHERE completed_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("no completed run in the database")
    return int(row["id"])


def _window_hours(conn: sqlite3.Connection, run_id: int, run_at: str) -> float | None:
    """The recency window the run actually used.

    feeds.fetch_feeds filters on ``db.get_last_run_time()`` -- the previous COMPLETED
    run's timestamp -- so the window is a gap between runs, not a constant.
    """
    row = conn.execute(
        "SELECT MAX(run_at) AS prev FROM digest_runs WHERE completed_at IS NOT NULL AND id < ?",
        (run_id,),
    ).fetchone()
    if row is None or not row["prev"]:
        return None
    delta = datetime.fromisoformat(run_at) - datetime.fromisoformat(row["prev"])
    return delta.total_seconds() / 3600


def _usage_rows(conn: sqlite3.Connection, run_id: int) -> tuple[UsageRow, ...]:
    merged: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT subagent, model, output_tokens, cache_read_tokens, api_cost_usd, duration_ms, thinking "
        "FROM run_usage WHERE run_id = ? ORDER BY id",
        (run_id,),
    ):
        label = _THREAD_LABEL if row["subagent"].startswith(_THREAD_PREFIX) else row["subagent"]
        acc = merged.setdefault(
            label,
            {"model": row["model"], "out": 0, "read": 0, "cost": 0.0, "ms": 0, "thinking": row["thinking"], "calls": 0},
        )
        acc["out"] += row["output_tokens"] or 0
        acc["read"] += row["cache_read_tokens"] or 0
        acc["cost"] += row["api_cost_usd"] or 0.0
        acc["ms"] += row["duration_ms"] or 0
        acc["calls"] += 1
    return tuple(
        UsageRow(
            subagent=label,
            model=acc["model"],
            output_tokens=acc["out"],
            cache_read_tokens=acc["read"],
            cost_usd=acc["cost"],
            duration_ms=acc["ms"],
            thinking=acc["thinking"],
            calls=acc["calls"],
        )
        for label, acc in merged.items()
    )


def _artifacts(conn: sqlite3.Connection, run_id: int) -> dict[str, str]:
    return {
        row["artifact_name"]: row["content"]
        for row in conn.execute("SELECT artifact_name, content FROM run_artifacts WHERE run_id = ?", (run_id,))
    }


def _json_artifact(artifacts: dict[str, str], name: str) -> Any:
    raw = artifacts.get(name)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def load_run(db_path: Path, run_id: int | None = None) -> RunFigures:
    """Every figure the page shows, for one run, read read-only."""
    with _connect_ro(db_path) as conn:
        rid = run_id if run_id is not None else _latest_completed_run(conn)
        run = conn.execute(
            "SELECT id, run_at, completed_at, git_sha, articles_kept, articles_emailed FROM digest_runs WHERE id = ?",
            (rid,),
        ).fetchone()
        if run is None:
            raise SystemExit(f"run {rid} not found in {db_path}")

        health = conn.execute(
            "SELECT COUNT(*) AS n, SUM(success) AS ok, SUM(articles_fetched) AS fetched, "
            "SUM(articles_kept) AS kept FROM source_health WHERE run_id = ?",
            (rid,),
        ).fetchone()
        artifacts = _artifacts(conn, rid)
        usage = _usage_rows(conn, rid)
        window = _window_hours(conn, rid, run["run_at"])

    index = _json_artifact(artifacts, "article_index.json")
    clusters = _json_artifact(artifacts, "clusters.json")
    selected = _json_artifact(artifacts, "selected.json")
    selections = _json_artifact(artifacts, "selections.json")
    coherence = _json_artifact(artifacts, "coherence_report.json")
    branches = _json_artifact(artifacts, "write_branches.json")

    tier_counts: dict[str, int] = {}
    if isinstance(selected, dict):
        tier_counts = {tier: len(selected.get(tier) or []) for tier in ("must_know", "should_know")}

    stories, mean_sources, grader_checks = None, None, None
    if isinstance(selections, dict):
        items = [s for tier in ("must_know", "should_know") for s in (selections.get(tier) or [])]
        stories = len(items) or None
        counts = [len(s.get("sources") or []) for s in items]
        mean_sources = (sum(counts) / len(counts)) if counts else None
        grader_checks = _grader_check_count(selections)

    checked = flagged = None
    if isinstance(coherence, dict) and isinstance(coherence.get("results"), list):
        checked = len(coherence["results"])
        flagged = sum(1 for r in coherence["results"] if not r.get("pass"))

    branch_count, branch_source = None, None
    if isinstance(branches, list):
        branch_count, branch_source = len(branches), "write_branches.json"
    elif tier_counts:
        # WRITE runs once per SELECTED story (run_write_phase), so the branch count the
        # deployed code would produce for this run is its selected-story count. Runs that
        # predate the fan-out have no write_branches.json; say which source was used.
        branch_count, branch_source = sum(tier_counts.values()), "selected.json"

    return RunFigures(
        run_id=int(run["id"]),
        run_at=run["run_at"],
        completed_at=run["completed_at"],
        git_sha=run["git_sha"],
        window_hours=window,
        active_sources=_active_source_count(),
        feeds_attempted=int(health["n"] or 0),
        feeds_ok=int(health["ok"] or 0),
        articles_fetched=int(health["fetched"] or 0),
        articles_kept=int(health["kept"] or run["articles_kept"] or 0),
        articles_indexed=len(index) if isinstance(index, dict) else None,
        clusters=len(clusters["clusters"]) if isinstance(clusters, dict) and "clusters" in clusters else None,
        tier_counts=tier_counts,
        stories_shipped=stories,
        mean_sources=mean_sources,
        coherence_checked=checked,
        coherence_flagged=flagged,
        write_branches=branch_count,
        write_branches_source=branch_source,
        grader_checks=grader_checks,
        recipients=run["articles_emailed"],
        usage=usage,
    )


def _grader_check_count(selections: dict) -> int | None:
    """How many L1 checks merge ran on this run's output, from the graders themselves."""
    try:
        return len(eval_graders.grade_selections(selections).checks)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Pipeline shape, read from the orchestrator and the agent specs.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fanout:
    per_call: str
    concurrency: int
    count: int | None
    budget_usd: float | None = None
    expand: bool = False


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    lane: str
    engine: str
    caption: str
    model: str | None = None
    thinking: str | None = None
    output: str | None = None
    fanout: Fanout | None = None
    # Facts about the pipeline itself: thresholds, models, check counts. Safe on a
    # README, where the diagram outlives any one run.
    meta: str | None = None
    # Facts about ONE run. The page shows them; the README diagram must not, or it
    # states last Tuesday's article count as if it were the pipeline's shape.
    run_meta: str | None = None
    edge_label: str | None = None
    conditional: bool = False


def _spec(filename: str) -> orchestrate.AgentSpec:
    return orchestrate.parse_agent_spec(AGENTS_DIR / filename)


def _thinking_name(thinking: Any) -> str:
    if thinking is None:
        return "sdk default"
    return str(thinking.get("type", "sdk default"))


def model_display(model: str) -> str:
    """`claude-sonnet-4-6` -> `Sonnet 4.6`."""
    parts = model.removeprefix("claude-").split("-")
    family = parts[0].capitalize()
    version = ".".join(parts[1:])
    return f"{family} {version}" if version else family


def model_pill(model: str | None, thinking: str | None) -> str:
    if not model:
        return "Python"
    label = model_display(model)
    if thinking in ("adaptive", "enabled"):
        label += " + thinking"
    return label


_SCHEDULING_SENTENCE = re.compile(r"^Runs? \b")


def spec_caption(description: str) -> str:
    """The agent's own description, minus the sentence about when it runs.

    Using the spec's words rather than a caption written here is what keeps the
    drawing from describing a stage differently than the stage describes itself.
    """
    sentences = re.split(r"(?<=\.)\s+", description.strip())
    kept = [s for s in sentences if not _SCHEDULING_SENTENCE.match(s)]
    return " ".join(kept).strip() or description.strip()


def intake_stages(fig: RunFigures) -> list[Stage]:
    kept = f"{fig.articles_kept:,} kept"
    if fig.articles_indexed is not None:
        kept += f" → {fig.articles_indexed:,} sent"
    if fig.window_hours:
        kept += f" · {fig.window_hours:.0f}h"
    return [
        Stage(
            key="feeds",
            title=f"{fig.active_sources} RSS feeds",
            lane=INTAKE_LANE,
            engine="python",
            caption="active entries in sources.json",
            run_meta=f"{fig.articles_fetched:,} items · {fig.feeds_ok} of {fig.feeds_attempted} answered",
        ),
        Stage(
            key="fetch",
            title="fetch + dedup",
            lane=INTAKE_LANE,
            engine="python",
            caption="window = time since the last completed run",
            meta=f"near-verbatim cut at {config.DEDUP_SIMILARITY_THRESHOLD:.2f}",
            run_meta=kept,
        ),
        Stage(
            key="ids",
            title="assign opaque IDs",
            lane=INTAKE_LANE,
            engine="python",
            caption="URLs never reach the model",
            meta="A1, A2, A3…",
        ),
    ]


def _cluster_stages(fig: RunFigures, spec: orchestrate.AgentSpec, output: str) -> list[Stage]:
    model = config.CLUSTER_EXTRACT_MODEL
    batch = cluster_extractjoin._EXTRACT_BATCH
    batches = math.ceil(fig.articles_indexed / batch) if fig.articles_indexed else None
    joined = None
    if fig.articles_indexed and fig.clusters:
        joined = f"{fig.articles_indexed:,} articles → {fig.clusters} stories"
    return [
        Stage(
            key="cluster",
            title="CLUSTER",
            lane=CURATION_LANE,
            engine="claude",
            caption=spec_caption(spec.description),
            model=model,
            thinking=_thinking_name(cluster_extractjoin._thinking_for(model)),
            output=output,
            fanout=Fanout(
                per_call=f"{batch} articles a call",
                concurrency=cluster_extractjoin._EXTRACT_CONCURRENCY,
                count=batches,
            ),
        ),
        Stage(
            key="join",
            title="deterministic join",
            lane=CURATION_LANE,
            engine="python",
            caption="TF-IDF over the extracted tags, in Python",
            meta=f"threshold {config.CLUSTER_JOIN_THRESHOLD:.2f}",
            run_meta=joined,
        ),
    ]


def _write_stages(fig: RunFigures, spec: orchestrate.AgentSpec, output: str) -> list[Stage]:
    preheader = _spec("preheader.md")
    return [
        Stage(
            key="write",
            title="WRITE",
            lane=CURATION_LANE,
            engine="claude",
            caption=spec_caption(spec.description),
            model=spec.model,
            thinking=_thinking_name(orchestrate._resolved_thinking(spec)),
            output=output,
            fanout=Fanout(
                per_call="one story a call",
                concurrency=orchestrate._WRITE_BRANCH_CONCURRENCY,
                count=fig.write_branches,
                budget_usd=orchestrate._WRITE_BRANCH_BUDGET_USD,
                expand=True,
            ),
        ),
        Stage(
            key="fanin",
            title="fan in",
            lane=CURATION_LANE,
            engine="python",
            caption="SELECT's order, near-duplicate check",
        ),
        Stage(
            key="preheader",
            title="PREHEADER",
            lane=CURATION_LANE,
            engine="claude",
            caption=spec_caption(preheader.description),
            model=preheader.model,
            thinking=_thinking_name(orchestrate._resolved_thinking(preheader)),
            output=orchestrate._PREHEADER_NAME,
        ),
    ]


def _repair_stage() -> Stage:
    repair = _spec("repair.md")
    recheck = _spec("coherence.md")
    return Stage(
        key="repair",
        title="REPAIR + RECHECK",
        lane=CURATION_LANE,
        engine="claude",
        caption=spec_caption(repair.description),
        model=repair.model,
        thinking=_thinking_name(orchestrate._resolved_thinking(repair)),
        output="repair_resolution.json",
        meta=f"re-check on {model_pill(recheck.model, _thinking_name(orchestrate._resolved_thinking(recheck)))}",
        edge_label="flagged field",
        conditional=True,
    )


def curation_stages(fig: RunFigures) -> list[Stage]:
    """One Stage per entry in `orchestrate._STAGES`, in order, plus the Python
    steps and extra agent calls the phase functions interleave with them."""
    stages: list[Stage] = []
    for label, spec_filename, output_filename, _validate in orchestrate._STAGES:
        spec = _spec(spec_filename)
        if label == "cluster":
            stages += _cluster_stages(fig, spec, output_filename)
        elif label == "write":
            stages += _write_stages(fig, spec, output_filename)
        else:
            run_meta = None
            if label == "select" and fig.clusters and fig.selected_stories:
                run_meta = f"{fig.clusters} → {fig.selected_stories} stories"
            if label == "coherence" and fig.coherence_checked is not None:
                run_meta = f"{fig.coherence_flagged} of {fig.coherence_checked} flagged"
            stages.append(
                Stage(
                    key=label,
                    title=label.upper(),
                    lane=CURATION_LANE,
                    engine="claude",
                    caption=spec_caption(spec.description),
                    model=spec.model,
                    thinking=_thinking_name(orchestrate._resolved_thinking(spec)),
                    output=output_filename,
                    run_meta=run_meta,
                )
            )
    stages.append(_repair_stage())
    return stages


def assembly_stages(fig: RunFigures) -> list[Stage]:
    return [
        Stage(
            key="assemble",
            title="assemble + grade",
            lane=ASSEMBLY_LANE,
            engine="python",
            caption="schema validation, then non-fatal grading",
            meta=f"{fig.grader_checks} L1 checks" if fig.grader_checks else "L1 checks",
            run_meta=f"{fig.stories_shipped} stories" if fig.stories_shipped else None,
        ),
        Stage(
            key="resolve",
            title="IDs → real URLs",
            lane=ASSEMBLY_LANE,
            engine="python",
            caption="plus source name and bias",
            run_meta=f"{fig.mean_sources:.1f} sources a story" if fig.mean_sources else None,
        ),
        Stage(
            key="render",
            title="render HTML + MJML",
            lane=ASSEMBLY_LANE,
            engine="python",
            caption="email + archive page",
        ),
        Stage(
            key="send",
            title="Resend",
            lane=ASSEMBLY_LANE,
            engine="python",
            caption="one broadcast",
            run_meta=f"{fig.recipients} recipients" if fig.recipients is not None else None,
        ),
    ]


def all_stages(fig: RunFigures) -> list[Stage]:
    return intake_stages(fig) + curation_stages(fig) + assembly_stages(fig)


# --------------------------------------------------------------------------- #
# SVG box/arrow helpers. Sizes come from label length, so nothing overflows.
# --------------------------------------------------------------------------- #

CHAR_W = 0.62  # advance per character, as a fraction of font size
BOX_PAD_X = 13.0
BOX_PAD_TOP = 13.0
BOX_PAD_BOTTOM = 9.0
LINE_H = 14.5
MIN_BOX_W = 96.0
GAP_X = 44.0
GAP_Y = 40.0
LANE_X = 92.0
CANVAS_W = 1000.0
STACK_OFFSET = 4.5

# Single-quoted family names: the whole stack is an XML attribute value delimited by
# double quotes, and GitHub serves the standalone files without the webfont link, so
# every stack has to fall back on its own. CHAR_W is sized for that fallback.
SANS = "'IBM Plex Sans', system-ui, sans-serif"
MONO = "'IBM Plex Mono', ui-monospace, Menlo, monospace"


@dataclass(frozen=True)
class Palette:
    """Colours for one rendering of the diagram.

    The page can hand in CSS custom properties, which follow the reader's theme.
    The standalone files cannot: GitHub renders them through its image proxy as
    separate documents with no stylesheet, so every colour must be a literal and
    the background has to be painted by the file itself.
    """

    name: str
    ink: str
    raised: str
    flow: str
    cost: str
    good: str
    background: str | None = None


# The page sits inside digest-anatomy's own stylesheet, so it uses the tokens and
# follows a theme switch; `currentColor` resolves to `figure svg{color:var(--slate)}`.
PAGE_PALETTE = Palette(
    name="page",
    ink="currentColor",
    raised="var(--raised)",
    flow="var(--flow)",
    cost="var(--cost)",
    good="var(--good)",
)

LIGHT_PALETTE = Palette(
    name="light",
    ink="#0f1518",
    raised="#ffffff",
    flow="#166b78",
    cost="#9a6a12",
    good="#3f6d4a",
    background="#f1f4f3",
)

DARK_PALETTE = Palette(
    name="dark",
    ink="#e8efed",
    raised="#141c1f",
    flow="#5cb8c6",
    cost="#d6a441",
    good="#7fb98c",
    background="#0c1113",
)


@dataclass(frozen=True)
class TextLine:
    text: str
    fill: str
    size: float = 11.0
    font: str = SANS
    weight: str = "normal"
    opacity: float = 1.0

    @property
    def width(self) -> float:
        return len(self.text) * self.size * CHAR_W


@dataclass
class NodeBox:
    key: str
    lines: tuple[TextLine, ...]
    accent: str
    fill: str
    stroke_width: float = 1.4
    stack: int = 1
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    def measure(self) -> None:
        self.w = max(MIN_BOX_W, max(line.width for line in self.lines) + 2 * BOX_PAD_X)
        self.h = BOX_PAD_TOP + LINE_H * len(self.lines) + BOX_PAD_BOTTOM

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def overflowing(self) -> list[TextLine]:
        """Labels wider than the space inside the box."""
        inner = self.w - 2 * BOX_PAD_X
        return [line for line in self.lines if line.width > inner + 1e-6]


def pack_rows(boxes: list[NodeBox], max_width: float) -> list[list[NodeBox]]:
    rows: list[list[NodeBox]] = []
    current: list[NodeBox] = []
    used = 0.0
    for box in boxes:
        extra = box.w + (GAP_X if current else 0.0)
        if current and used + extra > max_width:
            rows.append(current)
            current, used = [box], box.w
        else:
            current.append(box)
            used += extra
    if current:
        rows.append(current)
    return rows


def place_rows(rows: list[list[NodeBox]], x0: float, y0: float) -> float:
    """Lay each row out left to right, top to bottom. Returns the bottom edge."""
    y = y0
    for row in rows:
        x = x0
        height = max(box.h for box in row)
        for box in row:
            box.x, box.y = x, y + (height - box.h) / 2
            x += box.w + GAP_X
        y += height + GAP_Y
    return y - GAP_Y


def _esc(text: str) -> str:
    """Escape for both text content and attribute values -- `aria-label` and `alt`
    carry stage titles and run figures, and one quote there ends the attribute."""
    return html.escape(text, quote=True)


def _marker_id(colour: str) -> str:
    return "arrow-" + re.sub(r"[^a-z0-9]", "", colour.lower())


def draw_box(box: NodeBox) -> list[str]:
    out: list[str] = []
    for depth in range(box.stack - 1, 0, -1):
        out.append(
            f'<rect x="{box.x + depth * STACK_OFFSET:.1f}" y="{box.y + depth * STACK_OFFSET:.1f}" '
            f'width="{box.w:.1f}" height="{box.h:.1f}" rx="3" fill="none" '
            f'stroke="{box.accent}" stroke-width="1" opacity="{0.7 - 0.2 * depth:.2f}"/>'
        )
    out.append(
        f'<rect x="{box.x:.1f}" y="{box.y:.1f}" width="{box.w:.1f}" height="{box.h:.1f}" rx="3" '
        f'fill="{box.fill}" stroke="{box.accent}" stroke-width="{box.stroke_width}"/>'
    )
    ty = box.y + BOX_PAD_TOP
    for line in box.lines:
        ty += LINE_H * 0.78
        out.append(
            f'<text x="{box.cx:.1f}" y="{ty:.1f}" text-anchor="middle" font-family="{line.font}" '
            f'font-size="{line.size}" font-weight="{line.weight}" fill="{line.fill}" '
            f'opacity="{line.opacity}">{_esc(line.text)}</text>'
        )
        ty += LINE_H * 0.22
    return out


def draw_hop(a: NodeBox, b: NodeBox, colour: str, ink: str, label: str | None = None) -> list[str]:
    """Arrow between two boxes on the same row."""
    marker = _marker_id(colour)
    out = [
        f'<line x1="{a.right:.1f}" y1="{a.cy:.1f}" x2="{b.x - 2:.1f}" y2="{a.cy:.1f}" '
        f'stroke="{colour}" stroke-width="1.4" marker-end="url(#{marker})"/>'
    ]
    # An edge label wider than the gap would print across both boxes; the stage's own
    # caption already carries the condition, so drop it rather than overlap.
    if label and len(label) * 9 * CHAR_W > b.x - a.right:
        label = None
    if label:
        out.append(
            f'<text x="{(a.right + b.x) / 2:.1f}" y="{a.cy - 7:.1f}" text-anchor="middle" '
            f'font-family="{MONO}" font-size="9" fill="{ink}" opacity=".7">{_esc(label)}</text>'
        )
    return out


def draw_wrap(a: NodeBox, b: NodeBox, colour: str, ink: str, label: str | None = None) -> list[str]:
    """Elbow from the end of one row down and back to the start of the next, so
    every row still reads left to right."""
    marker = _marker_id(colour)
    mid = (a.bottom + b.y) / 2
    out = [
        f'<path d="M {a.cx:.1f} {a.bottom:.1f} L {a.cx:.1f} {mid:.1f} L {b.cx:.1f} {mid:.1f} '
        f'L {b.cx:.1f} {b.y - 2:.1f}" fill="none" stroke="{colour}" stroke-width="1.4" '
        f'marker-end="url(#{marker})"/>'
    ]
    if label:
        out.append(
            f'<text x="{(a.cx + b.cx) / 2:.1f}" y="{mid - 5:.1f}" text-anchor="middle" '
            f'font-family="{MONO}" font-size="9" fill="{ink}" opacity=".7">{_esc(label)}</text>'
        )
    return out


def _wrap_caption(caption: str, width: int = 36, limit: int = 5) -> list[str]:
    lines = textwrap.wrap(caption, width=width) or [""]
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1].rstrip(" .,;") + "…"
    return lines


def stage_box(stage: Stage, palette: Palette) -> NodeBox:
    claude = stage.engine == "claude"
    accent = palette.cost if stage.key in ("coherence", "repair") else (palette.flow if claude else palette.ink)
    if stage.key == "send":
        accent = palette.good
    title_fill = accent if claude or stage.key == "send" else palette.ink
    lines = [TextLine(stage.title, title_fill, size=12, font=MONO if claude else SANS, weight="600")]
    lines += [TextLine(part, palette.ink, size=10.5, opacity=0.78) for part in _wrap_caption(stage.caption)]
    meta: list[str] = []
    if stage.model:
        meta.append(model_pill(stage.model, stage.thinking))
    if stage.fanout:
        count = f"×{stage.fanout.count}" if stage.fanout.count else "×N"
        meta.append(f"{count} · {stage.fanout.per_call} · {stage.fanout.concurrency} in flight")
    meta += [part for part in (stage.meta, stage.run_meta) if part]
    lines += [TextLine(part, palette.ink, size=9.5, font=MONO, opacity=0.6) for part in meta]
    box = NodeBox(
        key=stage.key,
        lines=tuple(lines),
        accent=accent,
        fill=palette.raised,
        stroke_width=1.8 if stage.key in ("coherence", "send") else 1.4,
        stack=3 if stage.fanout else 1,
    )
    box.measure()
    return box


@dataclass
class Diagram:
    palette: Palette
    boxes: list[NodeBox] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    width: float = CANVAS_W
    height: float = 0.0
    colours: set[str] = field(default_factory=set)

    def svg(self, aria: str, *, standalone: bool = False) -> str:
        """The diagram as one SVG element.

        ``standalone`` adds what a file loaded on its own needs and a fragment inside
        the page does not: the namespace, intrinsic dimensions, a ``<title>``, and a
        background rect (GitHub's image proxy paints nothing behind it).
        """
        defs = "".join(
            f'<marker id="{_marker_id(c)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
            f'markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{c}"/></marker>'
            for c in sorted(self.colours)
        )
        head = f'<svg viewBox="0 0 {self.width:.0f} {self.height:.0f}" role="img" aria-label="{_esc(aria)}"'
        title = ""
        background = ""
        if standalone:
            head = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width:.0f} {self.height:.0f}" '
                f'width="{self.width:.0f}" height="{self.height:.0f}" role="img" aria-label="{_esc(aria)}"'
            )
            title = f"<title>{_esc(aria)}</title>"
            if self.palette.background:
                background = (
                    f'<rect x="0" y="0" width="{self.width:.0f}" height="{self.height:.0f}" '
                    f'fill="{self.palette.background}"/>'
                )
        return head + ">" + title + f"<defs>{defs}</defs>" + background + "".join(self.body) + "</svg>"


def build_diagram(stages: list[Stage], palette: Palette = PAGE_PALETTE) -> Diagram:
    """Lay the whole flow out from the stage list alone.

    Three lanes stacked top to bottom, each packed left to right and wrapped when it
    runs out of width; a lane change drops down and returns to the leftmost box.
    """
    diagram = Diagram(palette=palette)
    lanes = [INTAKE_LANE, CURATION_LANE, ASSEMBLY_LANE]
    content_w = CANVAS_W - LANE_X - 20
    y = 26.0
    previous_last: NodeBox | None = None
    previous_label: str | None = None

    for lane in lanes:
        lane_stages = [s for s in stages if s.lane == lane]
        if not lane_stages:
            continue
        boxes = [stage_box(s, palette) for s in lane_stages]
        rows = pack_rows(boxes, content_w)
        lane_top = y
        bottom = place_rows(rows, LANE_X, y)
        diagram.boxes.extend(boxes)

        if lane == CURATION_LANE:
            pad = 16
            diagram.body.append(
                f'<rect x="{LANE_X - pad:.1f}" y="{lane_top - pad:.1f}" width="{content_w + 2 * pad:.1f}" '
                f'height="{bottom - lane_top + 2 * pad:.1f}" rx="4" fill="none" stroke="{palette.flow}" '
                f'stroke-width="1.2" stroke-dasharray="3 3" opacity=".5"/>'
            )
        colour = palette.flow if lane == CURATION_LANE else palette.ink
        diagram.body.append(
            f'<text x="6" y="{lane_top + 12:.1f}" font-family="{MONO}" font-size="10" '
            f'fill="{colour}" opacity=".75" '
            f'letter-spacing="1">{_esc(lane.split(" · ")[-1].upper())}</text>'
        )

        for box in boxes:
            diagram.body.extend(draw_box(box))

        diagram.colours.add(colour)
        # Keyed by box identity, not by stage key: two stages in a lane may share a
        # key (an added `_STAGES` label colliding with one of this tool's own steps),
        # and a dict keyed on that would hand the wrong edge label to the survivor.
        edge_label = {id(box): stage.edge_label for box, stage in zip(boxes, lane_stages, strict=True)}
        for row in rows:
            for a, b in itertools.pairwise(row):
                diagram.body.extend(draw_hop(a, b, colour, palette.ink, edge_label[id(b)]))
        for upper, lower in itertools.pairwise(rows):
            diagram.body.extend(draw_wrap(upper[-1], lower[0], colour, palette.ink, edge_label[id(lower[0])]))

        if previous_last is not None:
            diagram.colours.add(palette.ink)
            diagram.body = draw_wrap(previous_last, boxes[0], palette.ink, palette.ink, previous_label) + diagram.body
        previous_last, previous_label = boxes[-1], None
        y = bottom + GAP_Y + 18

    diagram.height = y - GAP_Y + 6
    return diagram


def out_of_canvas(diagram: Diagram) -> list[str]:
    """Boxes that fall outside the fixed viewBox.

    The real overflow risk is not a label inside its box -- ``measure`` sizes the box
    to the label -- but a box too wide for the lane, which ``pack_rows`` then places
    alone and lets run past ``CANVAS_W``. Nothing in the layout clamps that, so it is
    checked rather than assumed.
    """
    return [
        box.key
        for box in diagram.boxes
        if box.x < 0 or box.right > diagram.width + 1e-6 or box.y < 0 or box.bottom > diagram.height + 1e-6
    ]


def diagram_aria(fig: RunFigures, stages: list[Stage]) -> str:
    names = ", ".join(s.title for s in stages if s.lane == CURATION_LANE)
    return (
        f"Data flow for run {fig.run_id}: {fig.active_sources} RSS feeds and {fig.articles_fetched:,} items "
        f"into Python intake, then the Claude curation lane ({names}), then Python assembly out to "
        + (f"{fig.recipients} recipients." if fig.recipients is not None else "the broadcast.")
    )


# --------------------------------------------------------------------------- #
# Mermaid.
# --------------------------------------------------------------------------- #

# Mermaid node ids that collide with its own keywords.
MERMAID_RESERVED = frozenset({"end", "graph", "subgraph", "click", "class", "style", "call", "o", "x", "default"})
_MERMAID_QUOTE_TRIGGER = re.compile(r"""[()\[\]{}"'#;]""")


def mermaid_id(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", key)
    if not safe or not safe[0].isalpha():
        safe = "n_" + safe
    return safe + "_" if safe.lower() in MERMAID_RESERVED else safe


def unique_mermaid_ids(stages: list[Stage]) -> dict[int, str]:
    """One id per stage, unique across the whole graph, keyed by object identity.

    A stage added to `_STAGES` under a label this tool already uses for one of its
    Python steps (`render`, `assemble`, `join`, ...) would otherwise emit the same
    node id twice; mermaid merges same-id nodes, which silently relabels the other
    lane's box and can close a cycle. Suffixing the later one keeps them separate.
    """
    used: set[str] = set()
    ids: dict[int, str] = {}
    for stage in stages:
        base = mermaid_id(stage.key)
        node_id, n = base, 2
        while node_id in used:
            node_id, n = f"{base}_{n}", n + 1
        used.add(node_id)
        ids[id(stage)] = node_id
    return ids


def mermaid_label(parts: list[str]) -> str:
    text = "<br/>".join(p for p in parts if p)
    if _MERMAID_QUOTE_TRIGGER.search(text):
        return '"' + text.replace('"', "#quot;") + '"'
    return text


def _mermaid_parts(stage: Stage) -> list[str]:
    """Three lines at most: an identity line, then the stage's own description.

    ``run_meta`` is deliberately left out: this block is the shape-and-configuration
    rendering, for places that cannot show an image. The run figures live in the
    drawing, which is always published next to the run id it was measured on.
    """
    head = [stage.title]
    if stage.model:
        head.append(model_pill(stage.model, stage.thinking))
    if stage.fanout and not stage.fanout.expand:
        head.append(f"{stage.fanout.per_call} · {stage.fanout.concurrency} in flight")
    if stage.meta:
        head.append(stage.meta)
    return [" · ".join(head), *_wrap_caption(stage.caption, width=50, limit=2)]


def _node(stage: Stage, ids: dict[int, str]) -> str:
    return f"{ids[id(stage)]}[{mermaid_label(_mermaid_parts(stage))}]"


def render_mermaid(stages: list[Stage]) -> str:
    """A `flowchart TB` of three lane subgraphs; GitHub renders 17 nodes legibly
    stacked, not side by side."""
    ids = unique_mermaid_ids(stages)
    intake = [s for s in stages if s.lane == INTAKE_LANE]
    curation = [s for s in stages if s.lane == CURATION_LANE]
    assembly = [s for s in stages if s.lane == ASSEMBLY_LANE]

    out = ["```mermaid", "flowchart TB"]

    out.append(f"  subgraph intake [{INTAKE_LANE}]")
    out.append("    direction LR")
    out.append("    " + " --> ".join(_node(s, ids) for s in intake))
    out.append("  end")

    out.append(f"  subgraph curation [{CURATION_LANE}]")
    out.append("    direction TB")
    chain = [s for s in curation if not s.conditional]
    previous: str | None = None
    for stage in chain:
        if stage.fanout and stage.fanout.expand:
            heads = [f"{ids[id(stage)]}{suffix}" for suffix in (1, 2, "n")]
            tails = [
                mermaid_label([f"{stage.title} story 1"]),
                mermaid_label([f"{stage.title} story 2"]),
                mermaid_label([f"{stage.title} story n", *_wrap_caption(stage.caption, width=50, limit=2)]),
            ]
            declared = " & ".join(f"{head}[{tail}]" for head, tail in zip(heads, tails, strict=True))
            out.append(f"    {previous} --> {declared}")
            previous = " & ".join(heads)
            continue
        node = _node(stage, ids)
        out.append(f"    {previous} --> {node}" if previous else f"    {node}")
        previous = ids[id(stage)]
    for stage in curation:
        if stage.conditional:
            out.append(f"    {previous} -- {stage.edge_label} --> {_node(stage, ids)}")
    out.append("  end")

    out.append(f"  subgraph assembly [{ASSEMBLY_LANE}]")
    out.append("    direction LR")
    out.append("    " + " --> ".join(_node(s, ids) for s in assembly))
    out.append("  end")

    out.append(f"  {ids[id(intake[-1])]} --> {ids[id(curation[0])]}")
    out.append(f"  {previous} -- passed --> {ids[id(assembly[0])]}")
    for stage in curation:
        if stage.conditional:
            out.append(f"  {ids[id(stage)]} --> {ids[id(assembly[0])]}")
    out.append("```")
    return "\n".join(out)


def update_readme(readme: Path, block: str) -> str:
    text = readme.read_text(encoding="utf-8")
    start, end = text.find(README_BEGIN), text.find(README_END)
    if start < 0 or end < 0 or end < start:
        raise SystemExit(f"{readme}: missing {README_BEGIN} / {README_END} markers")
    return text[: start + len(README_BEGIN)] + "\n" + block + "\n" + text[end:]


# The standalone pair. GitHub swaps them on the reader's theme via <picture>; the
# names are fixed so the README link survives a regeneration.
SVG_LIGHT_NAME = "pipeline-anatomy.svg"
SVG_DARK_NAME = "pipeline-anatomy-dark.svg"


def write_standalone_svgs(svg_dir: Path, fig: RunFigures, stages: list[Stage]) -> tuple[Path, Path]:
    """The same drawing as the page, once per theme, as two self-contained files."""
    svg_dir.mkdir(parents=True, exist_ok=True)
    aria = diagram_aria(fig, stages)
    written = []
    for name, palette in ((SVG_LIGHT_NAME, LIGHT_PALETTE), (SVG_DARK_NAME, DARK_PALETTE)):
        path = svg_dir / name
        path.write_text(build_diagram(stages, palette).svg(aria, standalone=True) + "\n", encoding="utf-8")
        written.append(path)
    return written[0], written[1]


def readme_picture_block(
    readme: Path,
    svg_dir: Path,
    fig: RunFigures,
    stages: list[Stage],
    html_path: Path | None = None,
    code_version: str | None = None,
) -> str:
    """A <picture> that follows the reader's GitHub theme, plus one line of provenance."""
    light = _posix_relpath(svg_dir / SVG_LIGHT_NAME, readme.parent)
    dark = _posix_relpath(svg_dir / SVG_DARK_NAME, readme.parent)
    alt = _esc(diagram_aria(fig, stages))
    caption = f"<sub>{provenance_caption(fig, code_version)} Regenerate with `make anatomy`."
    if html_path is not None:
        # A code span, not a link: GitHub renders a linked .html as source, which reads
        # like a broken page.
        caption += f" Per-stage models and costs: `{_posix_relpath(html_path, readme.parent)}`."
    return (
        "<picture>\n"
        f'  <source media="(prefers-color-scheme: dark)" srcset="{dark}">\n'
        f'  <img src="{light}" alt="{alt}">\n'
        "</picture>\n"
        "\n" + caption + "</sub>"
    )


def _posix_relpath(target: Path, start: Path) -> str:
    return Path(os.path.relpath(target, start)).as_posix()


# --------------------------------------------------------------------------- #
# HTML page.
# --------------------------------------------------------------------------- #

CSS = """
  :root{
    --paper:#f1f4f3; --raised:#ffffff; --ink:#0f1518; --body:#2b383d;
    --slate:#5c6b71; --faint:#8b9a9f; --rule:#d3dcda; --rule-soft:#e4eae8;
    --flow:#166b78; --flow-soft:#e0eff0;
    --cost:#9a6a12; --cost-soft:#f5ebd8;
    --alarm:#9e3626; --alarm-soft:#f7e3df;
    --good:#3f6d4a;
    --sans:"IBM Plex Sans",ui-sans-serif,system-ui,sans-serif;
    --serif:"IBM Plex Serif",Georgia,serif;
    --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  @media (prefers-color-scheme:dark){
    :root:not([data-theme="light"]){
      --paper:#0c1113; --raised:#141c1f; --ink:#e8efed; --body:#c3d0d0;
      --slate:#93a3a7; --faint:#6d7f84; --rule:#253135; --rule-soft:#1b2529;
      --flow:#5cb8c6; --flow-soft:#132b2f;
      --cost:#d6a441; --cost-soft:#2c2413;
      --alarm:#dd7059; --alarm-soft:#2e1a16;
      --good:#7fb98c;
    }
  }
  :root[data-theme="dark"]{
    --paper:#0c1113; --raised:#141c1f; --ink:#e8efed; --body:#c3d0d0;
    --slate:#93a3a7; --faint:#6d7f84; --rule:#253135; --rule-soft:#1b2529;
    --flow:#5cb8c6; --flow-soft:#132b2f;
    --cost:#d6a441; --cost-soft:#2c2413;
    --alarm:#dd7059; --alarm-soft:#2e1a16;
    --good:#7fb98c;
  }

  *{box-sizing:border-box}
  body{background:var(--paper);color:var(--body);font-family:var(--sans);font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:0 28px 96px}

  header.top{padding:56px 0 34px;border-bottom:2px solid var(--ink);margin-bottom:44px}
  .eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--flow);margin:0 0 14px}
  h1{font-family:var(--sans);font-weight:700;font-size:clamp(30px,5vw,46px);line-height:1.06;letter-spacing:-.02em;color:var(--ink);margin:0 0 16px;text-wrap:balance}
  .standfirst{font-family:var(--serif);font-size:18px;line-height:1.6;color:var(--slate);max-width:60ch;margin:0}
  .meta{font-family:var(--mono);font-size:11.5px;color:var(--faint);margin-top:20px;letter-spacing:.02em;line-height:1.8}

  h2{font-family:var(--sans);font-weight:600;font-size:24px;letter-spacing:-.012em;color:var(--ink);margin:0 0 6px;text-wrap:balance}
  h3{font-family:var(--sans);font-weight:600;font-size:16.5px;color:var(--ink);margin:0 0 8px}
  section{margin-bottom:60px;scroll-margin-top:20px}
  .sec-head{display:flex;align-items:baseline;gap:14px;border-bottom:1px solid var(--rule);padding-bottom:12px;margin-bottom:26px}
  .sec-num{font-family:var(--mono);font-size:12px;color:var(--flow);font-weight:600;letter-spacing:.06em;flex:none}
  .sec-sub{font-family:var(--serif);font-style:italic;color:var(--slate);font-size:16px;margin:0}
  p{max-width:68ch}
  p+p{margin-top:14px}
  strong{color:var(--ink);font-weight:600}
  code{font-family:var(--mono);font-size:.88em;background:var(--rule-soft);padding:1px 5px;border-radius:3px;color:var(--ink)}
  a{color:var(--flow)}

  .callout{background:var(--flow-soft);border-left:3px solid var(--flow);padding:16px 20px;border-radius:0 4px 4px 0;margin:24px 0}
  .callout p{margin:0;font-size:15px;max-width:none}
  .callout.warn{background:var(--alarm-soft);border-left-color:var(--alarm)}

  figure{margin:28px 0;background:var(--raised);border:1px solid var(--rule);border-radius:4px;padding:26px 24px 18px;overflow-x:auto}
  figure svg{display:block;max-width:100%;height:auto;margin:0 auto;color:var(--slate)}
  figcaption{font-family:var(--serif);font-size:14px;font-style:italic;color:var(--slate);margin-top:18px;padding-top:14px;border-top:1px solid var(--rule-soft);text-align:left;max-width:74ch}

  .tw{overflow-x:auto;margin:24px 0;border:1px solid var(--rule);border-radius:4px;background:var(--raised)}
  table{border-collapse:collapse;width:100%;font-size:14px;min-width:640px}
  th{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);text-align:left;padding:12px 14px;border-bottom:1px solid var(--rule);font-weight:600;white-space:nowrap}
  td{padding:11px 14px;border-bottom:1px solid var(--rule-soft);vertical-align:middle}
  tr:last-child td{border-bottom:none}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
  .stage-name{font-family:var(--mono);font-weight:600;color:var(--ink);font-size:13px}
  .dim{color:var(--faint)}

  .bar{display:flex;align-items:center;gap:9px}
  .bar-track{flex:1;height:7px;background:var(--rule-soft);border-radius:4px;overflow:hidden;min-width:60px}
  .bar-fill{height:100%;background:var(--cost);border-radius:4px}
  .bar-pct{font-family:var(--mono);font-size:11px;color:var(--slate)}

  .pill{display:inline-block;font-family:var(--mono);font-size:10.5px;padding:2px 7px;border-radius:3px;letter-spacing:.04em;white-space:nowrap}
  .pill.think{background:var(--cost-soft);color:var(--cost);border:1px solid var(--cost)}
  .pill.plain{background:var(--flow-soft);color:var(--flow);border:1px solid var(--flow)}
  .pill.py{background:var(--rule-soft);color:var(--slate);border:1px solid var(--rule)}

  footer.end{border-top:2px solid var(--ink);padding-top:20px;margin-top:20px;font-family:var(--mono);font-size:11.5px;color:var(--faint);line-height:1.8}

  @media (max-width:640px){
    .wrap{padding:0 18px 64px}
    header.top{padding-top:36px}
  }
  :focus-visible{outline:2px solid var(--flow);outline-offset:2px}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def _pill(stage_or_row_model: str | None, thinking: str | None) -> str:
    if not stage_or_row_model:
        return '<span class="pill py">Python</span>'
    cls = "think" if thinking in ("adaptive", "enabled") else "plain"
    return f'<span class="pill {cls}">{_esc(model_pill(stage_or_row_model, thinking))}</span>'


def _stage_table(stages: list[Stage]) -> str:
    rows = []
    for stage in stages:
        if stage.lane != CURATION_LANE:
            continue
        bounds = []
        if stage.fanout:
            count = f"×{stage.fanout.count}" if stage.fanout.count else "×N"
            bounds.append(f"{count}, {stage.fanout.concurrency} in flight")
            if stage.fanout.budget_usd:
                bounds.append(f"${stage.fanout.budget_usd:.2f} a call")
        if stage.meta:
            bounds.append(stage.meta)
        rows.append(
            "<tr>"
            f'<td class="stage-name">{_esc(stage.title)}</td>'
            f"<td>{_pill(stage.model, stage.thinking)}</td>"
            f"<td>{_esc(stage.caption)}</td>"
            f"<td><code>{_esc(stage.output or '—')}</code></td>"
            f'<td class="dim">{_esc(" · ".join(bounds)) or "—"}</td>'
            "</tr>"
        )
    return (
        '<div class="tw"><table><thead><tr><th>Stage</th><th>Model</th><th>What its own spec says it does</th>'
        "<th>Writes</th><th>Bounds</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


# The per-story WRITE fan-out landed here; before it, one call wrote every story.
# A run without a write_branches.json artifact predates it, and the caption says so
# until such a run exists -- at which point the sentence stops being emitted.
PER_STORY_WRITE_FROM = "2026-09-03"


def repo_head_sha() -> str | None:
    """The short sha of the code being drawn, read from .git without a git binary.

    Best effort: the CI image has no git, and a worktree's `.git` file points at an
    absolute path outside the container. Unresolvable means the caption names the
    generation date instead of claiming a revision it could not read.
    """
    try:
        git = REPO / ".git"
        if git.is_file():
            git = Path(git.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip())
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7]
        ref = head.split(":", 1)[1].strip()
        direct = git / ref
        if direct.exists():
            return direct.read_text(encoding="utf-8").strip()[:7]
        common = git / "commondir"
        base = (git / common.read_text(encoding="utf-8").strip()).resolve() if common.exists() else git
        packed = base / "packed-refs"
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0][:7]
    except OSError, IndexError, ValueError:
        return None
    return None


def provenance_caption(fig: RunFigures, code_version: str | None = None) -> str:
    """One sentence saying which code the drawing describes and which run the numbers
    came from -- the two can differ, and only the page can say so.

    ``code_version`` is what `make anatomy` passes in: the container has no git binary
    and a worktree's `.git` points outside it, so the host resolves the revision and
    hands it over. :func:`repo_head_sha` is the fallback for a direct invocation.
    """
    sha = code_version or repo_head_sha()
    code = f"as of {sha}" if sha else f"as generated on {datetime.now(UTC):%Y-%m-%d}"
    caption = f"Stages and models {code}. Run figures from run {fig.run_id} ({fig.run_at[:10]})."
    if fig.write_branches_source == "selected.json":
        caption += (
            " That run wrote all stories in one call; the per-story WRITE shown here first ran on "
            f"{PER_STORY_WRITE_FROM}."
        )
    return caption


def stages_without_usage(fig: RunFigures, stages: list[Stage]) -> list[str]:
    """Stages the code runs that this run recorded no usage for, so the cost table can
    show them at "--" rather than let the total read as complete."""
    billed = {row.subagent for row in fig.usage}
    return sorted({s.key for s in stages if s.model} - billed)


def _cost_table(fig: RunFigures, absent: list[str]) -> str:
    rows = sorted(fig.usage, key=lambda r: r.cost_usd, reverse=True)
    if not rows:
        return "<p>No <code>run_usage</code> rows recorded for this run.</p>"
    top = rows[0].cost_usd or 1.0
    total = fig.total_cost_usd or 1.0
    body = []
    for row in rows:
        # `calls` is the number of run_usage ROWS, which is not the number of model
        # calls: cluster_extractjoin merges its batches into one row before recording.
        name = row.subagent + (f" ({row.calls} usage rows)" if row.calls > 1 else "")
        body.append(
            "<tr>"
            f'<td class="stage-name">{_esc(name)}</td>'
            f"<td>{_pill(row.model, row.thinking)}</td>"
            f'<td class="num">{row.cache_read_tokens:,}</td>'
            f'<td class="num">{row.output_tokens:,}</td>'
            f'<td class="num">{row.duration_ms / 1000:.0f}s</td>'
            f'<td class="num">${row.cost_usd:.2f}</td>'
            f'<td><div class="bar"><div class="bar-track"><div class="bar-fill" '
            f'style="width:{100 * row.cost_usd / top:.0f}%"></div></div>'
            f'<span class="bar-pct">{100 * row.cost_usd / total:.0f}%</span></div></td>'
            "</tr>"
        )
    # A stage the code runs but this run recorded nothing for still gets a row, at "—",
    # so the total below cannot be read as the whole of today's pipeline.
    for key in absent:
        body.append(
            f'<tr><td class="stage-name">{_esc(key)}</td>'
            f'<td class="dim">—</td><td class="num dim">—</td><td class="num dim">—</td>'
            f'<td class="num dim">—</td><td class="num dim">—</td>'
            f'<td class="dim">did not run in run {fig.run_id}</td></tr>'
        )
    return (
        '<div class="tw"><table><thead><tr><th>Stage</th><th>Model</th><th class="num">Text re-read</th>'
        '<th class="num">Text written</th><th class="num">Time</th><th class="num">Cost</th>'
        '<th style="width:150px">Share of run</th></tr></thead><tbody>' + "".join(body) + "</tbody></table></div>"
    )


def _read_write_figure(fig: RunFigures) -> str:
    """The reader vs the writer: the run's own heaviest re-reader against its
    heaviest producer, both drawn from run_usage."""
    if len(fig.usage) < 2:
        return ""
    reader = max(fig.usage, key=lambda r: r.cache_read_tokens)
    writer = max(fig.usage, key=lambda r: r.output_tokens)
    if reader.subagent == writer.subagent:
        return ""

    w, h = 900.0, 250.0
    body = [
        f'<text x="{w / 2:.0f}" y="22" text-anchor="middle" font-family="{SANS}" font-size="12" '
        f'fill="currentColor" opacity=".75">Two stages. Opposite reasons.</text>',
        f'<line x1="{w / 2:.0f}" y1="46" x2="{w / 2:.0f}" y2="212" stroke="currentColor" stroke-width="1" '
        f'opacity=".25" stroke-dasharray="3 4"/>',
    ]
    for side, (row, colour, kind) in enumerate(
        ((reader, PAGE_PALETTE.cost, "reads"), (writer, PAGE_PALETTE.flow, "writes"))
    ):
        x0 = 40 + side * (w / 2)
        body.append(
            f'<text x="{x0:.0f}" y="62" font-family="{MONO}" font-size="13" font-weight="600" '
            f'fill="{colour}">{_esc(row.subagent)} — ${row.cost_usd:.2f}</text>'
        )
        big, small = (row.cache_read_tokens, row.output_tokens)
        left_label, right_label = ("re-read", "written")
        if kind == "writes":
            big, small = small, big
            left_label, right_label = ("written", "re-read")
        for i, (value, label) in enumerate(((big, left_label), (small, right_label))):
            bar = 20 + 300 * (value / max(big, 1))
            body.append(
                f'<text x="{x0:.0f}" y="{100 + i * 46:.0f}" font-family="{SANS}" font-size="11.5" '
                f'fill="currentColor" opacity=".8">{_esc(label)}</text>'
            )
            body.append(
                f'<rect x="{x0:.0f}" y="{106 + i * 46:.0f}" width="{bar:.0f}" height="14" rx="2" '
                f'fill="{colour}" opacity="{0.85 if i == 0 else 0.35}"/>'
            )
            body.append(
                f'<text x="{x0 + bar + 8:.0f}" y="{117 + i * 46:.0f}" font-family="{MONO}" font-size="12" '
                f'fill="currentColor" opacity=".85">{value:,}</text>'
            )
        ratio = row.cache_read_tokens / max(row.output_tokens, 1)
        body.append(
            f'<text x="{x0:.0f}" y="212" font-family="{MONO}" font-size="11" fill="currentColor" '
            f'opacity=".7">re-read ÷ written = {ratio:.1f}</text>'
        )
    svg = f'<svg viewBox="0 0 {w:.0f} {h:.0f}" role="img" aria-label="{_esc(reader.subagent)} re-reads {reader.cache_read_tokens:,} tokens and writes {reader.output_tokens:,}; {_esc(writer.subagent)} re-reads {writer.cache_read_tokens:,} and writes {writer.output_tokens:,}">{"".join(body)}</svg>'
    return (
        "<figure>" + svg + "<figcaption>Cost tracks re-reading more than producing. "
        f"<code>{_esc(reader.subagent)}</code> re-read {reader.cache_read_tokens:,} cached tokens to write "
        f"{reader.output_tokens:,}; <code>{_esc(writer.subagent)}</code> wrote {writer.output_tokens:,} from "
        f"{writer.cache_read_tokens:,}. Shrinking the article pool moves the first and barely touches the "
        "second.</figcaption></figure>"
    )


def _funnel_caption(fig: RunFigures, stages: list[Stage]) -> str:
    steps = [f"{fig.articles_fetched:,} items"]
    if fig.articles_kept:
        steps.append(f"{fig.articles_kept:,} inside the window")
    if fig.articles_indexed:
        steps.append(f"{fig.articles_indexed:,} handed to the model")
    if fig.clusters:
        steps.append(f"{fig.clusters} stories after the join")
    if fig.stories_shipped:
        steps.append(f"{fig.stories_shipped} shipped")
    caption = "One run, end to end: " + " → ".join(steps) + ". "
    outputs = [s.output for s in stages if s.output]
    if outputs:
        caption += (
            f"Each curation stage hands the next one a file on disk ({_esc(', '.join(outputs))}). "
            "A run that fails resumes with <code>--resume</code> from the last stage whose output validated; "
            "<code>repair_resolution.json</code> is regenerated. "
        )
    fanouts = [s for s in stages if s.fanout]
    if fanouts:
        caption += (
            "Stacked boxes are fan-outs: "
            + ", ".join(
                f"{s.title} {('×' + str(s.fanout.count)) if s.fanout.count else '×N'}, {s.fanout.concurrency} in flight"
                for s in fanouts
            )
            + ". "
        )
    if fig.write_branches_source == "selected.json":
        caption += (
            f"Run {fig.run_id} has no <code>write_branches.json</code> — it predates the per-story fan-out — "
            "so WRITE's ×N is this run's selected-story count, the number of branches the deployed code "
            "would build for it."
        )
    return caption


def render_html(fig: RunFigures, stages: list[Stage], code_version: str | None = None) -> str:
    diagram = build_diagram(stages)
    absent = stages_without_usage(fig, stages)
    generated = datetime.now(UTC).strftime("%Y-%m-%d")
    wall = f"{fig.wall_clock_s / 60:.0f} min" if fig.wall_clock_s else "—"
    run_line = f"Run {fig.run_id}, started {_esc(fig.run_at)} UTC, {wall} wall clock"
    if fig.git_sha:
        run_line += f", git <code>{_esc(fig.git_sha)}</code>"
    meta = [
        f"Generated {generated} by <code>newsroom/tools/pipeline_anatomy.py</code> — every number below is read "
        "from the code and the database, none is written here.",
        run_line,
    ]
    parts = [
        "<title>Digest Pipeline Anatomy</title>",
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600'
        "&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,400;0,600;1,400"
        '&display=swap">',
        f"<style>{CSS}</style>",
        '<div class="wrap">',
        '<header class="top">',
        '<p class="eyebrow">News digest · system reference</p>',
        "<h1>Digest Pipeline Anatomy</h1>",
        '<p class="standfirst">How an article gets from an RSS feed to someone’s inbox, and what each stage '
        "of that trip cost the last time it ran.</p>",
        '<p class="meta">' + "<br/>".join(meta) + "</p>",
        "</header>",
        "<section>",
        '<div class="sec-head"><span class="sec-num">01</span><div><h2>How the data flows</h2>'
        f'<p class="sec-sub">{fig.articles_fetched:,} articles in, '
        f"{fig.stories_shipped or '?'} stories out, once a day.</p></div></div>",
        "<figure>"
        + diagram.svg(diagram_aria(fig, stages))
        + f"<figcaption>{_esc(provenance_caption(fig, code_version))} {_funnel_caption(fig, stages)}"
        "</figcaption></figure>",
        "</section>",
        "<section>",
        '<div class="sec-head"><span class="sec-num">02</span><div><h2>The stages</h2>'
        '<p class="sec-sub">Order from <code>orchestrate._STAGES</code>; models and descriptions from each '
        "agent’s own spec file.</p></div></div>",
        _stage_table(stages),
        "</section>",
        "<section>",
        '<div class="sec-head"><span class="sec-num">03</span><div><h2>Where the money goes</h2>'
        f'<p class="sec-sub">${fig.total_cost_usd:.2f} on run {fig.run_id}.</p></div></div>',
        '<div class="callout"><p>Costs are <strong>API-equivalent</strong>: the pipeline runs on a Claude '
        "subscription, so these are what the same calls would have cost on the API, not a bill. They are still "
        "the right unit for asking where the effort goes.</p></div>",
        _cost_table(fig, absent),
        _read_write_figure(fig),
        "</section>",
        '<footer class="end">',
        f"Run {fig.run_id} of {_esc(fig.run_at[:10])} — <code>digest_runs</code>, <code>source_health</code>, "
        "<code>run_usage</code>, <code>run_artifacts</code>.<br/>"
        "Regenerate with <code>make anatomy</code> (<code>RUN=</code> and <code>DB=</code> optional).",
        "</footer>",
        "</div>",
    ]
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    parser.add_argument("--run", type=int, default=None, help="run id (default: latest completed run)")
    parser.add_argument("--html", type=Path, default=None, help="write the full page here")
    parser.add_argument(
        "--svg-dir",
        type=Path,
        default=None,
        help=f"write {SVG_LIGHT_NAME} and {SVG_DARK_NAME} here (implied by --readme)",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=None,
        help="splice the <picture> block into this README; writes the SVGs it points at",
    )
    parser.add_argument("--mermaid", action="store_true", help="print a mermaid flowchart of the same stages")
    parser.add_argument(
        "--code-version",
        default=None,
        help="revision of the code being drawn, for the caption (make anatomy passes the host's git sha)",
    )
    args = parser.parse_args(argv)

    if not (args.html or args.svg_dir or args.mermaid or args.readme):
        parser.error("nothing to do: pass --html, --svg-dir, --readme or --mermaid")
    if not args.db.exists():
        raise SystemExit(f"database not found: {args.db}")

    fig = load_run(args.db, args.run)
    stages = all_stages(fig)

    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(fig, stages, args.code_version), encoding="utf-8")
        print(f"wrote {args.html} (run {fig.run_id})")

    # A README pointing at SVGs nobody wrote is worse than no README change, so
    # --readme always writes the pair it links.
    svg_dir = args.svg_dir or (args.readme.parent / "docs" if args.readme else None)
    if svg_dir is not None:
        for path in write_standalone_svgs(svg_dir, fig, stages):
            print(f"wrote {path} (run {fig.run_id})")
    if args.readme:
        block = readme_picture_block(
            args.readme, svg_dir, fig, stages, html_path=args.html, code_version=args.code_version
        )
        args.readme.write_text(update_readme(args.readme, block), encoding="utf-8")
        print(f"updated {args.readme} (run {fig.run_id})")
    if args.mermaid:
        print(render_mermaid(stages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
