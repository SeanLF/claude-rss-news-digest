"""Full-text fetch and extract (trafilatura) for the digest workflow's `fetchFulltext` activity.

The TypeScript side plans the tasks and stores the result (digest/src/activities/fulltext.ts); this
module only turns (article_id, url) pairs into extracted text. Forked from newsroom/src/fulltext.py,
which the still-deployed newsroom pipeline keeps using until the cut-over.

THE INVARIANT: no URL reaches a model or a log line. Results are keyed by article_id; log lines name
the domain only.

Best-effort: a failed fetch, a dead worker or a bug here is "no full text" for that article, never
an exception out of `_collect_isolated`.

THE BOUND: no fetch may outlive the step. A thread cannot be killed from Python, so the fetching
runs in a CHILD PROCESS (`python -m fulltext --worker`, fed its task list on stdin, emitting
results as JSONL on stdout) that the parent kills when the hard bound expires. The child keeps
the in-process deadline as its own soft budget, so the kill is only ever the backstop.
See docs/lessons/a-deadline-on-the-waiter-does-not-bound-the-worker.md.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit

import certifi
import settings
import trafilatura
import urllib3
from trafilatura.utils import detect_encoding
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

logger = logging.getLogger(__name__)

# trafilatura's own internal logging (e.g. trafilatura.downloads: `LOGGER.error("download
# error: %s %s", url, err)`) logs the FULL url on fetch failures. By default that record
# propagates up to the root logger's handlers (stdout + the rotating file), which would leak
# the exact thing this module exists to keep away from the model and the logs (see module
# docstring). Cutting propagation at the "trafilatura" logger stops every child logger under it
# (trafilatura.downloads, trafilatura.core, ...) from reaching root, without silencing our own
# `logger` (module-scoped, name "fulltext", unaffected by this).
logging.getLogger("trafilatura").propagate = False

_MAX_WORKERS = 6
# Per connect and per read, as trafilatura's DOWNLOAD_TIMEOUT was set here.
_PER_FETCH_TIMEOUT_S = 10
# The whole fetch, redirects included. The per-read timeout never fires on a host that sends a byte
# every few seconds, which then held a thread for the step's whole budget. Six threads share a
# 120 s step, about 57 fetches, so a fetch gets ~12 s on average; 20 s leaves slow hosts room.
_PER_FETCH_WALL_S = 20

# Puts this module into worker mode (see _worker_main). The task list goes over stdin, never
# argv: argv is world-readable in `ps` and the task list contains URLs.
_WORKER_FLAG = "--worker"

# Ceiling on the child's log relay: a broken worker must not flood the shared run log.
_MAX_RELAYED_LOG_LINES = 200
_LOG_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}

# A truncated extract may end mid-sentence or mid-number ("...nearly 50"). WRITE has a
# no-completing-cut-off-text rule; without an explicit marker it can't tell a true cut from a
# source that just ends there, and risks "completing" the fact. Matches sentence-ending
# punctuation followed by whitespace or end-of-string.
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
_TRUNCATION_MARKER = "\n[truncated]"


def _domain(url: str) -> str:
    """The domain only, for logging -- never the full URL (see module docstring)."""
    try:
        return urlparse(url).netloc or "unknown"
    except ValueError:
        return "unknown"


class FetchRefused(Exception):
    """The fetch would leave the public internet, or break a limit. Never carries the URL."""


# trafilatura.fetch_url followed redirects into internal addresses, so the fetch is ours: every hop is
# resolved, checked and connected to by address, which leaves no second lookup for DNS to answer
# differently. trafilatura still does the extraction.
_MAX_REDIRECTS = 2  # trafilatura's MAX_REDIRECTS
_MAX_FILE_SIZE = 20_000_000  # trafilatura's MAX_FILE_SIZE, on the decoded body, so it caps a bomb too
_MIN_FILE_SIZE = 10  # trafilatura's MIN_FILE_SIZE
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_HEADERS = {
    **urllib3.util.make_headers(accept_encoding=True),
    "User-Agent": f"trafilatura/{importlib.metadata.version('trafilatura')} (+https://github.com/adbar/trafilatura)",
}
_getaddrinfo = socket.getaddrinfo


def _connect(family: int, sockaddr: tuple, timeout: float | None) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Globally routable unicast. is_global alone admits multicast, and an IPv6 address can carry
    an IPv4 one (mapped, 6to4, Teredo) that has to pass on its own."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = [ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None]
        if any(v4 is not None and not _is_public(v4) for v4 in embedded):
            return False
    return ip.is_global and not ip.is_multicast


def _resolve(host: str, port: int, allow: frozenset[tuple[str, int]]) -> list[tuple]:
    """Every address `host` resolves to, or FetchRefused if any one is not public: a name with one
    private answer among public ones is treated as private, not tried until something connects.

    `allow` names exact (ip, port) pairs exempt from the check. Only tests pass it, to reach a
    loopback server; the activity never does.
    """
    infos = _getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not infos:
        raise FetchRefused(f"{host} resolves to nothing")
    for _family, _type, _proto, _name, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        if not _is_public(ip) and (str(ip), port) not in allow:
            raise FetchRefused(f"{host} resolves to a non-public address")
    return infos


class _Guard:
    """One fetch's connection policy and wall clock, handed to each connection urllib3 opens for it.

    At the limit a timer shuts down every socket the fetch opened, which unblocks a read in progress
    whatever the per-read timeout says. Sockets, not connections: http.client drops the connection's
    socket when the response says `Connection: close`, while the response goes on reading it. A TLS
    handshake in progress has no socket to shut down (the raw one is detached, the wrapped one not yet
    registered), so connect() gives the socket a timeout no longer than the time left, and CPython
    bounds a whole handshake by it.
    """

    def __init__(self, allow: frozenset[tuple[str, int]], wall_s: float):
        self.allow = allow
        self.deadline = time.monotonic() + wall_s
        self.expired = False
        self._socks: list[socket.socket] = []
        self._lock = threading.Lock()
        self._timer = threading.Timer(wall_s, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def remaining(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0 or self.expired:
            raise FetchRefused("over the wall-clock limit")
        return left

    def _expire(self) -> None:
        with self._lock:
            self.expired = True
            socks = list(self._socks)
        for sock in socks:
            _shutdown(sock)

    def register(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.append(sock)
            expired = self.expired
        if expired:
            _shutdown(sock)

    def cancel(self) -> None:
        self._timer.cancel()

    def connect(self, conn: HTTPConnection) -> socket.socket:
        infos = _resolve(conn.host, conn.port, self.allow)
        timeout = conn.timeout if isinstance(conn.timeout, (int, float)) else _PER_FETCH_TIMEOUT_S
        last: OSError | None = None
        for family, _type, _proto, _name, sockaddr in infos:
            try:
                sock = _connect(family, sockaddr, min(timeout, self.remaining()))
            except OSError as e:
                last = e
                continue
            self.register(sock)
            return sock
        raise last or OSError("no address to connect to")


def _shutdown(sock: socket.socket) -> None:
    # socket.socket's, not SSLSocket's: that one unwraps TLS under a reading thread. A closed or
    # detached socket has no descriptor left, so this cannot reach one the OS has since reused.
    with contextlib.suppress(OSError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


class _GuardedHTTPConnection(HTTPConnection):
    def __init__(self, *args, guard: _Guard, **kwargs):
        self._guard = guard
        super().__init__(*args, **kwargs)

    def _new_conn(self) -> socket.socket:
        return self._guard.connect(self)


class _GuardedHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, guard: _Guard, **kwargs):
        self._guard = guard
        super().__init__(*args, **kwargs)

    def _new_conn(self) -> socket.socket:
        return self._guard.connect(self)

    def connect(self) -> None:
        super().connect()
        self._guard.register(self.sock)  # the TLS socket the response will read


class _GuardedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


def _download(url: str, *, allow: frozenset[tuple[str, int]] = frozenset()) -> bytes | None:
    """GET `url`, following at most _MAX_REDIRECTS redirects, each hop checked as the first was.

    Returns the decoded body of a 200, None for any other status or a body under the minimum size,
    and raises FetchRefused for a hop off the public internet or over a limit.
    """
    guard = _Guard(allow, _PER_FETCH_WALL_S)
    try:
        return _follow(url, guard)
    except FetchRefused:
        raise
    except Exception as e:
        if guard.expired:  # the timer cut the socket; the read error is its symptom
            raise FetchRefused("over the wall-clock limit") from e
        raise
    finally:
        guard.cancel()


def _follow(url: str, guard: _Guard) -> bytes | None:
    timeout = urllib3.Timeout(connect=_PER_FETCH_TIMEOUT_S, read=_PER_FETCH_TIMEOUT_S)
    for hop in range(_MAX_REDIRECTS + 1):
        guard.remaining()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchRefused(f"not an http(s) URL on hop {hop}")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as e:
            raise FetchRefused("unreadable port") from e
        if parts.scheme == "https":
            pool = _GuardedHTTPSPool(
                parts.hostname, port, timeout=timeout, maxsize=1, retries=False, guard=guard,
                cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
            )  # fmt: skip
        else:
            pool = _GuardedHTTPPool(parts.hostname, port, timeout=timeout, maxsize=1, retries=False, guard=guard)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            response = pool.urlopen(
                "GET",
                path,
                headers=_HEADERS,
                redirect=False,
                retries=False,
                preload_content=False,
                assert_same_host=False,
            )
            try:
                if response.status in _REDIRECTS:
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    return None
                data = bytearray()
                for chunk in response.stream(2**17, decode_content=True):
                    data.extend(chunk)
                    if len(data) > _MAX_FILE_SIZE:
                        raise FetchRefused(f"body over {_MAX_FILE_SIZE} bytes")
            finally:
                response.release_conn()
        finally:
            pool.close()
        # A cut socket can end a close-delimited body cleanly; what was read is not the article.
        guard.remaining()
        return bytes(data) if len(data) >= _MIN_FILE_SIZE else None
    raise FetchRefused(f"more than {_MAX_REDIRECTS} redirects")


def _decode(data: bytes) -> str:
    """trafilatura's decode_file without its gunzip, which has no size cap: a body that is still
    compressed after the transfer decoding is not an article."""
    for encoding in detect_encoding(data):
        try:
            return data.decode(encoding)
        except LookupError, UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate ``text`` to at most ``max_chars``, cutting at the last sentence boundary.

    Text at or under the cap is returned unchanged. Over the cap, cuts at the last
    sentence-ending punctuation within the window and appends a truncation marker so a
    downstream consumer can never mistake the cut for a complete fact. Falls back to a hard cut
    at the cap if no sentence boundary is found in the window (e.g. one very long sentence).
    """
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    matches = list(_SENTENCE_END_RE.finditer(window))
    if matches:
        window = window[: matches[-1].end()]
    return window.rstrip() + _TRUNCATION_MARKER


def _fetch_one(
    article_id: str, url: str, max_chars: int, max_doc_chars: int = 0, allow: frozenset[tuple[str, int]] = frozenset()
) -> tuple[str, str | None]:
    """Fetch + extract one article. Returns (article_id, text) on success, (article_id, None) on
    any failure -- never raises, so one bad article can't take down the batch."""
    try:
        body = _download(url, allow=allow)
    except FetchRefused as e:
        logger.info("fulltext: fetch refused for %s (%s): %s", article_id, _domain(url), e)
        return article_id, None
    except Exception as e:  # urllib3, ssl and socket errors are not enumerable
        # The type only: urllib3's messages name the request path.
        logger.info("fulltext: fetch failed for %s (%s): %s", article_id, _domain(url), type(e).__name__)
        return article_id, None
    if not body:
        logger.info("fulltext: fetch returned nothing for %s (%s)", article_id, _domain(url))
        return article_id, None
    downloaded = _decode(body)

    # Cheap pre-parse cap: extraction cost is superlinear in node count. A heuristic, NOT the
    # bound -- a small document can still be pathological, which is what _collect_isolated is for.
    if max_doc_chars > 0 and len(downloaded) > max_doc_chars:
        logger.info(
            "fulltext: document too large for %s (%s): %d > %d chars, skipping",
            article_id,
            _domain(url),
            len(downloaded),
            max_doc_chars,
        )
        return article_id, None

    try:
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    except Exception as e:
        logger.info("fulltext: extract failed for %s (%s): %s: %s", article_id, _domain(url), type(e).__name__, e)
        return article_id, None
    if not text or not text.strip():
        logger.info("fulltext: extract returned nothing for %s (%s)", article_id, _domain(url))
        return article_id, None

    return article_id, truncate_at_sentence(text.strip(), max_chars)


def _collect_inline(
    tasks: list[tuple[str, str]],
    *,
    max_chars: int,
    deadline_s: float,
    max_doc_chars: int,
    on_result: Callable[[str, str], None] | None = None,
    allow: frozenset[tuple[str, int]] = frozenset(),
) -> tuple[dict[str, str], int]:
    """Fetch + extract every task on a thread pool, taking whatever finished by ``deadline_s``.
    Returns the results and how many fetches the deadline left unfinished.

    The body of the worker process, and the ONLY place the network is touched. ``deadline_s`` is
    a SOFT budget -- a thread inside a C extension cannot be cancelled, so work still running when
    it passes keeps running; the hard bound is `_collect_isolated`'s kill.

    ``on_result`` fires as each extraction lands, because under a hard kill only what has already
    been handed over survives.
    """
    results: dict[str, str] = {}
    unfinished = 0
    collected = 0
    executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    try:
        futures = {executor.submit(_fetch_one, aid, url, max_chars, max_doc_chars, allow): aid for aid, url in tasks}
        try:
            for future in as_completed(futures, timeout=deadline_s):
                collected += 1
                aid, text = future.result()
                if text:
                    results[aid] = text
                    if on_result is not None:
                        on_result(aid, text)
        except TimeoutError:
            # Not `not f.done()`: a fetch that finished after the timeout was never collected either.
            unfinished = len(futures) - collected
            logger.warning(
                "fulltext: deadline (%ss) hit, %d/%d fetches still in flight, taking what finished",
                deadline_s,
                unfinished,
                len(futures),
            )
    finally:
        # Cancels only what has not STARTED, and declines to join the rest. Sound only because
        # this process is itself bounded from outside.
        executor.shutdown(wait=False, cancel_futures=True)
    return results, unfinished


def _worker_command() -> list[str]:
    """The argv for the fetch worker. A separate function so a test can substitute a stand-in
    child and exercise the kill path without a network or a parser."""
    return [sys.executable, "-m", "fulltext", _WORKER_FLAG]


def _relay_worker_logs(stderr: bytes) -> None:
    """Re-emit the child's log lines through this process's logger.

    The child's own stderr reaches neither the run's rotating file nor `caplog`. Levels are
    carried as a line prefix; a line without one is abnormal output (an interpreter traceback,
    say) and is surfaced at WARNING rather than quietly dropped.
    """
    lines = [ln for ln in stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
    for line in lines[:_MAX_RELAYED_LOG_LINES]:
        level_name, _, rest = line.partition(" ")
        level = _LOG_LEVELS.get(level_name)
        logger.log(level if level is not None else logging.WARNING, "%s", rest if level is not None else line)
    if len(lines) > _MAX_RELAYED_LOG_LINES:
        logger.warning("fulltext: worker emitted %d more log lines, suppressed", len(lines) - _MAX_RELAYED_LOG_LINES)


def _parse_worker_results(stdout: bytes) -> tuple[dict[str, str], int, str | None]:
    """Read the worker's JSONL results, the count of lines that could not be read, and the
    worker's own status line (`{"outcome": ...}`), if it wrote one.

    A killed worker's last line can be a partial write, so an unparseable line is skipped rather
    than discarding the batch with it. The count is returned because a silent skip and an empty
    batch are otherwise the same observation.
    """
    results: dict[str, str] = {}
    skipped = 0
    status: str | None = None
    for line in stdout.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict) and set(row) == {"outcome"} and isinstance(row["outcome"], str):
                status = row["outcome"]
                continue
            aid, text = row["id"], row["text"]
        except ValueError, KeyError, TypeError:
            skipped += 1
            continue
        if isinstance(aid, str) and isinstance(text, str) and text:
            results[aid] = text
        else:
            skipped += 1
    return results, skipped, status


def _collect_isolated(
    tasks: list[tuple[str, str]],
    *,
    max_chars: int,
    deadline_s: float,
    max_doc_chars: int,
    allow: frozenset[tuple[str, int]] = frozenset(),
) -> tuple[dict[str, str], str]:
    """Run `_collect_inline` in a child process the parent can kill, and return what it produced.

    THE BOUND (docs/lessons/a-deadline-on-the-waiter-does-not-bound-the-worker.md). `deadline_s`
    is the child's soft budget; this adds `settings.FULLTEXT_KILL_GRACE_S` and enforces the total
    with a SIGKILL, the only thing that reliably stops a runaway lxml parse -- it holds the GIL,
    so no in-process timer can be counted on to be scheduled at all.

    Results come back as JSONL on stdout, flushed per line, so a kill costs only the unfinished
    fetches. `subprocess.run` attaches partial output to the TimeoutExpired it raises, which is
    what lets the killed path and the normal path share the code below.

    Never raises: a worker that cannot start, crashes, or is killed is the same "no full text"
    outcome as a batch of failed fetches, and the caller falls back to the CSV summaries.
    """
    src_dir = str(Path(__file__).resolve().parent)
    payload = json.dumps(
        {
            "tasks": [[aid, url] for aid, url in tasks],
            "max_chars": max_chars,
            "deadline_s": deadline_s,
            "max_doc_chars": max_doc_chars,
            "allow": sorted(allow),
        }
    ).encode()
    # The worker is `python -m fulltext`, so this module's directory has to be importable in the
    # child. Prepending rather than replacing keeps any PYTHONPATH the run was started with.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([src_dir, env["PYTHONPATH"]]) if env.get("PYTHONPATH") else src_dir
    hard_deadline = deadline_s + settings.FULLTEXT_KILL_GRACE_S

    try:
        completed = subprocess.run(
            _worker_command(),
            input=payload,
            capture_output=True,
            timeout=hard_deadline,
            env=env,
            cwd=src_dir,
            check=False,
        )
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as e:
        stdout, stderr, returncode = e.output or b"", e.stderr or b"", None
        results, skipped, _status = _parse_worker_results(stdout)
        _relay_worker_logs(stderr)
        if skipped:
            logger.warning("fulltext: %d unreadable result line(s) from the killed worker", skipped)
        logger.warning(
            "fulltext: worker exceeded its hard bound (%ss = %ss deadline + %ss grace) and was killed, "
            "keeping the %d result(s) it had already emitted",
            hard_deadline,
            deadline_s,
            settings.FULLTEXT_KILL_GRACE_S,
            len(results),
        )
        return results, "killed"
    except OSError as e:  # the interpreter is missing, fork failed, ...
        logger.warning("fulltext: could not start the fetch worker: %s: %s", type(e).__name__, e)
        return {}, "spawn_failed"

    _relay_worker_logs(stderr)
    results, skipped, status = _parse_worker_results(stdout)
    if skipped:
        logger.warning("fulltext: %d unreadable result line(s) from the worker", skipped)
    if returncode != 0:
        # A crashed worker is a failed batch, not a failed run.
        logger.warning("fulltext: worker exited %s, keeping whatever it emitted first", returncode)
        return results, f"crashed:{returncode}"
    # Fetches the deadline cut short were never tried to the end: not settled, so a resume retries.
    if status == "deadline":
        return results, "deadline"
    return results, "completed"


def _configure_worker_logging(stream) -> None:
    """Send this module's records to ``stream`` (the worker's stderr), with the level as a bare
    prefix so `_relay_worker_logs` can put each one back at the level it was written at.

    ONLY this module's own records: the stream is relayed verbatim into the run's log, and
    `trafilatura.downloads` and `urllib3.connectionpool` both log full URLs or article paths
    underneath. Filtering on the record's origin keeps that a closed set rather than a list of
    offenders to maintain.
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(lambda record: record.name == logger.name)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _worker_main(argv: list[str]) -> int:
    """The child process: read a task list on stdin, emit results as JSONL on stdout.

    Every parameter arrives on stdin rather than from `config`, so the child cannot silently
    disagree with the parent about its own budget. Logs go to stderr with the level as a bare
    prefix (see `_relay_worker_logs`); results are flushed line by line so a SIGKILL costs only
    the fetches that had not finished.
    """
    if _WORKER_FLAG not in argv:
        print(f"usage: python -m fulltext {_WORKER_FLAG}  (reads a task list on stdin)", file=sys.stderr)
        return 2
    _configure_worker_logging(sys.stderr)

    try:
        request = json.loads(sys.stdin.buffer.read())
        tasks = [(str(aid), str(url)) for aid, url in request["tasks"]]
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("fulltext: worker got an unreadable request: %s: %s", type(e).__name__, e)
        return 1

    def _emit(article_id: str, text: str) -> None:
        sys.stdout.write(json.dumps({"id": article_id, "text": text}) + "\n")
        sys.stdout.flush()

    try:
        _results, unfinished = _collect_inline(
            tasks,
            max_chars=int(request["max_chars"]),
            deadline_s=float(request["deadline_s"]),
            max_doc_chars=int(request["max_doc_chars"]),
            on_result=_emit,
            allow=frozenset((str(ip), int(port)) for ip, port in request.get("allow", [])),
        )
    except Exception as e:  # a bug in here is a failed batch, never a failed run
        # Our own record, not a bare traceback: the relay would re-emit that line by line.
        logger.warning("fulltext: worker failed: %s: %s", type(e).__name__, e, exc_info=True)
        return 1
    if unfinished:
        sys.stdout.write(json.dumps({"outcome": "deadline"}) + "\n")
    return 0


if __name__ == "__main__":
    # `python -m fulltext --worker`, spawned by _collect_isolated. Never a pipeline entry point.
    _code = _worker_main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    # os._exit, not sys.exit: concurrent.futures' atexit hook JOINS its non-daemon pool threads,
    # so a normal exit would block on the very parse we gave up waiting on and turn the soft
    # deadline into the hard one. Safe -- everything worth keeping was flushed as it was produced.
    os._exit(_code)
