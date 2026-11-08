"""Read-only continuation of an accepted ChatGPT web turn after transport loss.

The original POST must NEVER be repeated. The caller owns the exact accepted
message ID; a result is accepted only after a read-only saved conversation GET
verifies the original message and its completed final answer.
"""
from __future__ import annotations

import time
from typing import Any, Callable


def wait_for_original_final(
    fetch: Callable[[], dict[str, Any]],
    verify: Callable[[dict[str, Any]], bool],
    *,
    on_progress: Callable[[int], None] | None = None,
    transient_errors: tuple[type[Exception], ...] = (OSError,),
    rate_limit_delay: Callable[[Exception], float | None] | None = None,
    timeout_seconds: float = 1050,
    poll_seconds: float = 75,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll only GETs; return a verified final or time out without resending."""
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("positive recovery bounds required")
    deadline = clock() + timeout_seconds
    attempt = 0
    while True:
        attempt += 1
        delay = poll_seconds
        try:
            document = fetch()
            if verify(document):
                return document
        except transient_errors as error:
            if rate_limit_delay is not None:
                extra = rate_limit_delay(error)
                if extra is not None:
                    delay = max(delay, float(extra) + 5)
                elif type(error).__name__ == "ChatGPTWebError":
                    # The caller supplied a custom transport/protocol error.
                    # An unknown custom exception must not hide an auth or
                    # project-ownership failure as a retryable disconnect.
                    raise
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Original accepted ChatGPT turn remains unverified")
        if on_progress and (attempt == 1 or attempt % 5 == 0):
            on_progress(attempt)
        sleep(min(delay, remaining))
