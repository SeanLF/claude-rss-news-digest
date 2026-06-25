"""Reconstruct historical RECAP input windows from shown_narratives.

RECAP's production input is ``get_previous_headlines(7)`` -- the trailing 7 days
of ``shown_narratives`` (original RSS title, tier, date), written to
``recent_rss_titles.csv``. To evaluate RECAP across many weeks we rebuild that
exact window for a spread of historical end-dates from the same rows.

Pure functions over row-lists (DB-agnostic, trivially testable); a thin DB
loader sits on top in ``load_shown_rows``.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path


def _parse_dt(value: str) -> dt.datetime:
    """Parse an ISO-ish shown_at into an aware/naive datetime (UTC assumed)."""
    text = value.strip().replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        # date-only fallback
        return dt.datetime.fromisoformat(text[:10])


def build_window(rows: list[dict], end_date: dt.date, days: int = 7) -> list[dict]:
    """The trailing ``days``-day window of shown titles ending at ``end_date``.

    Mirrors get_previous_headlines output: ``{headline, tier, date}`` rows,
    newest-first. A row is in-window when ``end_date - days < date(shown_at) <= end_date``.
    """
    lower = end_date - dt.timedelta(days=days)
    selected: list[tuple[dt.datetime, dict]] = []
    for r in rows:
        when = _parse_dt(r["shown_at"])
        d = when.date()
        if lower < d <= end_date:
            selected.append((when, {"headline": r["title"], "tier": r["tier"], "date": d.isoformat()}))
    selected.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in selected]


def select_window_dates(
    rows: list[dict],
    n: int,
    *,
    days: int = 7,
    min_titles: int = 20,
    spacing_days: int = 7,
) -> list[dt.date]:
    """Pick up to ``n`` end-dates spread across history, newest-first.

    Greedy from the most recent date backwards: take a date whose window has at
    least ``min_titles`` titles and is at least ``spacing_days`` earlier than the
    last pick. Guarantees non-overlapping, well-populated windows.
    """
    all_dates = sorted({_parse_dt(r["shown_at"]).date() for r in rows}, reverse=True)
    picked: list[dt.date] = []
    for d in all_dates:
        if len(picked) >= n:
            break
        if picked and (picked[-1] - d).days < spacing_days:
            continue
        if len(build_window(rows, end_date=d, days=days)) >= min_titles:
            picked.append(d)
    return picked


# Common words to ignore when ranking salient title terms. Not exhaustive -- just
# enough that frequency ranking surfaces topics, not grammar.
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "we",
        "you",
        "his",
        "her",
        "their",
        "our",
        "your",
        "i",
        "me",
        "my",
        "over",
        "under",
        "after",
        "before",
        "into",
        "out",
        "up",
        "down",
        "off",
        "about",
        "than",
        "then",
        "but",
        "not",
        "no",
        "yes",
        "who",
        "whom",
        "which",
        "what",
        "when",
        "where",
        "why",
        "how",
        "new",
        "says",
        "say",
        "said",
        "amid",
        "over",
        "vs",
        "amp",
        "will",
        "would",
        "could",
        "can",
        "may",
        "might",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "get",
        "gets",
        "got",
        "make",
        "makes",
        "more",
        "most",
        "first",
    ]
)


def theme_frequencies(titles: list[str], top_n: int = 25) -> list[tuple[str, int]]:
    """Rank salient terms across titles by document frequency (a neutral topic aid).

    Pure word counting -- no semantics -- so it can drive independent golden
    labelling without borrowing the judge's reasoning. Counts each term once per
    title (document frequency), drops stopwords and tokens under 3 chars.
    """
    import re
    from collections import Counter

    counter: Counter[str] = Counter()
    for title in titles:
        tokens = {tok for tok in re.findall(r"[a-z]+", title.lower()) if len(tok) >= 3 and tok not in _STOPWORDS}
        counter.update(tokens)
    return counter.most_common(top_n)


def load_shown_rows(db_path: str | Path) -> list[dict]:
    """Load shown_narratives as {title, tier, shown_at} rows (original_title preferred)."""
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            """
            SELECT COALESCE(original_title, headline) AS title, tier, shown_at
            FROM shown_narratives
            WHERE shown_at IS NOT NULL
            """
        )
        return [{"title": t, "tier": tier, "shown_at": shown_at} for t, tier, shown_at in cursor.fetchall()]
