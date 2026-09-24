"""Tests for fulltext.py's in-process collector, with trafilatura faked.

Forked from newsroom/tests/test_fulltext.py. The planning and storing tests (selected.json,
article_index.json, the output file) stay there: that half lives in digest/src/activities/fulltext.ts
here. What stays is the collector: per-article failures never raise, results carry no URL, the
deadline takes what finished. The process bound is proved in test_fulltext_isolation.py.
"""

import logging
from concurrent.futures import Future

import fulltext


class _FakeTrafilatura:
    """A fake download and a fake trafilatura module, keyed by URL, so a test can script
    per-article outcomes (success text, None-download, None-extract, or a raise)."""

    def __init__(self, downloads: dict[str, str | None] | None = None, extracts: dict[str, str | None] | None = None):
        self.downloads = downloads or {}
        self.extracts = extracts or {}
        self.fetch_calls: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(fulltext, "trafilatura", self)
        monkeypatch.setattr(fulltext, "_download", self.download)

    def download(self, url, allow=frozenset()):
        self.fetch_calls.append(url)
        result = self.downloads.get(url, "<html>default</html>")
        if isinstance(result, Exception):
            raise result
        return None if result is None else result.encode()

    def extract(self, downloaded, **_kwargs):
        result = self.extracts.get(downloaded, downloaded)
        if isinstance(result, Exception):
            raise result
        return result


def _collect(tasks, deadline_s=120):
    return fulltext._collect_inline(tasks, max_chars=4000, deadline_s=deadline_s, max_doc_chars=0)


class TestTrafilaturaLoggerDoesNotLeakUrls:
    """trafilatura.downloads logs full URLs on fetch failures; importing fulltext must keep those
    records away from the root logger's handlers."""

    def test_trafilatura_logger_does_not_propagate_to_root(self):
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
            logging.getLogger("trafilatura.downloads").error(
                "download error: %s %s", "https://example.com/secret-path/story", "boom"
            )
        finally:
            root.removeHandler(handler)

        assert not any("http" in r.getMessage() for r in root_records)


class TestCollector:
    def test_results_are_keyed_by_article_id_with_no_url(self, monkeypatch):
        fake = _FakeTrafilatura(extracts={"<html>default</html>": "The full extracted article body. Real sentences."})
        fake.install(monkeypatch)

        assert _collect([("A1", "https://example.com/secret-path/story")]) == {
            "A1": "The full extracted article body. Real sentences."
        }

    def test_failed_article_is_skipped_but_others_succeed(self, monkeypatch, caplog):
        fake = _FakeTrafilatura(
            downloads={"https://bad.example.com/b": None},
            extracts={"<html>default</html>": "A perfectly good article body with enough text."},
        )
        fake.install(monkeypatch)

        with caplog.at_level("INFO"):
            results = _collect([("A1", "https://good.example.com/a"), ("A2", "https://bad.example.com/b")])

        assert set(results) == {"A1"}
        assert "A2" in caplog.text  # logged with the article_id
        assert "bad.example.com" in caplog.text  # domain only
        assert "https://bad.example.com/b" not in caplog.text  # never the full URL

    def test_fetch_and_extract_exceptions_are_caught_and_skipped(self, monkeypatch):
        fake = _FakeTrafilatura(
            downloads={"https://example.com/a": ConnectionError("boom")},
            extracts={"<html>default</html>": RuntimeError("parser bug")},
        )
        fake.install(monkeypatch)

        assert _collect([("A1", "https://example.com/a"), ("A2", "https://example.com/b")]) == {}

    def test_results_are_handed_over_as_they_land(self, monkeypatch):
        fake = _FakeTrafilatura(extracts={"<html>default</html>": "Body text that is long enough."})
        fake.install(monkeypatch)
        seen = []

        fulltext._collect_inline(
            [("A1", "https://example.com/a")],
            max_chars=4000,
            deadline_s=120,
            max_doc_chars=0,
            on_result=lambda aid, text: seen.append(aid),
        )

        assert seen == ["A1"]

    def test_deadline_hit_takes_finished_work_and_skips_the_rest(self, monkeypatch):
        never_done: Future = Future()
        monkeypatch.setattr(fulltext.ThreadPoolExecutor, "submit", lambda self, fn, *a, **k: never_done)

        assert _collect([("A1", "https://example.com/a")], deadline_s=0) == {}  # must not hang or raise


class TestTruncation:
    def test_text_under_cap_is_unchanged(self):
        text = "Short article text."
        assert fulltext.truncate_at_sentence(text, 4000) == text

    def test_truncates_at_last_sentence_boundary_with_marker(self):
        text = "First sentence here. Second sentence follows nicely. This third one gets cut off m"
        cap = len("First sentence here. Second sentence follows nicely. This third one")
        result = fulltext.truncate_at_sentence(text, cap)

        assert result == "First sentence here. Second sentence follows nicely.\n[truncated]"
        assert "third one" not in result  # no completing a cut-off fact

    def test_falls_back_to_hard_cut_when_no_sentence_boundary(self):
        assert fulltext.truncate_at_sentence("a" * 5000, 100) == ("a" * 100) + "\n[truncated]"
