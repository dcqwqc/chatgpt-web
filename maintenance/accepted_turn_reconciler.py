#!/usr/bin/env python3
"""Restore saved ChatGPT Web results to failed BB turns without model retries.

Runs periodically as a systemd user timer. Only act when: (1) an original
accepted ACP submission is durably recorded, (2) the BB thread is in error,
(3) its original BB prompt hash matches that submission, and (4) a read-only
ChatGPT GET contains the same submitted user message and a completed final
response. Never POST a ChatGPT prompt, delete a conversation or restart the
shared daemon here. The BB retry must be recognized by bb_retry_guard and
serves only to replay that already-verified response.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

if __package__:
    from .verified_recovery import verified_final
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from verified_recovery import verified_final

HOME = Path.home()
BB = HOME / ".bb"
SESSIONS = HOME / ".local/state/chatgpt-web/bb-acp"
STATE = HOME / ".local/state/chatgpt-web/reconciler"
CLI = HOME / ".local/bin/chatgpt-web"
BB_CLI = HOME / ".local/bin/bb"
SESSION_RE = re.compile(r"^[a-f0-9]{32}$")
THREAD_RE = re.compile(r"^thr_[a-z0-9]{6,32}$")
CONVERSATION_RE = re.compile(r"^[a-f0-9-]{36}$")
MAX_ATTEMPTS = 2
MIN_RETRY_INTERVAL = 300


def run(*args: str, timeout: int = 40) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, text=True, capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def original_prompt(db: sqlite3.Connection, tid: str, digest: str) -> str | None:
    """Require one unique originating BB request, never a synthetic retry."""
    found: list[str] = []
    for (raw,) in db.execute("""
        SELECT data FROM events WHERE thread_id=? AND type='client/turn/requested'
        ORDER BY sequence ASC
        """, (tid,)):
        try:
            event = json.loads(raw)
            if event.get("retryOfRequestId") or event.get("source") == "tell":
                continue
            blocks = event.get("input")
            if not isinstance(blocks, list) or not blocks:
                continue
            prompt = blocks[0].get("text") if isinstance(blocks[0], dict) else None
            if (isinstance(prompt, str) and
                    hashlib.sha256(prompt.encode()).hexdigest() == digest):
                found.append(prompt)
        except (KeyError, TypeError, ValueError):
            continue
    return found[0] if len(found) == 1 else None


def candidates() -> list[dict[str, str]]:
    result = []
    if not SESSIONS.is_dir():
        return result
    with sqlite3.connect(f"file:{BB / 'bb.db'}?mode=ro",
                         uri=True, timeout=5) as db:
        for path in SESSIONS.glob("*.json"):
            if not SESSION_RE.fullmatch(path.stem):
                continue
            try:
                if path.stat().st_size > 65536:
                    continue
                session = json.loads(path.read_text(encoding="utf8"))
                if not isinstance(session, dict):
                    continue
                pending = session.get("pending_turn")
                if not isinstance(pending, dict) or pending.get("accepted") is not True:
                    continue
                cwd = session.get("cwd")
                if not isinstance(cwd, str):
                    continue
                tid = Path(cwd).name
                cid = pending.get("conversation_id")
                mid = pending.get("message_id")
                digest = pending.get("prompt_sha256")
                if (not THREAD_RE.fullmatch(tid)
                        or not isinstance(cid, str) or not CONVERSATION_RE.fullmatch(cid)
                        or not isinstance(mid, str) or not mid
                        or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}",digest)):
                    continue
                thread = db.execute("SELECT provider_id,status FROM threads WHERE id=?",
                                    (tid,)).fetchone()
                if thread != ("acp-chatgpt-web", "error"):
                    continue
                prompt = original_prompt(db, tid, digest)
                if prompt is None:
                    continue
                result.append({"thread_id": tid, "conversation_id": cid,
                               "message_id": mid, "prompt": prompt,
                               "session_id": path.stem})
            except (OSError, ValueError, TypeError, sqlite3.Error):
                continue
    # Duplicate accepted sessions for one BB thread are ambiguous. Fail closed.
    counts = {}
    for x in result:
        counts[x["thread_id"]] = counts.get(x["thread_id"], 0) + 1
    return [x for x in result if counts[x["thread_id"]] == 1]


def verify_saved_result(item: dict[str,str]) -> bool:
    got = run(str(CLI), "show", item["conversation_id"], "--output", "json",
              timeout=42)
    if got is None or got.returncode != 0 or len(got.stdout) > 12_000_000:
        return False
    try:
        document = json.loads(got.stdout)
    except ValueError:
        return False
    return verified_final(document, conversation_id=item["conversation_id"],
                          message_id=item["message_id"],
                          user_prompt=item["prompt"]) is not None


def check_status(tid: str) -> str | None:
    try:
        with sqlite3.connect(f"file:{BB / 'bb.db'}?mode=ro",
                             uri=True, timeout=4) as db:
            row = db.execute("SELECT status FROM threads WHERE id=?",(tid,)).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def reconcile_once() -> list[dict[str, str]]:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    report = []
    with (STATE / "lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        try:
            budget = candidates()
        except (sqlite3.Error, OSError):
            return []
        for item in budget:
            tid = item["thread_id"]
            key = item["message_id"]
            path = STATE / (hashlib.sha256((tid + ":" + key).encode()).hexdigest() + ".json")
            try:
                state = json.loads(path.read_text())
            except (ValueError, OSError):
                state = {}
            if state.get("attempts", 0) >= MAX_ATTEMPTS:
                continue
            now = time.time()
            if now - state.get("last_attempt", 0) < MIN_RETRY_INTERVAL:
                continue
            if not verify_saved_result(item):
                continue
            if check_status(tid) != "error":
                continue
            # This controls only an already-errored BB bridge runtime. The
            # corresponding website turn is proven complete, so it is safe to
            # release the stale adapter before retrying the BB projection.
            stopped = run(str(BB_CLI), "thread", "stop", tid, "--json", timeout=30)
            if stopped is None or stopped.returncode != 0:
                continue
            state["attempts"] = int(state.get("attempts", 0)) + 1
            state["last_attempt"] = now
            state["thread_id"] = tid
            state["session_id"] = item["session_id"]
            state["source"] = "verified-saved-response"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, indent=2) + "\n")
            os.chmod(temp, 0o600)
            temp.replace(path)
            retried = run(str(BB_CLI), "thread", "retry", tid,
                          "--reason", "Recover verified original ChatGPT final without sending again",
                          "--json", timeout=35)
            status = "dispatched" if retried and retried.returncode == 0 else "retry-error"
            report.append({"thread": tid, "status": status})
        if report:
            print(json.dumps({"reconciled": report}))
    return report


if __name__ == "__main__":
    reconcile_once()
