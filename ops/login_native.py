#!/usr/bin/env python3
"""Launch a visible Chromium browser for interactive ChatGPT authentication."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def chrome_executable() -> str:
    supplied = os.environ.get("CHATGPT_WEB_BROWSER_EXECUTABLE", "").strip()
    if supplied:
        path = Path(supplied).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("Configured browser executable is missing or not executable")
        return str(path)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        installed = shutil.which(name)
        if installed:
            return installed
    raise RuntimeError("No Chrome/Chromium executable found; set CHATGPT_WEB_BROWSER_EXECUTABLE")


def browser_profile() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "chatgpt-web" / "browser-profile"


def main() -> int:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("Interactive login requires a visible desktop session")
    port = int(os.environ.get("CHATGPT_WEB_BROWSER_DEBUG_PORT", "9334"))
    if not 1024 <= port <= 65535:
        raise ValueError("Browser debug port must be in range 1024-65535")
    browser = chrome_executable()
    profile = browser_profile()
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile.chmod(0o700)
    args = [
        browser, f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}", "--no-first-run",
        "--no-default-browser-check", "--window-size=1280,900",
        "https://chatgpt.com/",
    ]
    print("Launching a visible browser; complete authentication manually.", flush=True)
    return subprocess.call(args, stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(main())
