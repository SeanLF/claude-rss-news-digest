"""Feed validation CLI for testing RSS sources.

Separate from feeds.py to keep runtime code clean from diagnostic tooling.
"""

import json
from datetime import datetime
from pathlib import Path

from db import get_failing_sources, init_db
from feeds import fetch_source, load_sources, parse_date


def validate_single_feed(source: dict) -> dict:
    """Validate a single RSS feed. Returns result dict."""
    source_id = source["id"]
    _, articles, error = fetch_source(source, timeout=15)

    result = {
        "id": source_id,
        "name": source["name"],
        "url": source["url"],
        "status": "failed" if error else "ok",
        "article_count": 0,
        "error": error,
    }

    if error:
        return result

    result["article_count"] = len(articles)

    if articles:
        dates = [parse_date(a.get("published")) for a in articles]
        valid_dates = [d for d in dates if d is not None]
        result["parseable_dates"] = len(valid_dates)
        if valid_dates:
            result["oldest_article"] = min(valid_dates).isoformat()
            result["newest_article"] = max(valid_dates).isoformat()
        result["sample_headline"] = articles[0].get("title", "")

    return result


def print_feed_result(result: dict):
    """Print human-readable validation result for a single feed."""
    print(f"[{result['id']}] {result['name']}")
    url = result["url"]
    print(f"  URL: {url[:80]}{'...' if len(url) > 80 else ''}")

    if result["error"]:
        print(f"  Status: FAILED - {result['error']}")
    else:
        article_count = result["article_count"]
        print(f"  Status: OK - {article_count} articles")

        if result.get("oldest_article"):
            oldest = datetime.fromisoformat(result["oldest_article"])
            newest = datetime.fromisoformat(result["newest_article"])
            parseable = result.get("parseable_dates", 0)
            print(
                f"  Dates: {oldest.strftime('%Y-%m-%d %H:%M')} → {newest.strftime('%Y-%m-%d %H:%M')} "
                f"({parseable}/{article_count} parseable)"
            )
        elif article_count > 0:
            print("  Dates: No parseable dates found")

        if result.get("sample_headline"):
            sample = result["sample_headline"][:60]
            ellipsis = "..." if len(result["sample_headline"]) > 60 else ""
            print(f'  Sample: "{sample}{ellipsis}"')

    print()


def validate_feeds_cli(
    sources: list[dict],
    db_path: Path,
    migrations_dir: Path,
    json_output: bool = False,
) -> int:
    """Test all RSS feeds and report health status. Returns exit code."""
    if not json_output:
        print(f"\n{'=' * 60}")
        print("RSS Feed Validation")
        print(f"{'=' * 60}")
        print(f"Testing {len(sources)} sources...\n")

    results = [validate_single_feed(source) for source in sources]
    if not json_output:
        for result in results:
            print_feed_result(result)

    failed_count = sum(1 for r in results if r["error"])
    total_articles = sum(r["article_count"] for r in results)

    init_db(db_path, migrations_dir)
    persistently_failing = get_failing_sources(db_path, min_consecutive=3)

    if json_output:
        output = {
            "total_sources": len(sources),
            "successful": len(sources) - failed_count,
            "failed": failed_count,
            "total_articles": total_articles,
            "sources": results,
            "persistently_failing": [{"id": sid, "consecutive_failures": count} for sid, count in persistently_failing],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"{'=' * 60}")
        print("Summary")
        print(f"{'=' * 60}")
        print(f"Total sources: {len(sources)}")
        print(f"Successful: {len(sources) - failed_count}")
        print(f"Failed: {failed_count}")
        print(f"Total articles: {total_articles}")

        if failed_count > 0:
            print("\nFailed sources:")
            for r in results:
                if r["error"]:
                    print(f"  - {r['name']} ({r['id']}): {r['error']}")

        if persistently_failing:
            print("\nSources with 3+ consecutive historical failures:")
            for sid, count in persistently_failing:
                print(f"  - {sid}: {count} failures")

        print()

    return 1 if failed_count > 0 else 0


# Allow running directly for testing
if __name__ == "__main__":
    import sys

    from config import DB_PATH, MIGRATIONS_DIR, SOURCES_FILE

    sources = load_sources(SOURCES_FILE)
    json_output = "--json" in sys.argv
    sys.exit(validate_feeds_cli(sources, DB_PATH, MIGRATIONS_DIR, json_output))
