"""RSS feed fetching and source management.

Handles loading source definitions, fetching feeds with retries,
and filtering articles by publication date.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
from config import MAX_RETRIES, RETRY_DELAY

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result of fetching all RSS feeds."""

    total_kept: int
    total_fetched: int
    health_records: list[tuple[str, bool, str | None, int, int]] = field(default_factory=list)


def load_sources(sources_file: Path) -> list[dict]:
    """Load and validate RSS sources from JSON file."""
    with open(sources_file) as f:
        sources = json.load(f)

    # Validate schema
    required_keys = {"id", "name", "url", "bias", "factuality", "perspective"}
    for i, source in enumerate(sources):
        missing = required_keys - set(source.keys())
        if missing:
            raise ValueError(f"sources.json[{i}] missing keys: {missing}")
        if not source["url"].startswith(("http://", "https://")):
            raise ValueError(f"sources.json[{i}] invalid URL: {source['url']}")
        # Prevent path traversal - source_id is used in file paths
        if not re.match(r"^[a-z0-9_]+$", source["id"]):
            raise ValueError(
                f"sources.json[{i}] invalid id '{source['id']}': must be lowercase alphanumeric/underscore only"
            )

    return sources


def parse_date(date_str: str | None) -> datetime | None:
    """Parse RSS date formats (ISO 8601 or RFC 2822)."""
    if not date_str:
        return None
    try:
        # ISO 8601: 2025-01-15T10:30:00Z (has digit-T-digit pattern)
        if re.search(r"\dT\d", date_str):
            return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(UTC)
        # RFC 2822: Tue, 15 Jan 2025 10:30:00 GMT
        return parsedate_to_datetime(date_str).astimezone(UTC)
    except ValueError, TypeError:
        return None


def fetch_source(source: dict, timeout: int = 15) -> tuple[str, list[dict], str | None]:
    """Fetch single RSS source with retry logic.

    Returns (source_id, articles, error_or_none).
    """
    source_id = source["id"]
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310
                data = response.read()
            feed = feedparser.parse(data)

            if feed.bozo and not feed.entries:
                error_msg = f"Feed parse error: {feed.bozo_exception}"
                logger.warning("[%s] %s", source_id, error_msg)
                return source_id, [], error_msg

            articles = []
            for entry in feed.entries:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    try:
                        pub_str = datetime(*published[:6], tzinfo=UTC).isoformat()  # type: ignore[misc]
                    except TypeError, ValueError:
                        pub_str = entry.get("published") or entry.get("updated")
                else:
                    pub_str = entry.get("published") or entry.get("updated")

                article = {
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", ""),
                    "published": pub_str,
                    "summary": (entry.get("summary") or entry.get("description") or "")[:500],
                    # Kept for wire provenance: several outlets put the originating agency
                    # here verbatim ("Agence France-Presse", "Reuters"). Free -- feedparser
                    # already parsed it and we were discarding it.
                    "author": (entry.get("author") or entry.get("dc_creator") or "").strip()[:200],
                }
                if article["title"] and article["url"]:
                    articles.append(article)

            return source_id, articles, None  # Success

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (2**attempt)  # exponential from RETRY_DELAY; the last attempt never sleeps
                time.sleep(delay)
            continue
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.warning("[%s] Error: %s", source_id, error_msg)
            return source_id, [], error_msg

    # All retries exhausted
    error_msg = str(getattr(last_error, "reason", last_error)) if last_error else "Unknown"
    logger.warning("[%s] Failed after %d retries: %s", source_id, MAX_RETRIES, error_msg)
    return source_id, [], f"Failed after {MAX_RETRIES} retries: {error_msg}"


def collect_fetched_articles(fetched_dir: Path) -> list[dict]:
    """Collect all fetched articles from JSON files, adding source_id to each."""
    articles = []
    for json_file in fetched_dir.glob("*.json"):
        source_id = json_file.stem
        with open(json_file) as f:
            for article in json.load(f):
                articles.append({**article, "source_id": source_id})
    return articles


# Agencies whose name in a byline means the item is REPOSTED wire copy rather than the
# outlet's own reporting. Matched EXACTLY against a normalized author string, never as a
# substring: "Reuters Institute" is a research body, "Michael Bloomberg" is a person, and
# substring matching would credit an outlet's own journalism to a wire. That is the worse
# error of the two, so this deliberately under-detects.
#
# Measured 2026-07-25: this catches 32 of scmp_world's 50 items (its audited wire share is
# 67%), plus haaretz x2, daily_maverick, npr_world and rappler. It catches NOTHING on
# the_hindu, straits_times or al_monitor -- they publish no author field at all, and their
# provenance lives in the article body. See docs/2026-07-25-feed-sourcing-findings.md.
WIRE_AGENCIES = frozenset(
    {
        "reuters",
        "afp",
        "agence france-presse",
        "associated press",
        "ap",
        "dpa",
        "deutsche presse-agentur",
        "pa media",
        "press association",
        "efe",
        "agencia efe",
        "ansa",
        "bloomberg",
        "xinhua",
        "pti",
        "press trust of india",
        "ians",
        "anadolu agency",
        "kyodo",
        "yonhap",
        "tass",
        "upi",
        "united press international",
    }
)


def wire_agency(value: str | None) -> str | None:
    """The wire agency a byline names, or None if it names anyone else.

    Also used on a source's own name, so the agency's feed and a reposter of it resolve to
    the same key and collapse together.
    """
    if not value:
        return None
    name = " ".join(value.split()).strip(".,;:-–—").lower()
    # "The Associated Press" is NPR's form of the byline; the set stores the bare name.
    name = name.removeprefix("the ")
    return name if name in WIRE_AGENCIES else None


# A wire dateline opening the body: "WASHINGTON, July 24 (Reuters) - ", optionally preceded
# by the correspondent's name. Anchored at the START on purpose -- that anchoring, not the
# agency set, is what keeps a mid-prose "(AFP)" out, which in The Diplomat means Armed Forces
# of the Philippines. The captured name is letters-only and must also clear WIRE_AGENCIES, so
# a self-labelled "(FRANCE 24 with AFP)" never qualifies. One residual is accepted knowingly:
# an "(AFP)" sitting in dateline position is indistinguishable from the military usage, and
# dateline position is the stronger signal of the two.
_DATELINE = re.compile(
    r"^\s*(?:By\s+[^,]{0,60}?)?"  # optional "By <correspondent>", which runs straight into the place
    r"(?:[A-Z][A-Za-z.\-']*(?:[ ,][A-Z][A-Za-z.\-']*){0,4}\s*,?\s*)?"  # optional place: "WASHINGTON", "RIO DE JANEIRO"
    r"(?:\w+\s+\d{1,2}\s*)?"  # optional date: "July 24"
    r"\(\s*([A-Za-z][A-Za-z \-]{1,28}?)\s*\)\s*[-–—:]"  # the required part: "(Reuters) -"
)


def wire_from_dateline(text: str | None) -> str | None:
    """The agency named in a wire dateline at the start of an article body, else None.

    COST NOTE: the optional groups make this quadratic in the input length, and the only
    thing keeping that harmless is prepare.py truncating the summary to MAX_SUMMARY_LENGTH
    (200) before calling here. Measured: 200 chars costs 21us on prose and 9.8ms on a
    pathological all-letters input, but 2560 chars costs ~1.9s. If MAX_SUMMARY_LENGTH ever
    grows substantially, bound this input explicitly rather than relying on the caller.

    Complements the byline check for outlets that publish no author but do syndicate the
    body verbatim -- al_monitor is the measured case (6 of 20 items, all Reuters).

    Deliberately NOT paired with a trailing-sigil detector. Trying one immediately
    mislabelled globe_and_mail's "...sources told AP" -- an outlet CITING a wire's
    reporting, which is the opposite of republishing it.
    """
    if not text:
        return None
    match = _DATELINE.match(text)
    return wire_agency(match.group(1)) if match else None


def fetch_feeds(
    sources: list[dict],
    fetched_dir: Path,
    last_run: datetime | None = None,
) -> FetchResult:
    """Fetch all RSS feeds in parallel.

    Args:
        sources: List of source definitions from load_sources()
        fetched_dir: Directory to write per-source JSON files
        last_run: Filter articles published after this time

    Returns:
        FetchResult with counts and health records
    """
    logger.info("Fetching %d RSS feeds...", len(sources))

    fetched_dir.mkdir(parents=True, exist_ok=True)
    for f in fetched_dir.glob("*.json"):
        f.unlink()

    # Fetch all sources in parallel
    fetch_results = {}  # source_id -> (articles, error)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_source, s): s for s in sources}
        for future in as_completed(futures):
            source_id, articles, error = future.result()
            fetch_results[source_id] = (articles, error)

    # Filter by date, save files, and build health records
    total_kept = 0
    total_fetched = 0
    health_records = []

    for source in sources:
        source_id = source["id"]
        articles, error = fetch_results.get(source_id, ([], "Source not in results"))
        fetched_count = len(articles)

        if last_run:
            articles = [a for a in articles if (pub := parse_date(a.get("published"))) is None or pub > last_run]
        kept_count = len(articles)

        with open(fetched_dir / f"{source_id}.json", "w") as out_file:
            json.dump(articles, out_file, indent=2)

        total_kept += kept_count
        total_fetched += fetched_count
        health_records.append((source_id, error is None, error, fetched_count, kept_count))

    # Per-source breakdown (show sources with articles, sorted by kept desc)
    sources_with_articles = [(sid, fetched, kept) for sid, _, _, fetched, kept in health_records if fetched > 0]
    if sources_with_articles:
        if last_run:
            logger.info("Filtering after: %s (kept/fetched)", last_run.isoformat())
        sources_with_articles.sort(key=lambda x: (-x[2], -x[1]))
        for sid, fetched, kept in sources_with_articles:
            logger.info("  [%s] %d/%d", sid, kept, fetched)

    # Summary
    failed_this_run = [(sid, err) for sid, success, err, _, _ in health_records if not success]
    succeeded = len(sources) - len(failed_this_run)
    logger.info("Fetched %d/%d articles from %d/%d sources", total_kept, total_fetched, succeeded, len(sources))

    if failed_this_run:
        logger.warning("Failed sources this run: %s", ", ".join(sid for sid, _ in failed_this_run))

    return FetchResult(
        total_kept=total_kept,
        total_fetched=total_fetched,
        health_records=health_records,
    )
