# -*- coding: utf-8 -*-
"""
Quick test: send a real admin-only alert email to Harshkumawat9950@gmail.com
to verify Brevo delivery is working.
Run as: python test_admin_alert.py
"""
import main

# Disable TEST_MODE so the real email goes out
main.IS_TEST_MODE = False

print("Sending admin-only diagnostic alert to Harshkumawat9950@gmail.com ...")
main._send_admin_alert(
    "Diagnostic Check - System Is Online",
    "This is a test of the admin-only alert system. "
    "Root cause of the May 30 blank email has been identified and fixed. "
    "Scheduler was running in --mode serve (no scheduler). "
    "Fix: restart with python main.py --mode run"
)
print("Done. Check your inbox at Harshkumawat9950@gmail.com")
