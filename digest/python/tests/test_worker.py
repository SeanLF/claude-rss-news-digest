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


def test_sigterm_stops_the_worker_cleanly(tmp_path):
    # systemd stops the unit with SIGTERM on every deploy; dying of it exits 143, which OnFailure
    # mails as a failure. The worker must shut down and exit 0, as the TypeScript one does.
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    ready = tmp_path / "ready"
    script = f"""
import asyncio, pathlib, worker
class FakeWorker:
    def __init__(self, *a, **kw): pass
    async def __aenter__(self):
        pathlib.Path({str(ready)!r}).touch()
        return self
    async def __aexit__(self, *exc): pass
    async def run(self):
        pathlib.Path({str(ready)!r}).touch()
        await asyncio.Event().wait()
async def connect(*a, **kw): return object()
worker.Worker = FakeWorker
worker.Client.connect = connect
asyncio.run(worker.main())
"""
    proc = subprocess.Popen([sys.executable, "-c", script], cwd=Path(worker.__file__).parent)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert proc.poll() is None and time.monotonic() < deadline, "worker never started"
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()
