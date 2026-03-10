"""Prepare input files for Claude curation."""

import csv
import html
import json
import logging
import random
import shutil
from pathlib import Path

from config import (
    CLAUDE_INPUT_DIR,
    DATA_DIR,
    DEDUP_SIMILARITY_THRESHOLD,
    DEDUP_WINDOW_DAYS,
    FETCHED_DIR,
    MAX_ARTICLES_FOR_DRY_RUN,
    MAX_SUMMARY_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_TOKENS_PER_FILE,
    OUTPUT_DIR,
)
from db import get_previous_headlines, get_yesterday_digest_headlines, log_dedup_action
from dedup import TfidfMatcher
from render import estimate_tokens, is_safe_url, strip_html

logger = logging.getLogger(__name__)


def prepare_claude_input(sources: list[dict], dry_run: bool = False) -> list[Path]:
    """Prepare CSV input files for Claude - split if too large.

    Returns:
        List of article CSV file paths created
    """
    if CLAUDE_INPUT_DIR.exists():
        shutil.rmtree(CLAUDE_INPUT_DIR)
    CLAUDE_INPUT_DIR.mkdir(parents=True)

    # Get previous headlines for deduplication (returns RSS titles via COALESCE)
    previous_headlines = get_previous_headlines(days=DEDUP_WINDOW_DAYS)
    blocklist_headlines = [h["headline"] for h in previous_headlines if h["headline"]]

    # Build TF-IDF matcher for dedup pre-filtering
    dedup_matcher = TfidfMatcher(blocklist_headlines) if blocklist_headlines else None

    # Write sources CSV
    sources_file = CLAUDE_INPUT_DIR / "sources.csv"
    with open(sources_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "bias", "perspective"])
        for s in sources:
            writer.writerow([s["id"], s["name"], s["bias"], s["perspective"]])

    # Write recent RSS titles for RECAP subagent (replaces recent_headlines.csv)
    if previous_headlines:
        titles_file = CLAUDE_INPUT_DIR / "recent_rss_titles.csv"
        with open(titles_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["title", "date"])
            for h in previous_headlines:
                writer.writerow([h["headline"], h["date"]])
        logger.info("Context: %d recent RSS titles", len(previous_headlines))

    # Copy weekly_recap.txt if it exists
    weekly_recap = DATA_DIR / "weekly_recap.txt"
    if weekly_recap.exists():
        shutil.copy2(weekly_recap, CLAUDE_INPUT_DIR / "weekly_recap.txt")

    # Write yesterday's digest headlines for SELECT cross-day dedup
    yesterday_headlines = get_yesterday_digest_headlines()
    if yesterday_headlines:
        headlines_file = CLAUDE_INPUT_DIR / "yesterday_headlines.txt"
        with open(headlines_file, "w") as f:
            for h in yesterday_headlines:
                f.write(f"{h['tier']}: {h['headline']}\n")
        logger.info("Context: %d yesterday digest headlines", len(yesterday_headlines))

    # Collect all articles, filtering duplicates via TF-IDF
    all_articles = []
    article_index: dict[str, dict] = {}
    article_counter = 0
    filtered_count = 0
    filtered_similarities: list[float] = []

    for source in sources:
        source_file = FETCHED_DIR / f"{source['id']}.json"
        if source_file.exists():
            with open(source_file) as sf:
                articles = json.load(sf)
            for a in articles:
                url = a.get("url", "")[:2000]
                if not is_safe_url(url):
                    continue
                title = html.escape(strip_html(a.get("title") or ""))[:MAX_TITLE_LENGTH]
                summary = html.escape(strip_html(a.get("summary") or ""))[:MAX_SUMMARY_LENGTH]

                # TF-IDF dedup pre-filter
                if dedup_matcher and title:
                    matched_headline, similarity = dedup_matcher.find_most_similar(title)
                    if similarity >= DEDUP_SIMILARITY_THRESHOLD:
                        log_dedup_action(
                            article_title=title,
                            article_source_id=source["id"],
                            matched_headline=matched_headline or "",
                            similarity=similarity,
                            threshold=DEDUP_SIMILARITY_THRESHOLD,
                            action="filtered",
                        )
                        filtered_count += 1
                        filtered_similarities.append(similarity)
                        continue

                article_counter += 1
                article_id = f"A{article_counter}"

                # Article index: maps ID to resolved metadata (never seen by Claude)
                article_index[article_id] = {
                    "url": url,
                    "source_id": source["id"],
                    "bias": source["bias"],
                    "original_title": title,
                    "name": source["name"],
                }

                # CSV row: no URL (Claude sees article_id instead)
                all_articles.append([article_id, source["id"], title, a.get("published", ""), summary])

    # Write article index (for post-Claude resolution)
    index_path = CLAUDE_INPUT_DIR / "article_index.json"
    with open(index_path, "w") as f:
        json.dump(article_index, f, indent=2)

    # Also copy to OUTPUT_DIR for persistence across runs (--write-only needs it)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(index_path, OUTPUT_DIR / "article_index.json")

    # Truncate for dry runs: sample across sources instead of slicing alphabetically
    truncated_count = 0
    if dry_run and len(all_articles) > MAX_ARTICLES_FOR_DRY_RUN:
        random.shuffle(all_articles)
        seen_sources: set[str] = set()
        sampled: list = []
        rest: list = []
        for article in all_articles:
            source_id = article[1]
            (sampled if source_id not in seen_sources else rest).append(article)
            seen_sources.add(source_id)
        all_articles = (sampled + rest)[:MAX_ARTICLES_FOR_DRY_RUN]
        truncated_count = len(sampled) + len(rest) - len(all_articles)

    # Split articles into multiple files if needed
    article_files = []
    current_file_num = 1
    current_rows: list[list[str]] = []
    current_tokens = 0
    header = ["article_id", "source_id", "title", "published", "summary"]

    for row in all_articles:
        row_text = ",".join(str(x) for x in row)
        row_tokens = estimate_tokens(row_text)

        if current_tokens + row_tokens > MAX_TOKENS_PER_FILE and current_rows:
            file_path = CLAUDE_INPUT_DIR / f"articles_{current_file_num}.csv"
            with open(file_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(current_rows)
            article_files.append(file_path)
            current_file_num += 1
            current_rows = []
            current_tokens = 0

        current_rows.append(row)
        current_tokens += row_tokens

    # Write final file
    if current_rows:
        file_path = CLAUDE_INPUT_DIR / f"articles_{current_file_num}.csv"
        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(current_rows)
        article_files.append(file_path)

    # Log summary
    details = []
    if filtered_count > 0:
        sim_min, sim_max = min(filtered_similarities), max(filtered_similarities)
        details.append(f"{filtered_count} filtered as duplicates (sim {sim_min:.2f}-{sim_max:.2f})")
    if truncated_count > 0:
        details.append(f"{truncated_count} truncated for dry-run (limit {MAX_ARTICLES_FOR_DRY_RUN})")
    summary = f"Sending {len(all_articles)} articles to Claude"
    if details:
        summary += f" ({', '.join(details)})"
    logger.info(summary)

    return article_files
