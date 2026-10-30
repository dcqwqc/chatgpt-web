#!/usr/bin/env python3
"""Conservative BB ChatGPT Web uptime repair.

Never reset credentials, resend prompts, erase browser state, or restart an
active worker. Systemd already owns crash recovery; this heals *stalled*
services only after repeated failures and with bounded restart cooldowns.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path.home() / ".local/state/chatgpt-web/watchdog"
STATE = ROOT / "status.json"
RUNTIME = Path(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
DAEMON_SOCKET = RUNTIME / "chatgpt-web/daemon-v4.sock"
BROWSER = "qwqc-image-browser-session.service"
DAEMON = "chatgpt-web.service"
COOLDOWN = 900
FAILURE_THRESHOLD = 3


def cmd(*argv: str, timeout: int = 12) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def active(unit: str) -> bool:
    result = cmd("systemctl", "--user", "is-active", "--quiet", unit)
    return bool(result and result.returncode == 0)


def browser_ready() -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:9334/json/version", timeout=4) as response:
            version = json.load(response)
            return response.status == 200 and bool(version.get("webSocketDebuggerUrl"))
    except (OSError, ValueError, TimeoutError):
        return False


def daemon_ready() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(3)
            conn.connect(str(DAEMON_SOCKET))
            conn.sendall(b'{"command":"ping"}\n')
            conn.shutdown(socket.SHUT_WR)
            data = conn.recv(16384)
        return bool(json.loads(data.decode().splitlines()[0]).get("ok"))
    except (OSError, ValueError, IndexError, TimeoutError):
        return False


def busy_threads() -> bool | None:
    """Unknown is NOT permission to interrupt possible conversations."""
    result = cmd(str(Path.home() / ".local/bin/bb"), "thread", "list", "--json")
    if result is None or result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            return None
        terminal = {"idle", "error", "done", "completed", "stopped", "failed"}
        return any(isinstance(row, dict) and row.get("status") not in terminal
                   for row in rows)
    except (ValueError, TypeError):
        return None


def busy_image_jobs() -> bool | None:
    """Avoid restarting the shared Chrome service during an image render.

    The image workflow worker stays active even while idle, so service state
    alone is not evidence of an active render. Check its durable job ledger.
    """
    db = Path.home() / "services/image-workflow/data/image-workflow.sqlite3"
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM browser_jobs WHERE state='running'"
            ).fetchone()
        return bool(row and row[0])
    except (OSError, sqlite3.Error):
        return None


def allowed_repair(state: dict, service: str, now: float) -> bool:
    return (state.get(f"{service}_failures", 0) >= FAILURE_THRESHOLD
            and now - state.get(f"{service}_last_restart", 0) >= COOLDOWN)


def run_once() -> dict:
    try:
        state = json.loads(STATE.read_text())
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    now = time.time()
    browser = active(BROWSER) and browser_ready()
    daemon = active(DAEMON) and daemon_ready()
    for name, good in (("browser", browser), ("daemon", daemon)):
        previous = state.get(f"{name}_failures", 0)
        state[f"{name}_failures"] = 0 if good else previous + 1
        if good and previous:
            print(f"BB ChatGPT Web: {name} recovered", flush=True)
        if not good and previous == 0:
            print(f"BB ChatGPT Web: {name} health probe failed", flush=True)

    # No restart before 3 consecutive failed checks (normally >=4 minutes).
    # Never interrupt a BB run or act when the thread API cannot be checked.
    for name, service in (("browser", BROWSER), ("daemon", DAEMON)):
        if not allowed_repair(state, name, now):
            continue
        # If Chrome is down, restarting the daemon is neither useful nor safe.
        # Repair the browser first and let the next checks assess the daemon.
        if name == "daemon" and not browser:
            continue
        busy = busy_threads()
        if busy is not False:
            print(f"BB ChatGPT Web: deferred {name} restart; BB work is active or unknown",
                  flush=True)
            continue
        if name == "browser" and busy_image_jobs() is not False:
            print("BB ChatGPT Web: deferred browser restart; image job active or unknown",
                  flush=True)
            continue
        state[f"{name}_last_restart"] = now
        result = cmd("systemctl", "--user", "restart", service, timeout=45)
        ok = result is not None and result.returncode == 0
        print(f"BB ChatGPT Web: {name} restart {'issued' if ok else 'failed'}",
              flush=True)

    state["checked_at"] = now
    state["browser_healthy"] = browser
    state["daemon_healthy"] = daemon
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE)
    return state


def main() -> int:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    with (ROOT / "lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        state = run_once()
    if "--status" in sys.argv:
        print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
