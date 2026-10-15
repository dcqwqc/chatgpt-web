"""HTTP 429 cooldown parsing and display for ChatGPT Web terminal sessions.

The server-supplied Retry-After seconds indicate when to try again, not a
remaining ChatGPT subscription message balance.
"""
from __future__ import annotations

import math
import re
import time

# Keep user-visible failures distinct from model's general usage caps.
_RETRY_AFTER = re.compile(
    r"(?:ChatGPT web rate limit reached|HTTP 429|too many requests)"
    r".*?retry after\s+(\d+)\s+seconds?\b",
    re.IGNORECASE | re.DOTALL,
)
_DISPLAY_RETRY = re.compile(r"retry after\s+\d+\s+seconds?\b", re.I)


def retry_after_seconds(error: str) -> int | None:
    match = _RETRY_AFTER.search(str(error))
    if match is None:
        return None
    seconds = int(match.group(1))
    # A pathological server value should not deadlock the CLI for eternity.
    return min(max(1, seconds), 604800)


def remaining_seconds(deadline: float | None, now: float | None = None) -> int:
    if not isinstance(deadline, (int, float)):
        return 0
    current = time.time() if now is None else now
    if not math.isfinite(float(deadline)):
        return 0
    return max(0, math.ceil(float(deadline) - current))


def format_remaining(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m {seconds % 60:02d}s"


def display_rate_limit(text: str, deadline: float | None, now: float | None = None) -> str:
    if not isinstance(text, str) or not isinstance(deadline, (int, float)):
        return text
    seconds = remaining_seconds(deadline, now)
    suffix = f"retry in {format_remaining(seconds)}" if seconds else "ready to retry"
    return _DISPLAY_RETRY.sub(suffix, text, count=1)
