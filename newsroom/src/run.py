#!/usr/bin/env python3
"""
News Digest - Automated daily news curation

Pipeline: Fetch RSS -> Claude curation -> Email delivery
"""

import argparse
import os
import subprocess
import sys

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
from db import (
    archive_articles,
    archive_selections,
    complete_run,
    get_failing_sources,
    get_last_run_time,
    init_db,
    record_run,
    record_shown_headlines,
    record_source_health,
    save_digest,
    start_run,
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
from utils import check_internet, log, validate_env


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

    skip_email = args.dry_run or args.no_email or args.select_only
    skip_record = args.dry_run or args.no_record or args.select_only

    # Test email mode
    if args.test_email:
        validate_env(dry_run=True)
        return send_test_email(args.test_email, log_fn=log)

    # Validate mode
    if args.validate:
        sources = load_sources(SOURCES_FILE)
        return validate_feeds_cli(sources, DB_PATH, MIGRATIONS_DIR, json_output=args.json)

    # Health check mode
    if args.health_check:
        return health_check(log_fn=log)

    # Preview mode
    if args.preview:
        digest = find_latest_digest()
        if not digest:
            log("No digest found to preview", "ERROR")
            return 1
        if os.environ.get("IN_DOCKER"):
            log(f"Preview (Docker): {digest.absolute()}")
        else:
            log(f"Opening: {digest.name}")
            subprocess.run(["open", str(digest)])
        return 0

    # Send-only mode
    if args.send_only:
        validate_env(dry_run=False)
        init_db(DB_PATH, MIGRATIONS_DIR)
        digest = find_latest_digest()
        if not digest:
            log("No digest found to send", "ERROR")
            return 1
        log(f"Sending existing digest: {digest.name}")
        save_digest(DB_PATH, digest, log_fn=log)
        recipients = send_broadcast(digest, prepare_for_email, log_fn=log)
        shown_headlines = read_shown_headlines()
        if shown_headlines:
            record_shown_headlines(DB_PATH, shown_headlines, log_fn=log)
        record_run(DB_PATH, 0, articles_emailed=recipients, log_fn=log)
        cleanup_shown_headlines()
        return 0

    # Write-only mode
    if args.write_only:
        validate_env(dry_run=skip_email)
        init_db(DB_PATH, MIGRATIONS_DIR)
        selections = load_selections(CLAUDE_INPUT_DIR / "selections.json", log_fn=log)
        digest = write_digest(selections, TEMPLATE_FILE, log_fn=log)
        replace_placeholders(digest, selections, STYLES_FILE, extract_preheader(selections), log_fn=log)
        if not skip_record:
            save_digest(DB_PATH, digest, log_fn=log)
        recipients = 0
        if not skip_email:
            recipients = send_broadcast(digest, prepare_for_email, log_fn=log)
        if not skip_record:
            shown_headlines = read_shown_headlines()
            if shown_headlines:
                record_shown_headlines(DB_PATH, shown_headlines, log_fn=log)
            record_run(DB_PATH, 0, articles_emailed=recipients, log_fn=log)
        cleanup_shown_headlines()
        return 0

    # Full pipeline
    validate_env(dry_run=skip_email)

    if not check_internet():
        log("No internet connection, skipping")
        return 0

    sources = load_sources(SOURCES_FILE)
    init_db(DB_PATH, MIGRATIONS_DIR)

    # Start run for archival (will be completed at end)
    run_id = start_run(DB_PATH, log_fn=log) if not skip_record else None

    last_run = get_last_run_time(DB_PATH, log_fn=log)
    fetch_result = fetch_feeds(sources, FETCHED_DIR, last_run, log_fn=log)
    record_source_health(DB_PATH, fetch_result.health_records, log_fn=log)

    # Archive fetched articles
    if run_id:
        archive_articles(DB_PATH, run_id, collect_fetched_articles(FETCHED_DIR), log_fn=log)

    # Health alerts
    persistently_failing = get_failing_sources(DB_PATH, min_consecutive=HEALTH_ALERT_THRESHOLD, log_fn=log)
    if persistently_failing:
        failed_this_run = sum(1 for _, success, *_ in fetch_result.health_records if not success)
        send_health_alert(persistently_failing, failed_this_run, len(sources), log_fn=log)

    prepare_claude_input(sources, dry_run=args.dry_run, log_fn=log)

    generate_selections(log_fn=log)
    selections = load_selections(CLAUDE_INPUT_DIR / "selections.json", log_fn=log)

    # Archive selections
    selections_path = CLAUDE_INPUT_DIR / "selections.json"
    if run_id and selections_path.exists():
        archive_selections(DB_PATH, run_id, selections_path.read_text(), log_fn=log)

    if args.select_only:
        log("Select-only mode: stopping after selection")
        return 0

    digest = write_digest(selections, TEMPLATE_FILE, log_fn=log)
    replace_placeholders(digest, selections, STYLES_FILE, extract_preheader(selections), log_fn=log)

    if not skip_record:
        save_digest(DB_PATH, digest, log_fn=log)

    recipients = 0
    if not skip_email:
        recipients = send_broadcast(digest, prepare_for_email, log_fn=log)
    else:
        log(f"Skipping broadcast: {digest.name}")

    if not skip_record:
        shown_headlines = read_shown_headlines()
        if not shown_headlines:
            log("No headlines recorded - Claude may not have generated shown_headlines.json", "WARN")
        record_shown_headlines(DB_PATH, shown_headlines, log_fn=log)
        if run_id:
            complete_run(DB_PATH, run_id, fetch_result.total_kept, articles_emailed=recipients, log_fn=log)
        else:
            record_run(DB_PATH, fetch_result.total_kept, articles_emailed=recipients, log_fn=log)

    cleanup_shown_headlines()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted", "WARN")
        sys.exit(130)
    except Exception as e:
        log(f"{type(e).__name__}: {e}", "ERROR")
        sys.exit(1)
