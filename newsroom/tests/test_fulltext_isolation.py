"""Tests for the hard bound on fulltext.py's fetch+extract work.

An in-process deadline bounds the waiter, not the work, so the bound is enforced from OUTSIDE
by a process (``docs/lessons/a-deadline-on-the-waiter-does-not-bound-the-worker.md``). Unlike
``test_fulltext.py``, which inlines the collector so it can script outcomes with fakes, nothing
here is faked: real child processes, real ``subprocess`` kills, and real trafilatura against a
loopback HTTP server. This is the only place the production path is proved, so it pays the
seconds deliberately.
"""

import contextlib
import io
import json
import logging
import resource
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import fulltext

# One <p> per sentence, no nesting: trafilatura's readability comparison is superlinear in node
# count, so this is slow for size reasons alone. It reproduces the CLASS (extraction that does not
# return within the budget), not the one document that triggered it, and takes several times the
# hard bound the kill tests give it.
_SLOW_NODES = 100_000


def _slow_document() -> bytes:
    body = "".join(f"<p>Sentence number {i} here with words.</p>" for i in range(_SLOW_NODES))
    return f"<html><head><title>T</title></head><body><article>{body}</article></body></html>".encode()


def _normal_document() -> bytes:
    body = "".join(
        f"<p>The council met on Tuesday and agreed to fund the bridge repair, item {i} on the "
        f"agenda, after a debate lasting most of the afternoon.</p>"
        for i in range(12)
    )
    return (
        f"<html><head><title>Council funds bridge</title></head><body><article>{body}</article></body></html>".encode()
    )


def _cpu_seconds() -> float:
    """CPU consumed by this process AND its children -- the measure that catches work which
    outlived the call that started it, wherever it is running."""
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime


@contextlib.contextmanager
def _serving(routes: dict[str, bytes]):
    """A throwaway loopback HTTP server. Hermetic: no publisher is touched by these tests."""

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # keep the test output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_inputs(tmp_path: Path, urls: dict[str, str]) -> None:
    (tmp_path / "selected.json").write_text(
        json.dumps({"must_know": [{"cluster_index": 0, "article_ids": list(urls)}], "should_know": []}),
        encoding="utf-8",
    )
    (tmp_path / "article_index.json").write_text(
        json.dumps(
            {aid: {"url": url, "source_id": "src", "bias": "center", "name": "Src"} for aid, url in urls.items()}
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _tight_bounds(monkeypatch):
    monkeypatch.setattr(config, "FULLTEXT_ENABLED", True)
    monkeypatch.setattr(config, "FULLTEXT_PER_STORY", 10)
    monkeypatch.setattr(config, "FULLTEXT_MAX_CHARS", 4000)
    monkeypatch.setattr(config, "FULLTEXT_DEADLINE_S", 2)
    monkeypatch.setattr(config, "FULLTEXT_KILL_GRACE_S", 2)
    # Off by default so the kill tests prove the PROCESS bound: with the cap on, the slow
    # document never reaches the parser and the test would prove nothing.
    monkeypatch.setattr(config, "FULLTEXT_MAX_DOC_CHARS", 0)


class TestTheWorkerCannotOutliveItsBound:
    def test_a_worker_that_never_returns_is_killed(self, caplog):
        """The primitive, with no network and no parser: a child that ignores every deadline is
        killed by the parent and the step returns."""
        hard = config.FULLTEXT_DEADLINE_S + config.FULLTEXT_KILL_GRACE_S
        original = fulltext._worker_command

        def _sleepy_command():
            return [sys.executable, "-c", "import time; time.sleep(600)"]

        fulltext._worker_command = _sleepy_command
        try:
            start = time.monotonic()
            with caplog.at_level("WARNING"):
                results, _outcome = fulltext._collect_isolated(
                    [("A1", "http://127.0.0.1:1/a")], max_chars=4000, deadline_s=2, max_doc_chars=0
                )
            elapsed = time.monotonic() - start
        finally:
            fulltext._worker_command = original

        assert results == {}
        assert elapsed < hard + 10, f"the bound did not hold: {elapsed:.1f}s for a {hard}s budget"
        assert any("killed" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")

    def test_a_runaway_extraction_is_gone_when_the_step_returns(self, tmp_path, caplog):
        """A real child, real trafilatura, a real lxml parse far longer than the budget.

        Returning on time is NOT the property under test -- an in-process waiter did that too,
        while leaving the parse running and starving the rest of the pipeline. The assertion is:
        when the step returns, none of its work is still running."""
        hard = config.FULLTEXT_DEADLINE_S + config.FULLTEXT_KILL_GRACE_S
        with _serving({"/slow": _slow_document()}) as base:
            _write_inputs(tmp_path, {"A1": f"{base}/slow"})
            start = time.monotonic()
            with caplog.at_level("WARNING"):
                result = fulltext.fetch_for_selected(tmp_path)
            elapsed = time.monotonic() - start
            settled = _cpu_seconds()
            time.sleep(3.0)
            leaked = _cpu_seconds() - settled

        assert elapsed < hard + 10, f"the bound did not hold: {elapsed:.1f}s for a {hard}s budget"
        assert leaked < 0.5, f"{leaked:.2f} CPU-seconds still being burned after the step returned"
        assert result is None  # nothing extracted, so no output file -- the CSV floor, as designed
        assert not (tmp_path / "article_fulltext.json").exists()

    def test_a_slow_fetch_costs_the_soft_deadline_not_the_hard_one(self, tmp_path, monkeypatch):
        """The kill is the backstop, not the mechanism. A worker that hits its own deadline hands
        back what it has and goes; it must not sit out the grace period too, or every slow run
        pays for the pathological one. (concurrent.futures joins its non-daemon threads at exit,
        which is why the worker exits via os._exit.)"""
        monkeypatch.setattr(config, "FULLTEXT_KILL_GRACE_S", 8)
        with _serving({"/slow": _slow_document(), "/ok": _normal_document()}) as base:
            _write_inputs(tmp_path, {"A1": f"{base}/ok", "A2": f"{base}/slow"})
            start = time.monotonic()
            result = fulltext.fetch_for_selected(tmp_path)
            elapsed = time.monotonic() - start

        assert result is not None
        assert set(json.loads(result.read_text())) == {"A1"}
        assert elapsed < 6, f"waited {elapsed:.1f}s for a 2s deadline -- the grace period was spent too"

    def test_results_that_finished_survive_the_kill(self, tmp_path):
        """A killed worker must not cost the articles that already succeeded. The worker emits each
        result as it completes, so whatever reached the parent before the kill is kept."""
        with _serving({"/slow": _slow_document(), "/ok": _normal_document()}) as base:
            _write_inputs(tmp_path, {"A1": f"{base}/ok", "A2": f"{base}/slow"})
            result = fulltext.fetch_for_selected(tmp_path)

        assert result is not None
        data = json.loads(result.read_text())
        assert set(data) == {"A1"}
        assert "bridge repair" in data["A1"]["text"]


class TestZeroResultsNamesItsCause:
    """A dead worker and a batch where nothing was extractable both yield zero results, so the
    outcome has to name which one it was."""

    def test_outcome_distinguishes_a_dead_worker_from_an_empty_batch(self, caplog):
        original = fulltext._worker_command
        try:
            fulltext._worker_command = lambda: [sys.executable, "-c", "raise SystemExit(3)"]
            with caplog.at_level("WARNING"):
                results, outcome = fulltext._collect_isolated(
                    [("A1", "http://127.0.0.1:1/a")], max_chars=4000, deadline_s=2, max_doc_chars=0
                )
            assert results == {}
            assert outcome == "crashed:3"

            fulltext._worker_command = lambda: [sys.executable, "-c", "pass"]
            _r, clean = fulltext._collect_isolated(
                [("A1", "http://127.0.0.1:1/a")], max_chars=4000, deadline_s=2, max_doc_chars=0
            )
            assert clean == "completed"
        finally:
            fulltext._worker_command = original

    def test_unreadable_result_lines_are_counted_not_swallowed(self, caplog):
        original = fulltext._worker_command
        try:
            fulltext._worker_command = lambda: [
                sys.executable,
                "-c",
                'print(\'{"id": "A1", "text": "ok"}\'); print(\'{partial\')',
            ]
            with caplog.at_level("WARNING"):
                results, outcome = fulltext._collect_isolated(
                    [("A1", "http://127.0.0.1:1/a")], max_chars=4000, deadline_s=5, max_doc_chars=0
                )
        finally:
            fulltext._worker_command = original
        assert results == {"A1": "ok"}
        assert outcome == "completed"
        assert any("unreadable result line" in r.getMessage() for r in caplog.records)


class TestTheIsolatedWorkerStillDoesTheJob:
    def test_a_real_document_is_fetched_and_extracted_through_the_child(self, tmp_path):
        """The production path, unfaked: parent -> child process -> trafilatura -> output file."""
        with _serving({"/a": _normal_document()}) as base:
            _write_inputs(tmp_path, {"A1": f"{base}/a"})
            result = fulltext.fetch_for_selected(tmp_path)

        assert result == tmp_path / "article_fulltext.json"
        data = json.loads(result.read_text())
        assert "bridge repair" in data["A1"]["text"]
        assert "http" not in result.read_text()  # the no-URLs invariant holds across the boundary
        assert list(tmp_path.glob("*.tmp*")) == []

    def test_worker_log_lines_reach_the_parents_logger(self, tmp_path, caplog):
        """The child logs to its own stderr; those lines must end up in the run's log (stdout AND
        the rotating file), or every per-article diagnostic this module writes would vanish."""
        with _serving({}) as base:  # every URL 404s
            _write_inputs(tmp_path, {"A1": f"{base}/missing"})
            with caplog.at_level("INFO"):
                assert fulltext.fetch_for_selected(tmp_path) is None

        assert any("A1" in r.getMessage() for r in caplog.records)
        assert not any("127.0.0.1" in r.getMessage() and "/missing" in r.getMessage() for r in caplog.records)

    def test_a_worker_that_crashes_keeps_what_it_had_already_emitted(self, caplog):
        """A worker that dies partway through is a failed batch, not a failed run: whatever it
        flushed before dying is kept, and the failure is reported rather than raised."""
        emit_then_die = (
            "import sys; sys.stdin.buffer.read(); "
            'sys.stdout.write(\'{"id": "A1", "text": "kept"}\\n\'); sys.stdout.flush(); '
            'sys.stderr.write("WARNING fulltext: worker fell over\\n"); sys.exit(3)'
        )
        original = fulltext._worker_command
        fulltext._worker_command = lambda: [sys.executable, "-c", emit_then_die]
        try:
            with caplog.at_level("WARNING"):
                results, _outcome = fulltext._collect_isolated(
                    [("A1", "http://127.0.0.1:1/a")], max_chars=4000, deadline_s=2, max_doc_chars=0
                )
        finally:
            fulltext._worker_command = original

        assert results == {"A1": "kept"}
        messages = [r.getMessage() for r in caplog.records]
        assert any("worker exited 3" in m for m in messages)
        assert any("fell over" in m for m in messages)  # the child's own log line was relayed

    def test_third_party_loggers_cannot_leak_a_url_through_the_relay(self):
        """The relay carries the child's stderr into the run's log verbatim, so it must carry ONLY
        this module's own records. Both offenders below are real: urllib3 logs the article path on
        a read timeout, trafilatura the full URL on a download error.

        Asserted against the logging setup rather than through a fetch: provoking a real
        read-timeout retry costs ten seconds and a connection refusal produces no record at all,
        so the fetch-driven version could not fail."""
        stream = io.StringIO()
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        try:
            fulltext._configure_worker_logging(stream)
            logging.getLogger("urllib3.connectionpool").warning(
                "Retrying (Retry(total=1)) after connection broken by 'ReadTimeoutError': %s",
                "/news/world/secret-path/story",
            )
            logging.getLogger("trafilatura.downloads").error(
                "download error: %s %s", "https://example.com/secret-path/story", "boom"
            )
            fulltext.logger.info("fulltext: fetch returned nothing for A1 (example.com)")
        finally:
            root.handlers[:] = saved_handlers
            root.level = saved_level

        written = stream.getvalue()
        assert "secret-path" not in written
        assert "://" not in written
        assert written == "INFO fulltext: fetch returned nothing for A1 (example.com)\n"

    def test_a_fetch_failure_leaks_no_url_end_to_end(self, tmp_path, caplog):
        """The same invariant across the real process boundary, on the path a run actually takes."""
        _write_inputs(tmp_path, {"A1": "http://127.0.0.1:9/never-served/secret-path/story"})
        with caplog.at_level("DEBUG"):
            assert fulltext.fetch_for_selected(tmp_path) is None

        assert any("A1" in r.getMessage() for r in caplog.records)
        for record in caplog.records:
            assert "secret-path" not in record.getMessage()
            assert "://" not in record.getMessage()


class TestDocumentSizeCap:
    """A cheap pre-parse cap on document size. NOT a bound -- a small document can still be slow,
    which is why the process bound above exists -- but extraction cost is superlinear in node
    count, so it trims most of the tail."""

    def test_document_over_the_cap_is_skipped(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(config, "FULLTEXT_MAX_DOC_CHARS", 5_000)
        with _serving({"/slow": _slow_document()}) as base:
            _write_inputs(tmp_path, {"A1": f"{base}/slow"})
            start = time.monotonic()
            with caplog.at_level("INFO"):
                assert fulltext.fetch_for_selected(tmp_path) is None
            elapsed = time.monotonic() - start

        # Skipped before the parser, so it returns well inside what the parse would take.
        assert elapsed < 10
        assert any("too large" in r.getMessage() for r in caplog.records)

    def test_document_under_the_cap_is_extracted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "FULLTEXT_MAX_DOC_CHARS", 2_000_000)
        with _serving({"/a": _normal_document()}) as base:
            _write_inputs(tmp_path, {"A1": f"{base}/a"})
            result = fulltext.fetch_for_selected(tmp_path)

        assert result is not None
        assert "bridge repair" in json.loads(result.read_text())["A1"]["text"]
