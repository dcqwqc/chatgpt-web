"""Global idle guard for the shared ChatGPT Web browser/daemon.

Every ACP thread and CLI new/send request shares this daemon. A scoped idle
check for one old BB turn is NOT evidence that a restart is safe.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import sqlite3
import subprocess


def busy_chatgpt_threads(db: Path) -> bool | None:
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
            rows = conn.execute(
                "SELECT status FROM threads WHERE provider_id='acp-chatgpt-web'"
            )
            terminal = {"idle", "error", "done", "completed", "stopped", "failed"}
            return any(status not in terminal for (status,) in rows)
    except (OSError, sqlite3.Error):
        return None


def busy_cli_processes() -> bool | None:
    try:
        result = subprocess.run(
            ["ps", "-eo", "args="], capture_output=True,
            text=True, timeout=8, check=False)
        if result.returncode:
            return None
        # Include new conversations: the previous guard only checked "send".
        return any(
            "chatgpt-web" in line and
            re.search(r"(?:^|\s)(?:new|send)(?:\s|$)", line)
            and not "maintenance/stream_guard.py" in line
            for line in result.stdout.splitlines()
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def unresolved_accepted_turns(folder: Path) -> bool | None:
    try:
        if not folder.is_dir():
            return None
        for path in folder.glob("*.json"):
            # One session is tiny; reject surprising massive/corrupt records
            # rather than treating unknown work as permission to restart.
            if path.stat().st_size > 65536:
                return None
            with path.open(encoding="utf-8") as stream:
                session = json.load(stream)
            if not isinstance(session, dict):
                return None
            pending = session.get("pending_turn")
            if isinstance(pending, dict) and pending.get("accepted") is True:
                return True
        return False
    except (OSError, ValueError):
        return None


def shared_daemon_busy(home: Path) -> bool | None:
    """Return False *only* when all independent checks establish idle."""
    checks = (busy_chatgpt_threads(home / ".bb/bb.db"),
              busy_cli_processes(),
              unresolved_accepted_turns(home / ".local/state/chatgpt-web/bb-acp"))
    if any(value is True for value in checks):
        return True
    if any(value is None for value in checks):
        return None
    return False
