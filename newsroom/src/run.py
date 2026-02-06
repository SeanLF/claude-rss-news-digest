#!/usr/bin/env python3
"""
News Digest - Automated daily news curation

Pipeline: Fetch RSS -> Claude curation -> Email delivery
"""

import argparse
import logging
import os
import subprocess
import sys

import db
from broadcast import send_broadcast, send_health_alert, send_test_email
from claude import generate_selections, health_check
from config import (
    CLAUDE_INPUT_DIR,
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
    write_digest,
)
from feeds import collect_fetched_articles, fetch_feeds, load_sources
from feeds_cli import validate_feeds_cli
from prepare import prepare_claude_input
from render import extract_preheader, prepare_for_email, replace_placeholders
from utils import check_internet, setup_logging, validate_env

logger = logging.getLogger(__name__)


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
    parser.add_argument("--preview", action="store_true", help="Open latest digest in browser")
    parser.add_argument("--test-email", metavar="EMAIL", help="Send test email and exit")
    parser.add_argument("--validate", action="store_true", help="Test all RSS feeds")
    parser.add_argument("--json", action="store_true", help="Output in JSON format (with --validate)")
    parser.add_argument("--health-check", action="store_true", help="Verify Claude auth is working")
    args = parser.parse_args()

    setup_logging()

    skip_email = args.dry_run or args.no_email or args.select_only
    skip_record = args.dry_run or args.no_record or args.select_only

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
        run_id = db.start_run()
        db.save_digest(digest)
        recipients = send_broadcast(digest, prepare_for_email)
        shown_headlines = read_shown_headlines()
        if shown_headlines:
            db.record_shown_headlines(shown_headlines)
        if run_id:
            db.complete_run(run_id, 0, articles_emailed=recipients)
        cleanup_shown_headlines()
        return 0

    # Write-only mode
    if args.write_only:
        validate_env(dry_run=skip_email)
        db.init(DB_PATH, MIGRATIONS_DIR)
        selections = load_selections(CLAUDE_INPUT_DIR / "selections.json")
        preheader = extract_preheader(selections)
        digest = write_digest(selections, TEMPLATE_FILE)
        replace_placeholders(digest, selections, STYLES_FILE, preheader)
        run_id = db.start_run() if not skip_record else None
        recipients = 0
        if not skip_record:
            db.save_digest(digest, preheader=preheader)
        if not skip_email:
            recipients = send_broadcast(digest, prepare_for_email)
        if not skip_record:
            shown_headlines = read_shown_headlines()
            if shown_headlines:
                db.record_shown_headlines(shown_headlines)
            if run_id:
                db.complete_run(run_id, 0, articles_emailed=recipients)
        cleanup_shown_headlines()
        return 0

    # Full pipeline
    validate_env(dry_run=skip_email)

    if not check_internet():
        logger.info("No internet connection, skipping")
        return 0

    sources = load_sources(SOURCES_FILE)
    db.init(DB_PATH, MIGRATIONS_DIR)

    # Start run for archival (will be completed at end)
    run_id = db.start_run() if not skip_record else None

    last_run = db.get_last_run_time()
    fetch_result = fetch_feeds(sources, FETCHED_DIR, last_run)
    db.record_source_health(fetch_result.health_records)

    # Archive fetched articles
    if run_id:
        db.archive_articles(run_id, collect_fetched_articles(FETCHED_DIR))

    # Health alerts
    persistently_failing = db.get_failing_sources(min_consecutive=HEALTH_ALERT_THRESHOLD)
    if persistently_failing:
        failed_this_run = sum(1 for _, success, *_ in fetch_result.health_records if not success)
        send_health_alert(persistently_failing, failed_this_run, len(sources))

    prepare_claude_input(sources, dry_run=args.dry_run)

    generate_selections()
    selections = load_selections(CLAUDE_INPUT_DIR / "selections.json")

    # Archive selections
    selections_path = CLAUDE_INPUT_DIR / "selections.json"
    if run_id and selections_path.exists():
        db.archive_selections(run_id, selections_path.read_text())

    if args.select_only:
        logger.info("Select-only mode: stopping after selection")
        return 0

    preheader = extract_preheader(selections)
    digest = write_digest(selections, TEMPLATE_FILE)
    replace_placeholders(digest, selections, STYLES_FILE, preheader)

    if not skip_record:
        db.save_digest(digest, preheader=preheader)

    recipients = 0
    if not skip_email:
        recipients = send_broadcast(digest, prepare_for_email)
    else:
        logger.info("Skipping broadcast: %s", digest.name)

    if not skip_record:
        shown_headlines = read_shown_headlines()
        if not shown_headlines:
            logger.warning("No headlines recorded - Claude may not have generated shown_headlines.json")
        db.record_shown_headlines(shown_headlines)
        if run_id:
            db.complete_run(run_id, fetch_result.total_kept, articles_emailed=recipients)

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
