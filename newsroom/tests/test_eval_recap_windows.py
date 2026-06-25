"""Tests for eval_recap_windows: reconstructing historical RECAP input windows.

RECAP's production input is get_previous_headlines(7) -- the last 7 days of
shown_narratives. To eval RECAP across weeks we rebuild that window for many
historical end-dates from the same data.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval_recap_windows import build_window, select_window_dates, theme_frequencies


def test_build_window_keeps_only_the_trailing_days_and_formats_rows():
    rows = [
        {"title": "Flood hits coastal towns", "tier": "must_know", "shown_at": "2026-03-10T08:00:00+00:00"},
        {"title": "Old summit wraps", "tier": "should_know", "shown_at": "2026-03-01T08:00:00+00:00"},
        {"title": "Election result certified", "tier": "must_know", "shown_at": "2026-03-09T20:00:00+00:00"},
    ]

    window = build_window(rows, end_date=dt.date(2026, 3, 10), days=7)

    # 03-01 is outside the 7-day trailing window ending 03-10; the other two are in.
    assert [w["headline"] for w in window] == ["Flood hits coastal towns", "Election result certified"]
    # Format mirrors get_previous_headlines: headline / tier / date.
    assert window[0] == {"headline": "Flood hits coastal towns", "tier": "must_know", "date": "2026-03-10"}


def test_build_window_orders_newest_first_within_the_window():
    rows = [
        {"title": "Tuesday story", "tier": "should_know", "shown_at": "2026-03-09T09:00:00+00:00"},
        {"title": "Thursday story", "tier": "must_know", "shown_at": "2026-03-11T09:00:00+00:00"},
        {"title": "Wednesday story", "tier": "should_know", "shown_at": "2026-03-10T09:00:00+00:00"},
    ]

    window = build_window(rows, end_date=dt.date(2026, 3, 11), days=7)

    assert [w["headline"] for w in window] == ["Thursday story", "Wednesday story", "Tuesday story"]


def test_select_window_dates_spaces_dates_and_requires_minimum_titles():
    # 40 days of data, 5 titles/day. Want 3 windows, >=20 titles each, >=7 days apart.
    rows = []
    base = dt.date(2026, 2, 1)
    for i in range(40):
        d = base + dt.timedelta(days=i)
        for k in range(5):
            rows.append({"title": f"t{i}-{k}", "tier": "must_know", "shown_at": f"{d.isoformat()}T08:00:00+00:00"})

    dates = select_window_dates(rows, n=3, days=7, min_titles=20, spacing_days=7)

    assert len(dates) == 3
    # Spaced at least 7 days apart.
    gaps = [(dates[i] - dates[i + 1]).days for i in range(len(dates) - 1)]
    assert all(abs(g) >= 7 for g in gaps)
    # Each selected window actually has >= min_titles.
    for d in dates:
        assert len(build_window(rows, end_date=d, days=7)) >= 20


def test_theme_frequencies_ranks_salient_terms_and_drops_stopwords():
    titles = [
        "Iran war halted by Senate vote",
        "Senate rebukes Trump on Iran war",
        "Iran nuclear talks resume in Geneva",
        "Heatwave grips Europe",
    ]
    freqs = theme_frequencies(titles, top_n=4)
    terms = [t for t, _ in freqs]

    assert freqs[0][0] == "iran"  # appears in 3 titles -> most salient
    assert "the" not in terms and "by" not in terms and "on" not in terms  # stopwords gone
    assert all(len(t) >= 3 for t in terms)  # no 1-2 char noise
