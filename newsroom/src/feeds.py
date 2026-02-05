"""RSS feed fetching and source management.

Handles loading source definitions, fetching feeds with retries,
and filtering articles by publication date.
"""

import json
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

# Cache for source name -> id mapping
_source_name_to_id_cache: dict[str, str] | None = None


def get_source_id_by_name(name: str, sources_file: Path) -> str | None:
    """Map display name (e.g., 'BBC World') to source_id (e.g., 'bbc_world')."""
    global _source_name_to_id_cache
    if _source_name_to_id_cache is None:
        sources = load_sources(sources_file)
        _source_name_to_id_cache = {s["name"].lower(): s["id"] for s in sources}
    return _source_name_to_id_cache.get(name.lower())


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
    required_keys = {"id", "name", "url", "bias", "perspective"}
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
                # Parse error - don't retry, feed is malformed
                error_msg = f"Feed parse error: {feed.bozo_exception}"
                print(f"  [{source_id}] {error_msg}", flush=True)
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
                }
                if article["title"] and article["url"]:
                    articles.append(article)

            return source_id, articles, None  # Success

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Transient errors - retry with exponential backoff
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (2**attempt)  # 1s, 2s, 4s
                time.sleep(delay)
            continue
        except Exception as e:
            # Non-transient error - don't retry
            error_msg = f"{type(e).__name__}: {e}"
            print(f"  [{source_id}] Error: {error_msg}", flush=True)
            return source_id, [], error_msg

    # All retries exhausted
    error_msg = str(getattr(last_error, "reason", last_error)) if last_error else "Unknown"
    print(f"  [{source_id}] Failed after {MAX_RETRIES} retries: {error_msg}", flush=True)
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


def fetch_feeds(
    sources: list[dict],
    fetched_dir: Path,
    last_run: datetime | None = None,
    log_fn=None,
) -> FetchResult:
    """Fetch all RSS feeds in parallel.

    Args:
        sources: List of source definitions from load_sources()
        fetched_dir: Directory to write per-source JSON files
        last_run: Filter articles published after this time
        log_fn: Optional logging function (message, level)

    Returns:
        FetchResult with counts and health records
    """
    if log_fn:
        log_fn(f"Fetching {len(sources)} RSS feeds...")

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
            print(f"  Filtering after: {last_run.isoformat()} (kept/fetched)", flush=True)
        sources_with_articles.sort(key=lambda x: (-x[2], -x[1]))
        for sid, fetched, kept in sources_with_articles:
            print(f"  [{sid}] {kept}/{fetched}", flush=True)

    # Summary
    failed_this_run = [(sid, err) for sid, success, err, _, _ in health_records if not success]
    succeeded = len(sources) - len(failed_this_run)
    if log_fn:
        log_fn(f"Fetched {total_kept}/{total_fetched} articles from {succeeded}/{len(sources)} sources")

    if failed_this_run and log_fn:
        log_fn(f"Failed sources this run: {', '.join(sid for sid, _ in failed_this_run)}", "WARN")

    return FetchResult(
        total_kept=total_kept,
        total_fetched=total_fetched,
        health_records=health_records,
    )
