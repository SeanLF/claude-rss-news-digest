import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import cluster_extractjoin
import cohesion
import orchestrate
import prompts
import thread_synthesis
import threads

AGENTS = Path(__file__).resolve().parents[2] / ".claude" / "agents"

PIPELINE_PROMPT_FILES = ("cohesion", "cluster-extract", "thread-synthesis", "thread-audit", "thread-link")


@pytest.fixture(autouse=True)
def _agents_dir(monkeypatch):
    monkeypatch.setattr(prompts, "AGENTS_DIR", AGENTS)


def test_every_model_stage_prompt_is_a_file():
    for name in PIPELINE_PROMPT_FILES:
        assert (AGENTS / f"{name}.md").exists(), name


def test_prompts_strip_agrees_with_orchestrates_parser_on_every_agent_file():
    for path in sorted(AGENTS.glob("*.md")):
        assert prompts.load_prompt_text(path.stem) == orchestrate.parse_agent_spec(path).body, path.name


def test_modules_read_their_prompt_from_the_file():
    assert cohesion.judge_system() == prompts.load_prompt_text("cohesion")
    assert cluster_extractjoin.extract_system() == prompts.load_prompt_text("cluster-extract")
    assert thread_synthesis.synthesis_system() == prompts.load_prompt_text("thread-synthesis")
    assert thread_synthesis.audit_system() == prompts.load_prompt_text("thread-audit")
    assert threads.link_system() == prompts.load_prompt_text("thread-link")


def test_prompts_are_read_at_call_time_not_import_time(tmp_path, monkeypatch):
    (tmp_path / "cohesion.md").write_text("---\nname: cohesion\n---\n\nchanged\n", encoding="utf-8")
    monkeypatch.setattr(prompts, "AGENTS_DIR", tmp_path)
    assert cohesion.judge_system() == "changed"


def test_no_prompt_constant_survives():
    for mod, name in (
        (cohesion, "JUDGE_SYSTEM"),
        (cluster_extractjoin, "EXTRACT_SYSTEM"),
        (thread_synthesis, "EVOLVE_SYSTEM"),
        (thread_synthesis, "AUDIT_SYSTEM"),
        (threads, "LINK_SYSTEM"),
    ):
        assert not hasattr(mod, name), f"{mod.__name__}.{name} still exists"


def test_a_file_without_frontmatter_is_refused(tmp_path, monkeypatch):
    (tmp_path / "x.md").write_text("no frontmatter", encoding="utf-8")
    monkeypatch.setattr(prompts, "AGENTS_DIR", tmp_path)
    with pytest.raises(ValueError):
        prompts.load_prompt_text("x")
