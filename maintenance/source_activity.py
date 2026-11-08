"""Read-only source observations for interrupted BB ChatGPT streams.

The saved chat is not a resumable SSE feed. Observations are provenance-checked,
contain only source metadata and public commentary, and cannot imply a final
response or authorize a second ChatGPT POST.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

CET = ZoneInfo("Europe/Berlin")


def observe_source(document: dict[str, Any], *, conversation_id: str,
                   message_id: str, user_prompt: str,
                   now: float) -> dict[str, Any] | None:
    if (not isinstance(document, dict) or document.get("id") != conversation_id
            or not isinstance(user_prompt, str) or not user_prompt.strip()):
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
    if (not isinstance(parts, list) or
            user_prompt not in "\n".join(p for p in parts if isinstance(p, str))):
        return None
    events: list[dict[str, Any]] = []
    for msg in messages[indexes[0]+1:]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            return None  # Another turn superseded the original request.
        if msg.get("role") not in ("tool", "assistant"):
            continue
        stamp = msg.get("create_time")
        if not isinstance(stamp, (int, float)) or not (0 < stamp <= now + 60):
            continue
        events.append(msg)
    last = max((float(msg["create_time"]) for msg in events),
               default=float(origin.get("create_time") or 0))
    if last <= 0:
        return None
    tool_calls = [
        {"when": float(m["create_time"]), "name": str(m["recipient"])}
        for m in events if (m.get("role") == "assistant"
                            and isinstance(m.get("recipient"), str)
                            and m["recipient"] not in ("all", "")
                            and not m["recipient"].startswith("api_tool."))
    ]
    # Keep the action list bounded. Never copy credentials, tool arguments,
    # reasoning, or other untrusted material from the saved source.
    times = [datetime.fromtimestamp(t["when"], CET).strftime("%H:%M")
             for t in tool_calls[-8:]]
    readable = [a["name"].split(".")[-1][:48] for a in tool_calls[-8:]]
    detail = "Saved source actions (last 8): " + (
        ", ".join(f"{when} {name}" for when, name in zip(times, readable))
        if times else "no readable tool-call summaries")
    return {
        "last_activity": last,
        "last_at": datetime.fromtimestamp(last, CET).strftime("%H:%M CET"),
        "age_seconds": max(0, now - last),
        "tool_calls": len(tool_calls),
        "detail": detail,
        "has_final": any(
            m.get("role") == "assistant" and m.get("channel") == "final"
            and m.get("status") == "finished_successfully"
            and isinstance(m.get("text"), str) and bool(m["text"].strip())
            for m in events
        ),
    }
