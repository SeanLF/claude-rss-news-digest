"""Prepare input files for Claude curation."""

import csv
import html
import json
import shutil
from pathlib import Path

from config import (
    CLAUDE_INPUT_DIR,
    DB_PATH,
    DEDUP_SIMILARITY_THRESHOLD,
    DEDUP_WINDOW_DAYS,
    FETCHED_DIR,
    MAX_ARTICLES_FOR_DRY_RUN,
    MAX_SUMMARY_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_TOKENS_PER_FILE,
)
from db import get_previous_headlines, log_dedup_action
from dedup import TfidfMatcher
from render import estimate_tokens, is_safe_url, strip_html


def prepare_claude_input(sources: list[dict], dry_run: bool = False, log_fn=None) -> list[Path]:
    """Prepare CSV input files for Claude - split if too large.

    Args:
        sources: List of source definitions from load_sources()
        dry_run: If True, truncate articles for faster testing
        log_fn: Optional logging function (message, level)

    Returns:
        List of article CSV file paths created
    """
    if CLAUDE_INPUT_DIR.exists():
        shutil.rmtree(CLAUDE_INPUT_DIR)
    CLAUDE_INPUT_DIR.mkdir(parents=True)

    # Get previous headlines for deduplication
    previous_headlines = get_previous_headlines(DB_PATH, days=DEDUP_WINDOW_DAYS)
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

    # Write recent headlines for Claude context
    if previous_headlines:
        headlines_file = CLAUDE_INPUT_DIR / "recent_headlines.csv"
        with open(headlines_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["headline", "date"])
            for h in previous_headlines:
                writer.writerow([h["headline"], h["date"]])
        if log_fn:
            log_fn(f"Context: {len(previous_headlines)} recent headlines")

    # Collect all articles, filtering duplicates via TF-IDF
    all_articles = []
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
                            DB_PATH,
                            article_title=title,
                            article_source_id=source["id"],
                            matched_headline=matched_headline or "",
                            similarity=similarity,
                            threshold=DEDUP_SIMILARITY_THRESHOLD,
                            action="filtered",
                            log_fn=log_fn,
                        )
                        filtered_count += 1
                        filtered_similarities.append(similarity)
                        continue

                all_articles.append([source["id"], title, url, a.get("published", ""), summary])

    # Truncate if too many articles during dry runs
    truncated_count = 0
    if dry_run and len(all_articles) > MAX_ARTICLES_FOR_DRY_RUN:
        truncated_count = len(all_articles) - MAX_ARTICLES_FOR_DRY_RUN
        all_articles = all_articles[:MAX_ARTICLES_FOR_DRY_RUN]

    # Split articles into multiple files if needed
    article_files = []
    current_file_num = 1
    current_rows: list[list[str]] = []
    current_tokens = 0
    header = ["source_id", "title", "url", "published", "summary"]

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
    if log_fn:
        parts = [f"Sending {len(all_articles)} articles to Claude"]
        if filtered_count > 0:
            sim_min, sim_max = min(filtered_similarities), max(filtered_similarities)
            parts.append(f"{filtered_count} filtered as duplicates (sim {sim_min:.2f}-{sim_max:.2f})")
        if truncated_count > 0:
            parts.append(f"{truncated_count} truncated for dry-run (limit {MAX_ARTICLES_FOR_DRY_RUN})")
        log_fn(f"{parts[0]} ({', '.join(parts[1:])})" if len(parts) > 1 else parts[0])

    return article_files
