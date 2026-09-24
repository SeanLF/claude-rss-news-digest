import fulltext
import worker
from temporalio.testing import ActivityEnvironment


def test_the_activity_name_is_the_one_the_workflow_calls():
    assert worker.fetch_fulltext.__temporal_activity_definition.name == "fetchFulltext"


def test_runs_the_production_fetch_with_the_production_config(monkeypatch):
    seen = {}

    def fake(tasks, *, max_chars, deadline_s, max_doc_chars):
        seen.update(tasks=tasks, max_chars=max_chars, deadline_s=deadline_s, max_doc_chars=max_doc_chars)
        return {"A1": "text"}, "completed"

    monkeypatch.setattr(fulltext, "_collect_isolated", fake)
    out = ActivityEnvironment().run(worker.fetch_fulltext, [["A1", "https://a.com/1"], ["A2", "https://b.com/2"]])
    assert out == {"tasks": 2, "results": {"A1": "text"}, "outcome": "completed"}
    assert seen["tasks"] == [("A1", "https://a.com/1"), ("A2", "https://b.com/2")]
    assert (seen["max_chars"], seen["deadline_s"], seen["max_doc_chars"]) == (4000, 120, 2_000_000)


def test_the_real_child_process_starts_and_answers(monkeypatch):
    # No network: an empty task list still spawns `python -m fulltext`, so a broken image or
    # PYTHONPATH shows up here rather than as a silent "no full text" in production.
    out = ActivityEnvironment().run(worker.fetch_fulltext, [])
    assert out == {"tasks": 0, "results": {}, "outcome": "completed"}


def test_the_namespace_is_temporal_namespace_so_production_uses_the_repos_own(monkeypatch):
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "news-digest")
    assert worker.namespace() == "news-digest"
    monkeypatch.delenv("TEMPORAL_NAMESPACE")
    assert worker.namespace() == "default"
