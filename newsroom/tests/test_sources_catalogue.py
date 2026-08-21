"""Parked sources: in the catalogue, out of the fetch.

The Hindu returned 403 for 45 consecutive runs (last success run 225). The block is on the
Hetzner ASN -- 403 from prod on IPv4 and IPv6 with any User-Agent including none, 200 from a
residential IP -- so no retry, header or feed-URL change reaches it. Deleting the entry was the
obvious move and the wrong one: circulation's archive builds each past issue's bias bar from
sources.json, and the_hindu coloured 150 archived issues across runs 56-225. Removing the row
would have moved the lean-left share on all 150 -- median 2pp, max 7pp -- with nothing to show it.

So an entry can be parked with `"active": false`: the catalogue keeps it, the fetch skips it, and
the persistent-failure alert stops firing about a decision already made.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import feeds_cli
from feeds import load_catalogue, load_sources

SOURCES_FILE = Path(__file__).parent.parent / "sources.json"

_VALID = {
    "id": "example_world",
    "name": "Example",
    "url": "https://example.com/rss",
    "bias": "center",
    "factuality": "high",
    "perspective": "western",
}


def _write(tmp_path, sources):
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(sources), encoding="utf-8")
    return p


def test_a_source_with_no_active_key_is_fetched(tmp_path):
    # Every existing entry omits the key, so the default decides whether the digest has any
    # sources at all.
    assert [s["id"] for s in load_sources(_write(tmp_path, [_VALID]))] == ["example_world"]


def test_a_parked_source_is_not_returned_for_fetching(tmp_path):
    parked = {**_VALID, "id": "parked_one", "active": False, "inactive_reason": "blocks our ASN"}
    ids = [s["id"] for s in load_sources(_write(tmp_path, [_VALID, parked]))]
    assert ids == ["example_world"]


def test_a_parked_source_is_still_validated(tmp_path):
    # A parked entry that rots unnoticed comes back broken on the day someone revives it.
    parked = {**_VALID, "id": "parked_one", "url": "ftp://nope", "active": False, "inactive_reason": "x"}
    with pytest.raises(ValueError, match="invalid URL"):
        load_sources(_write(tmp_path, [parked]))


def test_parking_a_source_requires_saying_why(tmp_path):
    parked = {**_VALID, "id": "parked_one", "active": False}
    with pytest.raises(ValueError, match="inactive_reason"):
        load_sources(_write(tmp_path, [parked]))


def test_the_shipped_catalogue_parks_the_hindu_rather_than_dropping_it():
    """Guards both directions of the decision, on the real file.

    If someone deletes the row, 150 archived issues silently restate their bias split. If
    someone un-parks it without the block being lifted, the daily 403 and its alert come back.
    """
    catalogue = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    hindu = next((s for s in catalogue if s["id"] == "the_hindu"), None)
    assert hindu is not None, "the_hindu was deleted; circulation's archive can no longer attribute its bias"
    assert hindu.get("active") is False
    assert "403" in hindu["inactive_reason"]
    assert "the_hindu" not in [s["id"] for s in load_sources(SOURCES_FILE)]


def test_the_catalogue_loader_keeps_parked_entries(tmp_path):
    # --validate probes this list. If parking also hid the source from the validator, nothing
    # anywhere would ever notice the publisher's block being lifted, and the park would be a
    # one-way door.
    parked = {**_VALID, "id": "parked_one", "active": False, "inactive_reason": "blocks our ASN"}
    ids = [s["id"] for s in load_catalogue(_write(tmp_path, [_VALID, parked]))]
    assert ids == ["example_world", "parked_one"]


def test_a_quoted_boolean_is_rejected_rather_than_read_as_active(tmp_path):
    """`"active": "false"` is the likeliest typo here, and truthiness gets it exactly wrong.

    A non-empty string is truthy, so `source.get("active", True)` would keep fetching the source
    AND skip the inactive_reason guard -- while circulation's serde rejects the same file outright.
    Two readers of one file must not disagree about what it says.
    """
    for value in ("false", "no", 0, None):
        bad = {**_VALID, "id": "parked_one", "active": value, "inactive_reason": "x"}
        with pytest.raises(ValueError, match="non-boolean 'active'"):
            load_catalogue(_write(tmp_path, [bad]))


def test_validate_does_not_go_red_over_a_parked_feeds_expected_403(monkeypatch, tmp_path):
    """The probe must report the parked feed without counting it as a failure.

    Restoring the probe and keeping the exit code honest pull in opposite directions: a parked
    source's 403 is the expected result of a decision already taken, so folding it into
    `failed_count` leaves the validator permanently red and lists the same source twice.
    """
    parked = {**_VALID, "id": "parked_one", "active": False, "inactive_reason": "blocks our ASN"}
    sources = load_catalogue(_write(tmp_path, [_VALID, parked]))

    # Patch the fetch, not validate_single_feed, so the real result shape is exercised.
    def fake_fetch(source, timeout=15):
        if source["id"] == "parked_one":
            return source["id"], [], "Failed after 3 retries: Forbidden"
        return source["id"], [{"title": "t", "url": "u", "published": None}], None

    monkeypatch.setattr(feeds_cli, "fetch_source", fake_fetch)
    monkeypatch.setattr(feeds_cli.db, "init", lambda *a, **k: None)
    monkeypatch.setattr(feeds_cli.db, "get_failing_sources", lambda **k: [])

    rc = feeds_cli.validate_feeds_cli(sources, tmp_path / "x.db", tmp_path, json_output=False)
    assert rc == 0, "a parked feed's expected block must not fail the validator"
