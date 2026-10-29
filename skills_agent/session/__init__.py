"""Append-only interaction history and immutable copies of input attachments."""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from ..images import MAX_REQUEST_IMAGES, image_bytes


_STREAM_EVENT_TYPES = frozenset({"text_delta", "thinking_delta", "tool_call_delta"})
_STANDARD_STREAM_HEADER = re.compile(
    rb'^\{"timestamp":"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
    rb'(?:\.[0-9]{6})?\+00:00","type":"(?:text_delta|thinking_delta|tool_call_delta)",'
)


class Session:
    def __init__(self, root: Path, session_id: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id):
            raise ValueError("session_id must contain only letters, digits, '.', '_', or '-'")
        self.path = Path(root).expanduser().resolve() / session_id
        self.path.mkdir(parents=True, exist_ok=True)
        self._events_path = self.path / "events.jsonl"

    def append(self, message: dict) -> dict:
        return self.append_event({"type": "message", "message": message})

    def append_event(self, event: dict) -> dict:
        with self._events_path.open("ab") as stream:
            event = {"timestamp": datetime.now(timezone.utc).isoformat(),
                     **event, "event_id": f"event-{stream.tell()}"}
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            stream.write((line + "\n").encode("utf-8"))
        return event

    def iter_events(self, after: int = 0):
        """Read only appended bytes; legacy logs get stable references without rewriting."""
        if not self._events_path.exists():
            return
        with self._events_path.open("rb") as stream:
            stream.seek(after)
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    return
                if line.strip():
                    event = json.loads(line)
                    event.setdefault("event_id", f"event-{offset}")
                    yield stream.tell(), event

    def iter_receipt_events(self, after: int = 0):
        """Read raw semantic events, with a trailing (cursor, None) for skipped bytes.

        Standard stream fragments cannot contain a background receipt. Recognize
        their append_event header without decoding their payload; legacy or
        nonstandard headers still go through the complete JSON decoder.
        """
        if not self._events_path.exists():
            return
        cursor = delivered = after
        with self._events_path.open("rb") as stream:
            stream.seek(after)
            while True:
                line = stream.readline()
                if not line:
                    break
                offset = cursor
                if not line.endswith(b"\n"):
                    # Legacy logs can end with a complete JSON value but no
                    # newline. An unfinished concurrent append must be retried.
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        break
                    cursor += len(line)
                else:
                    cursor += len(line)
                    header = _STANDARD_STREAM_HEADER.match(line)
                    if (header and line.find(b'"type"', header.end()) < 0
                            and line.find(b"\\u", header.end()) < 0):
                        continue
                    # A duplicate or escaped type key is not a standard runtime
                    # fragment; decode it rather than trusting the first type.
                    if not line.strip():
                        continue
                    event = json.loads(line)
                if event.get("type") in _STREAM_EVENT_TYPES:
                    continue
                event.setdefault("event_id", f"event-{offset}")
                delivered = cursor
                yield cursor, event
            if cursor != delivered:
                yield cursor, None

    def events(self) -> list[dict]:
        return [event for _, event in self.iter_events()]

    def replay_events(self) -> list[dict]:
        """Read the rebuildable display projection, leaving raw events intact."""
        from .replay import replay_events
        return replay_events(self._events_path)

    def messages(self) -> list[dict]:
        return [event["message"] for event in self.events() if event.get("type") == "message"]

    def save_attachment(self, supplied_path: Path, *, data: bytes | None = None) -> dict:
        """Save one immutable input or tool-feedback snapshot in this session."""
        source = Path(supplied_path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Attachment is not a file: {source}")
        attachment_dir = self.path / "attachments"
        attachment_dir.mkdir(exist_ok=True)
        destination = attachment_dir / f"{uuid4().hex}_{source.name}"
        if data is None:
            shutil.copyfile(source, destination)
        else:
            destination.write_bytes(data)
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        return {"path": str(destination), "name": source.name, "mime_type": mime_type,
                "url": destination.as_uri()}

    def make_user_message(self, text: str, paths: list[Path]) -> dict:
        content = [{"type": "text", "text": text}] if text else []
        images = 0
        # Validate before copying anything, so rejected input leaves no partial message.
        prepared = []
        for supplied_path in paths:
            source = Path(supplied_path).expanduser().resolve(strict=True)
            if not source.is_file():
                raise ValueError(f"Attachment is not a file: {source}")
            mime = mimetypes.guess_type(source.name)[0]
            data = None
            if mime in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                images += 1
                if images > MAX_REQUEST_IMAGES:
                    raise ValueError(f"A request can contain at most {MAX_REQUEST_IMAGES} images")
                data = image_bytes({"path": str(source)})
            prepared.append((source, data))
        for supplied_path, data in prepared:
            metadata = self.save_attachment(supplied_path, data=data)
            destination = Path(metadata["path"])
            mime_type = metadata["mime_type"]
            if mime_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                content.append({"type": "image", **metadata,
                                "source_url": supplied_path.as_uri()})
            elif mime_type == "application/pdf":
                content.append({"type": "file", **metadata})
            else:
                data = destination.read_bytes()
                try:
                    file_text = data.decode("utf-8") if b"\0" not in data else None
                except UnicodeDecodeError:
                    file_text = None
                label = f"Attachment: {metadata['name']}\nPath: {destination}\n"
                body = file_text if file_text is not None else f"Binary file ({len(data)} bytes; {mime_type}). Read it using a script."
                content.append({"type": "text", "text": label + "\n" + body, "attachment": True, **metadata})
        if not content:
            raise ValueError("A message must contain text or an attachment")
        return {"role": "user", "content": content}


__all__ = ["Session"]
