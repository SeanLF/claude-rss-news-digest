"""A host that trickles bytes costs one fetch its wall-clock limit, and a batch the run's deadline
cut short says so.

urllib3's timeout is per read, so a server sending a byte every few seconds held a thread for the
whole step budget. And when the step's deadline left fetches unfinished, the outcome was still
`completed`, which reads as a clean step in fulltext_health.json and the FULLTEXT_TOTAL_LOSS alert.
"""

import contextlib
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import fulltext


@contextlib.contextmanager
def _trickling(prefix: bytes, drip: bytes = b"x", interval: float = 0.2):
    """A raw TCP server: `prefix` at once, then `drip` one byte per `interval` until the client goes."""
    server = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def serve_one(conn):
        with conn:
            try:
                conn.recv(65536)
                conn.sendall(prefix)
                while not stop.is_set():
                    for b in drip:
                        conn.sendall(bytes([b]))
                        time.sleep(interval)
            except OSError:
                pass

    def accept():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=serve_one, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1]
    finally:
        stop.set()
        server.close()
        thread.join(timeout=5)


def _exempt(port):
    return frozenset({("127.0.0.1", port)})


BODY_TRICKLE = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n<html><body>"
CHUNKED_TRICKLE = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nTransfer-Encoding: chunked\r\n\r\n"


@pytest.mark.parametrize(
    ("prefix", "drip"),
    [
        (BODY_TRICKLE, b"x"),  # a body that never finishes
        (b"HTTP/1.1 200 OK\r\n", b"X-Slow: y\r\n"),  # headers that never finish
        (CHUNKED_TRICKLE, b"1\r\nx\r\n"),  # a chunked body, where an early EOF looks like the end
    ],
    ids=["body", "headers", "chunked"],
)
def test_a_trickling_host_costs_the_wall_clock_limit_not_the_step(monkeypatch, prefix, drip):
    monkeypatch.setattr(fulltext, "_PER_FETCH_WALL_S", 1.5)
    with _trickling(prefix, drip, interval=0.05) as port:
        start = time.monotonic()
        with pytest.raises(Exception):  # noqa: B017 -- refused or a broken read, as long as it ends
            fulltext._download(f"http://127.0.0.1:{port}/a", allow=_exempt(port))
        elapsed = time.monotonic() - start
    # Every drip lands well inside the 10 s per-read timeout, so only a wall clock stops this.
    assert elapsed < 4, f"a trickling host held the fetch for {elapsed:.1f}s against a 1.5s limit"


def test_a_fetch_that_outlives_its_limit_is_no_page_even_if_the_stream_ends_cleanly(monkeypatch):
    """Cutting the socket can look like the end of a close-delimited body: what was read must not be
    taken for the article."""
    monkeypatch.setattr(fulltext, "_PER_FETCH_WALL_S", 1.0)
    close_delimited = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n<html><body>"
    with (
        _trickling(close_delimited, b"<p>partial</p>", interval=0.05) as port,
        pytest.raises(fulltext.FetchRefused, match="wall-clock"),
    ):
        fulltext._download(f"http://127.0.0.1:{port}/a", allow=_exempt(port))


def test_a_trickling_host_is_logged_as_over_its_limit(monkeypatch, caplog):
    monkeypatch.setattr(fulltext, "_PER_FETCH_WALL_S", 1.0)
    with _trickling(BODY_TRICKLE, interval=0.05) as port, caplog.at_level("INFO"):
        assert fulltext._fetch_one("A1", f"http://127.0.0.1:{port}/a", 4000, allow=_exempt(port)) == ("A1", None)
    assert "A1" in caplog.text


class TestTheDeadlineOutcome:
    def test_the_collector_counts_what_the_deadline_cut_short(self, monkeypatch):
        monkeypatch.setattr(fulltext, "_PER_FETCH_WALL_S", 30)
        with _trickling(BODY_TRICKLE, interval=0.1) as port:
            results, unfinished = fulltext._collect_inline(
                [("A1", f"http://127.0.0.1:{port}/a")],
                max_chars=4000,
                deadline_s=1,
                max_doc_chars=0,
                allow=_exempt(port),
            )
        assert results == {}
        assert unfinished == 1

    def test_a_fetch_that_lands_after_the_timeout_still_counts_as_unfinished(self, monkeypatch):
        """Done but never collected: its result was not handed over, so the batch is not settled."""
        monkeypatch.setattr(fulltext, "_download", lambda url, allow=frozenset(): None)

        def times_out_after_the_fetch_finished(futures, timeout=None):
            for f in futures:
                f.result()
            raise TimeoutError
            yield  # a generator, as as_completed is

        monkeypatch.setattr(fulltext, "as_completed", times_out_after_the_fetch_finished)
        _results, unfinished = fulltext._collect_inline(
            [("A1", "https://example.com/a")], max_chars=4000, deadline_s=5, max_doc_chars=0
        )
        assert unfinished == 1

    def test_a_batch_that_finished_leaves_nothing_unfinished(self, monkeypatch):
        monkeypatch.setattr(fulltext, "_download", lambda url, allow=frozenset(): None)
        results, unfinished = fulltext._collect_inline(
            [("A1", "https://example.com/a")], max_chars=4000, deadline_s=5, max_doc_chars=0
        )
        assert (results, unfinished) == ({}, 0)

    def test_a_worker_cut_short_by_its_deadline_reports_deadline_not_completed(self, monkeypatch):
        """Through the real child."""
        monkeypatch.setattr(config, "FULLTEXT_KILL_GRACE_S", 10)
        with _trickling(BODY_TRICKLE, interval=0.1) as port:
            start = time.monotonic()
            results, outcome = fulltext._collect_isolated(
                [("A1", f"http://127.0.0.1:{port}/a")],
                max_chars=4000,
                deadline_s=1,
                max_doc_chars=0,
                allow=_exempt(port),
            )
            elapsed = time.monotonic() - start
        assert results == {}
        assert outcome == "deadline"
        assert elapsed < 6  # its own deadline, not the kill

    def test_a_worker_that_finishes_in_time_still_reports_completed(self):
        _results, outcome = fulltext._collect_isolated([], max_chars=4000, deadline_s=5, max_doc_chars=0)
        assert outcome == "completed"

    def test_a_status_line_is_read_as_the_outcome_not_as_a_result(self, monkeypatch):
        monkeypatch.setattr(
            fulltext,
            "_worker_command",
            lambda: [sys.executable, "-c", 'print(\'{"id": "A1", "text": "ok"}\'); print(\'{"outcome": "deadline"}\')'],
        )
        assert fulltext._collect_isolated([], max_chars=4000, deadline_s=5, max_doc_chars=0) == (
            {"A1": "ok"},
            "deadline",
        )
