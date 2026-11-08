"""Pure, fail-closed identity check for an accepted ChatGPT Web request.

This is deliberately read-only. A failed stream must never result in an
automatic second POST: only a verified response to the original message ID
may be replayed to BB.
"""
from __future__ import annotations

from typing import Any


def verified_final(document: dict[str, Any], *, conversation_id: str,
                   message_id: str, user_prompt: str) -> str | None:
    if (not isinstance(document, dict) or document.get("id") != conversation_id
            or not conversation_id or not message_id or not user_prompt.strip()):
        return None
    messages = document.get("messages")
    if not isinstance(messages, list):
        return None
    indexes = [i for i, msg in enumerate(messages)
               if isinstance(msg, dict) and msg.get("id") == message_id
               and msg.get("role") == "user"
               and msg.get("status") == "finished_successfully"]
    if len(indexes) != 1:
        return None
    origin = messages[indexes[0]]
    parts = origin.get("parts")
    if not isinstance(parts, list):
        return None
    source = "\n".join(piece for piece in parts if isinstance(piece, str))
    if user_prompt not in source:
        return None
    for msg in messages[indexes[0] + 1:]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            return None  # This response has been superseded by another turn.
        if (msg.get("role") == "assistant" and msg.get("channel") == "final"
                and msg.get("recipient") in (None, "all")
                and msg.get("status") == "finished_successfully"):
            answer = msg.get("text")
            if isinstance(answer, str) and answer.strip():
                return answer
    return None
