"""Tests for the pre-deploy web gate (bin/web-check -> tools/web_check.py).

Pins route discovery, because that is where the gate can silently under-check: if
`/today` or `/threads` stops yielding a target, the run must SAY it checked fewer
pages rather than quietly passing on the static routes alone.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import web_check


def fake_fetch(pages):
    """pages: path -> (status, location, body); static routes default to a plain 200."""

    def fetch(path):
        if path in pages:
            return pages[path]
        if path in web_check.STATIC_ROUTES:
            return 200, None, "<main>ok</main>"
        raise AssertionError(f"unexpected fetch: {path}")

    return fetch


THREADS_HTML = """
<main><ul>
  <li><a href="/thread/dc19f0">Gaza ceasefire talks</a></li>
  <li><a href="/thread/aa0201">Tariff escalation</a></li>
</ul></main>
"""


def test_discovers_static_routes_plus_latest_issue_and_first_thread():
    fetch = fake_fetch(
        {
            "/today": (307, "/issues/2026-07-27", ""),
            "/threads": (200, None, THREADS_HTML),
        }
    )
    urls, warnings, failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert warnings == []
    for route in web_check.STATIC_ROUTES:
        assert f"http://localhost:8080{route}" in urls
    assert "http://localhost:8080/issues/2026-07-27" in urls
    # first thread only -- the detail template is shared, so one instance covers it
    assert "http://localhost:8080/thread/dc19f0" in urls
    assert "http://localhost:8080/thread/aa0201" not in urls
    assert failures == []


def test_absolute_redirect_target_is_reduced_to_the_local_base():
    """Prod sets DIGEST_DOMAIN, so /today can redirect to an absolute canonical URL.
    Following it would Lighthouse production instead of the build under test."""
    fetch = fake_fetch(
        {
            "/today": (307, "https://news-digest.seanfloyd.dev/issues/2026-07-27", ""),
            "/threads": (200, None, THREADS_HTML),
        }
    )
    urls, warnings, failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert "http://localhost:8080/issues/2026-07-27" in urls
    assert not any("seanfloyd.dev" in u for u in urls)
    assert warnings == []
    assert failures == []


def test_offsite_redirect_is_dropped_not_graded():
    """/privacy 307s to seanfloyd.dev. Lighthouse follows redirects, so leaving it in
    scored that site's noindex as our SEO failure -- a false red on every run."""
    fetch = fake_fetch(
        {
            "/privacy": (307, "https://seanfloyd.dev/privacy", ""),
            "/today": (307, "/issues/2026-07-27", ""),
            "/threads": (200, None, THREADS_HTML),
        }
    )
    urls, warnings, failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert not any("privacy" in u for u in urls)
    assert any("off-site" in w for w in warnings)
    assert failures == []


def test_same_origin_redirect_is_followed_and_graded():
    fetch = fake_fetch(
        {
            "/search": (308, "http://localhost:8080/search/", ""),
            "/today": (307, "/issues/2026-07-27", ""),
            "/threads": (200, None, THREADS_HTML),
        }
    )
    urls, warnings, failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert "http://localhost:8080/search/" in urls
    assert warnings == []
    assert failures == []


def test_broken_static_route_is_a_failure_not_a_warning():
    """A page that does not serve is the thing a pre-deploy gate exists to catch.
    Classing it as a warning let the gate print 'passed' and ship the 500."""
    fetch = fake_fetch(
        {
            "/stats": (500, None, ""),
            "/today": (307, "/issues/2026-07-27", ""),
            "/threads": (200, None, THREADS_HTML),
        }
    )
    urls, warnings, failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert not any(u.endswith("/stats") for u in urls)
    assert any("500" in f for f in failures)
    assert warnings == []


def test_missing_digest_warns_loudly_instead_of_silently_checking_less():
    fetch = fake_fetch(
        {
            "/today": (404, None, ""),
            "/threads": (200, None, "<main><p>No threads yet.</p></main>"),
        }
    )
    urls, warnings, failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert len(urls) == len(web_check.STATIC_ROUTES)
    assert any("issue" in w for w in warnings)
    assert any("thread" in w for w in warnings)
    assert failures == []


def test_unreachable_server_warns_rather_than_raising():
    def fetch(path):
        raise OSError("connection refused")

    urls, _warnings, failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert urls == []
    assert len(failures) == len(web_check.STATIC_ROUTES) + 2  # every route + both dynamic lookups


@pytest.mark.parametrize(
    "body",
    ["<a href='/thread/'>empty</a>", "<a href='/threads'>index self-link</a>", "<a href='/thread'>bare</a>"],
)
def test_thread_id_scan_ignores_non_detail_links(body):
    fetch = fake_fetch({"/today": (404, None, ""), "/threads": (200, None, body)})
    urls, warnings, _failures = web_check.discover_routes("http://localhost:8080", fetch)

    assert not any("/thread/" in u for u in urls)
    assert any("thread" in w for w in warnings)


# --- the gate's exit code, not just its route list -------------------------------
#
# Every test above grades discover_routes. The bug that survived the first round
# lived past it: with zero resolved routes the gate invoked `bin/a11y-check` with
# no --url flags at all, which falls back to globbing the design mockups and exits
# 0. So the gate printed "passed" having verified nothing -- and, because scratch/
# is gitignored, it failed CLOSED on a fresh clone and OPEN on the machine that
# actually deploys. These drive main() and assert on the exit status.


@pytest.fixture
def stub_main(monkeypatch):
    """Run main() without Docker, HTTP or subprocesses. Returns the gate calls made."""
    calls = []
    monkeypatch.setattr(web_check, "wait_for_health", lambda base, timeout=90: (True, None))
    monkeypatch.setattr(web_check, "run_gate", lambda name, cmd: calls.append(cmd) or True)
    monkeypatch.setattr(web_check, "check_page_weight", lambda urls, max_kb: True)
    monkeypatch.setattr(sys, "argv", ["web-check", "--base", "http://localhost:8080", "--fast"])
    return calls


def _routes(urls, warnings=(), failures=()):
    return lambda base, fetch: (list(urls), list(warnings), list(failures))


def test_zero_resolved_routes_fails_the_gate_instead_of_grading_mockups(stub_main, monkeypatch):
    monkeypatch.setattr(web_check, "discover_routes", _routes([], failures=["/: HTTP 500"]))

    with pytest.raises(SystemExit) as e:
        web_check.main()

    assert e.value.code != 0
    assert stub_main == [], "must not invoke a sub-gate with an empty --url list"


def test_a_route_that_does_not_serve_aborts_before_the_sub_gates_run(stub_main, monkeypatch):
    monkeypatch.setattr(
        web_check,
        "discover_routes",
        _routes(["http://localhost:8080/"], failures=["/stats: HTTP 500"]),
    )

    with pytest.raises(SystemExit) as e:
        web_check.main()

    assert e.value.code != 0
    assert stub_main == [], "a 500 on any route must stop the gate, not be graded around"


def test_benign_warnings_alone_still_pass(stub_main, monkeypatch):
    """An off-site redirect and an empty database are legitimate states, not failures."""
    monkeypatch.setattr(
        web_check,
        "discover_routes",
        _routes(
            ["http://localhost:8080/", "http://localhost:8080/stats"],
            warnings=["/privacy: redirects off-site", "no issue to check -- database empty"],
        ),
    )

    web_check.main()  # must not raise

    assert len(stub_main) == 1
    assert stub_main[0].count("--url") == 2
