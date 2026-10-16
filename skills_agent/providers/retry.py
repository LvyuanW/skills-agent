"""Retry one model operation without replaying tools or merging partial responses."""

from copy import deepcopy
import math
import os
import time

from .base import ProviderError


RETRY_DELAY_SECONDS = 5


def retry_call(operation, *, on_retry=None, max_attempts=None,
               max_elapsed_seconds=None):
    """Retry one request without replaying any completed tool.

    Interactive calls remain unlimited by default. Evaluators can opt into
    attempts and elapsed budgets through arguments or SKILLS_AGENT_PROVIDER_*
    environment variables. The elapsed budget covers operations and waits; no
    further operation starts after it expires. A current blocking operation
    retains its transport timeout. Cancellation always propagates.
    """
    if max_attempts is None and os.environ.get("SKILLS_AGENT_PROVIDER_MAX_ATTEMPTS"):
        max_attempts = int(os.environ["SKILLS_AGENT_PROVIDER_MAX_ATTEMPTS"])
    if max_elapsed_seconds is None and os.environ.get("SKILLS_AGENT_PROVIDER_RETRY_BUDGET_SECONDS"):
        max_elapsed_seconds = float(os.environ["SKILLS_AGENT_PROVIDER_RETRY_BUDGET_SECONDS"])
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts < 1):
        raise ValueError("max_attempts must be a positive integer")
    if max_elapsed_seconds is not None and (
            isinstance(max_elapsed_seconds, bool) or not isinstance(max_elapsed_seconds, (int, float))
            or not math.isfinite(max_elapsed_seconds) or max_elapsed_seconds <= 0):
        raise ValueError("max_elapsed_seconds must be positive and finite")
    began = time.monotonic()
    attempt = 1
    while True:
        try:
            return operation()
        except ProviderError as exc:
            if not exc.retryable:
                raise
            elapsed = time.monotonic() - began
            delay = max(RETRY_DELAY_SECONDS, exc.retry_after_seconds or 0)
            reason = ("attempt_limit" if max_attempts is not None and attempt >= max_attempts else
                      "elapsed_limit" if max_elapsed_seconds is not None and elapsed >= max_elapsed_seconds else
                      "retry_after_exceeds_budget" if max_elapsed_seconds is not None and delay >= max_elapsed_seconds - elapsed else None)
            # Existing Agent error events preserve diagnostics. Keep bounded
            # retry metadata there without replacing the original failure.
            exc.diagnostics = dict(exc.diagnostics) if isinstance(exc.diagnostics, dict) else {}
            exc.diagnostics["retry"] = {
                "attempts": attempt, "max_attempts": max_attempts,
                "elapsed_seconds": round(elapsed, 6), "max_elapsed_seconds": max_elapsed_seconds,
                "exhausted": reason is not None, "reason": reason,
                "next_delay_seconds": None if reason else delay,
            }
            if reason is not None:
                raise
            if on_retry is not None:
                on_retry(exc, attempt)
            # KeyboardInterrupt / SystemExit (including benchmark SIGTERM) are
            # deliberately not caught, whether raised by the request or sleep.
            time.sleep(delay)
            if max_elapsed_seconds is not None:
                after_wait = time.monotonic() - began
                if after_wait >= max_elapsed_seconds:
                    exc.diagnostics["retry"].update(exhausted=True, reason="elapsed_limit", next_delay_seconds=None,
                                                    elapsed_seconds=round(after_wait, 6))
                    raise
            attempt += 1


def retry_event(exc, attempt, *, operation):
    """Preserve the failed attempt separately from accepted assistant messages."""
    diagnostics = exc.diagnostics if isinstance(exc.diagnostics, dict) else {}
    event = {"type": "retry", "message": str(exc), "operation": operation,
             "attempt": attempt,
             "retry_in_seconds": diagnostics.get("retry", {}).get("next_delay_seconds", RETRY_DELAY_SECONDS),
             "category": exc.category, "http_status": exc.http_status,
             "provider_code": exc.provider_code, "provider_type": exc.provider_type}
    if exc.diagnostics is not None:
        event["diagnostics"] = deepcopy(exc.diagnostics)
    if exc.raw is not None:
        event["_raw"] = deepcopy(exc.raw)
    if exc.partial_content:
        event["partial_content"] = deepcopy(exc.partial_content)
    return event
