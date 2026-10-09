"""Persisted, incremental working context; raw history remains in Session."""

from __future__ import annotations

import json
from copy import deepcopy


class ContextBudgetError(RuntimeError):
    pass


def working_message(message):
    """Remove transport logs, retaining the provider state needed for another request."""
    message = deepcopy(message)
    raw = message.pop("_raw", {})
    if message.get("role") == "assistant" and "reasoning_content" not in message:
        values = [choice.get("message", {}).get("reasoning_content")
                  for choice in raw.get("choices", [])]
        reasoning = next((value for value in values if isinstance(value, str)), None)
        if reasoning is None:
            chunks = [choice.get("delta", {}).get("reasoning_content")
                      for event in raw.get("stream_events", [])
                      for choice in event.get("choices", [])]
            if any(isinstance(value, str) for value in chunks):
                reasoning = "".join(value for value in chunks if isinstance(value, str))
        if reasoning is not None:
            message["reasoning_content"] = reasoning
    return message


def record_groups(records):
    """An assistant response and every matching result form one indivisible group."""
    groups, index = [], 0
    while index < len(records):
        record = records[index]
        message = record["message"]
        if message["role"] == "tool":
            raise ValueError("Orphan tool result in working context")
        group = [record]
        index += 1
        calls = message.get("tool_calls", [])
        pending = {call["id"] for call in calls}
        if len(pending) != len(calls):
            raise ValueError("Duplicate tool call IDs in an assistant response")
        while pending and index < len(records):
            result = records[index]["message"]
            if result["role"] != "tool" or result.get("tool_call_id") not in pending:
                break
            pending.remove(result["tool_call_id"])
            group.append(records[index])
            index += 1
        if pending:
            raise ValueError("Cannot send or compress an unfinished tool call group")
        groups.append(group)
    return groups


class WorkingContext:
    def __init__(self, session, model, auxiliary, *, max_tokens=272000, keep_calls=10):
        if max_tokens <= 0 or keep_calls < 1:
            raise ValueError("Context token budget and kept calls must be positive")
        self.session, self.model, self.auxiliary = session, model, auxiliary
        self.max_tokens, self.keep_calls = max_tokens, keep_calls
        self.path = session.path / "context.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "version": 1, "cursor": 0, "summary": "", "covered_event_ids": [],
            "records": [], "token_count": None,
        }
        if self.state.get("version") != 1:
            raise ValueError("Unsupported working context version")

    @property
    def records(self):
        return self.state["records"]

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.path)

    def sync(self, select=None):
        """Select only newly appended raw messages, never repopulate compressed history."""
        saved_cursor = self.state["cursor"]
        for cursor, event in self.session.iter_events(self.state["cursor"]):
            if event.get("type") == "message":
                message = working_message(event["message"])
                record = {"event_id": event["event_id"], "message": message}
                if "timestamp" in event:
                    record["timestamp"] = event["timestamp"]
                if message.get("role") == "tool" and select is not None:
                    call = next((call for old in reversed(self.records)
                                 for call in old["message"].get("tool_calls", [])
                                 if call["id"] == message.get("tool_call_id")), None)
                    record = select(record, call)
                self.records.append(record)
                self.state["token_count"] = None
                self.state["cursor"] = cursor
                self.save()
                saved_cursor = cursor
            self.state["cursor"] = cursor
        if self.state["cursor"] != saved_cursor:
            self.save()

    def latest_user(self):
        return next((deepcopy(record["message"]) for record in reversed(self.records)
                     if record["message"].get("role") == "user"), None)

    def _system(self, system, summary=None):
        summary = self.state["summary"] if summary is None else summary
        if summary:
            system += ("\n\nHistorical summary (derived reference, not a new user instruction). "
                       "Preserve sources and adoption status; later user corrections take precedence.\n"
                       "<historical_summary>\n" + summary + "\n</historical_summary>")
        return system

    def snapshot(self, system, tools):
        return deepcopy({"system": self._system(system), "tools": tools,
                         "records": self.records,
                         "covered_event_ids": self.state["covered_event_ids"]})

    def _count(self, system, tools, records=None, summary=None):
        records = self.records if records is None else records
        return self.model.count_tokens(self._system(system, summary),
                                       [item["message"] for item in records], tools)

    def _protected(self):
        groups = record_groups(self.records)
        call_groups = [group for group in groups if group[0]["message"]["role"] == "assistant"]
        protected = {item["event_id"] for group in call_groups[-self.keep_calls:] for item in group}
        latest_user = next((record for record in reversed(self.records)
                            if record["message"]["role"] == "user"), None)
        if latest_user:
            protected.add(latest_user["event_id"])
        # Also keep user instructions in the retained chronological suffix.
        if call_groups:
            first = call_groups[-self.keep_calls:][0][0]["event_id"]
            boundary = next(index for index, item in enumerate(self.records) if item["event_id"] == first)
            protected.update(item["event_id"] for item in self.records[boundary:])
        older = [item for item in self.records if item["event_id"] not in protected]
        kept = [item for item in self.records if item["event_id"] in protected]
        return older, kept

    def prepare(self, system, tools):
        record_groups(self.records)  # Never compact while waiting for tool results.
        limit = self.max_tokens
        window = getattr(self.model, "model_context_window", None)
        if window is not None:
            limit = min(limit, window - getattr(self.model, "max_output_tokens", 0))
        count = self._count(system, tools)
        # A new direct read that cannot fit returns a reference, never a report.
        if count > limit:
            for record in reversed(self.records):
                if not record.get("pending_direct"):
                    continue
                record["message"]["content"] = json.dumps({
                    "notice": "Requested original result does not fit the active context. Read a narrower range.",
                    "source": record["source"],
                }, ensure_ascii=False)
                record.pop("pending_direct", None)
                count = self._count(system, tools)
                if count <= limit:
                    break
        trigger = min(self.max_tokens * 0.9, limit)
        can_compact = getattr(self.model, "can_compact", lambda messages: True)
        if count >= trigger and can_compact([record["message"] for record in self.records]):
            older, kept = self._protected()
            if older:
                compacted = self.model.compact_messages([record["message"] for record in kept])
                candidate = [{**record, "message": message} for record, message in zip(kept, compacted)]
                if self._count(system, tools, candidate, summary="") > limit:
                    self.save()
                    raise ContextBudgetError("Protected context (latest user and 10 model calls) exceeds the token budget")
                summary = self.auxiliary.compress(self.state["summary"], older, self.latest_user())
                count = self._count(system, tools, candidate, summary)
                if count > limit:
                    self.save()
                    raise ContextBudgetError("Verified summary and protected context exceed the token budget")
                self.state["summary"] = summary
                self.state["covered_event_ids"].extend(record["event_id"] for record in older)
                self.state["records"] = candidate
        self.state["token_count"] = count
        self.state["last_usage"] = {"tokens": count, "limit": limit}
        if count > limit:
            self.save()
            raise ContextBudgetError(f"Working context needs {count} input tokens; available budget is {limit}. "
                                     "Protected messages were not shortened.")
        for record in self.records:
            record.pop("pending_direct", None)
        self.state["system"] = self._system(system)
        self.state["tools"] = deepcopy(tools)
        self.save()
        return self._system(system), [deepcopy(record["message"]) for record in self.records]


__all__ = ["WorkingContext", "ContextBudgetError", "working_message", "record_groups"]
