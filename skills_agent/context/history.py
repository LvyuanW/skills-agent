"""Incremental, source-linked search over a main session's original messages."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path


def _terms(text: str) -> list[str]:
    """Use the same Unicode words and CJK characters/bigrams for index and query."""
    parts = re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+|[^\W_\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", text.casefold())
    terms = []
    for part in parts:
        if re.fullmatch(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", part):
            terms.extend(part)
            terms.extend(part[index:index + 2] for index in range(len(part) - 1))
        else:
            terms.append(part)
    return terms


def _value_text(value) -> str:
    """Decode tool JSON without turning its stdout's line breaks into escapes."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(
            f"{key}:\n{_value_text(item)}" for key, item in value.items()
        )
    if isinstance(value, list):
        return "\n".join(_value_text(item) for item in value)
    return json.dumps(value, ensure_ascii=False)


def _message_text(message: dict) -> str:
    parts = []
    if message.get("reasoning_content"):
        parts.append("Thinking:\n" + message["reasoning_content"])
    content = message.get("content", "")
    if isinstance(content, str):
        if message.get("role") == "tool":
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                pass
        parts.append(_value_text(content))
    elif isinstance(content, list):
        for block in content:
            if block.get("type") == "text":
                text = block["text"]
                if message.get("role") == "tool":
                    try:
                        text = json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        pass
                parts.append(_value_text(text))
            elif block.get("type") == "thinking":
                parts.append("Thinking:\n" + block.get("thinking", ""))
            elif block.get("type") in {"image", "file"}:
                label = f"Attachment: {block.get('name', '')}\nPath: {block.get('path', '')}"
                if block.get("url"):
                    label += f"\nURL: {block['url']}"
                if block.get("source_url"):
                    label += f"\nSource URL: {block['source_url']}"
                parts.append(label)
    for call in message.get("tool_calls", []):
        original_arguments = arguments = call.get("arguments", "")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, RecursionError):
                pass
        try:
            argument_text = _value_text(arguments)
        except RecursionError:
            argument_text = original_arguments
        parts.append(f"Tool call {call.get('id', '')}: {call.get('name', '')}\n{argument_text}")
    return "\n\n".join(part for part in parts if part)


def _chunks(text: str):
    """Chunk on original line boundaries; keep every line including long lines."""
    lines = text.splitlines(keepends=True) or [""]
    start, size, group = 1, 0, []
    for number, line in enumerate(lines, 1):
        if group and (len(group) >= 80 or size + len(line) > 6000):
            yield start, number - 1, "".join(group)
            start, size, group = number, 0, []
        group.append(line)
        size += len(line)
    if group:
        yield start, len(lines), "".join(group)


class HistoryIndex:
    """Index only events.jsonl in this directory, never independent task records."""

    def __init__(self, session_path: Path | str):
        supplied = Path(session_path).expanduser().resolve()
        self.path = supplied.parent if supplied.name == "events.jsonl" else supplied
        self.events_path = self.path / "events.jsonl"
        self.database_path = self.path / "history.sqlite"

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, byte_offset INTEGER UNIQUE NOT NULL,
                raw_line INTEGER NOT NULL, turn_id TEXT NOT NULL,
                role TEXT NOT NULL, kind TEXT NOT NULL, task TEXT NOT NULL,
                timestamp TEXT NOT NULL, text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT NOT NULL, event_id TEXT NOT NULL,
                kind TEXT NOT NULL, task TEXT NOT NULL, call_event_id TEXT NOT NULL,
                PRIMARY KEY (call_id, event_id)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                tokens, event_id UNINDEXED, start_line UNINDEXED,
                end_line UNINDEXED, text UNINDEXED
            );
        """)
        return connection

    def sync(self) -> int:
        """Append complete messages/errors; return the newly indexed record count."""
        if not self.events_path.exists():
            return 0
        with closing(self._connect()) as connection, connection:
            state = dict(connection.execute("SELECT key, value FROM state"))
            cursor = int(state.get("cursor", 0))
            raw_line = int(state.get("raw_line", 0))
            turn_id = state.get("turn_id", "")
            task = state.get("task", "")
            indexed = 0
            with self.events_path.open("rb") as source:
                source.seek(cursor)
                while True:
                    offset = source.tell()
                    line = source.readline()
                    if not line or not line.endswith(b"\n"):
                        break
                    cursor, raw_line = source.tell(), raw_line + 1
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    event_kind = event.get("type")
                    if event_kind == "message":
                        message = event["message"]
                    elif event_kind == "error":
                        partial = _message_text({"role": "assistant", "content": event.get("partial_content", [])})
                        message = {"role": "system", "content": "\n\n".join(
                            text for text in (str(event.get("message", "")), partial) if text
                        )}
                    else:
                        continue
                    role = message.get("role", "")
                    if role not in {"user", "assistant", "tool", "system"}:
                        continue
                    event_id = event.get("event_id", f"event-{offset}")
                    text = _message_text(message)
                    if role == "user":
                        turn_id, task = event_id, text.splitlines()[0][:240] if text else ""
                    message_task = task
                    calls = []
                    for call in message.get("tool_calls", []):
                        try:
                            arguments = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]
                        except (KeyError, ValueError, RecursionError):
                            arguments = {}
                        if not isinstance(arguments, dict):
                            arguments = {}
                        name = arguments.get("name") or arguments.get("goal") or arguments.get("summary") or call.get("name", "")
                        call_task = str(name)
                        calls.append((call["id"], event_id, "call", call_task, event_id))
                    if calls:
                        message_task = "\n".join(call[3] for call in calls)
                    if role == "tool" and message.get("tool_call_id"):
                        match = connection.execute(
                            "SELECT calls.* FROM calls JOIN events USING (event_id) "
                            "WHERE call_id = ? AND calls.kind = 'call' ORDER BY byte_offset DESC LIMIT 1",
                            (message["tool_call_id"],),
                        ).fetchone()
                        message_task = match["task"] if match else task
                        calls.append((message["tool_call_id"], event_id, "result", message_task,
                                      match["call_event_id"] if match else event_id))
                    connection.execute(
                        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (event_id, offset, raw_line, turn_id, role, "error" if event_kind == "error" else message.get("name") or role,
                         message_task, str(event.get("timestamp", "")), text),
                    )
                    connection.executemany("INSERT INTO calls VALUES (?, ?, ?, ?, ?)", calls)
                    for start, end, chunk in _chunks(text):
                        connection.execute(
                            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                            (" ".join(_terms(chunk + "\n" + message_task)), event_id, start, end, chunk),
                        )
                    indexed += 1
            connection.executemany("INSERT OR REPLACE INTO state VALUES (?, ?)", [
                ("cursor", str(cursor)), ("raw_line", str(raw_line)),
                ("turn_id", turn_id), ("task", task),
            ])
            return indexed

    def _reference(self, row) -> dict:
        result = {key: row[key] for key in ("event_id", "turn_id", "role", "kind", "task", "timestamp")}
        result["raw_ref"] = f"{self.events_path}:{row['raw_line']}"
        result["line_count"] = len(row["text"].splitlines()) or 1
        return result

    def search(self, query: str, limit: int = 8, *, role: str | None = None, task: str | None = None) -> list[dict]:
        """Prefer literal matches, then BM25; query syntax is always literal data."""
        if not query.strip() or limit < 1:
            raise ValueError("Search requires a nonempty query and positive limit")
        self.sync()
        with closing(self._connect()) as connection:
            filters, parameters = [], []
            for field, value in (("role", role), ("task", task)):
                if value is not None:
                    filters.append(f"events.{field} = ?")
                    parameters.append(value)
            where = (" AND " + " AND ".join(filters)) if filters else ""
            select = """SELECT events.*, chunks.start_line, chunks.end_line,
                        chunks.text AS chunk_text FROM chunks JOIN events USING (event_id)"""
            exact = connection.execute(
                select + " WHERE (instr(lower(chunks.text), lower(?)) > 0 OR events.event_id = ?"
                " OR EXISTS (SELECT 1 FROM calls WHERE calls.event_id = events.event_id AND calls.call_id = ?))"
                + where + " ORDER BY events.byte_offset DESC, CAST(chunks.start_line AS INTEGER) LIMIT ?",
                [query, query, query, *parameters, limit],
            ).fetchall()
            terms = list(dict.fromkeys(_terms(query)))
            ranked = []
            if terms and len(exact) < limit:
                expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                ranked = connection.execute(
                    select + " WHERE chunks MATCH ?" + where +
                    " ORDER BY bm25(chunks), events.byte_offset DESC LIMIT ?",
                    [expression, *parameters, limit + len(exact)],
                ).fetchall()
            results, seen = [], set()
            for row in [*exact, *ranked]:
                key = (row["event_id"], row["start_line"])
                if key in seen:
                    continue
                seen.add(key)
                result = self._reference(row)
                result.update(start_line=int(row["start_line"]), end_line=int(row["end_line"]))
                result["preview"] = row["chunk_text"][:700]
                result["preview_truncated"] = len(row["chunk_text"]) > 700
                results.append(result)
                if len(results) >= limit:
                    break
            return results

    def read(self, event_id: str, start_line: int = 1, end_line: int | None = None) -> dict:
        """Read inclusive lines of decoded original content, with paired references."""
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ValueError("Line ranges require 1 <= start_line <= end_line")
        self.sync()
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown history event: {event_id}")
            lines = row["text"].splitlines(keepends=True) or [""]
            end = min(len(lines), end_line) if end_line is not None else len(lines)
            result = self._reference(row)
            result.update(start_line=start_line, end_line=end, text="".join(lines[start_line - 1:end]))
            result["paired_events"] = [item[0] for item in connection.execute(
                """SELECT DISTINCT paired.event_id FROM calls own JOIN calls paired USING (call_id, call_event_id)
                   WHERE own.event_id = ? AND paired.event_id != own.event_id""", (event_id,),
            )]
            return result

    def neighbors(self, event_id: str, before: int = 1, after: int = 1) -> list[dict]:
        """Return nearby message references plus all associated call/result events."""
        if before < 0 or after < 0:
            raise ValueError("Neighbor counts must be nonnegative")
        current = self.read(event_id, end_line=1)
        with closing(self._connect()) as connection:
            offset = connection.execute("SELECT byte_offset FROM events WHERE event_id = ?", (event_id,)).fetchone()[0]
            rows = connection.execute("SELECT * FROM events WHERE byte_offset < ? ORDER BY byte_offset DESC LIMIT ?", (offset, before)).fetchall()
            rows += connection.execute("SELECT * FROM events WHERE byte_offset >= ? ORDER BY byte_offset LIMIT ?", (offset, after + 1)).fetchall()
            for paired in current["paired_events"]:
                rows.append(connection.execute("SELECT * FROM events WHERE event_id = ?", (paired,)).fetchone())
            ordered = sorted({row["event_id"]: row for row in rows}.values(), key=lambda row: row["byte_offset"])
            return [self._reference(row) for row in ordered]

    def list(self, *, after: str | None = None, limit: int = 20, role: str | None = None) -> list[dict]:
        """Browse chronology when a search misses; after is an event ID cursor."""
        if limit < 1:
            raise ValueError("Limit must be positive")
        self.sync()
        with closing(self._connect()) as connection:
            offset = -1
            if after:
                found = connection.execute("SELECT byte_offset FROM events WHERE event_id = ?", (after,)).fetchone()
                if found is None:
                    raise ValueError(f"Unknown history event: {after}")
                offset = found[0]
            rows = connection.execute(
                "SELECT * FROM events WHERE byte_offset > ? AND (? IS NULL OR role = ?) ORDER BY byte_offset LIMIT ?",
                (offset, role, role, limit),
            )
            return [self._reference(row) for row in rows]
