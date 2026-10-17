"""Ordered UI delivery without making execution wait for terminal rendering."""

from collections import deque
from dataclasses import dataclass, field
from threading import Lock


def _display_event(event):
    # The raw provider stream is already in the session log. Do not copy or
    # retain it in the UI backlog, or share mutable content with context work.
    result = {key: value for key, value in event.items() if key != "_raw"}
    if isinstance(result.get("message"), dict):
        message = {key: value for key, value in result["message"].items()
                   if key not in {"_raw", "_responses_output", "_anthropic_content"}}
        for key in ("content", "tool_calls"):
            if isinstance(message.get(key), list):
                message[key] = [dict(item) if isinstance(item, dict) else item
                                for item in message[key]]
        result["message"] = message
    if isinstance(result.get("partial_content"), list):
        result["partial_content"] = [dict(item) for item in result["partial_content"]]
    return result


@dataclass
class _Event:
    renderer: object
    event: dict
    parts: list = field(default_factory=list)

    def run(self):
        if self.parts:
            self.event["text"] = "".join(self.parts)
        self.renderer(self.event)


@dataclass
class _Call:
    callback: object
    args: tuple
    kwargs: dict

    def run(self):
        self.callback(*self.args, **self.kwargs)


class DisplayQueue:
    """One FIFO for display events and UI calls; only its consumer renders.

    Producers hold a separate short lock only to append. Adjacent text deltas
    use chunk lists so an arbitrarily slow UI does not cause quadratic string
    copies. A control event or another callback is an ordering boundary.
    """

    def __init__(self, wake=lambda: None):
        self._wake = wake
        self._pending = deque()
        self._lock = Lock()
        self._closed = False

    def post(self, callback, *args, **kwargs):
        with self._lock:
            if self._closed:
                return False
            wake = not self._pending
            self._pending.append(_Call(callback, args, kwargs))
        if wake:
            self._wake()
        return True

    def post_event(self, renderer, event):
        event = _display_event(event)
        merge = event.get("type") in {"text_delta", "thinking_delta"}
        with self._lock:
            if self._closed:
                return False
            wake = not self._pending
            previous = self._pending[-1] if self._pending else None
            if (merge and isinstance(previous, _Event) and previous.parts
                    and previous.renderer is renderer
                    and previous.event.get("type") == event["type"]):
                previous.parts.append(event["text"])
            else:
                self._pending.append(_Event(renderer, event, [event["text"]] if merge else []))
        if wake:
            self._wake()
        return True

    def drain(self):
        # Never call renderer code or join a growing stream under the producer
        # lock. New events form the next batch while this one is being drawn.
        with self._lock:
            pending, self._pending = self._pending, deque()
        count = 0
        while pending:
            item = pending.popleft()
            try:
                item.run()
            except BaseException:
                with self._lock:
                    pending.extend(self._pending)
                    self._pending = pending
                raise
            count += 1
        return count

    def close(self):
        """Reject further posts, retaining already accepted events for draining."""
        with self._lock:
            self._closed = True
