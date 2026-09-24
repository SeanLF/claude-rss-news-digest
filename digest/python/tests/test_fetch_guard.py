"""The fetch reaches public addresses only, on every hop.

trafilatura.fetch_url followed a 302 to 127.0.0.1 and returned that page as article text: a publisher
(or anyone who can put a URL in a feed) could make the worker read the box's own services. Every test
here runs the real fetch against a loopback server, with the resolver and the connect step replaced so
a "public" name can be served locally while the address policy sees what it would see in production.
"""

import contextlib
import gzip
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fulltext
import pytest

PUBLIC_V4 = "93.184.215.14"
PUBLIC_V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
PAGE = b"<html><body><article><p>The council met on Tuesday.</p></article></body></html>"


@contextlib.contextmanager
def _serving(routes):
    """routes: path -> (status, headers, body). Records every path requested."""
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


@pytest.fixture
def net(monkeypatch):
    """A fake resolver, and a connect step that sends every public address to one loopback server.

    `connects` records each address the fetch actually opened a socket to: a refused address never
    appears there, which is what separates "refused" from "tried and failed".
    """
    names: dict[str, list[str]] = {"news.test": [PUBLIC_V4], "news6.test": [PUBLIC_V6]}
    connects: list[tuple[str, int]] = []
    state = {"port": None}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        ips = names.get(host, [host])  # an IP literal resolves to itself, as the real resolver does
        out = []
        for ip in ips:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sa = (ip, port, 0, 0) if fam == socket.AF_INET6 else (ip, port)
            out.append((fam, socket.SOCK_STREAM, 6, "", sa))
        return out

    def fake_connect(family, sockaddr, timeout):
        connects.append((sockaddr[0], sockaddr[1]))
        return socket.create_connection(("127.0.0.1", state["port"]), timeout=timeout)

    monkeypatch.setattr(fulltext, "_getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(fulltext, "_connect", fake_connect)

    class Net:
        def __init__(self):
            self.names, self.connects = names, connects

        def route_to(self, port):
            state["port"] = port

    return Net()


def test_a_public_page_is_fetched(net):
    with _serving({"/a": (200, {"Content-Type": "text/html"}, PAGE)}) as (port, _hits):
        net.route_to(port)
        assert fulltext._download("http://news.test/a") == PAGE
    assert net.connects == [(PUBLIC_V4, 80)]


def test_a_public_ipv6_page_is_fetched(net):
    with _serving({"/a": (200, {}, PAGE)}) as (port, _hits):
        net.route_to(port)
        assert fulltext._download("http://news6.test/a") == PAGE


def test_a_redirect_to_a_public_page_is_followed(net):
    routes = {"/a": (302, {"Location": "/b"}, b""), "/b": (200, {}, PAGE)}
    with _serving(routes) as (port, hits):
        net.route_to(port)
        assert fulltext._download("http://news.test/a") == PAGE
    assert hits == ["/a", "/b"]


def test_a_redirect_to_loopback_is_refused_and_never_requested():
    """The reported case, unfaked below the address check: a real 302 to a real loopback server."""
    with _serving({"/secret": (200, {}, b"<html>internal admin page</html>")}) as (inner, inner_hits):
        routes = {"/a": (302, {"Location": f"http://127.0.0.1:{inner}/secret"}, b"")}
        # The first hop is allowed by exact address, as the isolation tests do; the redirect is not.
        with _serving(routes) as (outer, outer_hits), pytest.raises(fulltext.FetchRefused):
            fulltext._download(f"http://127.0.0.1:{outer}/a", allow=frozenset({("127.0.0.1", outer)}))
    assert outer_hits == ["/a"]
    assert inner_hits == []


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/",
        "http://127.8.9.10:8080/",
        "http://10.0.0.5/x",
        "http://172.16.3.4/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/x",  # shared address space (carrier NAT)
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://255.255.255.255/",
        "http://[::1]/x",
        "http://[::]/x",
        "http://[fe80::1]/x",
        "http://[fc00::1]/x",
        "http://[fd12:3456::1]/x",
        "http://[ff02::1]/x",
        "http://[::ffff:127.0.0.1]/x",  # IPv4-mapped loopback
        "http://[::ffff:10.0.0.1]/x",
        "http://[2002:a00:1::]/x",  # 6to4 wrapping 10.0.0.1
    ],
)
def test_a_redirect_to_a_non_public_address_is_refused_before_connecting(net, target):
    with _serving({"/a": (302, {"Location": target}, b"")}) as (port, hits):
        net.route_to(port)
        with pytest.raises(fulltext.FetchRefused):
            fulltext._download("http://news.test/a")
    assert hits == ["/a"]
    assert net.connects == [(PUBLIC_V4, 80)]  # the second hop never opened a socket


@pytest.mark.parametrize("url", ["http://10.1.2.3/a", "https://192.168.0.10/a", "http://[::1]:8233/"])
def test_a_private_address_in_the_url_is_refused(net, url):
    with pytest.raises(fulltext.FetchRefused):
        fulltext._download(url)
    assert net.connects == []


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_name_that_resolves_to_a_private_address_is_refused(net, scheme):
    net.names["intranet.test"] = ["192.168.1.5"]
    with pytest.raises(fulltext.FetchRefused):
        fulltext._download(f"{scheme}://intranet.test/a")
    assert net.connects == []


def test_a_name_with_any_private_address_among_public_ones_is_refused(net):
    net.names["mixed.test"] = [PUBLIC_V4, "10.0.0.1"]
    with pytest.raises(fulltext.FetchRefused):
        fulltext._download("http://mixed.test/a")
    assert net.connects == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://news.test/a", "gopher://news.test/a", "news.test/a"])
def test_a_scheme_other_than_http_is_refused(net, url):
    with pytest.raises(fulltext.FetchRefused):
        fulltext._download(url)
    assert net.connects == []


def test_a_redirect_to_another_scheme_is_refused(net):
    with _serving({"/a": (302, {"Location": "file:///etc/passwd"}, b"")}) as (port, _hits):
        net.route_to(port)
        with pytest.raises(fulltext.FetchRefused):
            fulltext._download("http://news.test/a")


def test_at_most_two_redirects_are_followed(net):
    routes = {
        "/a": (302, {"Location": "/b"}, b""),
        "/b": (301, {"Location": "/c"}, b""),
        "/c": (307, {"Location": "/d"}, b""),
        "/d": (200, {}, PAGE),
    }
    with _serving(routes) as (port, hits):
        net.route_to(port)
        with pytest.raises(fulltext.FetchRefused):
            fulltext._download("http://news.test/a")
    assert hits == ["/a", "/b", "/c"]


def test_a_non_200_answer_is_no_page(net):
    with _serving({"/a": (503, {}, b"<html>busy</html>")}) as (port, _hits):
        net.route_to(port)
        assert fulltext._download("http://news.test/a") is None


def test_a_body_over_the_size_cap_is_no_page(net, monkeypatch):
    monkeypatch.setattr(fulltext, "_MAX_FILE_SIZE", 1000)
    with _serving({"/a": (200, {}, b"x" * 5000)}) as (port, _hits):
        net.route_to(port)
        with pytest.raises(fulltext.FetchRefused):
            fulltext._download("http://news.test/a")


def test_a_compressed_body_is_capped_after_decompression(net, monkeypatch):
    """A decompression bomb: small on the wire, over the cap once decoded."""
    monkeypatch.setattr(fulltext, "_MAX_FILE_SIZE", 100_000)
    bomb = gzip.compress(b"a" * 5_000_000)
    assert len(bomb) < 100_000
    with _serving({"/a": (200, {"Content-Encoding": "gzip"}, bomb)}) as (port, _hits):
        net.route_to(port)
        with pytest.raises(fulltext.FetchRefused):
            fulltext._download("http://news.test/a")


def test_a_raw_gzip_body_is_not_expanded_when_decoded():
    """trafilatura's decode_file gunzips a body that merely looks compressed, with no cap, so a
    gzip served as text/html would expand past the size cap after it was enforced."""
    decoded = fulltext._decode(gzip.compress(b"<p>hello world</p>" * 1000))
    assert "hello world" not in decoded


def test_decoding_keeps_non_ascii_text():
    assert fulltext._decode("<p>Montréal, São Paulo</p>".encode()) == "<p>Montréal, São Paulo</p>"


def test_a_refused_fetch_is_logged_with_the_domain_only(net, caplog):
    net.names["intranet.test"] = ["10.9.8.7"]
    with caplog.at_level("INFO"):
        assert fulltext._fetch_one("A1", "http://intranet.test/secret-path", 4000) == ("A1", None)
    assert "A1" in caplog.text
    assert "intranet.test" in caplog.text
    assert "secret-path" not in caplog.text
