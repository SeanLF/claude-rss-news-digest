"""The fetch guard and the deadline outcome, through `fetch_for_selected` and its real child.

test_fulltext_fetch_guard.py and test_fulltext_slow_sender.py prove the parts in-process; these prove
the pipeline's own entry point writes no internal page into article_fulltext.json and records a
deadline-cut step as `deadline` in fulltext_health.json.
"""

import contextlib
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import fulltext

SECRET = "internal admin page, bridge repair ledger, council credentials"


def _secret_page() -> bytes:
    body = "".join(f"<p>{SECRET}, item {i}, recorded after a long afternoon of debate.</p>" for i in range(12))
    return f"<html><head><title>Admin</title></head><body><article>{body}</article></body></html>".encode()


@contextlib.contextmanager
def _serving(routes):
    """routes: path -> (status, headers, body). Yields (port, hits)."""
    hits: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            hits.append(self.path)
            status, headers, body = routes.get(self.path, (404, {}, b""))
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextlib.contextmanager
def _trickling():
    """A 200 whose body arrives one byte per 0.1 s and never ends."""
    server = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def serve_one(conn):
        with conn:
            try:
                conn.recv(65536)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n<html>")
                while not stop.is_set():
                    conn.sendall(b"x")
                    time.sleep(0.1)
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


def _write_inputs(tmp_path: Path, urls: dict[str, str]) -> None:
    (tmp_path / "selected.json").write_text(
        json.dumps({"must_know": [{"cluster_index": 0, "article_ids": list(urls)}], "should_know": []}),
        encoding="utf-8",
    )
    (tmp_path / "article_index.json").write_text(
        json.dumps({aid: {"url": url, "source_id": "src", "bias": "center"} for aid, url in urls.items()}),
        encoding="utf-8",
    )


def _exempt_first_hops(monkeypatch):
    """Exempt each task's own URL by exact address, as a public publisher would be; a redirect
    elsewhere gets no exemption. Production passes no `allow` at all."""
    real = fulltext._collect_isolated

    def exempting(tasks, **kwargs):
        allow = frozenset((urlsplit(u).hostname, urlsplit(u).port) for _aid, u in tasks)
        return real(tasks, allow=allow, **kwargs)

    monkeypatch.setattr(fulltext, "_collect_isolated", exempting)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "FULLTEXT_ENABLED", True)
    monkeypatch.setattr(config, "FULLTEXT_PER_STORY", 10)
    monkeypatch.setattr(config, "FULLTEXT_MAX_CHARS", 4000)
    monkeypatch.setattr(config, "FULLTEXT_DEADLINE_S", 3)
    monkeypatch.setattr(config, "FULLTEXT_KILL_GRACE_S", 10)
    monkeypatch.setattr(config, "FULLTEXT_MAX_DOC_CHARS", 0)


def _health(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "fulltext_health.json").read_text(encoding="utf-8"))


def test_an_article_url_on_loopback_is_never_fetched(tmp_path):
    with _serving({"/admin": (200, {"Content-Type": "text/html"}, _secret_page())}) as (port, hits):
        _write_inputs(tmp_path, {"A1": f"http://127.0.0.1:{port}/admin"})
        assert fulltext.fetch_for_selected(tmp_path) is None

    assert hits == []
    assert not (tmp_path / "article_fulltext.json").exists()


def test_a_redirect_from_a_publisher_to_loopback_is_not_followed(tmp_path, monkeypatch):
    _exempt_first_hops(monkeypatch)
    with _serving({"/admin": (200, {"Content-Type": "text/html"}, _secret_page())}) as (inner, inner_hits):
        routes = {"/a": (302, {"Location": f"http://127.0.0.1:{inner}/admin"}, b"")}
        with _serving(routes) as (outer, outer_hits):
            _write_inputs(tmp_path, {"A1": f"http://127.0.0.1:{outer}/a"})
            assert fulltext.fetch_for_selected(tmp_path) is None

    assert outer_hits == ["/a"]
    assert inner_hits == []
    assert _health(tmp_path) == {"tasks": 1, "extracted": 0, "outcome": "completed"}


def test_a_step_its_deadline_cut_short_is_recorded_as_deadline(tmp_path, monkeypatch):
    _exempt_first_hops(monkeypatch)
    monkeypatch.setattr(config, "FULLTEXT_DEADLINE_S", 1)
    with _trickling() as port:
        _write_inputs(tmp_path, {"A1": f"http://127.0.0.1:{port}/a"})
        assert fulltext.fetch_for_selected(tmp_path) is None

    assert _health(tmp_path) == {"tasks": 1, "extracted": 0, "outcome": "deadline"}
