"""Correlate the synthetic BB retry prompt with the original accepted turn.

BB thread retry sends "Please continue.", not the user input from the
original failed turn. Only trust it after verifying the complete durable BB
retry link back to the original request ID and exact prompt digest.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


THREAD_RE = re.compile(r"^thr_[a-z0-9]{6,32}$")


def original_prompt_for_retry(cwd: Any, pending: dict[str, Any],
                              received_prompt: str, bb_dir: Path) -> str | None:
    """Verify exact BB-generated retry lineage, returning the ORIGINAL prompt.

    On ambiguity, malformed state or unexpected prompt, return None. Never
    interpret unverified continuation text as permission to resend a request.
    """
    if received_prompt != "Please continue." or not isinstance(cwd, str):
        return None
    tid = Path(cwd).name
    if not THREAD_RE.fullmatch(tid):
        return None
    if not isinstance(pending.get("prompt_sha256"), str):
        return None
    try:
        with sqlite3.connect(f"file:{bb_dir / 'bb.db'}?mode=ro",
                             uri=True, timeout=2) as db:
            thread = db.execute(
                "SELECT provider_id,status FROM threads WHERE id=?", (tid,)).fetchone()
            if thread is None or thread[0] != "acp-chatgpt-web":
                return None
            recent = db.execute("""
                SELECT data FROM events WHERE thread_id=?
                AND type='client/turn/requested' ORDER BY sequence DESC LIMIT 1
                """, (tid,)).fetchone()
            if recent is None:
                return None
            attempt = json.loads(recent[0])
            old_request_id = attempt.get("retryOfRequestId")
            if not isinstance(old_request_id, str) or not old_request_id:
                return None
            inputs = attempt.get("input")
            if (attempt.get("source") != "tell" or not isinstance(inputs, list)
                    or len(inputs) != 1 or not isinstance(inputs[0], dict)
                    or inputs[0].get("type") != "text"
                    or inputs[0].get("text") != received_prompt):
                return None
            rows = db.execute("""
                SELECT data FROM events WHERE thread_id=?
                AND type='client/turn/requested'
                ORDER BY sequence ASC
                """, (tid,)).fetchall()
            origin = next((json.loads(row[0]) for row in rows
                           if json.loads(row[0]).get("requestId") == old_request_id), None)
            if origin is None or not origin.get("input"):
                return None
            original = origin["input"][0].get("text", "")
            if (not isinstance(original, str) or not original
                    or hashlib.sha256(original.encode()).hexdigest()
                    != pending["prompt_sha256"]):
                return None
            return original
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        return None
