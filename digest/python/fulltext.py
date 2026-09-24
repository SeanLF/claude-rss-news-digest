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

import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import settings
import trafilatura
from trafilatura.settings import use_config

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
_PER_FETCH_TIMEOUT_S = 10

# Puts this module into worker mode (see _worker_main). The task list goes over stdin, never
# argv: argv is world-readable in `ps` and the task list contains URLs.
_WORKER_FLAG = "--worker"

# Ceiling on the child's log relay: a broken worker must not flood the shared run log.
_MAX_RELAYED_LOG_LINES = 200
_LOG_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}

_config_lock_value = None  # lazily-built trafilatura Config, module-cached (see _trafilatura_config)

# A truncated extract may end mid-sentence or mid-number ("...nearly 50"). WRITE has a
# no-completing-cut-off-text rule; without an explicit marker it can't tell a true cut from a
# source that just ends there, and risks "completing" the fact. Matches sentence-ending
# punctuation followed by whitespace or end-of-string.
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
_TRUNCATION_MARKER = "\n[truncated]"


def _trafilatura_config():
    """A trafilatura Config with an explicit, shorter-than-default download timeout.

    trafilatura's own default (settings.cfg: DOWNLOAD_TIMEOUT=30) is too generous for a
    concurrent batch bounded by an overall step deadline -- one slow/hanging host could eat a
    large share of the deadline on its own. Built once and cached at module scope (a ConfigParser
    build is cheap but there's no reason to redo it per article).

    MAX_FILE_SIZE stays at trafilatura's 20 MB default: it is enforced by aborting the stream
    mid-response, which is indistinguishable from "fetch returned nothing". FULLTEXT_MAX_DOC_CHARS
    is applied after the download in `_fetch_one` instead, so an oversized document says so.
    """
    global _config_lock_value
    if _config_lock_value is None:
        cfg = use_config()
        cfg.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(_PER_FETCH_TIMEOUT_S))
        _config_lock_value = cfg
    return _config_lock_value


def _domain(url: str) -> str:
    """The domain only, for logging -- never the full URL (see module docstring)."""
    try:
        return urlparse(url).netloc or "unknown"
    except ValueError:
        return "unknown"


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


def _fetch_one(article_id: str, url: str, max_chars: int, max_doc_chars: int = 0) -> tuple[str, str | None]:
    """Fetch + extract one article. Returns (article_id, text) on success, (article_id, None) on
    any failure -- never raises, so one bad article can't take down the batch."""
    try:
        downloaded = trafilatura.fetch_url(url, config=_trafilatura_config())
    except Exception as e:  # network/parsing errors from trafilatura's stack are not enumerable
        logger.info("fulltext: fetch failed for %s (%s): %s: %s", article_id, _domain(url), type(e).__name__, e)
        return article_id, None
    if not downloaded:
        logger.info("fulltext: fetch returned nothing for %s (%s)", article_id, _domain(url))
        return article_id, None

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
) -> dict[str, str]:
    """Fetch + extract every task on a thread pool, taking whatever finished by ``deadline_s``.

    The body of the worker process, and the ONLY place the network is touched. ``deadline_s`` is
    a SOFT budget -- a thread inside a C extension cannot be cancelled, so work still running when
    it passes keeps running; the hard bound is `_collect_isolated`'s kill.

    ``on_result`` fires as each extraction lands, because under a hard kill only what has already
    been handed over survives.
    """
    results: dict[str, str] = {}
    executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    try:
        futures = {executor.submit(_fetch_one, aid, url, max_chars, max_doc_chars): aid for aid, url in tasks}
        try:
            for future in as_completed(futures, timeout=deadline_s):
                aid, text = future.result()
                if text:
                    results[aid] = text
                    if on_result is not None:
                        on_result(aid, text)
        except TimeoutError:
            logger.warning(
                "fulltext: deadline (%ss) hit, %d/%d fetches still in flight, taking what finished",
                deadline_s,
                sum(1 for f in futures if not f.done()),
                len(futures),
            )
    finally:
        # Cancels only what has not STARTED, and declines to join the rest. Sound only because
        # this process is itself bounded from outside.
        executor.shutdown(wait=False, cancel_futures=True)
    return results


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


def _parse_worker_results(stdout: bytes) -> tuple[dict[str, str], int]:
    """Read the worker's JSONL results, and the count of lines that could not be read.

    A killed worker's last line can be a partial write, so an unparseable line is skipped rather
    than discarding the batch with it. The count is returned because a silent skip and an empty
    batch are otherwise the same observation.
    """
    results: dict[str, str] = {}
    skipped = 0
    for line in stdout.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            aid, text = row["id"], row["text"]
        except ValueError, KeyError, TypeError:
            skipped += 1
            continue
        if isinstance(aid, str) and isinstance(text, str) and text:
            results[aid] = text
        else:
            skipped += 1
    return results, skipped


def _collect_isolated(
    tasks: list[tuple[str, str]], *, max_chars: int, deadline_s: float, max_doc_chars: int
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
        results, skipped = _parse_worker_results(stdout)
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
    results, skipped = _parse_worker_results(stdout)
    if skipped:
        logger.warning("fulltext: %d unreadable result line(s) from the worker", skipped)
    if returncode != 0:
        # A crashed worker is a failed batch, not a failed run.
        logger.warning("fulltext: worker exited %s, keeping whatever it emitted first", returncode)
        return results, f"crashed:{returncode}"
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
        _collect_inline(
            tasks,
            max_chars=int(request["max_chars"]),
            deadline_s=float(request["deadline_s"]),
            max_doc_chars=int(request["max_doc_chars"]),
            on_result=_emit,
        )
    except Exception as e:  # a bug in here is a failed batch, never a failed run
        # Our own record, not a bare traceback: the relay would re-emit that line by line.
        logger.warning("fulltext: worker failed: %s: %s", type(e).__name__, e, exc_info=True)
        return 1
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
