"""Wire-repost provenance: detecting when an outlet is republishing agency copy.

A 2026-07-25 audit (936 full articles) found scmp_world is 67% wire, al_monitor 53%,
straits_times 49% and the_hindu 48%. Showing four source links under one story implies
four newsrooms corroborated it when one agency wrote it and three reposted -- a
credibility claim the digest cannot back.

Dropping those feeds would also throw away the 33-52% that IS their own reporting, so
the fix is detection, not removal. Precision matters more than recall here: a false
positive credits an outlet's own journalism to a wire, which is the worse error.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import digest
import pytest
from digest import collapse_reposts
from feeds import wire_agency, wire_from_dateline


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        # Exactly what the feeds actually emit (measured 2026-07-25).
        ("Agence France-Presse", "agence france-presse"),
        ("Reuters", "reuters"),
        ("Associated Press", "associated press"),
        ("The Associated Press", "associated press"),  # NPR's form; leading article dropped
        ("  reuters  ", "reuters"),
        ("AFP", "afp"),
        ("dpa", "dpa"),
        ("Reuters.", "reuters"),
        # --- precision traps: each of these would mislabel real journalism as wire copy ---
        ("Reuters Institute", None),  # a research body, not a byline
        ("Michael Bloomberg", None),  # a person, not the agency
        ("Julia Frankel And Stella Martin", None),  # substring match would fire on neither
        ("A correspondent in Tehran", None),  # al_monitor's actual author value
        ("Clarin.com - Home", None),  # clarin_mundo's actual author value
        ("FRANCE24", None),  # the outlet's own staff byline
        ("Guardian Staff", None),
        ("APEC Secretariat", None),
        ("", None),
        (None, None),
    ],
)
def test_wire_agency_matches_exactly_and_nothing_else(author, expected):
    assert wire_agency(author) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # al_monitor's real summaries -- it publishes no author, so this is its only signal.
        ("By Kanishka SinghWASHINGTON, July 24 (Reuters) - President Donald Trump said", "reuters"),
        ("By Ali SawaftaRAMALLAH, July 25 (Reuters) - Israeli forces advanced", "reuters"),
        ("PARIS, July 3 (AFP) — French officials confirmed", "afp"),
        ("(Reuters) - Shares fell sharply", "reuters"),
        # Dateline position wins even where the acronym is ambiguous: The Diplomat uses
        # (AFP) for the Armed Forces of the Philippines, but never in dateline position, so
        # this is the residual false positive the detector knowingly accepts.
        ("MANILA, July 4 (AFP) - is how a wire would open, but bare prose is not", "afp"),
        # --- must not fire ---
        # A CITATION, not a repost. A trailing-sigil detector matched this on its first
        # outing against globe_and_mail, which is why there isn't one.
        ("Ottawa will announce the change, sources told AP", None),
        ("The deal was confirmed to Reuters by two diplomats", None),
        # Mid-prose parentheticals: the start anchor is what excludes these.
        ("The Diplomat reports the (AFP) - troops moved in overnight", None),
        ("The report (Reuters Institute) surveyed 40 countries", None),
        ("FRANCE 24 with AFP and Reuters reports that", None),
        ("", None),
        (None, None),
    ],
)
def test_wire_from_dateline_is_anchored_at_the_start(text, expected):
    assert wire_from_dateline(text) == expected


def test_collapse_reposts_keeps_distinct_articles_from_one_outlet():
    """Regression: keying on the agency folded FOUR distinct Reuters articles about the
    Korea chip deals into one link. An outlet is never a repost of itself, and WRITE had
    cited all four -- that is attribution loss, not deduplication."""
    sources = [
        {
            "name": "Reuters",
            "original_title": "Samsung and SK seal $950bn in chip deals",
            "wire_agency": "reuters",
            "wire": True,
        },
        {
            "name": "Reuters",
            "original_title": "Nvidia and SK to build $500bn of data centres",
            "wire_agency": "reuters",
            "wire": True,
        },
        {
            "name": "Reuters",
            "original_title": "South Korea chipmakers expand US footprint",
            "wire_agency": "reuters",
            "wire": True,
        },
    ]
    assert len(collapse_reposts(sources)) == 3


def test_collapse_reposts_still_folds_a_verbatim_repost_of_the_wire_itself():
    """Regression: the agency key made Reuters' own item and a VERBATIM Straits Times
    repost diverge -- Reuters got `agency:reuters`, Straits Times (no byline, no dateline,
    so no detected provenance) kept a title key -- and they stopped collapsing."""
    title = "Venezuela notifies UN of withdrawal from ICC, alleging 'bias'"
    sources = [
        {"name": "Reuters", "original_title": f"{title} - Reuters", "wire_agency": "reuters", "wire": True},
        {"name": "Straits Times", "original_title": title, "wire_agency": None},
    ]
    assert [x["name"] for x in collapse_reposts(sources)] == ["Reuters"]


def test_collapse_reposts_leaves_rewritten_headlines_alone():
    """Documents the gap the reverted key tried to close, so it is not mistaken for
    solved: one AFP story under two rewritten headlines still renders as two links.
    Closing it needs a similarity measure (title unigram containment measured 57% recall
    at precision 1.00), not an equality key."""
    sources = [
        {
            "name": "SCMP",
            "original_title": "Wildfires force 250,000 to flee Spain and France",
            "wire_agency": "agence france-presse",
        },
        {
            "name": "Straits Times",
            "original_title": "Untamed blazes in Spain, France force 200,000 out",
            "wire_agency": "agence france-presse",
        },
        {"name": "The Guardian", "original_title": "Bordeaux evacuations as fire spreads", "wire_agency": None},
    ]
    assert len(collapse_reposts(sources)) == 3, "known gap: rewritten headlines do not fold"


def test_collapse_reposts_prefers_the_wire_itself_as_canonical():
    """When the agency's own feed is in the story, it is the honest link to show."""
    sources = [
        {"name": "SCMP", "original_title": "Shares fall as central bank holds rates", "wire_agency": "reuters"},
        {
            "name": "Reuters",
            "original_title": "Shares fall as central bank holds rates - Reuters",
            "wire_agency": "reuters",
            "wire": True,
        },
    ]
    out = collapse_reposts(sources)
    assert [s["name"] for s in out] == ["Reuters"]


def test_collapse_reposts_keeps_different_agencies_apart():
    """Two agencies covering one story IS genuine corroboration -- do not merge it away."""
    sources = [
        {"name": "SCMP", "original_title": "A", "wire_agency": "agence france-presse"},
        {"name": "Haaretz", "original_title": "B", "wire_agency": "reuters"},
    ]
    assert len(collapse_reposts(sources)) == 2


def test_collapse_reposts_never_merges_unknown_provenance():
    """No author field (the_hindu, straits_times) must stay un-collapsed rather than be
    guessed at -- silence is not evidence of independence, but it is not evidence of
    reposting either."""
    sources = [
        {"name": "The Hindu", "original_title": "A distinct headline", "wire_agency": None},
        {"name": "Straits Times", "original_title": "Another distinct headline", "wire_agency": None},
    ]
    assert len(collapse_reposts(sources)) == 2


def test_collapse_reposts_still_folds_verbatim_titles_without_provenance():
    """The pre-existing exact-title path must keep working for feeds with no author."""
    sources = [
        {"name": "Straits Times", "original_title": "Identical wire headline"},
        {"name": "The Hindu", "original_title": "Identical wire headline"},
    ]
    assert len(collapse_reposts(sources)) == 1


def test_wire_agency_survives_resolution_end_to_end(tmp_path, monkeypatch):
    """The contract between prepare (writes article_index) and digest (reads it).

    Every test above passes with the feature completely inert, because they hand
    `collapse_reposts` dicts that already carry `wire_agency`. `resolve_source` rebuilds
    each source from a WHITELIST of keys, so a field prepare writes is dropped unless it is
    named there -- which is exactly how this shipped as a no-op through a green suite.
    """
    index = {
        "A1": {
            "url": "https://scmp.com/a",
            "source_id": "scmp_world",
            "bias": "lean-left",
            "name": "SCMP",
            "original_title": "Wildfires force 250,000 to flee Spain and France",
            "wire": False,
            "wire_agency": "agence france-presse",
        },
        "A2": {
            "url": "https://straitstimes.com/b",
            "source_id": "straits_times",
            "bias": "lean-right",
            "name": "Straits Times",
            "original_title": "Untamed blazes in Spain, France force 200,000 out",
            "wire": False,
            "wire_agency": "agence france-presse",
        },
    }
    index_path = tmp_path / "article_index.json"
    index_path.write_text(json.dumps(index))
    monkeypatch.setattr(digest, "CLAUDE_INPUT_DIR", tmp_path)

    resolved = digest.resolve_article_ids(
        {"must_know": [{"headline": "h", "sources": [{"article_id": "A1"}, {"article_id": "A2"}]}]}
    )

    got = {s["name"]: s.get("wire_agency") for s in resolved["must_know"][0]["sources"]}
    assert got == {"SCMP": "agence france-presse", "Straits Times": "agence france-presse"}, (
        "provenance lost at the resolve boundary -- render cannot credit the agency"
    )


def test_resolution_tolerates_an_article_index_predating_wire_agency(tmp_path, monkeypatch):
    """--write-only re-renders from a persisted index that may lack the field entirely.
    It must degrade to title-only collapsing, not drop the sources (and then the story)."""
    index = {
        "A1": {
            "url": "https://x.test/a",
            "source_id": "the_hindu",
            "bias": "lean-left",
            "name": "The Hindu",
            "original_title": "A headline",
        }
    }
    (tmp_path / "article_index.json").write_text(json.dumps(index))
    monkeypatch.setattr(digest, "CLAUDE_INPUT_DIR", tmp_path)

    resolved = digest.resolve_article_ids({"must_know": [{"headline": "h", "sources": [{"article_id": "A1"}]}]})
    assert [s["name"] for s in resolved["must_know"][0]["sources"]] == ["The Hindu"]


def test_source_table_credits_the_agency_and_names_the_route():
    """We hold direct feeds for only Reuters and UPI, so an AFP story's canonical link is
    always somebody's repost. Collapsing three AFP reposts to one row labelled "SCMP"
    would credit SCMP with copy it did not write -- worse than the pre-collapse state,
    where at least three outlets were visible."""
    from render import _render_sources_block

    html_out = _render_sources_block(
        [
            {
                "name": "SCMP",
                "bias": "lean-left",
                "bucket": "l",
                "urls": ["https://scmp.com/a"],
                "wire_agency": "agence france-presse",
            },
            {
                "name": "The Guardian",
                "bias": "lean-left",
                "bucket": "l",
                "urls": ["https://theguardian.com/b"],
                "wire_agency": None,
            },
        ]
    )
    assert "AFP" in html_out
    assert "via SCMP" in html_out
    # An outlet's own reporting must NOT be relabelled.
    assert ">The Guardian<" in html_out
    assert "via The Guardian" not in html_out


def test_source_table_does_not_say_via_the_agency_itself():
    """Reuters' own feed is the agency -- "Reuters · via Reuters" reads as a bug."""
    from render import _render_sources_block

    out = _render_sources_block(
        [
            {
                "name": "Reuters",
                "bias": "center",
                "bucket": "c",
                "urls": ["https://reuters.com/a"],
                "wire_agency": "reuters",
            }
        ]
    )
    assert "via Reuters" not in out


def test_source_table_does_not_lend_the_reposters_leaning_to_wire_copy():
    """An AFP story routed through SCMP does not acquire SCMP's lean-left editorial voice,
    and we hold no MBFC rating for AFP to substitute, so the honest cell is neither."""
    from render import _render_sources_block

    out = _render_sources_block(
        [
            {
                "name": "SCMP",
                "bias": "lean-left",
                "bucket": "l",
                "urls": ["https://scmp.com/a"],
                "wire_agency": "agence france-presse",
            }
        ]
    )
    assert ">wire<" in out
    assert ">lean-left<" not in out
