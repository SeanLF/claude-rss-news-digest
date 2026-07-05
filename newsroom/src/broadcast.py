"""Email delivery via Resend.

Handles broadcast sending, test emails, and health alerts.
"""

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import resend

logger = logging.getLogger(__name__)


class BroadcastResult(NamedTuple):
    """Outcome of a broadcast send: recipients plus the Resend id/status so the
    caller can persist them for idempotent retries."""

    recipients: int
    broadcast_id: str | None
    status: str | None


def resend_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """Call Resend API with exponential backoff on rate limit errors."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except resend.exceptions.RateLimitError:
            if attempt < max_retries - 1:
                delay = 2**attempt
                logger.info("Rate limited, retrying in %ds...", delay)
                time.sleep(delay)
            else:
                raise


# A broadcast in any of these states has been accepted by Resend for delivery,
# so a send call that raised (e.g. a read timeout on the HTTP response) actually
# succeeded server-side and must NOT be treated as a failure. "sent" is only safe
# here because send_broadcast always creates a fresh draft before sending, so a
# broadcast can never already be "sent" when its first (and only) send is made --
# a future resend/retry path must not rely on this set without revisiting that.
ACCEPTED_BROADCAST_STATES = {"queued", "sending", "sent"}


def probe_status(broadcast_id: str) -> str | None:
    """Best-effort fetch of a broadcast's status; None if it can't be read.

    Catches everything: callers use this to decide whether a send was actually
    accepted. A probe failure must degrade to None (the caller then fails loud /
    does not assume delivery), never mask the real outcome with its own error.
    """
    try:
        broadcast = resend.Broadcasts.get(broadcast_id)
    except Exception as e:
        logger.warning("Could not read status for broadcast %s: %s", broadcast_id, e)
        return None
    if isinstance(broadcast, dict):
        return broadcast.get("status")
    return getattr(broadcast, "status", None)


def resend_existing(broadcast_id: str) -> str:
    """Send an already-created broadcast; return its delivery status.

    Never creates a new broadcast -- used both for the first send and to recover
    a prior attempt's draft on resume, so a retry can't duplicate. On a send
    response failure, verify the broadcast was accepted server-side (an accepted
    status means the send landed despite the slow/failed response) and return
    that; otherwise re-raise the original error. See incident 2026-06-16.
    """
    resend.api_key = os.environ["RESEND_API_KEY"]
    try:
        resend_with_retry(resend.Broadcasts.send, {"broadcast_id": broadcast_id})
        return "sent"
    except resend.exceptions.ResendError as e:
        status = probe_status(broadcast_id)
        if status not in ACCEPTED_BROADCAST_STATES:
            raise
        logger.warning(
            "Broadcasts.send raised (%s) but broadcast %s is %r; treating as delivered",
            e,
            broadcast_id,
            status,
        )
        return status


def get_audience_contact_count(audience_id: str) -> int:
    """Get number of contacts in an audience."""
    try:
        contacts = resend_with_retry(resend.Contacts.list, audience_id=audience_id)
        if not isinstance(contacts, dict) or "data" not in contacts:
            return 0
        return len([c for c in contacts["data"] if not c.get("unsubscribed")])
    except resend.exceptions.ResendError:
        return 0


def send_broadcast(digest_path: Path, prepare_for_email_fn, on_created=None) -> BroadcastResult:
    """Create and send a digest broadcast via Resend.

    Returns a BroadcastResult (recipients, broadcast_id, status) so the caller can
    persist the id/status and make a later retry idempotent. ``on_created`` (if
    given) is called with the broadcast id the instant the draft is created --
    BEFORE the send is attempted -- so a send that fails still leaves the id
    persisted for a resume to recover (the 2026-06-16 gap where the id lived only
    in the logs and a manual retry risked a double-send).
    """
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    audience_id = os.environ["RESEND_AUDIENCE_ID"]

    content = digest_path.read_text()
    content = prepare_for_email_fn(content)
    date_str = datetime.now(UTC).strftime("%B %d, %Y")

    try:
        contact_count = get_audience_contact_count(audience_id)

        broadcast = resend_with_retry(
            resend.Broadcasts.create,
            {
                "from": f"{digest_name} <{from_email}>",
                "audience_id": audience_id,
                "subject": f"{digest_name} – {date_str}",
                "html": content,
                "name": f"Digest {date_str}",
            },
        )
        broadcast_id = broadcast["id"]
        logger.info("Created broadcast: %s", broadcast_id)
        if on_created is not None:
            on_created(broadcast_id)

        delivery_status = resend_existing(broadcast_id)
        logger.info("Sent broadcast to %d contacts in audience %s", contact_count, audience_id)

        return BroadcastResult(contact_count, broadcast_id, delivery_status)
    except resend.exceptions.ResendError as e:
        logger.error("Broadcast error: %s", e)
        raise


def send_test_email(to_email: str) -> int:
    """Send a test email to verify Resend config. Returns exit code."""
    for var in ["RESEND_API_KEY", "RESEND_FROM"]:
        if not os.environ.get(var):
            logger.error("Missing %s", var)
            return 1

    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")

    try:
        resend_with_retry(
            resend.Emails.send,
            {
                "from": f"{digest_name} <{from_email}>",
                "to": [to_email],
                "subject": f"{digest_name} - Test Email",
                "html": "<p>This is a test email from News Digest.</p><p>If you received this, your Resend config is working.</p>",
            },
        )
        logger.info("Test email sent to %s", to_email)
        return 0
    except resend.exceptions.ResendError as e:
        logger.error("Resend error: %s", e)
        return 1


def send_test_digest(html: str, to_addr: str, *, subject_prefix: str = "[TEST] ") -> str | None:
    """Send ONE already-email-prepared digest to a single address for email-client QA.

    Uses Resend's single-recipient ``Emails.send`` -- deliberately NOT the
    Broadcasts + audience flow -- so it can target an arbitrary address without
    ever touching the production audience. Reuses ``send_broadcast``'s from/subject
    conventions, prefixed (default "[TEST] ") so the QA send is unmistakable.

    ``html`` MUST already be the email-prepared HTML (caller runs
    ``prepare_for_email`` first); this function does not re-prepare it. Returns the
    Resend email id. Raises on API error -- this is a real send, so a failure must
    surface, never be swallowed.
    """
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    date_str = datetime.now(UTC).strftime("%B %d, %Y")

    try:
        response = resend_with_retry(
            resend.Emails.send,
            {
                "from": f"{digest_name} <{from_email}>",
                "to": [to_addr],
                "subject": f"{subject_prefix}{digest_name} – {date_str}",
                "html": html,
            },
        )
    except resend.exceptions.ResendError as e:
        logger.error("Test digest send to %s failed: %s", to_addr, e)
        raise

    email_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
    logger.info("Test digest sent to %s (id=%s)", to_addr, email_id)
    return email_id


def send_thread_audit_alert(audit_failures: int, run_id: int):
    """Alert when the thread faithfulness audit failed-open this run.

    The audit fails OPEN (keeps facts) so the digest never breaks, but that means unsupported
    facts went UNCHECKED into the thread state. This must surface -- a persistently-failing audit
    is a silent quality regression once threads are reader-visible."""
    to_email = os.environ.get("HEALTH_ALERT_EMAIL")
    from_email = os.environ.get("RESEND_FROM")
    if not to_email or not os.environ.get("RESEND_API_KEY") or not from_email:
        logger.warning("Skipping thread-audit alert: HEALTH_ALERT_EMAIL/RESEND_API_KEY/RESEND_FROM not set")
        return

    resend.api_key = os.environ["RESEND_API_KEY"]
    content = f"""<h2>News Digest Thread Audit Alert</h2>
<p>The thread faithfulness audit failed-open on <strong>{audit_failures}</strong> thread(s) in run {run_id}.</p>
<p>Those installments' facts were kept WITHOUT being fact-checked. If this recurs, unsupported
facts are reaching thread state unchecked -- investigate the audit model/endpoint.</p>
<p style="color: #777; font-size: 0.85em;">This is an automated alert from your News Digest system.</p>
"""
    try:
        resend_with_retry(
            resend.Emails.send,
            {
                "from": f"News Digest Alerts <{from_email}>",
                "to": [to_email],
                "subject": f"[Alert] Thread audit failed-open on {audit_failures} thread(s)",
                "html": content,
            },
        )
        logger.info("Thread-audit alert sent to %s", to_email)
    except resend.exceptions.ResendError as e:
        logger.error("Failed to send thread-audit alert: %s", e)


def send_health_alert(
    failing_sources: list[tuple[str, int]],
    failed_this_run: int,
    total_sources: int,
):
    """Send alert email when sources are persistently failing."""
    to_email = os.environ.get("HEALTH_ALERT_EMAIL")
    if not to_email:
        logger.warning("Skipping health alert: HEALTH_ALERT_EMAIL not set")
        return
    if not os.environ.get("RESEND_API_KEY"):
        logger.warning("Skipping health alert: RESEND_API_KEY not set")
        return

    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]

    source_list = "\n".join(f"  • {sid}: {count} consecutive failures" for sid, count in failing_sources)
    content = f"""<h2>News Digest Source Health Alert</h2>
<p><strong>{failed_this_run}/{total_sources}</strong> sources failed this run.</p>
<p>The following sources have failed 3+ times in a row:</p>
<pre>{source_list}</pre>
<p>Consider checking these feeds or removing them from sources.json.</p>
<p style="color: #777; font-size: 0.85em;">This is an automated alert from your News Digest system.</p>
"""

    try:
        resend_with_retry(
            resend.Emails.send,
            {
                "from": f"News Digest Alerts <{from_email}>",
                "to": [to_email],
                "subject": f"[Alert] {len(failing_sources)} RSS sources failing",
                "html": content,
            },
        )
        logger.info("Health alert sent to %s", to_email)
    except resend.exceptions.ResendError as e:
        logger.error("Failed to send health alert: %s", e)
