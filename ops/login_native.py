#!/usr/bin/env python3
"""Human-controlled ChatGPT login in Xpra's private display.

Launch Chrome as a regular visible browser process instead of through
Playwright's automated browser launcher. Browser state and CDP remain local.
This does not automate security verification, solve CAPTCHAs or submit chats.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path("/home/qwqc/services/image-workflow")
PROFILE=ROOT/"data/web-browser-profile"
CHROME=Path("/home/qwqc/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome")

def main():
    if not os.environ.get("DISPLAY"):
        sys.exit("A private Xpra DISPLAY is required")
    if not CHROME.is_file():
        sys.exit("The pinned Chrome binary is missing")
    PROFILE.mkdir(mode=0o700, parents=True, exist_ok=True)
    PROFILE.chmod(0o700)
    # Nonzero debugging port, fixed loopback binding, separate user profile.
    # No Playwright launch/automation flags or fingerprint modification.
    args=[
        str(CHROME),
        f"--user-data-dir={PROFILE}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9334",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--window-size=1280,900",
        "https://chatgpt.com/",
    ]
    print("Launching owner-visible Chrome in Xpra; verification remains manual.",flush=True)
    return subprocess.call(args,stdin=subprocess.DEVNULL)

if __name__=="__main__":
    sys.exit(main())
