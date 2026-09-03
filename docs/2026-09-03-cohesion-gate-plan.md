# Cohesion Gate (Phase C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One reasoning step over the ~17 selected clusters, after SELECT and before WRITE, that says which of a cluster's articles are the story's event and which are strays, so WRITE never sees the helipad next to the AI-safety lawsuit.

**Architecture:** A new module `newsroom/src/cohesion.py` (same layer as `cluster_extractjoin`: imports `claude_cli`, `usage`) makes one single-turn, tool-free judgement call over the selected clusters (batched 12 per call, titles plus a 200-character summary snippet, opaque ids only) and writes `cluster_cohesion.json`. `write_fanout.build_branches` (a leaf module) reads that artifact if present and narrows a branch's evidence to the dominant event. Everything is fail-open: an invalid or missing verdict leaves the branch as it is today. The gate only partitions; it never merges clusters or moves an article between stories.

**Tech Stack:** Python 3.14, Claude Agent SDK via `claude_cli.run_agent` (tools=[], max_turns=1, as the CLUSTER extract call), pytest in Docker, `bin/eval-cohesion` for the replay measurement.

**Spec:** Phase C of `docs/2026-09-03-stage-invocation-rewrite-plan.md`; the mechanism and the "thin gate on the selected clusters, pre-WRITE" recommendation are in `docs/2026-08-01-cluster-junk-drawer-findings.md` §3 and §5; the rubric ("event identity, not genre") was decided 2026-09-01 (memory `project_2026_08_31_junk_citations_and_cluster_gate`).

## Global Constraints

- Off by default: `COHESION_ENABLED` (env, default `false`), same parsing as `FULLTEXT_ENABLED`. Prod turns it on by terraform after the measurement, not by this plan.
- Fail-open at every layer: a failed call, an unparseable reply, a partition that is not exactly the cluster's ids, or a dominant group that drops every one of SELECT's cited ids, each leave that cluster untouched and are recorded in the artifact with a reason.
- Never merges. A verdict may only split one cluster into groups; the branch gets the largest group.
- The model never sees URLs, source names or bias: ids, titles, snippets only (same rule as every stage).
- One `run_usage` row, subagent `cohesion`, model `COHESION_MODEL` (default `claude-sonnet-4-6`, thinking per `cluster_extractjoin._thinking_for`).
- The measurement's metric is agreement with independent labels and the known run-285 cases, never "fewer multi-event clusters" (the 2026-09-01 embedding gate's metric was maximised by fragmentation).
- TDD per task; reviewer pass per commit; no deploy from this plan.

---

## Design

### The judgement

Input per selected story: its cluster's articles (`cluster_index` resolved against `clusters.json`, unioned with SELECT's `article_ids`, the same set `build_branches` gives WRITE today). Prompt: for each GROUP, the article ids with title and snippet. Output per group: `events`, a list of lists of ids that partitions the group, each inner list one event. Rubric, in the system prompt:

- Different angles, reactions, analysis, follow-ups or later developments of ONE underlying event are one event.
- Events that merely share a place, a country, a person, an organisation or a topic are different events.
- Judge from the titles and snippets given; do not guess at content you cannot see.
- Every id exactly once.

The dominant event is the largest group; ties go to the group holding the earliest of SELECT's `article_ids` (SELECT's first citation is its representative article). Strays are every other group's ids.

### The artifact: `cluster_cohesion.json`

```json
{
  "model": "claude-sonnet-4-6",
  "outcome": "completed",
  "judged": 17, "split": 4, "strays_removed": 7,
  "verdicts": [
    {"cluster_index": 33, "article_ids": ["A44", "A70", "A666"],
     "events": [["A44"], ["A70"], ["A666"]], "dominant": ["A44"], "strays": ["A70", "A666"],
     "applied": true, "reason": null}
  ]
}
```

`outcome` is `completed`, `failed` (the call or the parse failed; no verdict applied) or `skipped` (flag off). `applied: false` carries a `reason`: `"one event"`, `"not a partition"`, `"dominant drops every cited id"`.

### Where it applies

`write_fanout.build_branches` today: `context = cluster ids; context_ids = context ∪ story_ids`. With a verdict for the story's `cluster_index` that is `applied: true`: `context = dominant`, and the story's own `article_ids` in the branch's `selected.json` are filtered to `dominant` (WRITE cites from that list). `Branch.context_article_ids` records what WRITE actually saw, so `write_branches.json` stays honest; a new `Branch.strays_removed` count reaches the artifact.

### Measurement (Task 4)

Replay runs 284 and 285 (34 selected clusters). Before the judge runs, I label each cluster blind from titles: number of events and which ids are strays. Then: per-cluster agreement (events count exact; stray set Jaccard), the five known run-285 cases (helipad + WSJ opinion, OpenAI 30 lawsuits, SCO Mongolia + US envoys, EU ministers + NI reunification, Xi + Trump AI remark), over-splits (a same-event pair separated, by my labels), cost and time. Labels are one reader's, not ground truth; the doc says so.

---

### Task 1: `cohesion.py` — prompt, parse, partition, artifact

**Files:**
- Create: `newsroom/src/cohesion.py`
- Test: `newsroom/tests/test_cohesion.py`

**Interfaces (produces):**
- `COHESION_ARTIFACT = "cluster_cohesion.json"`
- `build_judge_prompt(groups: list[dict], articles: dict[str, dict]) -> str` — `groups` items `{"group": int, "article_ids": [str]}`; `articles` maps id to `{"title", "summary"}`.
- `parse_verdicts(text: str) -> dict[int, list[list[str]]]` — group number to events; `{}` when unparseable.
- `validate_partition(article_ids: list[str], events: list[list[str]]) -> list[list[str]] | None` — the events largest-first when they partition `article_ids` exactly, else `None`.
- `judge_selected(selected: dict, clusters: list[dict], verdicts_by_group: dict[int, list[list[str]]], groups: list[dict]) -> dict` — the artifact document (no I/O).
- `async run_cohesion_stage(claude_input_dir: Path, *, model: str, cwd) -> dict` — writes the artifact, returns a `run_usage` row.

- [ ] **Step 1: Failing tests**

```python
# newsroom/tests/test_cohesion.py
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import cohesion
from claude_cli import StageResult

ARTS = {"A1": {"title": "Trump may reveal secret AI rules", "summary": "Ars on the lawsuit"},
        "A2": {"title": "A look at Trump's new helipad", "summary": "BBC"},
        "A3": {"title": "Opinion: Defunding the military", "summary": "WSJ"}}

def test_prompt_lists_ids_titles_and_snippets_and_nothing_else():
    text = cohesion.build_judge_prompt([{"group": 0, "article_ids": ["A1", "A2"]}], ARTS)
    assert "GROUP 0" in text and "A1" in text and "helipad" in text
    assert "http" not in text and "bbc_world" not in text

def test_parse_reads_events_per_group_and_tolerates_fences():
    text = 'ok\n```json\n{"results": [{"group": 0, "events": [["A1"], ["A2", "A3"]]}]}\n```'
    assert cohesion.parse_verdicts(text) == {0: [["A1"], ["A2", "A3"]]}
    assert cohesion.parse_verdicts("no json") == {}

def test_partition_must_cover_exactly_and_orders_largest_first():
    assert cohesion.validate_partition(["A1", "A2", "A3"], [["A1"], ["A2", "A3"]]) == [["A2", "A3"], ["A1"]]
    assert cohesion.validate_partition(["A1", "A2", "A3"], [["A1"], ["A2"]]) is None          # missing
    assert cohesion.validate_partition(["A1", "A2"], [["A1"], ["A2", "A1"]]) is None          # duplicate
    assert cohesion.validate_partition(["A1", "A2"], [["A1"], ["A2", "A9"]]) is None          # unknown

def test_verdict_applies_dominant_and_records_strays():
    selected = {"must_know": [], "should_know": [{"cluster_index": 33, "article_ids": ["A1", "A2", "A3"]}]}
    clusters = [{"story": "x", "article_ids": []}] * 33 + [{"story": "ai rules", "article_ids": ["A1", "A2", "A3"]}]
    groups = [{"group": 0, "cluster_index": 33, "article_ids": ["A1", "A2", "A3"]}]
    doc = cohesion.judge_selected(selected, clusters, {0: [["A1"], ["A2"], ["A3"]]}, groups)
    v = doc["verdicts"][0]
    assert v["applied"] is True and v["dominant"] == ["A1"] and sorted(v["strays"]) == ["A2", "A3"]
    assert doc["split"] == 1 and doc["strays_removed"] == 2

def test_ties_go_to_the_group_holding_selects_first_citation():
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A3", "A1"]}], "should_know": []}
    clusters = [{"story": "s", "article_ids": ["A1", "A2", "A3"]}]
    groups = [{"group": 0, "cluster_index": 0, "article_ids": ["A1", "A2", "A3"]}]
    doc = cohesion.judge_selected(selected, clusters, {0: [["A1"], ["A2"], ["A3"]]}, groups)
    assert doc["verdicts"][0]["dominant"] == ["A3"]

def test_one_event_and_bad_partitions_are_not_applied():
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A1"]}], "should_know": []}
    clusters = [{"story": "s", "article_ids": ["A1", "A2"]}]
    groups = [{"group": 0, "cluster_index": 0, "article_ids": ["A1", "A2"]}]
    one = cohesion.judge_selected(selected, clusters, {0: [["A1", "A2"]]}, groups)["verdicts"][0]
    assert one["applied"] is False and one["reason"] == "one event"
    bad = cohesion.judge_selected(selected, clusters, {0: [["A1"]]}, groups)["verdicts"][0]
    assert bad["applied"] is False and bad["reason"] == "not a partition"
    gone = cohesion.judge_selected(selected, clusters, {0: [["A2"], ["A1"]]}, groups)["verdicts"][0]
    # A1 is SELECT's only citation and lands alone; the tie rule keeps it dominant, so this applies.
    assert gone["applied"] is True and gone["dominant"] == ["A1"]

def test_a_dominant_that_drops_every_cited_id_is_refused():
    selected = {"must_know": [{"cluster_index": 0, "article_ids": ["A3"]}], "should_know": []}
    clusters = [{"story": "s", "article_ids": ["A1", "A2", "A3"]}]
    groups = [{"group": 0, "cluster_index": 0, "article_ids": ["A1", "A2", "A3"]}]
    v = cohesion.judge_selected(selected, clusters, {0: [["A1", "A2"], ["A3"]]}, groups)["verdicts"][0]
    assert v["applied"] is False and v["reason"] == "dominant drops every cited id"

def _stage_dir(tmp_path):
    (tmp_path / "selected.json").write_text(json.dumps(
        {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2", "A3"]}], "should_know": [], "not_covered_blurb": ""}))
    (tmp_path / "clusters.json").write_text(json.dumps({"clusters": [{"story": "s", "article_ids": ["A1", "A2", "A3"]}]}))
    (tmp_path / "articles_1.csv").write_text(
        "article_id,source_id,title,published,summary\nA1,s,Trump may reveal secret AI rules,2026,ars\n"
        "A2,s,A look at Trump's new helipad,2026,bbc\nA3,s,Opinion: Defunding the military,2026,wsj\n")
    return tmp_path

def test_stage_writes_the_artifact_and_a_usage_row(tmp_path, monkeypatch):
    async def fake(prompt, **kw):
        assert kw["tools"] == [] and kw["max_turns"] == 1
        return StageResult(subtype="success", text=json.dumps({"results": [{"group": 0, "events": [["A1"], ["A2"], ["A3"]]}]}),
                           usage={"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                           total_cost_usd=0.01, duration_ms=10, is_error=False)
    monkeypatch.setattr(cohesion.claude_cli, "run_agent", fake)
    row = asyncio.run(cohesion.run_cohesion_stage(_stage_dir(tmp_path), model="claude-sonnet-4-6", cwd=None))
    doc = json.loads((tmp_path / cohesion.COHESION_ARTIFACT).read_text())
    assert doc["outcome"] == "completed" and doc["verdicts"][0]["applied"] is True
    assert row["subagent"] == "cohesion"

def test_stage_fails_open(tmp_path, monkeypatch):
    async def boom(prompt, **kw):
        raise RuntimeError("529")
    monkeypatch.setattr(cohesion.claude_cli, "run_agent", boom)
    row = asyncio.run(cohesion.run_cohesion_stage(_stage_dir(tmp_path), model="claude-sonnet-4-6", cwd=None))
    doc = json.loads((tmp_path / cohesion.COHESION_ARTIFACT).read_text())
    assert doc["outcome"] == "failed" and doc["verdicts"] == []
    assert row["subagent"] == "cohesion"
```

- [ ] **Step 2: Run, expect `ModuleNotFoundError: cohesion`**
- [ ] **Step 3: Implement `cohesion.py`** per the Design section (prompt from `build_judge_prompt`, `JUDGE_SYSTEM` rubric, `parse_verdicts` via `orchestrate.parse_json_object`-equivalent local extractor to avoid importing orchestrate, `validate_partition`, `judge_selected`, `run_cohesion_stage` with `claude_cli.run_agent(prompt, model=model, system_prompt=JUDGE_SYSTEM, tools=[], max_turns=1, cwd=cwd, thinking=_thinking_for(model))`, batches of 12 groups, `usage.usage_row_from_sdk("cohesion", ...)`).
- [ ] **Step 4: Run, expect PASS; `bin/ci`**
- [ ] **Step 5: Commit** `feat(cohesion): judge which of a selected cluster's articles are the story's event`

### Task 2: `write_fanout` applies the artifact

**Files:** Modify `newsroom/src/write_fanout.py` (`build_branches`, `Branch`), test `newsroom/tests/test_write_fanout.py`.

- [ ] **Step 1: Failing tests**

```python
class TestCohesionNarrowsTheBranch:
    def test_dominant_replaces_the_cluster_and_filters_selects_citations(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2"]}], "should_know": []}
        _seed(tmp_path, selected=sel)   # cluster 0 = A1, A2
        (tmp_path / "cluster_cohesion.json").write_text(json.dumps({"outcome": "completed", "verdicts": [
            {"cluster_index": 0, "article_ids": ["A1", "A2"], "events": [["A1"], ["A2"]],
             "dominant": ["A1"], "strays": ["A2"], "applied": True, "reason": None}]}))
        branch = write_fanout.build_branches(tmp_path).branches[0]
        assert branch.context_article_ids == ("A1",)
        assert branch.strays_removed == 1
        one = json.loads((branch.dir / "selected.json").read_text())
        assert one["must_know"][0]["article_ids"] == ["A1"]
        assert [r[0] for r in _branch_rows(branch)] == ["A1"]

    def test_an_unapplied_or_absent_verdict_changes_nothing(self, tmp_path):
        sel = {"must_know": [{"cluster_index": 0, "article_ids": ["A1", "A2"]}], "should_know": []}
        _seed(tmp_path, selected=sel)
        (tmp_path / "cluster_cohesion.json").write_text(json.dumps({"outcome": "completed", "verdicts": [
            {"cluster_index": 0, "applied": False, "reason": "one event", "dominant": ["A1"]}]}))
        branch = write_fanout.build_branches(tmp_path).branches[0]
        assert set(branch.context_article_ids) == {"A1", "A2"} and branch.strays_removed == 0
```

- [ ] **Step 2: Run, expect `AttributeError: strays_removed`**
- [ ] **Step 3: Implement**: `_load_cohesion(dir) -> dict[int, list[str]]` (cluster_index to dominant, applied only, tolerant of a missing or malformed file), `Branch.strays_removed: int = 0`, and in `build_branches`: when the story's `cluster_index` has a dominant, `context = dominant`, `story_ids = [i for i in story_ids if i in dominant] or story_ids`, the branch's story copy carries the filtered `article_ids`, `strays_removed = len(cluster ids) - len(dominant)`. Add `strays_removed` to the `write_branches.json` rows in `orchestrate.py` (~line 872).
- [ ] **Step 4: Run all fan-out tests; `bin/ci`**
- [ ] **Step 5: Commit** `feat(write): a branch sees only its story's event when the cohesion gate has a verdict`

### Task 3: Orchestrator hook and flag

**Files:** `newsroom/src/config.py` (`COHESION_ENABLED`, `COHESION_MODEL`), `newsroom/src/orchestrate.py` (`run_write_phase`: before `build_branches`, `if config.COHESION_ENABLED: on_usage(await cohesion.run_cohesion_stage(...))`, else write the artifact with `outcome: "skipped"`), `newsroom/src/db.py` + `run_health.py` only if a rule is wanted (not in this plan), tests in `newsroom/tests/test_write_per_story.py`.

- [ ] Failing test: with the flag on and `cohesion.run_cohesion_stage` monkeypatched, `run_write_phase` emits a `cohesion` usage row before any `write` row and the branch dirs reflect the verdict; with the flag off, no row and the artifact says `skipped`.
- [ ] Implement; `bin/ci`; commit `feat(orchestrate): run the cohesion gate between SELECT and WRITE behind COHESION_ENABLED`.

### Task 4: Replay measurement

*Run 2026-09-03 (d6b6996): **gate failed** -- 67% count agreement (floor 80%), 5 over-splits (floor 0); all six known run-285 strays separated; stray set exact on 24 of 33; $0.05 and 19 s per run. Two causes, neither fixable on these labels: the dominant is chosen by size and size does not know which facet SELECT chose (show the judge the cluster label), and the rubric lacks the report-and-response and same-appearance cases. Next iteration needs a fresh run and fresh blind labels. See `docs/2026-09-03-cohesion-gate-measurement.md`. Not deployed.*

**Files:** Create `newsroom/src/eval_cohesion.py`, `bin/eval-cohesion` (copy the `bin/eval-write-turns` wrapper), `docs/2026-09-03-cohesion-gate-labels.json` (blind labels, written BEFORE the judge runs), `docs/2026-09-03-cohesion-gate-measurement.md`.

- [x] Restore runs 284 and 285's `selected.json`, `clusters.json`, `articles_*.csv` from `run_artifacts`; print each selected cluster's ids and titles; write the blind labels file by hand (events count, stray ids) without running the judge.
- [x] Run the judge on both runs, 3 reps each (the verdict is stochastic; report agreement per rep and the modal verdict).
- [x] Report: per-cluster agreement on event count; stray-set Jaccard against labels; the five known cases; over-splits; cost and seconds. Verdict against a gate stated before the run: modal verdict agrees with the labels on event count for >= 80% of clusters, zero over-splits of a labelled same-event pair on the modal verdict, all five known cases separated.
- [x] Commit the harness, then the labels and measurement doc.

Deploy is a separate decision after the measurement; it is one terraform variable.
