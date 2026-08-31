"""Tests for fulltext.py -- full-text fetch for SELECTED stories (trafilatura).

No live network: every test replaces the module-level ``trafilatura`` binding (or its
``fetch_url``/``extract`` attributes) with fakes. The invariant under test throughout is that
``fetch_for_selected`` NEVER raises and NEVER writes a URL into its output -- a network-dependent,
best-effort step that must not be able to break the run.
"""

import json
import logging
import sys
from concurrent.futures import Future
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import fulltext


class _FakeTrafilatura:
    """A fake trafilatura module: fetch_url/extract keyed by URL, so a test can script
    per-article outcomes (success text, None-download, None-extract, or a raise)."""

    def __init__(self, downloads: dict[str, str | None] | None = None, extracts: dict[str, str | None] | None = None):
        self.downloads = downloads or {}
        self.extracts = extracts or {}
        self.fetch_calls: list[str] = []

    def fetch_url(self, url, config=None):
        self.fetch_calls.append(url)
        result = self.downloads.get(url, "<html>default</html>")
        if isinstance(result, Exception):
            raise result
        return result

    def extract(self, downloaded, **_kwargs):
        result = self.extracts.get(downloaded, downloaded)
        if isinstance(result, Exception):
            raise result
        return result


def _write_selected(tmp_path, must_know=None, should_know=None):
    (tmp_path / "selected.json").write_text(
        json.dumps({"must_know": must_know or [], "should_know": should_know or []}), encoding="utf-8"
    )


def _write_index(tmp_path, entries: dict[str, str]):
    """entries: {article_id: url}."""
    (tmp_path / "article_index.json").write_text(
        json.dumps(
            {aid: {"url": url, "source_id": "src", "bias": "center", "name": "Src"} for aid, url in entries.items()}
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _fulltext_enabled(monkeypatch):
    monkeypatch.setattr(config, "FULLTEXT_ENABLED", True)
    monkeypatch.setattr(config, "FULLTEXT_PER_STORY", 3)
    monkeypatch.setattr(config, "FULLTEXT_MAX_CHARS", 4000)
    monkeypatch.setattr(config, "FULLTEXT_DEADLINE_S", 120)
    monkeypatch.setattr(config, "FULLTEXT_KILL_GRACE_S", 30)
    monkeypatch.setattr(config, "FULLTEXT_MAX_DOC_CHARS", 0)


@pytest.fixture(autouse=True)
def _collect_in_process(monkeypatch):
    """Run the fetch collector in-process instead of in its worker child.

    A child process re-imports the real trafilatura, which monkeypatching in THIS process
    cannot reach, so every test below that scripts a per-article outcome would hit the live
    network. The process bound itself is proved in test_fulltext_isolation.py.
    """

    def _inline(*args, **kwargs):
        return fulltext._collect_inline(*args, **kwargs), "completed"

    monkeypatch.setattr(fulltext, "_collect_isolated", _inline)


class TestTrafilaturaLoggerDoesNotLeakUrls:
    """trafilatura's own ``trafilatura.downloads`` logger logs full URLs on fetch failures
    (e.g. ``LOGGER.error("download error: %s %s", url, err)``). Importing fulltext must
    configure logging so those records never reach the root logger's handlers (stdout +
    rotating file) -- the ``no URLs in logs`` invariant applies to trafilatura's internal
    logging too, not just fulltext's own log lines."""

    def test_trafilatura_logger_does_not_propagate_to_root(self):
        # Import-time module config, asserted directly rather than fulltext's own logger.
        assert logging.getLogger("trafilatura").propagate is False

    def test_url_logged_by_trafilatura_downloads_never_reaches_root_handlers(self):
        root_records: list[logging.LogRecord] = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                root_records.append(record)

        root = logging.getLogger()
        handler = _CapturingHandler()
        root.addHandler(handler)
        try:
            # Simulate trafilatura.downloads emitting a record exactly like its real
            # "download error: %s %s" call, URL included.
            downloads_logger = logging.getLogger("trafilatura.downloads")
            downloads_logger.error("download error: %s %s", "https://example.com/secret-path/story", "boom")
        finally:
            root.removeHandler(handler)

        assert not any("http" in r.getMessage() for r in root_records)


class TestHappyPath:
    def test_writes_file_keyed_by_article_id_with_no_url(self, tmp_path, monkeypatch):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        _write_index(tmp_path, {"A1": "https://example.com/secret-path/story"})
        fake = _FakeTrafilatura(
            extracts={"<html>default</html>": "This is the full extracted article body. It has real sentences."}
        )
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        out_path = fulltext.fetch_for_selected(tmp_path)

        assert out_path == tmp_path / "article_fulltext.json"
        data = json.loads(out_path.read_text())
        assert data == {"A1": {"text": "This is the full extracted article body. It has real sentences."}}
        # The output must never leak the source URL -- that's the whole point of fetching in
        # Python instead of letting the model see the article page.
        assert "http" not in out_path.read_text()

    def test_returns_none_when_fulltext_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "FULLTEXT_ENABLED", False)
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        fake = _FakeTrafilatura()
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        assert fulltext.fetch_for_selected(tmp_path) is None
        assert not (tmp_path / "article_fulltext.json").exists()
        assert fake.fetch_calls == []  # disabled means no network activity at all


class TestPerArticleFailure:
    def test_failed_article_is_skipped_but_others_succeed(self, tmp_path, monkeypatch, caplog):
        _write_selected(
            tmp_path,
            must_know=[
                {"cluster_index": 0, "article_ids": ["A1"]},
                {"cluster_index": 1, "article_ids": ["A2"]},
            ],
        )
        _write_index(tmp_path, {"A1": "https://good.example.com/a", "A2": "https://bad.example.com/b"})
        fake = _FakeTrafilatura(
            downloads={"https://bad.example.com/b": None},  # fetch "succeeds" but returns nothing
            extracts={"<html>default</html>": "A perfectly good article body with enough text."},
        )
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        with caplog.at_level("INFO"):
            out_path = fulltext.fetch_for_selected(tmp_path)

        data = json.loads(out_path.read_text())
        assert set(data.keys()) == {"A1"}
        assert "A2" in caplog.text  # logged with the article_id
        assert "bad.example.com" in caplog.text  # domain only
        assert "https://bad.example.com/b" not in caplog.text  # never the full URL

    def test_fetch_exception_is_caught_and_skipped(self, tmp_path, monkeypatch):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        fake = _FakeTrafilatura(downloads={"https://example.com/a": ConnectionError("boom")})
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        # Must not raise, and (since it's the only article) must produce no file.
        assert fulltext.fetch_for_selected(tmp_path) is None


class TestAllFail:
    def test_all_articles_fail_yields_no_file_and_none(self, tmp_path, monkeypatch, caplog):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1", "A2"]}])
        _write_index(tmp_path, {"A1": "https://example.com/a", "A2": "https://example.com/b"})
        fake = _FakeTrafilatura(downloads={"https://example.com/a": None, "https://example.com/b": None})
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        with caplog.at_level("WARNING"):
            result = fulltext.fetch_for_selected(tmp_path)  # must not raise

        assert result is None
        assert not (tmp_path / "article_fulltext.json").exists()
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("0/2 articles extracted" in r.getMessage() for r in warnings)

    def test_missing_selected_json_yields_none_no_raise(self, tmp_path, monkeypatch, caplog):
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        with caplog.at_level("WARNING"):
            assert fulltext.fetch_for_selected(tmp_path) is None

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("selected.json" in m for m in warnings)

    def test_missing_article_index_yields_none_no_raise(self, tmp_path, monkeypatch, caplog):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        with caplog.at_level("WARNING"):
            assert fulltext.fetch_for_selected(tmp_path) is None

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("article_index.json" in m for m in warnings)

    def test_unexpected_exception_inside_fetch_is_never_raised(self, tmp_path, monkeypatch, caplog):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        _write_index(tmp_path, {"A1": "https://example.com/a"})

        def _boom(*_a, **_k):
            raise RuntimeError("totally unexpected bug")

        monkeypatch.setattr(fulltext, "_fetch_for_selected_inner", _boom)

        with caplog.at_level("WARNING"):
            assert fulltext.fetch_for_selected(tmp_path) is None

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("RuntimeError" in r.getMessage() and "totally unexpected bug" in r.getMessage() for r in warnings)
        # exc_info=True: the traceback must be attached, not just the stringified message.
        assert any(r.exc_info is not None for r in warnings)


class TestInputReadErrorsIdentifyTheFile:
    """A malformed input must name WHICH file failed -- selected.json and article_index.json
    are read in separate try/except blocks so a failure in one can't be misattributed to the
    other, and the shape-check warning names both the file and the actual type it found."""

    def test_malformed_selected_json_names_that_file(self, tmp_path, monkeypatch, caplog):
        (tmp_path / "selected.json").write_text("{not valid json", encoding="utf-8")
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        with caplog.at_level("WARNING"):
            assert fulltext.fetch_for_selected(tmp_path) is None

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("selected.json" in m and "article_index.json" not in m for m in warnings)

    def test_malformed_article_index_json_names_that_file(self, tmp_path, monkeypatch, caplog):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        (tmp_path / "article_index.json").write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        with caplog.at_level("WARNING"):
            assert fulltext.fetch_for_selected(tmp_path) is None

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("article_index.json" in m for m in warnings)

    def test_selected_json_wrong_shape_names_file_and_type(self, tmp_path, monkeypatch, caplog):
        (tmp_path / "selected.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        with caplog.at_level("WARNING"):
            assert fulltext.fetch_for_selected(tmp_path) is None

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("selected.json" in m and "list" in m for m in warnings)


class TestStaleFileHazard:
    """A pre-existing article_fulltext.json must never survive an ENABLED attempt that then
    early-returns None -- WRITE would Read the stale file and think it's fresh. The disabled
    path is the one exception: it's a true no-op and must not touch the file at all."""

    def test_stale_file_removed_when_selected_json_missing(self, tmp_path, monkeypatch):
        stale = tmp_path / "article_fulltext.json"
        stale.write_text(json.dumps({"A99": {"text": "yesterday's leftovers"}}), encoding="utf-8")
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        assert fulltext.fetch_for_selected(tmp_path) is None
        assert not stale.exists()

    def test_stale_file_removed_when_all_fetches_fail(self, tmp_path, monkeypatch):
        stale = tmp_path / "article_fulltext.json"
        stale.write_text(json.dumps({"A99": {"text": "yesterday's leftovers"}}), encoding="utf-8")
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        fake = _FakeTrafilatura(downloads={"https://example.com/a": None})
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        assert fulltext.fetch_for_selected(tmp_path) is None
        assert not stale.exists()

    def test_stale_file_left_untouched_when_fulltext_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "FULLTEXT_ENABLED", False)
        stale = tmp_path / "article_fulltext.json"
        stale.write_text(json.dumps({"A99": {"text": "yesterday's leftovers"}}), encoding="utf-8")
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        assert fulltext.fetch_for_selected(tmp_path) is None
        # Disabled is a true no-op: the stale file (an accepted hazard of a toggled-off-mid-day
        # resume, documented in fetch_for_selected's docstring) is left exactly as-is.
        assert stale.exists()
        assert json.loads(stale.read_text()) == {"A99": {"text": "yesterday's leftovers"}}


class TestAtomicWrite:
    def test_no_tmp_file_left_behind_on_success(self, tmp_path, monkeypatch):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        _write_index(tmp_path, {"A1": "https://example.com/a"})
        fake = _FakeTrafilatura(extracts={"<html>default</html>": "A perfectly good article body right here."})
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        out_path = fulltext.fetch_for_selected(tmp_path)

        assert out_path is not None
        assert list(tmp_path.glob("*.tmp*")) == []


class TestTruncation:
    def test_text_under_cap_is_unchanged(self):
        text = "Short article text."
        assert fulltext.truncate_at_sentence(text, 4000) == text

    def test_truncates_at_last_sentence_boundary_with_marker(self):
        text = "First sentence here. Second sentence follows nicely. This third one gets cut off m"
        # Cap lands mid-way through the third sentence.
        cap = len("First sentence here. Second sentence follows nicely. This third one")
        result = fulltext.truncate_at_sentence(text, cap)

        assert result == "First sentence here. Second sentence follows nicely.\n[truncated]"
        assert result.endswith("[truncated]")
        # The cut-off third sentence must not appear at all (no completing a fact).
        assert "third one" not in result

    def test_falls_back_to_hard_cut_when_no_sentence_boundary(self):
        text = "a" * 5000  # one giant token, no punctuation anywhere
        result = fulltext.truncate_at_sentence(text, 100)

        assert result == ("a" * 100) + "\n[truncated]"


class TestCapAndDedupe:
    def test_only_first_n_article_ids_per_story_are_fetched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "FULLTEXT_PER_STORY", 2)
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1", "A2", "A3", "A4"]}])
        _write_index(tmp_path, {f"A{i}": f"https://example.com/{i}" for i in range(1, 5)})
        fake = _FakeTrafilatura(extracts={"<html>default</html>": "Some article body text right here."})
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        out_path = fulltext.fetch_for_selected(tmp_path)

        data = json.loads(out_path.read_text())
        assert set(data.keys()) == {"A1", "A2"}
        assert sorted(fake.fetch_calls) == ["https://example.com/1", "https://example.com/2"]

    def test_article_shared_across_stories_is_fetched_once(self, tmp_path, monkeypatch):
        _write_selected(
            tmp_path,
            must_know=[{"cluster_index": 0, "article_ids": ["A1"]}],
            should_know=[{"cluster_index": 1, "article_ids": ["A1", "A2"]}],
        )
        _write_index(tmp_path, {"A1": "https://example.com/1", "A2": "https://example.com/2"})
        fake = _FakeTrafilatura(extracts={"<html>default</html>": "Some article body text right here."})
        monkeypatch.setattr(fulltext, "trafilatura", fake)

        fulltext.fetch_for_selected(tmp_path)

        assert fake.fetch_calls.count("https://example.com/1") == 1


class TestDeadline:
    def test_deadline_hit_takes_finished_work_and_skips_the_rest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "FULLTEXT_DEADLINE_S", 0)  # deadline already elapsed
        _write_selected(tmp_path, must_know=[{"cluster_index": 0, "article_ids": ["A1"]}])
        _write_index(tmp_path, {"A1": "https://example.com/a"})

        # A future that is submitted but will never be reported "done" within a 0s wait().
        never_done: Future = Future()
        monkeypatch.setattr(
            fulltext.ThreadPoolExecutor,
            "submit",
            lambda self, fn, *a, **k: never_done,
        )

        result = fulltext.fetch_for_selected(tmp_path)  # must not hang, must not raise

        assert result is None  # nothing finished before the (zero) deadline


class TestSelectedSchema:
    def test_ignores_stories_missing_article_ids(self, tmp_path, monkeypatch):
        _write_selected(tmp_path, must_know=[{"cluster_index": 0}])  # malformed: no article_ids
        _write_index(tmp_path, {})
        monkeypatch.setattr(fulltext, "trafilatura", _FakeTrafilatura())

        assert fulltext.fetch_for_selected(tmp_path) is None
