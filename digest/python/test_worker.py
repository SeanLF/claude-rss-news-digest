import sys
import types

import fulltext
import gnews
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


def GN(token):
    return f"https://news.google.com/rss/articles/{token}?oc=5"


def fake_decoder(monkeypatch, answers):
    """googlenewsdecoder as gnews._fetch uses it. `answers` maps a token to a publisher URL, to
    None for a failed decode, or to 429 for a refusal the transport sees."""
    seen, sleeps = [], []
    lib = types.ModuleType("googlenewsdecoder")

    class TransportError(Exception):
        def __init__(self, status):
            super().__init__(status)
            self.status = status

    def transport(token, **kw):
        if answers[token] == 429:
            raise TransportError(429)

    def decode(url, *, timeout, transport):
        token = url.split("/articles/")[1].split("?")[0]
        seen.append((token, timeout))
        try:
            transport(token)
        except TransportError:
            return {"status": False, "message": "refused"}
        to = answers[token]
        return {"status": True, "decoded_url": to} if to else {"status": False, "message": "no"}

    lib.TransportError, lib.default_transport, lib.decode = TransportError, lambda: transport, decode
    monkeypatch.setitem(sys.modules, "googlenewsdecoder", lib)
    monkeypatch.setattr(gnews, "time", types.SimpleNamespace(sleep=sleeps.append))
    return seen, sleeps


def test_the_decode_is_the_activity_the_workflow_calls():
    assert worker.decode_links.__temporal_activity_definition.name == "decodeLinks"


def test_decodes_each_link_serially_at_the_production_timeout_and_pace(monkeypatch):
    seen, sleeps = fake_decoder(
        monkeypatch, {"R1": "https://www.reuters.com/r1", "R2": None, "N3": "https://asia.nikkei.com/n3"}
    )
    out = ActivityEnvironment().run(worker.decode_links, [GN("R1"), GN("R2"), GN("N3")])
    decoded = {GN("R1"): "https://www.reuters.com/r1", GN("N3"): "https://asia.nikkei.com/n3"}
    assert out == {"links": 3, "decoded": decoded, "attempted": 3, "outcome": "completed"}
    assert seen == [("R1", 15), ("R2", 15), ("N3", 15)]
    assert sleeps == [2.0, 2.0, 2.0]


def test_a_429_stops_the_pass_and_keeps_what_was_decoded(monkeypatch):
    seen, _ = fake_decoder(
        monkeypatch, {"R1": "https://www.reuters.com/r1", "R2": 429, "R3": "https://www.reuters.com/r3"}
    )
    out = ActivityEnvironment().run(worker.decode_links, [GN("R1"), GN("R2"), GN("R3")])
    assert out == {
        "links": 3,
        "decoded": {GN("R1"): "https://www.reuters.com/r1"},
        "attempted": 2,
        "outcome": "rate_limited",
    }
    assert [t for t, _ in seen] == ["R1", "R2"]


def test_the_deadline_is_checked_between_links(monkeypatch):
    fake_decoder(monkeypatch, {"R1": "https://www.reuters.com/r1", "R2": "https://www.reuters.com/r2"})
    clock = iter([0.0, 1.0, 121.0])
    monkeypatch.setattr(worker, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    out = ActivityEnvironment().run(worker.decode_links, [GN("R1"), GN("R2")])
    assert out == {
        "links": 2,
        "decoded": {GN("R1"): "https://www.reuters.com/r1"},
        "attempted": 1,
        "outcome": "deadline",
    }


def test_a_token_is_decoded_once_per_pass_and_afresh_on_the_next(monkeypatch):
    seen, _ = fake_decoder(monkeypatch, {"R1": None})
    first = ActivityEnvironment().run(
        worker.decode_links, [GN("R1"), "https://news.google.com/rss/articles/R1?hl=en-US"]
    )
    second = ActivityEnvironment().run(worker.decode_links, [GN("R1")])
    assert (first["attempted"], second["attempted"]) == (1, 1)
    assert len(seen) == 2  # the worker outlives a run: a failure must not be cached into the next


def test_heartbeats_before_each_link(monkeypatch):
    fake_decoder(monkeypatch, {"R1": None, "R2": None})
    env = ActivityEnvironment()
    beats = []
    env.on_heartbeat = lambda *details: beats.append(details)
    env.run(worker.decode_links, [GN("R1"), GN("R2")])
    assert len(beats) >= 2


def test_a_cancelled_pass_stops_before_the_next_link(monkeypatch):
    # A timed-out activity is cancelled at its next heartbeat; its thread must stop decoding then.
    env = ActivityEnvironment()
    answers = {"R1": "https://www.reuters.com/r1", "R2": "https://www.reuters.com/r2"}
    seen, _ = fake_decoder(monkeypatch, answers)
    real_decode = sys.modules["googlenewsdecoder"].decode

    def decode_then_cancel(url, **kw):
        result = real_decode(url, **kw)
        env.cancel()
        return result

    monkeypatch.setattr(sys.modules["googlenewsdecoder"], "decode", decode_then_cancel)
    out = env.run(worker.decode_links, [GN("R1"), GN("R2")])
    assert [t for t, _ in seen] == ["R1"]
    assert out["outcome"] == "cancelled"


def test_two_passes_at_once_run_one_after_the_other(monkeypatch):
    # gnews keeps one cache and one tally per process, and Google counts requests per IP: two runs
    # decoding at once would corrupt both passes' counts and double the request rate.
    import threading
    import time as real_time

    answers = {f"R{i}": f"https://www.reuters.com/r{i}" for i in range(4)}
    fake_decoder(monkeypatch, answers)
    real_decode = sys.modules["googlenewsdecoder"].decode
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow_decode(url, **kw):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        real_time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return real_decode(url, **kw)

    monkeypatch.setattr(sys.modules["googlenewsdecoder"], "decode", slow_decode)
    results = {}

    def run(name, urls):
        results[name] = ActivityEnvironment().run(worker.decode_links, urls)

    threads = [
        threading.Thread(target=run, args=("a", [GN("R0"), GN("R1")])),
        threading.Thread(target=run, args=("b", [GN("R2"), GN("R3")])),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["peak"] == 1
    assert results["a"]["attempted"] == results["b"]["attempted"] == 2
    assert set(results["a"]["decoded"]) == {GN("R0"), GN("R1")}


def test_the_pinned_decoder_is_the_fork_gnews_calls():
    # PyPI's googlenewsdecoder 0.2.x has none of these, and gnews._fetch would fail open on every link.
    from googlenewsdecoder import TransportError, decode, default_transport

    assert "timeout" in decode.__code__.co_varnames
    assert callable(default_transport) and issubclass(TransportError, Exception)


def test_the_namespace_is_temporal_namespace_so_production_uses_the_repos_own(monkeypatch):
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "news-digest")
    assert worker.namespace() == "news-digest"
    monkeypatch.delenv("TEMPORAL_NAMESPACE")
    assert worker.namespace() == "default"
