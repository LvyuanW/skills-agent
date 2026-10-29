"""Rebuildable display-only projections of the append-only session log.

The raw log remains authoritative. A cached byte cursor lets a subsequent UI
startup read only new complete lines, without decoding old provider payloads
and replaying hundreds of thousands of individual stream fragments again.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


_VERSION = 1
_ANCHOR_BYTES = 4096
_DELTA_TYPES = {"text_delta", "thinking_delta"}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _identity(info):
    return [info.st_dev, info.st_ino]


def _anchors(stream, cursor):
    """Bounded guards for replacement/truncation of an append-only prefix."""
    position = stream.tell()
    try:
        stream.seek(0)
        head = stream.read(min(cursor, _ANCHOR_BYTES))
        stream.seek(max(0, cursor - _ANCHOR_BYTES))
        tail = stream.read(min(cursor, _ANCHOR_BYTES))
        return hashlib.sha256(head).hexdigest(), hashlib.sha256(tail).hexdigest()
    finally:
        stream.seek(position)


def _load_cache(path, stream, info):
    try:
        cached = json.loads(path.read_bytes())
        source = cached["source"]
        cursor = source["cursor"]
        events = cached["events"]
        if (cached["version"] != _VERSION or source["identity"] != _identity(info)
                or type(cursor) is not int or not 0 < cursor <= info.st_size
                or not isinstance(events, list)
                or not all(isinstance(event, dict) and isinstance(event.get("type"), str)
                           for event in events)):
            return None
        if (source["size"] == info.st_size and source["mtime_ns"] != info.st_mtime_ns
                or source["size"] > info.st_size):
            return None
        if list(_anchors(stream, cursor)) != source["anchors"]:
            return None
        if hashlib.sha256(_encoded(events)).hexdigest() != cached["events_sha256"]:
            return None
        return cached
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _project(event):
    # Provider response copies can be enormous. No console renderer reads them.
    projected = {key: value for key, value in event.items() if key != "_raw"}
    message = projected.get("message")
    if isinstance(message, dict) and "_raw" in message:
        projected["message"] = {key: value for key, value in message.items() if key != "_raw"}
    return projected


class _Compactor:
    def __init__(self, events=()):
        self.events = []
        self.pending = None
        self.chunks = []
        self.arguments = {}
        for event in events:
            self.add(event)

    def _flush(self):
        if self.pending is not None:
            event, field, _ = self.pending
            event[field] = "".join(self.chunks)
            self.events.append(event)
            self.pending = None
            self.chunks = []

    def add(self, original):
        event = _project(original)
        kind = event.get("type")
        field = "text" if kind in _DELTA_TYPES else None
        # Retain name/id fragments: they can display a receiving-call row before
        # the arguments finish. Merge only argument-only fragments of one call.
        if (kind == "tool_call_delta" and not event.get("name") and not event.get("id")
                and isinstance(event.get("arguments"), str)):
            field = "arguments"
        boundary = False
        if kind == "tool_call_delta":
            index = event.get("index")
            arguments = self.arguments.get(index, "") + (event.get("arguments") or "")
            self.arguments[index] = arguments
            try:
                json.loads(arguments)
                # Preserve every intermediate complete JSON value; a renderer
                # can announce its label before subsequent fragments arrive.
                boundary = True
            except (ValueError, RecursionError):
                pass
        elif kind in {"error", "retry"} or (kind == "message"
                and event.get("message", {}).get("role") == "assistant"):
            self.arguments.clear()
        if field is not None and isinstance(event.get(field), str):
            signature = {key: value for key, value in event.items()
                         if key not in {field, "timestamp", "event_id"}}
            if self.pending is None or self.pending[1:] != (field, signature):
                self._flush()
                self.pending = (event, field, signature)
            self.chunks.append(event[field])
            if boundary:
                self._flush()
        else:
            self._flush()
            self.events.append(event)

    def finish(self):
        self._flush()
        return self.events


def _write_cache(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(_encoded(value))
        os.replace(temporary, path)
    except OSError:
        # A read-only session still has fully usable history; this is a cache.
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class _SourceChanged(Exception):
    pass


def _read_once(source_path, cache_path):
    with source_path.open("rb") as stream:
        initial = os.fstat(stream.fileno())
        cached = _load_cache(cache_path, stream, initial)
        cursor = cached["source"]["cursor"] if cached else 0
        original_cursor = cursor
        original_anchors = _anchors(stream, cursor)
        compactor = _Compactor(cached["events"] if cached else ())
        stream.seek(cursor)
        while stream.tell() < initial.st_size:
            start = stream.tell()
            line = stream.readline(initial.st_size - start)
            # An append can be visible before its final newline. Do not cache
            # or display it until a later read observes the complete record.
            if not line.endswith(b"\n"):
                break
            if line.strip():
                event = json.loads(line)
                event.setdefault("event_id", f"event-{start}")
                compactor.add(event)
            cursor = stream.tell()
        current = source_path.stat()
        if (_identity(current) != _identity(initial) or current.st_size < initial.st_size
                or _anchors(stream, original_cursor) != original_anchors
                or (current.st_size == initial.st_size and current.st_mtime_ns != initial.st_mtime_ns)):
            raise _SourceChanged
        events = compactor.finish()
        if cursor and (cached is None or cursor != original_cursor):
            _write_cache(cache_path, {
                "version": _VERSION,
                "source": {"identity": _identity(initial), "cursor": cursor,
                           "size": initial.st_size, "mtime_ns": initial.st_mtime_ns,
                           "anchors": list(_anchors(stream, cursor))},
                "events": events, "events_sha256": hashlib.sha256(_encoded(events)).hexdigest(),
            })
        return events


def replay_events(source_path, *, cache_path=None):
    """Return compact display events, never changing raw history or context."""
    source_path = Path(source_path)
    cache_path = Path(cache_path) if cache_path is not None else source_path.with_name("replay-cache.json")
    for _ in range(3):
        try:
            return _read_once(source_path, cache_path)
        except FileNotFoundError:
            return []
        except _SourceChanged:
            continue
    raise OSError("Session log kept changing while its history was being loaded")
