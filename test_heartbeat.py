# -*- coding: utf-8 -*-
"""
STALKER - HEARTBEAT TEST MAILER
WARNING: TEMPORARY - REMOVE AFTER TESTING

Sends "Yes I'm active" email every 10 minutes via Gmail SMTP.
Uses Gmail SMTP (not Formsubmit) because Formsubmit is behind Cloudflare
which blocks all cloud server IPs (Render, AWS, etc.) with 403.

Required in .env or Render Environment Variables:
    GMAIL_USER=harshkumawat9950@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   (16-char Google App Password)
    FORMSUBMIT_TO=harshkumawat9950@gmail.com (used as recipient)

How to get Gmail App Password:
    1. Go to myaccount.google.com/security
    2. Enable 2-Step Verification (if not already)
    3. Search "App Passwords" -> Create one for Mail
    4. Copy the 16-character password into GMAIL_APP_PASSWORD
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ─── Load .env ─────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ─── Config ────────────────────────────────────────────────
GMAIL_USER          = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD  = os.getenv("GMAIL_APP_PASSWORD", "")
MAIL_TO             = os.getenv("FORMSUBMIT_TO", GMAIL_USER)
HEARTBEAT_INTERVAL_MINUTES = 10    # every 10 minutes

# ─── Logging ───────────────────────────────────────────────
logger = logging.getLogger("heartbeat")

# ─── Counter ───────────────────────────────────────────────
_heartbeat_count = 0


def send_heartbeat_email():
    """Send a single heartbeat email via Gmail SMTP."""
    global _heartbeat_count

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.error("[HEARTBEAT] GMAIL_USER or GMAIL_APP_PASSWORD not set. Add them to Render Environment Variables.")
        return

    _heartbeat_count += 1
    now_str = datetime.now().strftime("%d %b %Y - %I:%M:%S %p")
    server_name = os.getenv("RENDER_SERVICE_NAME", os.getenv("RENDER_EXTERNAL_URL", "local"))

    # Build email
    msg = MIMEMultipart("alternative")
    msg["From"]    = GMAIL_USER
    msg["To"]      = MAIL_TO
    msg["Subject"] = f"YES I'M ACTIVE - Stalker Heartbeat #{_heartbeat_count}"

    body_text = f"""
STALKER HEARTBEAT TEST
======================

Status   : YES I'M ACTIVE - Automation is working correctly!
Heartbeat: #{_heartbeat_count}
Timestamp: {now_str}
Interval : Every {HEARTBEAT_INTERVAL_MINUTES} minutes
Server   : {server_name}

WARNING: This is a TEMPORARY test email. Will be removed after testing.
"""

    body_html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:500px;margin:auto;padding:20px">
<h2 style="color:#16a34a">YES I'M ACTIVE</h2>
<p style="color:#15803d;font-size:18px">Stalker automation is working correctly!</p>
<table style="border-collapse:collapse;width:100%">
  <tr><td style="padding:8px;background:#f0fdf4;font-weight:bold">Status</td>
      <td style="padding:8px;background:#f0fdf4;color:#16a34a">Active</td></tr>
  <tr><td style="padding:8px;font-weight:bold">Heartbeat #</td>
      <td style="padding:8px">{_heartbeat_count}</td></tr>
  <tr><td style="padding:8px;background:#f0fdf4;font-weight:bold">Timestamp</td>
      <td style="padding:8px;background:#f0fdf4">{now_str}</td></tr>
  <tr><td style="padding:8px;font-weight:bold">Interval</td>
      <td style="padding:8px">Every {HEARTBEAT_INTERVAL_MINUTES} minutes</td></tr>
  <tr><td style="padding:8px;background:#f0fdf4;font-weight:bold">Server</td>
      <td style="padding:8px;background:#f0fdf4">{server_name}</td></tr>
</table>
<p style="color:#dc2626;font-size:12px;margin-top:20px">
  TEMPORARY TEST EMAIL - Remove after testing is done.
</p>
</body></html>
"""

    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info(f"[HEARTBEAT] #{_heartbeat_count} sent via Gmail SMTP to {MAIL_TO}")
    except smtplib.SMTPAuthenticationError:
        logger.error("[HEARTBEAT] Gmail auth failed. Check GMAIL_USER and GMAIL_APP_PASSWORD in env vars.")
    except Exception as e:
        logger.error(f"[HEARTBEAT] Gmail SMTP error: {e}")
