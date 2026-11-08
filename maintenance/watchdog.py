#!/usr/bin/env python3
"""Conservative BB ChatGPT Web uptime repair.

Never reset credentials, resend prompts, erase browser state, or restart an
active worker. Systemd already owns crash recovery; this heals *stalled*
services only after repeated failures and with bounded restart cooldowns.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

if __package__:
    from .stream_guard import shared_daemon_busy
else:
    # Also load correctly from a direct systemd ExecStart or importlib tests.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from stream_guard import shared_daemon_busy

ROOT = Path.home() / ".local/state/chatgpt-web/watchdog"
STATE = ROOT / "status.json"
RUNTIME = Path(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
DAEMON_SOCKET = RUNTIME / "chatgpt-web/daemon-v4.sock"
BROWSER = os.environ.get("CHATGPT_WEB_BROWSER_UNIT", "")
DAEMON = "chatgpt-web.service"
COOLDOWN = 900
FAILURE_THRESHOLD = 3
COMPOSER_CHECK_INTERVAL = 600
UPGRADE_COOLDOWN = 180


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
        # The daemon may be an already-running, owner-authenticated CLI
        # process outside systemd's service cgroup. Systemd's duplicate
        # invocation exits 0; that does NOT mean the daemon is down.
        st = DAEMON_SOCKET.lstat()
        if (not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & 0o777 != 0o600):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(3)
            conn.connect(str(DAEMON_SOCKET))
            conn.sendall(b'{"command":"ping"}\n')
            conn.shutdown(socket.SHUT_WR)
            data = conn.recv(16384)
        return bool(json.loads(data.decode().splitlines()[0]).get("ok"))
    except (OSError, ValueError, IndexError, TimeoutError):
        return False


def committed_daemon_source() -> bool:
    """Allow code rollout only when the installed daemon source is committed.

    Workers may be writing the file while a separate watchdog timer fires.
    A half-written or uncommitted source must never become an active process.
    """
    repo = Path(__file__).resolve().parents[1]
    try:
        probe = subprocess.run(
            ["git", "-C", str(repo), "diff", "--quiet", "HEAD", "--",
             "chatgpt-web", "maintenance/watchdog.py"],
            capture_output=True, timeout=8, check=False,
        )
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def daemon_build_current() -> bool | None:
    """Compare the live daemon's boot-time code fingerprint to installed code.

    Old daemons answer ping without a build field, which proves an update is
    needed. Inaccessible/malformed replies are UNKNOWN, not restart permission.
    """
    try:
        expected = hashlib.sha256(
            (Path(__file__).resolve().parents[1] / "chatgpt-web").read_bytes()
        ).hexdigest()[:16]
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(3)
            conn.connect(str(DAEMON_SOCKET))
            conn.sendall(b'{"command":"ping"}\n')
            conn.shutdown(socket.SHUT_WR)
            reply = json.loads(conn.recv(16384).decode().splitlines()[0])
        if not reply.get("ok") or not reply.get("result", {}).get("ready"):
            return None
        return reply.get("result", {}).get("build") == expected
    except (OSError, ValueError, IndexError, TypeError, TimeoutError):
        return None


def composer_ready() -> tuple[bool, str]:
    """Check the authenticated website, without inserting or sending a prompt.

    A working debugger port is not proof that the account/composer is usable.
    A failed website check is diagnostic only; restarting cannot fix login or
    a verification challenge and must never become a restart loop.
    """
    recipe = Path(__file__).resolve().parents[1] / "chrome-recipe.js"
    environment = dict(os.environ)
    environment["CHATGPT_WEB_CDP_URL"] = "http://127.0.0.1:9334"
    environment["CHATGPT_WEB_RECIPE_TIMEOUT_MS"] = "30000"
    try:
        result = subprocess.run(
            [environment.get("CHATGPT_WEB_NODE_BINARY", "/usr/bin/node"), str(recipe)],
            input=json.dumps({"prompt":"health check", "check":True}),
            text=True, capture_output=True, timeout=40, check=False, env=environment,
        )
        if result.returncode:
            # The helper's error contains reasons only, never a draft or cookies.
            return False, result.stderr.strip()[:400] or "composer check failed"
        state = json.loads(result.stdout)
        if not isinstance(state, dict) or state.get("composer_ready") is not True:
            return False, "composer check returned no readiness confirmation"
        return True, ""
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, "composer check unavailable or timed out"


def busy_threads() -> bool | None:
    """Unknown is NOT permission to interrupt possible conversations."""
    # Hidden BB mission workers also use the same ChatGPT daemon. Include
    # them, and only count THIS provider, not unrelated active Codex/Claude
    # workers that would otherwise prevent legitimate daemon recovery.
    result = cmd(str(Path.home() / ".local/bin/bb"),
                 "thread", "list", "--json", "--include-hidden")
    if result is None or result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            return None
        terminal = {"idle", "error", "done", "completed", "stopped", "failed"}
        for row in rows:
            if not isinstance(row, dict):
                return None
            if row.get("status") not in terminal and not row.get("providerId"):
                return None  # An unidentified active thread may be ours.
        return any(row.get("providerId") == "acp-chatgpt-web"
                   and row.get("status") not in terminal for row in rows)
    except (ValueError, TypeError):
        return None


def busy_image_jobs() -> bool | None:
    """Avoid restarting the shared Chrome service during an image render.

    The image workflow worker stays active even while idle, so service state
    alone is not evidence of an active render. Check its durable job ledger.
    """
    configured = os.environ.get("CHATGPT_WEB_IMAGE_JOBS_DB", "")
    if not configured:
        return None
    db = Path(configured).expanduser()
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
    browser = (active(BROWSER) if BROWSER else True) and browser_ready()
    # Real socket challenge-response is the source of truth, not
    # systemd.is-active: a duplicate CLI invocation exits cleanly while
    # the existing owner daemon continues to serve ACP sessions.
    daemon = daemon_ready()
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
        if not service or not allowed_repair(state, name, now):
            continue
        # If Chrome is down, restarting the daemon is neither useful nor safe.
        # Repair the browser first and let the next checks assess the daemon.
        if name == "daemon" and not browser:
            continue
        busy = busy_threads()
        # A terminated BB turn may still have an accepted ChatGPT request
        # generating on the website. Check all active CLI new/send processes
        # and durable accepted-but-unreconciled sessions as well.
        if busy is not False or shared_daemon_busy(Path.home()) is not False:
            print(f"BB ChatGPT Web: deferred {name} restart; "
                  "ChatGPT work is active or recovery is pending",
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

    # Source releases do not automatically reload a long-lived Python
    # daemon. Deploy only when the *global* BB/CLI/pending-source guards agree
    # idle; never send a prompt, restart Chrome, or touch active workers.
    build_current = daemon_build_current() if daemon else None
    state["daemon_build_current"] = build_current
    if (daemon and browser and build_current is False
            and now - state.get("daemon_upgrade_last_restart", 0)
            >= UPGRADE_COOLDOWN):
        if not committed_daemon_source():
            print("BB ChatGPT Web: deferred code upgrade; source is not a "
                  "clean committed revision", flush=True)
        elif (busy_threads() is False
                and shared_daemon_busy(Path.home()) is False):
            state["daemon_upgrade_last_restart"] = now
            result = cmd("systemctl", "--user", "restart", DAEMON, timeout=45)
            if result is not None and result.returncode == 0:
                print("BB ChatGPT Web: upgraded idle daemon to installed build",
                      flush=True)
            else:
                print("BB ChatGPT Web: idle daemon upgrade failed; cooldown active",
                      flush=True)
        else:
            print("BB ChatGPT Web: deferred code upgrade; work/recovery active",
                  flush=True)

    state["checked_at"] = now
    state["browser_healthy"] = browser
    state["daemon_healthy"] = daemon
    if (browser and daemon
            and now - state.get("composer_checked_at", 0) >= COMPOSER_CHECK_INTERVAL
            and busy_threads() is False
            and shared_daemon_busy(Path.home()) is False):
        ready, detail = composer_ready()
        state["composer_checked_at"] = now
        state["composer_healthy"] = ready
        state["composer_error"] = detail
        if not ready:
            print(f"BB ChatGPT Web: website/composer check failed: {detail}", flush=True)
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
