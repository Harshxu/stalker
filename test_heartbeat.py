# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════╗
║  STALKER — HEARTBEAT TEST MAILER                        ║
║  ⚠️  TEMPORARY — REMOVE AFTER TESTING ⚠️               ║
║                                                          ║
║  Purpose : Sends a "Yes, I'm active" test email every   ║
║            15 minutes so we can confirm:                 ║
║              1. Render / UptimeRobot keeps the app live  ║
║              2. Formsubmit email delivery is working     ║
║                                                          ║
║  Run     : python test_heartbeat.py                      ║
║  Remove  : Delete this file once testing is done        ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime

# ─── Load .env (same pattern as rest of project) ──────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ─── Config (read directly — NO import of config.py) ──────
FORMSUBMIT_TO = os.getenv("FORMSUBMIT_TO", "")
FORMSUBMIT_ENDPOINT = f"https://formsubmit.co/ajax/{FORMSUBMIT_TO}" if FORMSUBMIT_TO else ""
HEARTBEAT_INTERVAL_MINUTES = 15          # ← change here if needed

# ─── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [HEARTBEAT] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("heartbeat")

# ──────────────────────────────────────────────────────────
#  HEARTBEAT COUNTER  (increments each time mail is sent)
# ──────────────────────────────────────────────────────────
_heartbeat_count = 0


def send_heartbeat_email():
    """Send a single 'Yes, I'm active' test email via Formsubmit."""
    global _heartbeat_count

    if not FORMSUBMIT_ENDPOINT:
        logger.error("FORMSUBMIT_TO is not set in .env — cannot send heartbeat email.")
        return

    _heartbeat_count += 1
    now = datetime.now()
    timestamp_str = now.strftime("%d %b %Y — %I:%M:%S %p")

    payload = {
        "name": "STALKER Heartbeat",
        "_subject": f"✅ STALKER is ACTIVE — Heartbeat #{_heartbeat_count}",
        "_template": "table",
        "_captcha": "false",
        "_autoresponse": "false",
        # ── Mail body fields ──
        "Status":       "✅ Yes, I'm ACTIVE — Automation is working correctly!",
        "Heartbeat #":  str(_heartbeat_count),
        "Timestamp":    timestamp_str,
        "Interval":     f"Every {HEARTBEAT_INTERVAL_MINUTES} minutes",
        "Server":       os.getenv("RENDER_SERVICE_NAME", "local / render"),
        "Note":         "⚠️ This is a TEMPORARY test email. Will be removed after testing.",
    }

    try:
        response = requests.post(
            FORMSUBMIT_ENDPOINT,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept":       "application/json",
                "Referer":      "http://localhost:8000/",
            },
            timeout=15,
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success") in ("true", True):
                logger.info(f"Heartbeat #{_heartbeat_count} email sent ✅ → {FORMSUBMIT_TO}")
            else:
                logger.warning(f"Formsubmit responded but success=false: {result}")
                logger.warning("If this is your FIRST email, check inbox for Formsubmit activation link and click it!")
        else:
            logger.error(f"Formsubmit HTTP {response.status_code}: {response.text[:300]}")

    except requests.exceptions.Timeout:
        logger.error("Heartbeat email timed out (15 s) — server may be slow")
    except Exception as e:
        logger.error(f"Heartbeat email failed: {e}")


def main():
    print()
    print("=" * 60)
    print("  🫀  STALKER HEARTBEAT TEST MAILER")
    print("  ⚠️   TEMPORARY — remove after testing")
    print("=" * 60)
    print(f"  Email : {FORMSUBMIT_TO or '⛔ NOT SET — check .env'}")
    print(f"  Fires : every {HEARTBEAT_INTERVAL_MINUTES} minutes")
    print(f"  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    if not FORMSUBMIT_TO:
        print("❌ ERROR: FORMSUBMIT_TO is not set in your .env file.")
        print("   Add this line:  FORMSUBMIT_TO=your@email.com")
        sys.exit(1)

    # Send first heartbeat immediately so we don't wait 15 min to verify
    logger.info("Sending FIRST heartbeat immediately...")
    send_heartbeat_email()

    interval_seconds = HEARTBEAT_INTERVAL_MINUTES * 60

    while True:
        next_fire = datetime.now().strftime("%I:%M:%S %p")
        logger.info(f"Sleeping {HEARTBEAT_INTERVAL_MINUTES} min — next email at {next_fire} + {HEARTBEAT_INTERVAL_MINUTES}m")
        time.sleep(interval_seconds)
        send_heartbeat_email()


if __name__ == "__main__":
    main()
