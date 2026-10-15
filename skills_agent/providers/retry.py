"""Retry one model operation without replaying tools or merging partial responses."""

from copy import deepcopy
import time

from .base import ProviderError


RETRY_DELAY_SECONDS = 5


def retry_call(operation, *, on_retry=None):
    """Retry recoverable failures indefinitely; cancellation always propagates."""
    attempt = 1
    while True:
        try:
            return operation()
        except ProviderError as exc:
            if not exc.retryable:
                raise
            if on_retry is not None:
                on_retry(exc, attempt)
            # KeyboardInterrupt / SystemExit (including benchmark SIGTERM) are
            # deliberately not caught, whether raised by the request or sleep.
            time.sleep(RETRY_DELAY_SECONDS)
            attempt += 1


def retry_event(exc, attempt, *, operation):
    """Preserve the failed attempt separately from accepted assistant messages."""
    event = {"type": "retry", "message": str(exc), "operation": operation,
             "attempt": attempt, "retry_in_seconds": RETRY_DELAY_SECONDS,
             "category": exc.category, "http_status": exc.http_status,
             "provider_code": exc.provider_code, "provider_type": exc.provider_type}
    if exc.diagnostics is not None:
        event["diagnostics"] = deepcopy(exc.diagnostics)
    if exc.raw is not None:
        event["_raw"] = deepcopy(exc.raw)
    if exc.partial_content:
        event["partial_content"] = deepcopy(exc.partial_content)
    return event
