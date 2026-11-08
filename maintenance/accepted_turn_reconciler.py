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
            # BB uses source=tell for ordinary follow-ups as well as its
            # synthetic retries. A source=tell exclusion silently drops most
            # real requests and disables the self-healing reconciler.
            # The initiator and retry linkage, NOT source, identify a genuine
            # user-authored request.
            if (event.get("retryOfRequestId")
                    or event.get("initiator") not in (None, "user")
                    or event.get("source") not in ("spawn", "tell")):
                continue
            blocks = event.get("input")
            if (not isinstance(blocks, list) or len(blocks) != 1
                    or not isinstance(blocks[0], dict)
                    or blocks[0].get("type") != "text"):
                continue
            prompt = blocks[0].get("text")
            if (isinstance(prompt, str) and
                    hashlib.sha256(prompt.encode()).hexdigest() == digest):
                found.append(prompt)
        except (KeyError, TypeError, ValueError):
            continue
    return found[0] if len(found) == 1 else None



TRANSPORT_FAILURE_MARKERS = (
    "incomplete chunked read", "daemon stream ended without a result",
    "peer closed connection", "remoteprotocolerror", "read timeout",
)


def legacy_candidate(db: sqlite3.Connection, session: dict, path: Path) -> dict | None:
    """Detect errored, older ACP sessions that lost the accepted message ID.

    This read-only step does NOT infer acceptance: the real website message
    must later be fetched, matched by exact prompt and timestamp, and finished.
    """
    if session.get("pending_turn") is not None:
        return None
    cid = session.get("conversation_id")
    cwd = session.get("cwd")
    if (not isinstance(cid, str) or not CONVERSATION_RE.fullmatch(cid)
            or not isinstance(cwd, str)):
        return None
    tid = Path(cwd).name
    if not THREAD_RE.fullmatch(tid):
        return None
    if db.execute("SELECT provider_id,status FROM threads WHERE id=?",
                  (tid,)).fetchone() != ("acp-chatgpt-web", "error"):
        return None
    err = db.execute("""
        SELECT data FROM events WHERE thread_id=? AND type='provider/error'
        ORDER BY sequence DESC LIMIT 1""", (tid,)).fetchone()
    if not err:
        return None
    try:
        detail = str(json.loads(err[0]).get("detail", "")).lower()
    except (ValueError, TypeError):
        return None
    if not any(marker in detail for marker in TRANSPORT_FAILURE_MARKERS):
        return None
    # Only consider the most recent user-authored turn. An intervening BB
    # retry/continuation makes reconstruction ambiguous and must fail closed.
    request = db.execute("""
        SELECT data,created_at FROM events WHERE thread_id=?
        AND type='client/turn/requested' ORDER BY sequence DESC LIMIT 1
        """, (tid,)).fetchone()
    if request is None:
        return None
    try:
        data = json.loads(request[0])
        blocks = data.get("input")
        if (data.get("initiator") != "user" or data.get("retryOfRequestId")
                or not isinstance(blocks, list) or len(blocks) != 1
                or not isinstance(blocks[0], dict)
                or blocks[0].get("type") != "text"
                or not isinstance(blocks[0].get("text"), str)):
            return None
        prompt = blocks[0]["text"]
        created = int(request[1])
        if not prompt.strip():
            return None
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return {"thread_id": tid, "conversation_id": cid,
            "prompt": prompt, "created_at_ms": created,
            "session_id": path.stem, "legacy": True}


def reconstruct_identity(item: dict) -> bool:
    """Confirm the last ChatGPT user ID against BB before saving it atomically."""
    got = run(str(CLI), "show", item["conversation_id"], "--output", "json",
              timeout=42)
    if got is None or got.returncode or len(got.stdout) > 12_000_000:
        return False
    try:
        doc = json.loads(got.stdout)
        if doc.get("id") != item["conversation_id"]:
            return False
        messages = doc.get("messages")
        if not isinstance(messages, list):
            return False
        users = [(i, m) for i, m in enumerate(messages)
                 if isinstance(m, dict) and m.get("role") == "user"]
        if not users:
            return False
        index, last = users[-1]
        mid = last.get("id")
        source = "\n".join(part for part in last.get("parts", [])
                           if isinstance(part, str))
        created = last.get("create_time")
        if (not isinstance(mid, str) or not mid
                or last.get("status") != "finished_successfully"
                or not isinstance(created, (int, float))
                or abs(created * 1000 - item["created_at_ms"]) > 300000
                or item["prompt"] not in source):
            return False
        if verified_final(doc, conversation_id=item["conversation_id"],
                          message_id=mid, user_prompt=item["prompt"]) is None:
            return False
        session_file = SESSIONS / (item["session_id"] + ".json")
        original = session_file.read_bytes()
        session = json.loads(original)
        if (session.get("pending_turn") is not None
                or session.get("conversation_id") != item["conversation_id"]
                or Path(str(session.get("cwd", ""))).name != item["thread_id"]
                or check_status(item["thread_id"]) != "error"):
            return False
        session["pending_turn"] = {
            "accepted": True, "message_id": mid,
            "conversation_id": item["conversation_id"],
            "prompt_sha256": hashlib.sha256(item["prompt"].encode()).hexdigest(),
        }
        tmp = session_file.with_suffix(".json.reconcile.tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        # Confirm the source did not change during the GET or our write.
        if session_file.read_bytes() != original:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(session_file)
        item["message_id"] = mid
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False

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
                    old = legacy_candidate(db, session, path)
                    if old is not None:
                        result.append(old)
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
            # An older adapter can finish the website turn while losing its
            # pending_turn metadata. Reconstruct only after independent,
            # read-only final-response evidence; never re-post the prompt.
            if item.get("legacy") and not reconstruct_identity(item):
                continue
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
            # The errored turn is already terminated. Do NOT call thread stop:
            # stop may transition Error -> Idle, which makes BB reject retry as
            # no_failed_turn. Only retry the verified, latest failed request.
            with sqlite3.connect(f"file:{BB / 'bb.db'}?mode=ro",
                                 uri=True, timeout=4) as db:
                row = db.execute("""
                    SELECT data FROM events WHERE thread_id=?
                    AND type='client/turn/requested'
                    ORDER BY sequence DESC LIMIT 1
                    """, (tid,)).fetchone()
            if not row:
                continue
            try:
                latest = json.loads(row[0])
            except (TypeError, ValueError):
                continue
            retry_id = latest.get("requestId")
            if (not isinstance(retry_id, str) or not retry_id
                    or latest.get("initiator") not in (None, "user", "system")):
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
                          "--turn", retry_id,
                          "--reason", "Recover verified original ChatGPT final without sending again",
                          "--json", timeout=35)
            status = "dispatched" if retried and retried.returncode == 0 else "retry-error"
            report.append({"thread": tid, "status": status})
        if report:
            print(json.dumps({"reconciled": report}))
    return report


if __name__ == "__main__":
    reconcile_once()
