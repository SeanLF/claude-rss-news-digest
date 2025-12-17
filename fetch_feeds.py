#!/usr/bin/env python3
"""
Fetch RSS feeds from 21 news sources in parallel and save as JSON files.
Filters out articles published before the last digest run.
"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import feedparser

DB_PATH = Path(__file__).parent / "digest.db"

# Output directory
OUTPUT_DIR = Path(__file__).parent / "fetched"


def get_last_run_time() -> datetime | None:
    """Get the timestamp of the last digest run from the database."""
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute("SELECT MAX(run_at) FROM digest_runs")
        result = cursor.fetchone()[0]
        conn.close()
        if result:
            # Parse the SQLite datetime string
            return datetime.fromisoformat(result.replace(" ", "T")).replace(tzinfo=timezone.utc)
    except Exception as e:
        print(f"  [db] Could not get last run time: {e}")
    return None


def parse_article_date(date_str: str | None) -> datetime | None:
    """Parse various date formats from RSS feeds."""
    if not date_str:
        return None
    try:
        # Try ISO format first
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        # Try common RSS date format
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return None

# RSS Sources configuration
# Each source has: id, name, url, bias, perspective
SOURCES = [
    {
        "id": "globe_and_mail",
        "name": "Globe and Mail",
        "url": "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/",
        "bias": "center",
        "perspective": "canadian",
    },
    {
        "id": "al_jazeera",
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "bias": "center",
        "perspective": "middle_east",
    },
    {
        "id": "reuters",
        "name": "Reuters",
        "url": "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en",
        "bias": "center",
        "perspective": "wire_service",
    },
    {
        "id": "scmp_world",
        "name": "SCMP World",
        "url": "https://www.scmp.com/rss/5/feed",
        "bias": "center",
        "perspective": "asian",
    },
    {
        "id": "scmp_china",
        "name": "SCMP China",
        "url": "https://www.scmp.com/rss/4/feed",
        "bias": "center",
        "perspective": "asian",
    },
    {
        "id": "scmp_asia",
        "name": "SCMP Asia",
        "url": "https://www.scmp.com/rss/2/feed",
        "bias": "center",
        "perspective": "asian",
    },
    {
        "id": "hacker_news",
        "name": "Hacker News",
        "url": "https://hnrss.org/newest?points=100",
        "bias": "center",
        "perspective": "tech",
    },
    {
        "id": "propublica",
        "name": "ProPublica",
        "url": "https://www.propublica.org/feeds/propublica/main",
        "bias": "center-left",
        "perspective": "investigative",
    },
    {
        "id": "the_verge",
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "bias": "center-left",
        "perspective": "tech",
    },
    {
        "id": "the_intercept",
        "name": "The Intercept",
        "url": "https://theintercept.com/feed/?rss",
        "bias": "left",
        "perspective": "investigative",
    },
    {
        "id": "rest_of_world",
        "name": "Rest of World",
        "url": "https://restofworld.org/feed/latest",
        "bias": "center",
        "perspective": "global_tech",
    },
    {
        "id": "le_monde",
        "name": "Le Monde",
        "url": "https://www.lemonde.fr/rss/une.xml",
        "bias": "center",
        "perspective": "french",
    },
    {
        "id": "financial_times",
        "name": "Financial Times",
        "url": "https://www.ft.com/news-feed?format=rss",
        "bias": "center-right",
        "perspective": "western_finance",
    },
    {
        "id": "the_guardian",
        "name": "The Guardian",
        "url": "https://www.theguardian.com/international/rss",
        "bias": "center-left",
        "perspective": "western",
    },
    {
        "id": "ars_technica",
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "bias": "center",
        "perspective": "tech",
    },
    {
        "id": "der_spiegel",
        "name": "Der Spiegel",
        "url": "https://www.spiegel.de/international/index.rss",
        "bias": "center-left",
        "perspective": "german",
    },
    {
        "id": "nyt_world",
        "name": "NYT World",
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "bias": "center-left",
        "perspective": "american",
    },
    {
        "id": "washington_post",
        "name": "Washington Post",
        "url": "https://feeds.washingtonpost.com/rss/world",
        "bias": "center-left",
        "perspective": "american",
    },
    {
        "id": "wsj_world",
        "name": "WSJ World",
        "url": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
        "bias": "center-right",
        "perspective": "american",
    },
    {
        "id": "the_economist",
        "name": "The Economist",
        "url": "https://rss.app/feeds/9tqWs1xrWkLAfbEU.xml",
        "bias": "center-right",
        "perspective": "western",
    },
    {
        "id": "cbc_news",
        "name": "CBC News",
        "url": "https://www.cbc.ca/webfeed/rss/rss-world",
        "bias": "center",
        "perspective": "canadian",
    },
    # Non-Western sources for geographic balance
    {
        "id": "nikkei_asia",
        "name": "Nikkei Asia",
        "url": "https://asia.nikkei.com/rss/feed/nar",
        "bias": "center-right",
        "perspective": "japanese",
    },
    {
        "id": "the_hindu",
        "name": "The Hindu",
        "url": "https://www.thehindu.com/news/international/feeder/default.rss",
        "bias": "center",
        "perspective": "indian",
    },
    {
        "id": "straits_times",
        "name": "Straits Times",
        "url": "https://www.straitstimes.com/news/world/rss.xml",
        "bias": "center",
        "perspective": "singaporean",
    },
    {
        "id": "daily_maverick",
        "name": "Daily Maverick",
        "url": "https://www.dailymaverick.co.za/dmrss/",
        "bias": "center-left",
        "perspective": "south_african",
    },
    {
        "id": "rappler",
        "name": "Rappler",
        "url": "https://www.rappler.com/feed/",
        "bias": "center",
        "perspective": "filipino",
    },
    {
        "id": "ap_news",
        "name": "AP News",
        "url": "https://rss.app/feeds/cTP1MA5Cle6LFnnc.xml",
        "bias": "center",
        "perspective": "wire_service",
    },
    {
        "id": "afp",
        "name": "AFP",
        "url": "https://flipboard.com/topic/fr-afp.rss",
        "bias": "center",
        "perspective": "wire_service",
    },
]


def parse_published(entry) -> str | None:
    """Extract and normalize published date from feed entry."""
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        try:
            dt = datetime(*published[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except (TypeError, ValueError):
            pass
    return entry.get("published") or entry.get("updated")


def fetch_source(source: dict, timeout: int = 15) -> tuple[str, list[dict]]:
    """Fetch and parse a single RSS source. Returns (source_id, articles)."""
    import urllib.request
    source_id = source["id"]
    try:
        # Use urllib with timeout, then parse
        req = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
        feed = feedparser.parse(data)

        if feed.bozo and not feed.entries:
            print(f"  [{source_id}] Feed error: {feed.bozo_exception}")
            return source_id, []

        articles = []
        for entry in feed.entries:
            article = {
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", ""),
                "published": parse_published(entry),
                "summary": (entry.get("summary") or entry.get("description") or "")[:500],
            }
            if article["title"] and article["url"]:
                articles.append(article)

        print(f"  [{source_id}] Fetched {len(articles)} articles")
        return source_id, articles

    except Exception as e:
        print(f"  [{source_id}] Error: {e}")
        return source_id, []


def main(parallel: bool = True):
    """Fetch all feeds and save to JSON files."""
    print(f"Fetching {len(SOURCES)} RSS feeds {'(parallel)' if parallel else '(sequential)'}...", flush=True)

    # Get last run time for filtering
    last_run = get_last_run_time()
    if last_run:
        print(f"  Filtering articles published after: {last_run.isoformat()}", flush=True)
    else:
        print("  No previous run found, including all articles", flush=True)

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Clear old files
    for f in OUTPUT_DIR.glob("*.json"):
        f.unlink()

    results = {}

    if parallel:
        # Fetch all sources in parallel
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_source, s): s for s in SOURCES}
            for future in as_completed(futures):
                source_id, articles = future.result()
                results[source_id] = articles
                sys.stdout.flush()
    else:
        # Fetch sequentially with progress
        for i, source in enumerate(SOURCES, 1):
            print(f"[{i}/{len(SOURCES)}] Fetching {source['name']}...", end=" ", flush=True)
            source_id, articles = fetch_source(source)
            results[source_id] = articles
            print(f"got {len(articles)} articles", flush=True)

    # Filter and save each source to its own JSON file
    total_articles = 0
    total_filtered = 0
    for source_id, articles in results.items():
        # Filter articles by publish date if we have a last run time
        if last_run:
            filtered = []
            for article in articles:
                pub_date = parse_article_date(article.get("published"))
                # Include if: no publish date (can't filter) OR published after last run
                if pub_date is None or pub_date > last_run:
                    filtered.append(article)
            total_filtered += len(articles) - len(filtered)
            articles = filtered

        output_file = OUTPUT_DIR / f"{source_id}.json"
        with open(output_file, "w") as f:
            json.dump(articles, f, indent=2)
        total_articles += len(articles)

    # Save metadata with source info (bias, perspective, name)
    metadata = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources_count": len(SOURCES),
        "total_articles": total_articles,
        "article_counts": {s["id"]: len(results.get(s["id"], [])) for s in SOURCES},
        "sources": {
            s["id"]: {
                "name": s["name"],
                "bias": s["bias"],
                "perspective": s["perspective"],
            }
            for s in SOURCES
        },
    }
    with open(OUTPUT_DIR / "_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    if total_filtered > 0:
        print(f"\nDone! {total_articles} new articles ({total_filtered} old articles filtered out)", flush=True)
    else:
        print(f"\nDone! Fetched {total_articles} articles from {len(SOURCES)} sources.", flush=True)
    print(f"Output: {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    import sys
    main(parallel="--sequential" not in sys.argv)
