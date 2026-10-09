"""Append-only interaction history and immutable copies of input attachments."""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone


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

    def events(self) -> list[dict]:
        return [event for _, event in self.iter_events()]

    def messages(self) -> list[dict]:
        return [event["message"] for event in self.events() if event.get("type") == "message"]

    def make_user_message(self, text: str, paths: list[Path]) -> dict:
        content = [{"type": "text", "text": text}] if text else []
        for supplied_path in paths:
            source = Path(supplied_path).expanduser().resolve(strict=True)
            if not source.is_file():
                raise ValueError(f"Attachment is not a file: {source}")
            attachment_dir = self.path / "attachments"
            attachment_dir.mkdir(exist_ok=True)
            destination = attachment_dir / f"{uuid4().hex}_{source.name}"
            shutil.copyfile(source, destination)
            mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            metadata = {"path": str(destination), "name": source.name, "mime_type": mime_type}
            if mime_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                content.append({"type": "image", **metadata})
            elif mime_type == "application/pdf":
                content.append({"type": "file", **metadata})
            else:
                data = destination.read_bytes()
                try:
                    file_text = data.decode("utf-8") if b"\0" not in data else None
                except UnicodeDecodeError:
                    file_text = None
                label = f"Attachment: {source.name}\nPath: {destination}\n"
                body = file_text if file_text is not None else f"Binary file ({len(data)} bytes; {mime_type}). Read it using a script."
                content.append({"type": "text", "text": label + "\n" + body, "attachment": True, **metadata})
        if not content:
            raise ValueError("A message must contain text or an attachment")
        return {"role": "user", "content": content}


__all__ = ["Session"]
