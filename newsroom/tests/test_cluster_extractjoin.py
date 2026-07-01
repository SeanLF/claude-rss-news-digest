"""Tests for cluster_extractjoin.py -- the deterministic extract→join CLUSTER stage.

Pure functions (join_tags, parse_extract_items, build_extract_prompt) are tested directly.
run_extractjoin_stage is async and mocks ``claude_cli.run_agent`` (the SDK cannot run nested
under CLAUDECODE=1); sync bodies drive it via asyncio.run, matching test_orchestrate.py.
End-to-end behaviour on real articles is validated separately in a Docker dry run.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import cluster_extractjoin as cej
from claude_cli import StageResult


def _result(text, *, usage=None, cost=0.01, ok=True):
    return StageResult(
        subtype="success" if ok else "error",
        text=text,
        usage=usage
        or {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        total_cost_usd=cost,
        duration_ms=1000,
        is_error=not ok,
    )


# --------------------------------------------------------------------------- #
# join_tags -- the deterministic core.
# --------------------------------------------------------------------------- #
def _all_ids(clusters):
    return sorted(a for c in clusters for a in c["article_ids"])


def test_join_every_article_appears_exactly_once():
    """The invariant SELECT depends on: a partition, not overlaps or drops."""
    ids = ["A1", "A2", "A3", "A4"]
    tags = {
        "A1": {"entities": ["Iran"], "keywords": ["nuclear"], "primary_event": "iran talks"},
        "A2": {"entities": ["Iran"], "keywords": ["nuclear"], "primary_event": "iran talks"},
        "A3": {"entities": ["Venezuela"], "keywords": ["quake"], "primary_event": "venezuela earthquake"},
        "A4": {"entities": ["Apple"], "keywords": ["prices"], "primary_event": "apple price hike"},
    }
    clusters = cej.join_tags(ids, tags, threshold=0.80)
    flat = [a for c in clusters for a in c["article_ids"]]
    assert sorted(flat) == ids
    assert len(flat) == len(set(flat)), "an article appeared in more than one cluster"


def test_join_merges_identical_and_separates_distinct():
    """Same tags → one cluster; unrelated tags → their own clusters."""
    ids = ["A1", "A2", "A3"]
    tags = {
        "A1": {
            "entities": ["Iran", "Hormuz"],
            "keywords": ["ship"],
            "primary_event": "iran attacks cargo ship in hormuz",
        },
        "A2": {
            "entities": ["Iran", "Hormuz"],
            "keywords": ["ship"],
            "primary_event": "iran attacks cargo ship in hormuz",
        },
        "A3": {"entities": ["Venezuela"], "keywords": ["earthquake"], "primary_event": "venezuela twin earthquakes"},
    }
    clusters = cej.join_tags(ids, tags, threshold=0.80)
    sets = sorted((sorted(c["article_ids"]) for c in clusters), key=len, reverse=True)
    assert sets[0] == ["A1", "A2"], "identical-tag articles should merge"
    assert ["A3"] in [sorted(c["article_ids"]) for c in clusters], "distinct story should stay separate"


def test_join_all_distinct_gives_all_singletons():
    ids = [f"A{i}" for i in range(1, 6)]
    tags = {
        aid: {"entities": [f"Person{aid}"], "keywords": [f"topic{aid}"], "primary_event": f"unique event {aid}"}
        for aid in ids
    }
    clusters = cej.join_tags(ids, tags, threshold=0.80)
    assert len(clusters) == len(ids), "unrelated articles must not be force-merged"


def test_join_story_label_is_modal_primary_event():
    ids = ["A1", "A2", "A3"]
    tags = {
        "A1": {"entities": ["X"], "keywords": ["k"], "primary_event": "the big story"},
        "A2": {"entities": ["X"], "keywords": ["k"], "primary_event": "the big story"},
        "A3": {"entities": ["X"], "keywords": ["k"], "primary_event": "minority label"},
    }
    clusters = cej.join_tags(ids, tags, threshold=0.99)  # coarse -> one cluster
    big = max(clusters, key=lambda c: len(c["article_ids"]))
    assert big["story"] == "the big story"


def test_join_empty_and_single_are_safe():
    assert cej.join_tags([], {}, threshold=0.80) == []
    one = cej.join_tags(["A1"], {"A1": {"entities": [], "keywords": [], "primary_event": ""}}, threshold=0.80)
    assert _all_ids(one) == ["A1"]


def test_join_empty_tags_do_not_collapse_into_one_blob():
    """Degenerate guard: articles with no tags become singletons, never a giant junk cluster."""
    ids = [f"A{i}" for i in range(1, 5)]
    tags = {aid: {"entities": [], "keywords": [], "primary_event": ""} for aid in ids}
    clusters = cej.join_tags(ids, tags, threshold=0.80)
    assert _all_ids(clusters) == ids
    assert max(len(c["article_ids"]) for c in clusters) < len(ids)


# --------------------------------------------------------------------------- #
# join_tags time-decay (optional temporal signal; prod path unchanged when omitted)
# --------------------------------------------------------------------------- #
def test_join_timedecay_separates_same_tags_far_apart_in_time():
    """Same tags but many days apart (a recurring event) must NOT merge under decay,
    while same-tag same-hour articles still do -- the precision the temporal signal buys."""
    from datetime import UTC, datetime

    ids = ["A1", "A2", "A3"]
    tags = {
        aid: {"entities": ["Trump", "Congress"], "keywords": ["vote"], "primary_event": "budget vote"} for aid in ids
    }
    published = {
        "A1": datetime(2026, 7, 1, 10, tzinfo=UTC),
        "A2": datetime(2026, 7, 1, 11, tzinfo=UTC),  # 1h after A1
        "A3": datetime(2026, 7, 8, 10, tzinfo=UTC),  # 7 days after A1
    }
    clusters = cej.join_tags(ids, tags, threshold=0.30, published=published, sigma_hours=72)
    sets = [sorted(c["article_ids"]) for c in clusters]
    assert ["A1", "A2"] in sets, "same-tag, same-hour articles should still merge"
    assert ["A3"] in sets, "same-tag but 7 days later should stay separate under time-decay"


def test_join_timedecay_omitted_matches_plain_path():
    """Passing no temporal args must reproduce the plain (no-decay) partition exactly."""
    ids = ["A1", "A2", "A3", "A4"]
    tags = {
        "A1": {"entities": ["Iran"], "keywords": ["nuclear"], "primary_event": "iran talks"},
        "A2": {"entities": ["Iran"], "keywords": ["nuclear"], "primary_event": "iran talks"},
        "A3": {"entities": ["Venezuela"], "keywords": ["quake"], "primary_event": "venezuela earthquake"},
        "A4": {"entities": ["Apple"], "keywords": ["prices"], "primary_event": "apple price hike"},
    }

    def key(cs):
        return sorted(sorted(c["article_ids"]) for c in cs)

    plain = cej.join_tags(ids, tags, threshold=0.80)
    explicit_none = cej.join_tags(ids, tags, threshold=0.80, published=None, sigma_hours=None)
    assert key(plain) == key(explicit_none)


def test_join_timedecay_missing_publish_time_is_not_penalized():
    """An article with no publish time gets a neutral (no-penalty) temporal weight,
    so it clusters purely on tags rather than being spuriously isolated."""
    from datetime import UTC, datetime

    ids = ["A1", "A2"]
    tags = {aid: {"entities": ["X"], "keywords": ["k"], "primary_event": "same story"} for aid in ids}
    published = {"A1": datetime(2026, 7, 1, 10, tzinfo=UTC)}  # A2 missing
    clusters = cej.join_tags(ids, tags, threshold=0.30, published=published, sigma_hours=72)
    assert [sorted(c["article_ids"]) for c in clusters] == [["A1", "A2"]]


# --------------------------------------------------------------------------- #
# _thinking_for -- model-aware thinking. (Next-gen models used to 400 on thinking=disabled;
# no longer on SDK 0.2.110 per bin/sdk-canary -- retained as config, adaptive for next-gen.)
# --------------------------------------------------------------------------- #
def test_thinking_disabled_only_for_4x_family():
    assert cej._thinking_for("claude-sonnet-4-6") == {"type": "disabled"}
    assert cej._thinking_for("claude-haiku-4-5-20251001") == {"type": "disabled"}
    # next-gen models reject thinking=disabled (400) -> must omit it
    assert cej._thinking_for("claude-sonnet-5") is None
    assert cej._thinking_for("claude-opus-4-8") is None
    assert cej._thinking_for("claude-fable-5") is None


# --------------------------------------------------------------------------- #
# parse_extract_items -- tolerant JSON extraction.
# --------------------------------------------------------------------------- #
def test_parse_object_form():
    items = cej.parse_extract_items('{"items": [{"article_id": "A1", "entities": ["Iran"]}]}')
    assert items == [{"article_id": "A1", "entities": ["Iran"]}]


def test_parse_fenced_and_prose_wrapped():
    txt = 'Here you go:\n```json\n{"items": [{"article_id": "A1"}]}\n```\nDone.'
    assert cej.parse_extract_items(txt) == [{"article_id": "A1"}]


def test_parse_bare_array_fallback():
    assert cej.parse_extract_items('[{"article_id": "A1"}]') == [{"article_id": "A1"}]


def test_parse_garbage_returns_empty():
    assert cej.parse_extract_items("no json here") == []
    assert cej.parse_extract_items("") == []


# --------------------------------------------------------------------------- #
# run_extractjoin_stage -- async, mocked SDK.
# --------------------------------------------------------------------------- #
def _write_articles(tmp_path, n):
    csv = "article_id,source_id,title,summary\n" + "\n".join(
        f"A{i},src{i % 3},Title about topic {i % 4},Summary body {i}" for i in range(1, n + 1)
    )
    (tmp_path / "articles_1.csv").write_text(csv)


def test_stage_writes_valid_clusters(tmp_path, monkeypatch):
    _write_articles(tmp_path, 6)

    async def fake_run_agent(prompt, **kw):
        # tag every article uniquely so the join yields a clean partition
        items = [
            {"article_id": f"A{i}", "entities": [f"E{i % 4}"], "keywords": ["k"], "primary_event": f"event {i % 4}"}
            for i in range(1, 7)
        ]
        return _result(json.dumps({"items": items}))

    monkeypatch.setattr(cej.claude_cli, "run_agent", fake_run_agent)
    row = asyncio.run(cej.run_extractjoin_stage(tmp_path, model="claude-sonnet-4-6", cwd=None, threshold=0.80))

    data = json.loads((tmp_path / "clusters.json").read_text())
    assert data["clusters"], "clusters.json must be non-empty (validate_clusters)"
    flat = [a for c in data["clusters"] for a in c["article_ids"]]
    assert sorted(flat) == [f"A{i}" for i in range(1, 7)]
    assert row["subagent"] == "cluster"
    assert row["api_cost_usd"] > 0


async def _passthrough(fn, **_k):
    """Stand-in for with_retry_async that calls fn once (no backoff sleeps in tests)."""
    return await fn()


def test_stage_minority_fallback_preserves_coverage(tmp_path, monkeypatch):
    """A minority of unparsed/omitted articles fall back to title-only; full coverage, no raise."""
    _write_articles(tmp_path, 8)  # one batch

    async def partial(prompt, **kw):
        # return items for 7 of 8 -> 1 (12.5% < 25%) falls back
        items = [
            {"article_id": f"A{i}", "entities": [f"E{i}"], "keywords": ["k"], "primary_event": f"event {i}"}
            for i in range(1, 8)
        ]
        return _result(json.dumps({"items": items}))

    monkeypatch.setattr(cej.claude_cli, "run_agent", partial)
    asyncio.run(cej.run_extractjoin_stage(tmp_path, model="claude-sonnet-4-6", cwd=None, threshold=0.80))
    data = json.loads((tmp_path / "clusters.json").read_text())
    flat = sorted(a for c in data["clusters"] for a in c["article_ids"])
    assert flat == [f"A{i}" for i in range(1, 9)], "title fallback must preserve full coverage"


def test_stage_raises_on_empty_extraction(tmp_path, monkeypatch):
    """CRITICAL: an extractor that SUCCEEDS (ok=True) but returns empty items for every batch must
    RAISE (coverage gate), not silently ship an all-title-only degenerate partition."""
    _write_articles(tmp_path, 8)

    async def ok_but_empty(prompt, **kw):
        return _result(json.dumps({"items": []}))  # ok=True, zero items

    monkeypatch.setattr(cej.claude_cli, "run_agent", ok_but_empty)
    with pytest.raises(RuntimeError, match="degenerate partition"):
        asyncio.run(cej.run_extractjoin_stage(tmp_path, model="claude-sonnet-4-6", cwd=None, threshold=0.80))


def test_stage_raises_when_extraction_fails(tmp_path, monkeypatch):
    """Wholesale transport failure (auth/outage) must raise, not ship a degenerate partition."""
    _write_articles(tmp_path, 8)

    async def always_fail(prompt, **kw):
        return _result("", ok=False)

    monkeypatch.setattr(cej.claude_cli, "run_agent", always_fail)
    monkeypatch.setattr(cej, "with_retry_async", _passthrough)  # skip real backoff
    with pytest.raises(RuntimeError, match="degenerate partition"):
        asyncio.run(cej.run_extractjoin_stage(tmp_path, model="claude-sonnet-4-6", cwd=None, threshold=0.80))


def test_stage_retries_transient_via_with_retry_async(tmp_path, monkeypatch):
    """Parity with the other stages: each batch's extraction routes through with_retry_async so
    transient overloads are retried with backoff (not permanently degraded to title-only)."""
    _write_articles(tmp_path, 8)
    calls = {"n": 0}

    async def spy(fn, **_k):
        calls["n"] += 1
        return await fn()

    async def ok(prompt, **kw):
        items = [
            {"article_id": f"A{i}", "entities": [f"E{i}"], "keywords": ["k"], "primary_event": f"e{i}"}
            for i in range(1, 9)
        ]
        return _result(json.dumps({"items": items}))

    monkeypatch.setattr(cej.claude_cli, "run_agent", ok)
    monkeypatch.setattr(cej, "with_retry_async", spy)
    asyncio.run(cej.run_extractjoin_stage(tmp_path, model="claude-sonnet-4-6", cwd=None, threshold=0.80))
    assert calls["n"] == 1, "each batch's extraction must go through with_retry_async"


def test_stage_no_articles_raises(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="no articles"):
        asyncio.run(cej.run_extractjoin_stage(tmp_path, model="claude-sonnet-4-6", cwd=None, threshold=0.80))
