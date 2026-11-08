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
                SELECT sequence,data FROM events WHERE thread_id=?
                AND type='client/turn/requested'
                ORDER BY sequence ASC
                """, (tid,)).fetchall()
            by_id: dict[str, tuple[int, dict[str, Any]]] = {}
            for seq, raw in rows:
                event = json.loads(raw)
                key = event.get("requestId")
                if not isinstance(key, str) or not key or key in by_id:
                    return None
                by_id[key] = (seq, event)

            # BB may retry an already retried turn. Traverse the complete
            # authenticated retry lineage, rather than assuming one hop.
            # Every intermediate hop must be a system-generated retry, never
            # an unrelated user message or model-supplied continuation.
            latest_id = attempt.get("requestId")
            current_seq = by_id.get(latest_id, (None, None))[0]
            if current_seq is None:
                return None
            seen: set[str] = {latest_id}
            for _ in range(24):
                if old_request_id in seen or old_request_id not in by_id:
                    return None
                seen.add(old_request_id)
                previous_seq, previous = by_id[old_request_id]
                if previous_seq >= current_seq:
                    return None
                current_seq = previous_seq
                next_id = previous.get("retryOfRequestId")
                if isinstance(next_id, str) and next_id:
                    blocks = previous.get("input")
                    if (previous.get("source") != "tell"
                            or previous.get("initiator") not in (None, "system")
                            or not isinstance(blocks, list) or len(blocks) != 1
                            or not isinstance(blocks[0], dict)
                            or blocks[0].get("type") != "text"
                            or blocks[0].get("text") != "Please continue."):
                        return None
                    old_request_id = next_id
                    continue

                blocks = previous.get("input")
                if (previous.get("initiator") not in (None, "user")
                        or not isinstance(blocks, list) or len(blocks) != 1
                        or not isinstance(blocks[0], dict)
                        or blocks[0].get("type") != "text"):
                    return None
                original = blocks[0].get("text", "")
                if (not isinstance(original, str) or not original
                        or hashlib.sha256(original.encode()).hexdigest()
                        != pending["prompt_sha256"]):
                    return None
                return original
            return None  # No cycles or unbounded linked retry chains.
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        return None
