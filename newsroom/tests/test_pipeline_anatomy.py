"""Tests for newsroom/tools/pipeline_anatomy.py.

The page exists so the pipeline diagram cannot go stale, so these tests check the
link back to the source of truth rather than the wording: the stage list and the
models come from `orchestrate._STAGES` and the real `.claude/agents/*.md` files,
so renaming or re-modelling a stage fails here instead of quietly shipping a
diagram that lies. No model calls, no network, no production database.
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "newsroom" / "src"))
sys.path.insert(0, str(REPO_ROOT / "newsroom" / "tools"))

import cluster_extractjoin  # noqa: E402
import config  # noqa: E402
import orchestrate  # noqa: E402
import pipeline_anatomy as pa  # noqa: E402

AGENTS_DIR = REPO_ROOT / ".claude" / "agents"


# --------------------------------------------------------------------------- #
# A whole fake run, built to the real schema.
# --------------------------------------------------------------------------- #

FAKE = {
    "run_id": 901,
    "run_at": "2026-09-01 10:25:07",
    "prev_run_at": "2026-08-31 06:25:07",
    "completed_at": "2026-09-01 10:55:14",
    "git_sha": "d15e57c",
    "recipients": 42,
    "sources": 5,
    "fetched": 1234,
    "kept": 321,
}


def _fake_selections(n: int) -> dict:
    story = {
        "headline": "A committee agrees a budget after a long night of talks",
        "summary": (
            "Negotiators agreed a budget on Tuesday after eleven hours of talks, ending a standoff that had "
            "run since the spring. The deal raises spending on transport and holds every other line flat, "
            "and it now goes to a floor vote scheduled for Thursday morning."
        ),
        "why_it_matters": "The vote decides whether the transport programme is funded past December.",
        "sources": [{"article_id": "A1", "name": "Reuters", "url": "https://example.invalid/a", "bias": "centre"}],
        "reporting_varies": "",
        "cluster_id": "c1",
    }
    return {
        "must_know": [dict(story, headline=f"{story['headline']} number {i}") for i in range(n)],
        "should_know": [],
        "preheader": "A budget deal lands after eleven hours of talks.",
        "not_covered_blurb": "",
    }


@pytest.fixture
def fake_db(tmp_path: Path) -> Path:
    """A database with the tables and columns the real one has, one run in it."""
    db = tmp_path / "fake.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE digest_runs (id INTEGER PRIMARY KEY, run_at TEXT, articles_kept INTEGER,
            articles_emailed INTEGER, completed_at TEXT, git_sha TEXT, status TEXT, error TEXT);
        CREATE TABLE source_health (id INTEGER PRIMARY KEY, source_id TEXT, success INTEGER,
            error_message TEXT, recorded_at TEXT, articles_fetched INTEGER, articles_kept INTEGER, run_id INTEGER);
        CREATE TABLE run_usage (id INTEGER PRIMARY KEY, run_id INTEGER, subagent TEXT, model TEXT,
            input_tokens INTEGER, output_tokens INTEGER, cache_write_tokens INTEGER, cache_read_tokens INTEGER,
            api_cost_usd REAL, recorded_at TEXT, duration_ms INTEGER, thinking TEXT, effort TEXT);
        CREATE TABLE run_artifacts (id INTEGER PRIMARY KEY, run_id INTEGER, artifact_name TEXT,
            content TEXT, created_at TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO digest_runs (id, run_at, articles_kept, articles_emailed, completed_at, git_sha, status) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            (900, FAKE["prev_run_at"], 300, 40, "2026-08-31 06:50:00", "aaaaaaa", "completed"),
            (
                FAKE["run_id"],
                FAKE["run_at"],
                FAKE["kept"],
                FAKE["recipients"],
                FAKE["completed_at"],
                FAKE["git_sha"],
                "completed",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO source_health (source_id, success, articles_fetched, articles_kept, run_id) VALUES (?,?,?,?,?)",
        [(f"src_{i}", 1 if i else 0, FAKE["fetched"] // 5, FAKE["kept"] // 5, FAKE["run_id"]) for i in range(5)],
    )
    conn.executemany(
        "INSERT INTO run_usage (run_id, subagent, model, output_tokens, cache_read_tokens, api_cost_usd, "
        "duration_ms, thinking) VALUES (?,?,?,?,?,?,?,?)",
        [
            (FAKE["run_id"], "cluster", "claude-sonnet-4-6", 40000, 2000, 0.90, 200000, "disabled"),
            (FAKE["run_id"], "recap", "claude-haiku-4-5", 700, 27000, 0.06, 10000, "disabled"),
            (FAKE["run_id"], "coherence", "claude-sonnet-5", 33000, 700000, 1.10, 300000, "adaptive"),
            (FAKE["run_id"], "thread_synthesis", "claude-sonnet-4-6", 3000, 0, 0.10, 80000, "disabled"),
            (FAKE["run_id"], "thread_audit", "claude-sonnet-4-6", 1000, 0, 0.04, 20000, "disabled"),
        ],
    )
    conn.executemany(
        "INSERT INTO run_artifacts (run_id, artifact_name, content) VALUES (?,?,?)",
        [
            (
                FAKE["run_id"],
                "clusters.json",
                json.dumps({"clusters": [{"story": f"s{i}", "article_ids": ["A1"]} for i in range(88)]}),
            ),
            (
                FAKE["run_id"],
                "selected.json",
                json.dumps({"must_know": [{"cluster_index": i} for i in range(4)], "should_know": []}),
            ),
            (FAKE["run_id"], "selections.json", json.dumps(_fake_selections(4))),
            (
                FAKE["run_id"],
                "coherence_report.json",
                json.dumps({"results": [{"headline": f"h{i}", "pass": i > 0} for i in range(4)]}),
            ),
            (FAKE["run_id"], "article_index.json", json.dumps({f"A{i}": {"url": "u"} for i in range(311)})),
            (FAKE["run_id"], "write_branches.json", json.dumps([{"name": f"s{i:02d}"} for i in range(4)])),
        ],
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def figures(fake_db: Path) -> pa.RunFigures:
    return pa.load_run(fake_db, FAKE["run_id"])


# --------------------------------------------------------------------------- #
# The shape comes from the orchestrator and the agent specs, not from here.
# --------------------------------------------------------------------------- #


def test_curation_stages_follow_orchestrate_stages_in_order(figures):
    """Every `_STAGES` label is drawn, in the orchestrator's order."""
    keys = [s.key for s in pa.curation_stages(figures)]
    expected = [label for label, *_ in orchestrate._STAGES]
    assert [k for k in keys if k in expected] == expected


def test_every_stage_carries_the_model_its_spec_declares(figures):
    """Re-model a stage in its .md and this fails, rather than the page lying."""
    by_key = {s.key: s for s in pa.curation_stages(figures)}
    for label, spec_filename, _output, _validate in orchestrate._STAGES:
        spec = orchestrate.parse_agent_spec(AGENTS_DIR / spec_filename)
        # CLUSTER is the only stage whose model comes from config, not frontmatter:
        # orchestrate routes it to cluster_extractjoin with config.CLUSTER_EXTRACT_MODEL.
        expected = config.CLUSTER_EXTRACT_MODEL if label == "cluster" else spec.model
        assert by_key[label].model == expected, label


def test_a_changed_spec_model_changes_the_page(figures, tmp_path, monkeypatch):
    """Negative control on the model link: point the tool at a doctored spec dir
    and the drawn model follows, so the assertions above are not comparing two
    reads of the same constant."""
    doctored = tmp_path / "agents"
    doctored.mkdir()
    for spec_file in AGENTS_DIR.glob("*.md"):
        body = spec_file.read_text(encoding="utf-8")
        (doctored / spec_file.name).write_text(body.replace("claude-sonnet-4-6", "claude-opus-9-9"), encoding="utf-8")
    monkeypatch.setattr(pa, "AGENTS_DIR", doctored)
    monkeypatch.setattr(config, "CLUSTER_EXTRACT_MODEL", "claude-opus-9-9")
    stages = pa.curation_stages(figures)
    assert {s.model for s in stages if s.model} == {"claude-opus-9-9", "claude-sonnet-5", "claude-haiku-4-5"}
    assert "Opus 9.9" in pa.render_mermaid(pa.all_stages(figures))


def test_output_filenames_match_the_orchestrator(figures):
    by_key = {s.key: s for s in pa.curation_stages(figures)}
    for label, _spec, output_filename, _validate in orchestrate._STAGES:
        assert by_key[label].output == output_filename


def test_captions_are_the_agents_own_descriptions(figures):
    by_key = {s.key: s for s in pa.curation_stages(figures)}
    for label, spec_filename, _output, _validate in orchestrate._STAGES:
        spec = orchestrate.parse_agent_spec(AGENTS_DIR / spec_filename)
        assert spec.description, f"{spec_filename} has no description to draw"
        assert by_key[label].caption in spec.description


def test_fanouts_use_the_orchestrators_own_bounds(figures):
    by_key = {s.key: s for s in pa.curation_stages(figures)}
    assert by_key["cluster"].fanout.concurrency == cluster_extractjoin._EXTRACT_CONCURRENCY
    assert str(cluster_extractjoin._EXTRACT_BATCH) in by_key["cluster"].fanout.per_call
    assert by_key["write"].fanout.concurrency == orchestrate._WRITE_BRANCH_CONCURRENCY
    assert by_key["write"].fanout.budget_usd == orchestrate._WRITE_BRANCH_BUDGET_USD


def test_the_write_phases_extra_stages_are_drawn(figures):
    """run_write_phase fans out, fans in, then runs the preheader agent."""
    keys = [s.key for s in pa.curation_stages(figures)]
    assert keys.index("write") < keys.index("fanin") < keys.index("preheader") < keys.index("coherence")
    preheader = next(s for s in pa.curation_stages(figures) if s.key == "preheader")
    assert preheader.model == orchestrate.parse_agent_spec(AGENTS_DIR / "preheader.md").model
    assert preheader.output == orchestrate._PREHEADER_NAME


def test_repair_phase_is_drawn_as_a_conditional_stage(figures):
    repair = next(s for s in pa.curation_stages(figures) if s.key == "repair")
    assert repair.conditional and repair.edge_label
    assert repair.model == orchestrate.parse_agent_spec(AGENTS_DIR / "repair.md").model


def test_a_new_stage_is_drawn_without_touching_the_drawing(figures, monkeypatch):
    """The point of the tool: adding to `_STAGES` adds a box."""
    added = (*orchestrate._STAGES, ("tighten", "recap.md", "extra.json", lambda _p: None))
    monkeypatch.setattr(orchestrate, "_STAGES", added)
    stages = pa.all_stages(figures)
    assert [s.key for s in stages].count("tighten") == 1
    diagram = pa.build_diagram(stages)
    assert [b.key for b in diagram.boxes].count("tighten") == 1
    assert pa.out_of_canvas(diagram) == []
    assert "tighten[" in pa.render_mermaid(stages)


@pytest.mark.parametrize("colliding", ["render", "assemble", "join", "preheader", "recap"])
def test_a_new_stage_whose_name_collides_still_gets_its_own_node(figures, monkeypatch, colliding):
    """A `_STAGES` label that matches one of this tool's own step keys must not merge
    into that step's node: mermaid unifies same-id nodes, which would relabel the
    other lane's box and can close a cycle back into assembly."""
    added = (*orchestrate._STAGES, (colliding, "recap.md", "extra.json", lambda _p: None))
    monkeypatch.setattr(orchestrate, "_STAGES", added)
    stages = pa.all_stages(figures)
    assert [s.key for s in stages].count(colliding) == 2

    ids = pa.unique_mermaid_ids(stages)
    assert len(set(ids.values())) == len(stages)

    block = pa.render_mermaid(stages)
    declared = re.findall(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)\[", block)
    assert len(declared) == len(set(declared)), sorted(declared)

    # The drawing keeps both boxes too.
    assert [b.key for b in pa.build_diagram(stages).boxes].count(colliding) == 2


# --------------------------------------------------------------------------- #
# The drawing.
# --------------------------------------------------------------------------- #


def test_no_label_outgrows_its_box(figures):
    diagram = pa.build_diagram(pa.all_stages(figures))
    overflows = [(box.key, line.text) for box in diagram.boxes for line in box.overflowing()]
    assert overflows == []


def test_no_box_falls_outside_the_canvas(figures):
    """`measure` guarantees a label fits its box; nothing guarantees the box fits the
    fixed viewBox, so that is the assertion with teeth."""
    assert pa.out_of_canvas(pa.build_diagram(pa.all_stages(figures))) == []


def test_the_canvas_check_can_actually_fail(figures):
    """Negative control: a box wider than the lane is reported, not silently clipped."""
    diagram = pa.build_diagram(pa.all_stages(figures))
    assert pa.out_of_canvas(diagram) == []
    diagram.boxes[0].w = pa.CANVAS_W * 2
    assert pa.out_of_canvas(diagram) == [diagram.boxes[0].key]


def test_the_fit_check_can_actually_fail():
    """Negative control: the fit check is not vacuously true."""
    box = pa.NodeBox(
        key="probe",
        lines=(pa.TextLine("a label far too long for this box", "#000", size=12),),
        accent="#000",
        fill="#fff",
    )
    box.measure()
    assert box.overflowing() == []
    box.w = pa.MIN_BOX_W
    assert [line.text for line in box.overflowing()] == ["a label far too long for this box"]


def test_the_assembly_row_reads_left_to_right(figures):
    """Sean found a right-to-left bottom row confusing; each assembly box starts
    to the right of the one before it."""
    diagram = pa.build_diagram(pa.all_stages(figures))
    keys = [s.key for s in pa.assembly_stages(figures)]
    boxes = [b for b in diagram.boxes if b.key in keys]
    boxes.sort(key=lambda b: keys.index(b.key))
    xs = [b.x for b in boxes]
    assert xs == sorted(xs), xs


def test_fanout_stages_draw_as_a_stack(figures):
    diagram = pa.build_diagram(pa.all_stages(figures))
    stacked = {b.key for b in diagram.boxes if b.stack > 1}
    assert stacked == {s.key for s in pa.all_stages(figures) if s.fanout}


def test_svg_is_well_formed_and_carries_the_runs_numbers(figures):
    import xml.etree.ElementTree as ET

    stages = pa.all_stages(figures)
    svg = pa.build_diagram(stages).svg(pa.diagram_aria(figures, stages))
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    text = "".join(node.text or "" for node in root.iter())
    assert f"{FAKE['fetched'] // 5 * 5:,} items" in text
    assert "88" in text  # clusters
    assert "×4" in text  # write branches


# --------------------------------------------------------------------------- #
# Mermaid.
# --------------------------------------------------------------------------- #


def _declaration_positions(block: str, stages) -> list[int]:
    """Where each stage's node is DECLARED (`id[`), not merely mentioned.

    A fan-out stage is declared as several nodes sharing the stage id as a prefix
    (`write1[`, `write2[`, `writen[`), so the suffix is optional.
    """
    positions = []
    for stage in stages:
        match = re.search(rf"(?<![A-Za-z0-9_]){re.escape(pa.mermaid_id(stage.key))}(?:\d+|n)?\[", block)
        assert match, f"{stage.key} is never declared as a node:\n{block}"
        positions.append(match.start())
    return positions


def test_mermaid_contains_every_stage_in_order(figures):
    stages = pa.all_stages(figures)
    block = pa.render_mermaid(stages)
    positions = _declaration_positions(block, stages)
    assert positions == sorted(positions), block


def test_mermaid_has_the_three_lane_subgraphs(figures):
    block = pa.render_mermaid(pa.all_stages(figures))
    assert block.startswith("```mermaid\nflowchart TB")
    for lane in (pa.INTAKE_LANE, pa.CURATION_LANE, pa.ASSEMBLY_LANE):
        assert "subgraph " in block and f"[{lane}]" in block
    assert block.count("direction LR") == 2
    assert block.count("direction TB") == 1
    assert block.count("end") >= 3


def test_mermaid_draws_the_write_fanout_as_a_split_and_join(figures):
    block = pa.render_mermaid(pa.all_stages(figures))
    assert "write1 & write2 & writen --> fanin" in block


def test_mermaid_cross_lane_edges_sit_outside_the_subgraphs(figures):
    block = pa.render_mermaid(pa.all_stages(figures))
    tail = block[block.rindex("  end") :]
    assert "ids --> cluster" in tail
    assert "-- passed --> assemble" in tail
    assert "repair --> assemble" in tail


def test_mermaid_carries_configuration_but_not_one_runs_figures(figures):
    """The README block outlives the run, so it must not state last Tuesday's
    article count as if it were the pipeline's shape."""
    stages = pa.all_stages(figures)
    block = pa.render_mermaid(stages)
    run_scoped = [s.run_meta for s in stages if s.run_meta]
    assert run_scoped, "fixture should produce run-scoped figures to exclude"
    for value in run_scoped:
        assert value not in block, value
    # Configuration still shows.
    assert str(cluster_extractjoin._EXTRACT_BATCH) in block
    assert f"{config.CLUSTER_JOIN_THRESHOLD:.2f}" in block
    assert "WRITE story n" in block


def test_the_page_does_carry_the_runs_figures(figures):
    stages = pa.all_stages(figures)
    page = pa.render_html(figures, stages)
    for value in (s.run_meta for s in stages if s.run_meta):
        assert value in page, value


def test_mermaid_ids_are_github_safe(figures):
    block = pa.render_mermaid(pa.all_stages(figures))
    for stage in pa.all_stages(figures):
        node_id = pa.mermaid_id(stage.key)
        assert node_id.lower() not in pa.MERMAID_RESERVED
        assert node_id.replace("_", "").isalnum()
    for line in block.splitlines():
        # An unquoted label may not carry a bracket or paren; mermaid parses those.
        for chunk in line.split("[")[1:]:
            label = chunk.split("]")[0]
            if not label.startswith('"'):
                assert not set(label) & set("()[]{}\"'"), line


def test_mermaid_quotes_labels_that_need_it(figures):
    block = pa.render_mermaid(pa.all_stages(figures))
    # SELECT's own description names the tiers in parentheses.
    assert '["SELECT' in block


def test_mermaid_reserved_ids_are_renamed():
    assert pa.mermaid_id("end") == "end_"
    assert pa.mermaid_id("graph") == "graph_"
    assert pa.mermaid_id("write") == "write"
    assert pa.mermaid_id("IDs → real URLs") == "IDs___real_URLs"


# --------------------------------------------------------------------------- #
# README splice.
# --------------------------------------------------------------------------- #


def test_readme_block_is_replaced_between_the_markers(tmp_path, figures):
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nbefore\n\n{pa.README_BEGIN}\nOLD CONTENT\n{pa.README_END}\n\nafter\n")
    block = pa.readme_picture_block(readme, tmp_path / "docs", figures, pa.all_stages(figures))
    updated = pa.update_readme(readme, block)
    assert "OLD CONTENT" not in updated
    assert updated.startswith("# Title\n\nbefore\n")
    assert updated.endswith("\n\nafter\n")


def test_readme_block_is_a_theme_aware_picture(tmp_path, figures):
    readme = tmp_path / "README.md"
    readme.write_text(f"{pa.README_BEGIN}\n{pa.README_END}\n")
    block = pa.readme_picture_block(readme, tmp_path / "docs", figures, pa.all_stages(figures))
    assert '<source media="(prefers-color-scheme: dark)" srcset="docs/pipeline-anatomy-dark.svg">' in block
    assert '<img src="docs/pipeline-anatomy.svg" alt="' in block
    assert "```mermaid" not in block
    assert f"run {FAKE['run_id']}" in block


def test_readme_picture_paths_are_relative_to_the_readme(tmp_path, figures):
    nested = tmp_path / "sub"
    nested.mkdir()
    readme = nested / "README.md"
    readme.write_text(f"{pa.README_BEGIN}\n{pa.README_END}\n")
    block = pa.readme_picture_block(readme, nested / "docs", figures, pa.all_stages(figures))
    assert 'srcset="docs/pipeline-anatomy-dark.svg"' in block
    assert str(tmp_path) not in block


# --------------------------------------------------------------------------- #
# The standalone SVGs GitHub renders.
# --------------------------------------------------------------------------- #


def _standalone(figures, palette):
    stages = pa.all_stages(figures)
    return pa.build_diagram(stages, palette).svg(pa.diagram_aria(figures, stages), standalone=True)


@pytest.mark.parametrize("palette", [pa.LIGHT_PALETTE, pa.DARK_PALETTE])
def test_standalone_svg_is_self_contained(figures, palette):
    """GitHub's image proxy serves these as separate documents with no stylesheet,
    so a CSS variable or a currentColor renders as nothing."""
    import xml.etree.ElementTree as ET

    svg = _standalone(figures, palette)
    assert "var(--" not in svg
    assert "currentColor" not in svg
    assert "<link" not in svg and "@import" not in svg and "<style" not in svg
    root = ET.fromstring(svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("width") and root.get("height")
    assert root.find("{http://www.w3.org/2000/svg}title") is not None
    # An explicit background rect, first, covering the whole canvas.
    first = root.find("{http://www.w3.org/2000/svg}rect")
    assert first is not None and first.get("fill") == palette.background
    assert first.get("width") == root.get("width")


@pytest.mark.parametrize("palette", [pa.LIGHT_PALETTE, pa.DARK_PALETTE])
def test_standalone_svg_names_no_webfont_it_cannot_load(figures, palette):
    svg = _standalone(figures, palette)
    assert "fonts.googleapis.com" not in svg
    for stack in (pa.SANS, pa.MONO):
        assert stack in svg
        # Every stack ends in a generic family, so the fallback is defined.
        assert stack.rstrip().endswith(("sans-serif", "monospace"))


@pytest.mark.parametrize("palette", [pa.LIGHT_PALETTE, pa.DARK_PALETTE])
def test_no_label_outgrows_its_box_in_either_theme(figures, palette):
    diagram = pa.build_diagram(pa.all_stages(figures), palette)
    assert [(b.key, line.text) for b in diagram.boxes for line in b.overflowing()] == []


def test_the_two_themes_are_the_same_drawing(figures):
    """Only colours differ: a layout that moved between themes would mean the
    README's light and dark images disagree about the pipeline."""
    stages = pa.all_stages(figures)
    light = pa.build_diagram(stages, pa.LIGHT_PALETTE)
    dark = pa.build_diagram(stages, pa.DARK_PALETTE)
    geometry = [(b.key, b.x, b.y, b.w, b.h, b.stack) for b in light.boxes]
    assert geometry == [(b.key, b.x, b.y, b.w, b.h, b.stack) for b in dark.boxes]
    assert light.colours != dark.colours


def test_write_standalone_svgs_writes_both_files(tmp_path, figures):
    light, dark = pa.write_standalone_svgs(tmp_path / "out", figures, pa.all_stages(figures))
    assert light.name == pa.SVG_LIGHT_NAME and dark.name == pa.SVG_DARK_NAME
    assert pa.LIGHT_PALETTE.background in light.read_text()
    assert pa.DARK_PALETTE.background in dark.read_text()


def test_readme_without_markers_fails_loudly(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n\nno markers here\n")
    with pytest.raises(SystemExit):
        pa.update_readme(readme, "```mermaid\nflowchart TB\n```")


def test_the_real_readme_has_the_markers():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert text.count(pa.README_BEGIN) == 1
    assert text.count(pa.README_END) == 1
    assert text.index(pa.README_BEGIN) < text.index(pa.README_END)


# --------------------------------------------------------------------------- #
# The whole page, off a fake database.
# --------------------------------------------------------------------------- #


def test_load_run_reads_the_runs_figures(figures):
    assert figures.run_id == FAKE["run_id"]
    assert figures.articles_fetched == FAKE["fetched"] // 5 * 5
    assert figures.feeds_attempted == 5
    assert figures.feeds_ok == 4
    assert figures.articles_indexed == 311
    assert figures.clusters == 88
    assert figures.stories_shipped == 4
    assert figures.coherence_flagged == 1
    assert figures.write_branches == 4
    assert figures.write_branches_source == "write_branches.json"
    assert figures.recipients == FAKE["recipients"]
    assert figures.window_hours == pytest.approx(28.0, abs=0.01)
    assert figures.grader_checks and figures.grader_checks > 0


def test_thread_rows_collapse_into_one_line(figures):
    labels = {row.subagent for row in figures.usage}
    assert "threads" in labels
    assert not any(label.startswith("thread_") for label in labels)
    threads = next(row for row in figures.usage if row.subagent == "threads")
    assert threads.calls == 2
    assert threads.cost_usd == pytest.approx(0.14)


def test_write_branch_count_falls_back_to_the_selected_stories(fake_db):
    conn = sqlite3.connect(fake_db)
    conn.execute("DELETE FROM run_artifacts WHERE artifact_name = 'write_branches.json'")
    conn.commit()
    conn.close()
    figures = pa.load_run(fake_db, FAKE["run_id"])
    assert figures.write_branches == 4
    assert figures.write_branches_source == "selected.json"


def test_render_html_is_parseable_and_carries_the_runs_numbers(figures):
    import xml.etree.ElementTree as ET

    page = pa.render_html(figures, pa.all_stages(figures))
    ET.fromstring("<root>" + page[page.index('<div class="wrap">') :] + "</root>")
    assert "<title>Digest Pipeline Anatomy</title>" in page
    assert f"Run {FAKE['run_id']}" in page
    assert FAKE["git_sha"] in page
    assert f"{FAKE['fetched'] // 5 * 5:,}" in page
    assert f"{FAKE['recipients']} recipients" in page
    assert "API-equivalent" in page
    assert "$2.20" in page  # the fake run's total cost


# --------------------------------------------------------------------------- #
# The run and the code can disagree; the caption has to say so.
# --------------------------------------------------------------------------- #


def test_caption_names_both_the_code_and_the_run(figures):
    caption = pa.provenance_caption(figures)
    assert caption.startswith("Stages and models as ")
    assert f"Run figures from run {FAKE['run_id']} ({FAKE['run_at'][:10]})." in caption


def test_caption_uses_the_code_version_it_is_handed(figures):
    """`make anatomy` resolves the sha on the host: the CI image has no git binary and
    a worktree's .git points outside the container."""
    assert "as of deadbee." in pa.provenance_caption(figures, "deadbee")


def test_caption_falls_back_to_the_generation_date(figures, monkeypatch):
    monkeypatch.setattr(pa, "repo_head_sha", lambda: None)
    assert "as generated on " in pa.provenance_caption(figures)


def test_the_code_version_reaches_both_outputs(tmp_path, fake_db):
    readme = tmp_path / "README.md"
    readme.write_text(f"{pa.README_BEGIN}\n{pa.README_END}\n")
    out = tmp_path / "anatomy.html"
    pa.main(
        [
            "--db",
            str(fake_db),
            "--run",
            str(FAKE["run_id"]),
            "--html",
            str(out),
            "--readme",
            str(readme),
            "--code-version",
            "deadbee",
        ]
    )
    assert "as of deadbee." in out.read_text()
    assert "as of deadbee." in readme.read_text()


def test_caption_flags_a_run_that_wrote_every_story_in_one_call(fake_db):
    """A run with no write_branches.json predates the per-story fan-out, so the
    drawing's WRITE is not the WRITE that produced these figures."""
    conn = sqlite3.connect(fake_db)
    conn.execute("DELETE FROM run_artifacts WHERE artifact_name = 'write_branches.json'")
    conn.commit()
    conn.close()
    figures = pa.load_run(fake_db, FAKE["run_id"])
    caption = pa.provenance_caption(figures)
    assert "wrote all stories in one call" in caption
    assert pa.PER_STORY_WRITE_FROM in caption


def test_caption_says_nothing_extra_for_a_per_story_run(figures):
    """Control: the sentence disappears by itself once such a run exists."""
    assert figures.write_branches_source == "write_branches.json"
    assert "one call" not in pa.provenance_caption(figures)


def test_the_caption_reaches_both_the_page_and_the_readme(tmp_path, figures):
    stages = pa.all_stages(figures)
    caption = pa.provenance_caption(figures)
    assert caption in pa.render_html(figures, stages)
    readme = tmp_path / "README.md"
    readme.write_text(f"{pa.README_BEGIN}\n{pa.README_END}\n")
    assert caption in pa.readme_picture_block(readme, tmp_path / "docs", figures, stages)


def test_a_stage_with_no_usage_row_still_gets_a_cost_row(figures):
    """PREHEADER exists in the code but recorded nothing on this run; a total that
    silently omits it reads as the whole pipeline."""
    stages = pa.all_stages(figures)
    absent = pa.stages_without_usage(figures, stages)
    assert "preheader" in absent and "write" in absent
    page = pa.render_html(figures, stages)
    assert f"did not run in run {FAKE['run_id']}" in page
    for key in absent:
        assert f'<td class="stage-name">{key}</td>' in page


def test_stages_without_usage_ignores_rows_with_no_stage(figures):
    """`threads` and `repair_recheck` are usage rows with no stage of their own; they
    are not missing stages."""
    absent = pa.stages_without_usage(figures, pa.all_stages(figures))
    assert "threads" not in absent and "repair_recheck" not in absent


def test_the_cost_table_does_not_call_usage_rows_calls(figures):
    """cluster_extractjoin merges its batches into ONE run_usage row, so a row count
    is not a call count and must not be labelled as one."""
    page = pa.render_html(figures, pa.all_stages(figures))
    assert "usage rows)" in page
    assert " calls)" not in page


def test_the_page_carries_no_narrative_cards_or_watchlist(figures):
    page = pa.render_html(figures, pa.all_stages(figures))
    for banned in ("why-card", "Why the stages run in this order", "What to watch"):
        assert banned not in page


def test_main_writes_every_output(tmp_path, fake_db):
    readme = tmp_path / "README.md"
    readme.write_text(f"a\n{pa.README_BEGIN}\nold\n{pa.README_END}\nb\n")
    out = tmp_path / "nested" / "anatomy.html"
    code = pa.main(["--db", str(fake_db), "--run", str(FAKE["run_id"]), "--html", str(out), "--readme", str(readme)])
    assert code == 0
    assert out.exists() and "Digest Pipeline Anatomy" in out.read_text()
    assert "<picture>" in readme.read_text()


def test_readme_never_points_at_svgs_that_were_not_written(tmp_path, fake_db):
    """--readme implies the SVG pair; a README linking files nobody wrote is worse
    than no README change."""
    readme = tmp_path / "README.md"
    readme.write_text(f"{pa.README_BEGIN}\n{pa.README_END}\n")
    pa.main(["--db", str(fake_db), "--run", str(FAKE["run_id"]), "--readme", str(readme)])
    body = readme.read_text()
    for name in (pa.SVG_LIGHT_NAME, pa.SVG_DARK_NAME):
        assert name in body
        assert (tmp_path / "docs" / name).exists()


def test_main_mermaid_is_not_written_into_the_readme(tmp_path, fake_db, capsys):
    readme = tmp_path / "README.md"
    readme.write_text(f"{pa.README_BEGIN}\n{pa.README_END}\n")
    pa.main(["--db", str(fake_db), "--run", str(FAKE["run_id"]), "--readme", str(readme), "--mermaid"])
    assert "flowchart TB" in capsys.readouterr().out
    assert "mermaid" not in readme.read_text()


def test_main_refuses_a_missing_database(tmp_path):
    with pytest.raises(SystemExit):
        pa.main(["--db", str(tmp_path / "nope.db"), "--mermaid"])


def test_main_refuses_an_unknown_run(fake_db):
    with pytest.raises(SystemExit):
        pa.main(["--db", str(fake_db), "--run", "99999", "--mermaid"])
