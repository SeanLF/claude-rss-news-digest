"""Email delivery via Resend.

Handles broadcast sending, test emails, and health alerts.
"""

import logging
import os
import time
from datetime import UTC, datetime
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


def _reply_to_params() -> dict[str, str]:
    """Reply-To params for a Resend send: route replies to the monitored contact
    inbox (CONTACT_EMAIL), not the possibly send-only From. Empty when CONTACT_EMAIL
    is unset, so replies fall back to the From address (the old behaviour)."""
    reply_to = os.environ.get("CONTACT_EMAIL")
    return {"reply_to": reply_to} if reply_to else {}


def get_audience_contact_count(audience_id: str) -> int:
    """Get number of contacts in an audience."""
    try:
        contacts = resend_with_retry(resend.Contacts.list, audience_id=audience_id)
        if not isinstance(contacts, dict) or "data" not in contacts:
            return 0
        return len([c for c in contacts["data"] if not c.get("unsubscribed")])
    except resend.exceptions.ResendError:
        return 0


def send_broadcast(email_html: str, on_created=None) -> BroadcastResult:
    """Create and send a digest broadcast via Resend.

    ``email_html`` is the already-email-ready HTML (the caller renders it via
    render_email/MJML). Returns a BroadcastResult (recipients, broadcast_id, status)
    so the caller can persist the id/status and make a later retry idempotent.
    ``on_created`` (if given) is called with the broadcast id the instant the draft
    is created -- BEFORE the send is attempted -- so a send that fails still leaves
    the id persisted for a resume to recover (the 2026-06-16 gap where the id lived
    only in the logs and a manual retry risked a double-send).
    """
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    audience_id = os.environ["RESEND_AUDIENCE_ID"]

    content = email_html
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
                **_reply_to_params(),
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

    ``html`` MUST already be the rendered MJML email (caller runs
    ``render_email`` first); this function does not re-render it. Returns the
    Resend email id. Raises on API error -- this is a real send, so a failure must
    surface, never be swallowed.
    """
    resend.api_key = os.environ["RESEND_API_KEY"]
    from_email = os.environ["RESEND_FROM"]
    digest_name = os.environ.get("DIGEST_NAME", "News Digest")
    now = datetime.now(UTC)
    date_str = now.strftime("%B %d, %Y")
    # Unique per send: repeated identical-subject QA emails thread in Gmail, which
    # then collapses the (near-identical) body behind "show trimmed content". A time
    # nonce keeps each QA send in its own thread so the full render is always visible.
    nonce = now.strftime("%H:%M:%S")

    try:
        response = resend_with_retry(
            resend.Emails.send,
            {
                "from": f"{digest_name} <{from_email}>",
                "to": [to_addr],
                "subject": f"{subject_prefix}{digest_name} – {date_str} ({nonce})",
                "html": html,
                **_reply_to_params(),
            },
        )
    except resend.exceptions.ResendError as e:
        logger.error("Test digest send to %s failed: %s", to_addr, e)
        raise

    email_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
    logger.info("Test digest sent to %s (id=%s)", to_addr, email_id)
    return email_id


def _send_alert(kind: str, *, subject: str, content: str, dropped: str) -> None:
    """Send one operational alert email; if it can't be delivered, log what it would have said.

    Alerting IS the monitor, so a monitor that can't reach anyone is itself an outage -- and it
    is the one failure no alert can report. Both undeliverable paths (unusable config, Resend
    error) therefore log at ERROR, not WARNING, and repeat ``dropped``: a one-line rendering of
    the alert's payload, so the content survives in the log even when the email never sends.
    Terraform wrote DIGEST_ALERT_EMAIL while this read HEALTH_ALERT_EMAIL, and that mismatch hid
    18 consecutive days of a dead source behind a WARNING nobody greps for.
    """
    to_email = os.environ.get("HEALTH_ALERT_EMAIL")
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("RESEND_FROM")
    if not (to_email and api_key and from_email):
        missing = [
            name
            for name, value in (
                ("HEALTH_ALERT_EMAIL", to_email),
                ("RESEND_API_KEY", api_key),
                ("RESEND_FROM", from_email),
            )
            if not value
        ]
        logger.error(
            "ALERTING MISCONFIGURED (%s unset): %s alert DROPPED, not delivered. It said: %s",
            "/".join(missing),
            kind,
            dropped,
        )
        return

    resend.api_key = api_key
    try:
        resend_with_retry(
            resend.Emails.send,
            {
                "from": f"News Digest Alerts <{from_email}>",
                "to": [to_email],
                "subject": subject,
                "html": content,
            },
        )
    except resend.exceptions.ResendError as e:
        logger.error("%s alert send FAILED (%s); alert DROPPED. It said: %s", kind, e, dropped)
        return
    logger.info("%s alert sent to %s", kind, to_email)


def send_thread_audit_alert(audit_failures: int, run_id: int):
    """Alert when the thread faithfulness audit failed-open this run.

    The audit fails OPEN (keeps facts) so the digest never breaks, but that means unsupported
    facts went UNCHECKED into the thread state. This must surface -- a persistently-failing audit
    is a silent quality regression once threads are reader-visible."""
    _send_alert(
        "thread-audit",
        subject=f"[Alert] Thread audit failed-open on {audit_failures} thread(s)",
        content=f"""<h2>News Digest Thread Audit Alert</h2>
<p>The thread faithfulness audit failed-open on <strong>{audit_failures}</strong> thread(s) in run {run_id}.</p>
<p>Those installments' facts were kept WITHOUT being fact-checked. If this recurs, unsupported
facts are reaching thread state unchecked -- investigate the audit model/endpoint.</p>
<p style="color: #777; font-size: 0.85em;">This is an automated alert from your News Digest system.</p>
""",
        dropped=f"thread faithfulness audit failed-open on {audit_failures} thread(s) in run {run_id}",
    )


def send_archival_alert(failed_steps: list[str], run_id: int | None):
    """Alert when trace/analytics archival failed on a run.

    Archival is fail-soft (a trace write must never block a delivered digest), so
    a failure here does NOT stop the send -- but a persistent one silently rots
    the eval/reproducibility trace. This surfaces it so it doesn't stay silent."""
    steps = ", ".join(failed_steps)
    _send_alert(
        "archival",
        subject=f"[Alert] Digest archival failed ({steps})",
        content=f"""<h2>News Digest Archival Alert</h2>
<p>Trace/analytics archival failed for <strong>{steps}</strong> on run {run_id}.</p>
<p>The digest still delivered (archival is fail-soft), but this run's reproducibility
trace is incomplete. If this recurs, the eval golden set is silently rotting -- check
the DB volume (disk/permissions/locks).</p>
<p style="color: #777; font-size: 0.85em;">This is an automated alert from your News Digest system.</p>
""",
        dropped=f"archival failed for {steps} on run {run_id}",
    )


def send_health_alert(
    failing_sources: list[tuple[str, int]],
    failed_this_run: int,
    total_sources: int,
):
    """Send alert email when sources are persistently failing."""
    source_list = "\n".join(f"  • {sid}: {count} consecutive failures" for sid, count in failing_sources)
    failing_summary = ", ".join(f"{sid} ({n}x)" for sid, n in failing_sources)
    _send_alert(
        "source-health",
        subject=f"[Alert] {len(failing_sources)} RSS sources failing",
        content=f"""<h2>News Digest Source Health Alert</h2>
<p><strong>{failed_this_run}/{total_sources}</strong> sources failed this run.</p>
<p>The following sources have failed 3+ times in a row:</p>
<pre>{source_list}</pre>
<p>Consider checking these feeds or removing them from sources.json.</p>
<p style="color: #777; font-size: 0.85em;">This is an automated alert from your News Digest system.</p>
""",
        dropped=f"{failed_this_run}/{total_sources} sources failed this run; persistently failing: {failing_summary}",
    )
