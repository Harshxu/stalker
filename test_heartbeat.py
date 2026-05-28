# -*- coding: utf-8 -*-
"""
STALKER - HEARTBEAT TEST MAILER
WARNING: TEMPORARY - REMOVE AFTER TESTING

Uses Brevo (formerly Sendinblue) REST API over HTTPS port 443.
Why Brevo: Render free tier blocks SMTP (port 587/465) and
           Formsubmit is behind Cloudflare (returns 403 from server IPs).
           Brevo REST API works because it's plain HTTPS port 443.

Setup (3 minutes):
  1. Go to https://app.brevo.com  ->  Sign up free (no credit card)
  2. Settings -> Senders & IPs -> Add your email as a sender & verify it
  3. Settings -> API Keys -> Create API Key
  4. In Render Environment Variables add:
       BREVO_API_KEY = <your-api-key>
  5. Redeploy -> done
"""

import os
import logging
import requests
from datetime import datetime

# ─── Load .env ─────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ─── Config ────────────────────────────────────────────────
BREVO_API_KEY          = os.getenv("BREVO_API_KEY", "")
MAIL_TO                = os.getenv("FORMSUBMIT_TO", "")
MAIL_FROM              = os.getenv("FORMSUBMIT_TO", "")    # must be verified in Brevo
HEARTBEAT_INTERVAL_MINUTES = 10    # every 10 minutes

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"

# ─── Logging ───────────────────────────────────────────────
logger = logging.getLogger("heartbeat")

# ─── Counter ───────────────────────────────────────────────
_heartbeat_count = 0


def send_heartbeat_email():
    """Send heartbeat email via Brevo REST API (HTTPS port 443 — works on Render free tier)."""
    global _heartbeat_count

    if not BREVO_API_KEY:
        logger.error("[HEARTBEAT] BREVO_API_KEY not set. Add it to Render -> Environment Variables.")
        return

    if not MAIL_TO:
        logger.error("[HEARTBEAT] FORMSUBMIT_TO (recipient email) not set in env vars.")
        return

    _heartbeat_count += 1
    now_str     = datetime.now().strftime("%d %b %Y - %I:%M:%S %p")
    server_name = os.getenv("RENDER_EXTERNAL_URL", os.getenv("RENDER_SERVICE_NAME", "Render Server"))

    subject = f"YES I'M ACTIVE - Stalker Heartbeat #{_heartbeat_count}"

    html_body = f"""
<html><body style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:24px;background:#f9fafb">
  <div style="background:#fff;border-radius:12px;padding:24px;box-shadow:0 1px 4px rgba(0,0,0,.08)">
    <h2 style="color:#16a34a;margin-top:0">YES I'M ACTIVE</h2>
    <p style="color:#166534;font-size:16px;margin-bottom:20px">
      Stalker automation is running correctly on the server.
    </p>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr style="background:#f0fdf4">
        <td style="padding:10px 12px;font-weight:bold;width:40%">Status</td>
        <td style="padding:10px 12px;color:#16a34a;font-weight:bold">Active</td>
      </tr>
      <tr>
        <td style="padding:10px 12px;font-weight:bold">Heartbeat #</td>
        <td style="padding:10px 12px">{_heartbeat_count}</td>
      </tr>
      <tr style="background:#f0fdf4">
        <td style="padding:10px 12px;font-weight:bold">Timestamp</td>
        <td style="padding:10px 12px">{now_str}</td>
      </tr>
      <tr>
        <td style="padding:10px 12px;font-weight:bold">Interval</td>
        <td style="padding:10px 12px">Every {HEARTBEAT_INTERVAL_MINUTES} minutes</td>
      </tr>
      <tr style="background:#f0fdf4">
        <td style="padding:10px 12px;font-weight:bold">Server</td>
        <td style="padding:10px 12px;font-size:12px">{server_name}</td>
      </tr>
    </table>
    <p style="margin-top:20px;font-size:12px;color:#9ca3af">
      This is a temporary test email and will be removed after testing.
    </p>
  </div>
</body></html>
"""

    payload = {
        "sender": {"name": "STALKER Heartbeat", "email": MAIL_FROM},
        "to":     [{"email": MAIL_TO}],
        "subject": subject,
        "htmlContent": html_body,
    }

    try:
        resp = requests.post(
            BREVO_ENDPOINT,
            headers={
                "accept":       "application/json",
                "api-key":      BREVO_API_KEY,
                "content-type": "application/json",
            },
            json=payload,
            timeout=15,
        )

        if resp.status_code in (200, 201):
            logger.info(f"[HEARTBEAT] #{_heartbeat_count} sent via Brevo to {MAIL_TO}")
        else:
            logger.error(f"[HEARTBEAT] Brevo {resp.status_code}: {resp.text[:300]}")

    except Exception as e:
        logger.error(f"[HEARTBEAT] Brevo request failed: {e}")
