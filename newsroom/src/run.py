#!/usr/bin/env python3
"""
News Digest - Automated daily news curation

Pipeline: Fetch RSS -> Claude curation -> Email delivery
"""

import argparse
import logging
import os
import re
import subprocess
import sys
from datetime import UTC, datetime

import db
from broadcast import send_broadcast, send_health_alert, send_test_email
from claude import generate_selections, generate_weekly_recap, health_check
from claude_agent_sdk import ClaudeSDKError
from config import (
    CLAUDE_INPUT_DIR,
    DATA_DIR,
    DB_PATH,
    FETCHED_DIR,
    HEALTH_ALERT_THRESHOLD,
    MIGRATIONS_DIR,
    SOURCES_FILE,
    STYLES_FILE,
    TEMPLATE_FILE,
)
from digest import (
    cleanup_shown_headlines,
    find_latest_digest,
    load_selections,
    read_shown_headlines,
    resolve_article_ids,
    write_digest,
)
from feeds import collect_fetched_articles, fetch_feeds, load_sources
from feeds_cli import validate_feeds_cli
from merge import assemble_selections
from prepare import prepare_claude_input
from render import extract_preheader, prepare_for_email, replace_placeholders
from utils import check_internet, setup_logging, validate_env

logger = logging.getLogger(__name__)

WEEKLY_RECAP_MAX_WEEKS = 6


def maybe_update_weekly_recap():
    """Generate weekly recap if last entry is older than 7 days.

    Queries RSS titles from shown_narratives, calls Haiku to summarise,
    appends to data/weekly_recap.txt. Keeps last WEEKLY_RECAP_MAX_WEEKS weeks.
    """
    recap_path = DATA_DIR / "weekly_recap.txt"

    # Check if recap needs updating (parse last "## Week of YYYY-MM-DD" line)
    if recap_path.exists():
        content = recap_path.read_text()
        dates = re.findall(r"^## Week of (\d{4}-\d{2}-\d{2})", content, re.MULTILINE)
        if dates:
            try:
                last_date = datetime.strptime(dates[-1], "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                logger.warning("Could not parse date in weekly_recap.txt, regenerating")
            else:
                days_since = (datetime.now(UTC) - last_date).days
                if days_since < 7:
                    logger.info("Weekly recap is current (last: %s, %d days ago)", dates[-1], days_since)
                    return

    # Get RSS titles from the last 7 days
    titles = db.get_previous_headlines(days=7)
    if not titles:
        logger.info("No RSS titles available for weekly recap")
        return

    title_lines = "\n".join(f"- {t['headline']}" for t in titles if t["headline"])
    if not title_lines:
        return

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        summary = generate_weekly_recap(title_lines)
    except (RuntimeError, ClaudeSDKError, subprocess.SubprocessError, OSError) as e:
        logger.warning("Weekly recap generation failed (non-fatal): %s", e)
        return

    new_entry = f"## Week of {today}\n{summary.strip()}\n\n"

    # Append and trim to max weeks
    if recap_path.exists():
        existing = recap_path.read_text()
        sections = re.split(r"(?=^## Week of )", existing, flags=re.MULTILINE)
        sections = [s for s in sections if s.strip()]
        sections.append(new_entry)
        # Keep last N weeks
        sections = sections[-WEEKLY_RECAP_MAX_WEEKS:]
        recap_path.write_text("".join(sections))
    else:
        recap_path.write_text(new_entry)

    logger.info("Updated weekly recap: %s", today)


def main():
    """Run full digest pipeline."""
    parser = argparse.ArgumentParser(
        description="News Digest Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                    # Full run: fetch, select, write, email, record
  python run.py --dry-run          # Generate but don't email or record
  python run.py --no-email         # Generate and record, but don't email
  python run.py --no-record        # Generate and email, but don't record
  python run.py --select-only      # Run selection only (create selections.json)
  python run.py --write-only       # Run rendering only (use existing selections.json)
  python run.py --send-only        # Send latest digest (retry after failure)
  python run.py --preview          # Open latest digest in browser
  python run.py --test-email you@example.com  # Test Resend config
  python run.py --validate         # Test all RSS feeds and report status
  python run.py --validate --json  # Test RSS feeds with JSON output
        """,
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and generate only (no email, no DB record)")
    parser.add_argument("--no-email", action="store_true", help="Skip sending email (still records to DB)")
    parser.add_argument("--no-record", action="store_true", help="Skip recording to DB (still sends email)")
    parser.add_argument("--select-only", action="store_true", help="Run selection only (create selections.json)")
    parser.add_argument("--write-only", action="store_true", help="Run rendering only (use existing selections.json)")
    parser.add_argument("--send-only", action="store_true", help="Send latest digest without fetching/generating")
    parser.add_argument("--force", action="store_true", help="Override duplicate run guard")
    parser.add_argument("--preview", action="store_true", help="Open latest digest in browser")
    parser.add_argument("--test-email", metavar="EMAIL", help="Send test email and exit")
    parser.add_argument("--validate", action="store_true", help="Test all RSS feeds")
    parser.add_argument("--json", action="store_true", help="Output in JSON format (with --validate)")
    parser.add_argument("--health-check", action="store_true", help="Verify Claude auth is working")
    parser.add_argument(
        "--verify-today",
        action="store_true",
        help="Dead-man's switch: exit non-zero if no completed digest run exists for today (UTC)",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N", help="Limit articles for testing (like dry-run truncation but with recording)"
    )
    parser.add_argument("--model", metavar="MODEL", help="Override Claude model (e.g. haiku for cheap test runs)")
    args = parser.parse_args()

    setup_logging()

    skip_email = args.dry_run or args.no_email or args.select_only
    skip_record = args.dry_run or args.no_record or args.select_only
    skip_alert = args.dry_run or args.select_only

    # Test email mode
    if args.test_email:
        validate_env(dry_run=True)
        return send_test_email(args.test_email)

    # Validate mode
    if args.validate:
        sources = load_sources(SOURCES_FILE)
        return validate_feeds_cli(sources, DB_PATH, MIGRATIONS_DIR, json_output=args.json)

    # Health check mode
    if args.health_check:
        return health_check()

    # Verify-today mode (dead-man's switch): exit non-zero if today's digest didn't complete.
    # Read-only: skip migrations so this probe never writes to the production DB.
    if args.verify_today:
        db.init(DB_PATH, MIGRATIONS_DIR, apply_migrations=False)
        if db.has_completed_run_today(on_error=False):
            logger.info("Verified: a completed digest run exists for today.")
            return 0
        logger.error("No completed digest run for today -- the morning run may have failed to go out.")
        return 1

    # Preview mode
    if args.preview:
        digest = find_latest_digest()
        if not digest:
            logger.error("No digest found to preview")
            return 1
        if os.environ.get("IN_DOCKER"):
            logger.info("Preview (Docker): %s", digest.absolute())
        else:
            logger.info("Opening: %s", digest.name)
            subprocess.run(["open", str(digest)])
        return 0

    # Send-only mode
    if args.send_only:
        validate_env(dry_run=False)
        db.init(DB_PATH, MIGRATIONS_DIR)
        digest = find_latest_digest()
        if not digest:
            logger.error("No digest found to send")
            return 1
        logger.info("Sending existing digest: %s", digest.name)
        db.start_run(recording=True, broadcasting=True, alerting=False)
        try:
            db.save_digest(digest)
            recipients = send_broadcast(digest, prepare_for_email)
            shown_headlines = read_shown_headlines()
            if shown_headlines:
                db.record_shown_headlines(shown_headlines)
            db.complete_run(articles_fetched=0, articles_emailed=recipients)
        except Exception:
            db.abort_run()
            raise
        cleanup_shown_headlines()
        return 0

    # Write-only mode
    if args.write_only:
        validate_env(dry_run=skip_email)
        db.init(DB_PATH, MIGRATIONS_DIR)
        selections = load_selections(CLAUDE_INPUT_DIR / "selections.json")
        selections = resolve_article_ids(selections)
        preheader = extract_preheader(selections)
        digest = write_digest(selections, TEMPLATE_FILE)
        replace_placeholders(digest, selections, STYLES_FILE, preheader)
        db.start_run(recording=not skip_record, broadcasting=not skip_email, alerting=False)
        try:
            db.save_digest(digest, preheader=preheader)
            recipients = 0
            if db.should_broadcast():
                recipients = send_broadcast(digest, prepare_for_email)
            shown_headlines = read_shown_headlines()
            if shown_headlines:
                db.record_shown_headlines(shown_headlines)
            db.complete_run(articles_fetched=0, articles_emailed=recipients)
        except Exception:
            db.abort_run()
            raise
        cleanup_shown_headlines()
        return 0

    # Full pipeline
    validate_env(dry_run=skip_email)

    if not check_internet():
        logger.info("No internet connection, skipping")
        return 0

    sources = load_sources(SOURCES_FILE)
    db.init(DB_PATH, MIGRATIONS_DIR)

    if db.has_completed_run_today() and not args.force:
        logger.error("A completed run already exists for today. Use --force to override.")
        return 1

    last_run = db.get_last_run_time()
    db.start_run(recording=not skip_record, broadcasting=not skip_email, alerting=not skip_alert)

    try:
        fetch_result = fetch_feeds(sources, FETCHED_DIR, last_run)
        db.record_source_health(fetch_result.health_records)
        db.archive_articles(collect_fetched_articles(FETCHED_DIR))

        if db.should_alert():
            persistently_failing = db.get_failing_sources(min_consecutive=HEALTH_ALERT_THRESHOLD)
            if persistently_failing:
                failed_this_run = sum(1 for _, success, *_ in fetch_result.health_records if not success)
                try:
                    send_health_alert(persistently_failing, failed_this_run, len(sources))
                except Exception as e:
                    logger.error("Failed to send health alert (non-fatal): %s", e)

        prepare_claude_input(sources, dry_run=args.dry_run, article_limit=args.limit)
        maybe_update_weekly_recap()
        usage_rows = generate_selections(model=args.model)
        try:
            db.record_usage(usage_rows)
        except Exception:
            logger.warning("Usage tracking failed (non-fatal)", exc_info=True)
        selections_path = assemble_selections(CLAUDE_INPUT_DIR)
        selections = load_selections(selections_path)
        db.archive_selections(selections_path.read_text())
        clusters_path = CLAUDE_INPUT_DIR / "clusters.json"
        if clusters_path.exists():
            db.archive_clusters(clusters_path.read_text())
        db.archive_run_artifacts(CLAUDE_INPUT_DIR, models={"select": args.model or "claude-sonnet-4-6"})
        selections = resolve_article_ids(selections)

        if args.select_only:
            logger.info("Select-only mode: stopping after selection")
            return 0

        preheader = extract_preheader(selections)
        digest = write_digest(selections, TEMPLATE_FILE)
        replace_placeholders(digest, selections, STYLES_FILE, preheader)
        db.save_digest(digest, preheader=preheader)

        if db.should_broadcast():
            recipients = send_broadcast(digest, prepare_for_email)
        else:
            recipients = 0
            logger.info("Skipping broadcast: %s", digest.name)

        shown_headlines = read_shown_headlines()
        if not shown_headlines:
            logger.warning("No headlines recorded - Claude may not have generated shown_headlines.json")
        db.record_shown_headlines(shown_headlines)
        db.complete_run(articles_fetched=fetch_result.total_kept, articles_emailed=recipients)
    except Exception:
        db.abort_run()
        raise

    cleanup_shown_headlines()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logging.warning("Interrupted")
        sys.exit(130)
    except Exception as e:
        logging.error("%s: %s", type(e).__name__, e)
        sys.exit(1)
