#!/usr/bin/env python3
"""Simple email sender for news digest."""

import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

def send_digest(digest_path: str):
    # Config from environment
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_emails = os.environ.get("DIGEST_EMAIL", "")
    recipients = [e.strip() for e in to_emails.split(",") if e.strip()]

    if not all([smtp_user, smtp_pass]) or not recipients:
        print("Missing SMTP_USER, SMTP_PASS, or DIGEST_EMAIL in environment")
        sys.exit(1)

    # Read digest
    with open(digest_path, "r") as f:
        content = f.read()

    # Build email
    date_str = datetime.utcnow().strftime("%B %d, %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"News Digest - {date_str}"
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(content, "plain", "utf-8"))

    # Send
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())

    print(f"Sent to {', '.join(recipients)}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <digest-file.txt>")
        sys.exit(1)
    send_digest(sys.argv[1])
