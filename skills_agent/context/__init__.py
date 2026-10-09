"""Deterministic, character-budgeted model context; session data is never edited."""

from __future__ import annotations

import copy
import json
import re


class ContextBudgetError(ValueError):
    pass


def _text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content if block.get("type") == "text")


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[… omitted …]\n"
    if limit <= len(marker):
        return text[:limit]
    available = limit - len(marker)
    head = available * 2 // 3
    return text[:head] + marker + text[-(available - head):]


def _groups(messages: list[dict]) -> list[list[dict]]:
    """Keep each assistant tool call and every matching result together."""
    groups = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "tool":
            index += 1
            continue
        calls = message.get("tool_calls") or []
        if not calls:
            groups.append([message])
            index += 1
            continue
        ids = [call["id"] for call in calls]
        if message.get("role") != "assistant" or len(set(ids)) != len(ids):
            raise ValueError("Invalid assistant tool call IDs")
        end = index + 1
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
        results = messages[index + 1:end]
        result_ids = [result.get("tool_call_id") for result in results]
        if (len(results) != len(calls) or any(result.get("role") != "tool" for result in results)
                or set(result_ids) != set(ids) or len(set(result_ids)) != len(result_ids)):
            # A stopped run may have persisted a call before every result arrived.
            index = end
            continue
        groups.append([message, *results])
        index = end
    return groups


def _flatten(groups: list[list[dict]]) -> list[dict]:
    return [message for group in groups for message in group]


def _compact_commands(message: dict, limit: int) -> None:
    """Excerpt completed commands in a context copy, never executable input."""
    for call in message.get("tool_calls", []):
        if call["name"] not in {"shell", "sandbox"}:
            continue
        try:
            arguments = json.loads(call["arguments"])
        except (TypeError, ValueError):
            continue
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if not isinstance(command, str):
            continue
        marker = f"[Already-executed command excerpt; full source: session tool call {call['id']}]\n"
        is_excerpt = command.startswith(marker)
        if len(command) <= limit or (not is_excerpt and len(command) <= 128):
            continue
        # A second, tighter pass must keep one marker and the original call ID.
        source = command[len(marker):] if is_excerpt else command
        arguments["command"] = marker + source[:max(0, limit - len(marker))]
        call["arguments"] = json.dumps(arguments, ensure_ascii=False)


def _compact(groups: list[list[dict]], limit: int, attachment_limit: int | None = None) -> list[list[dict]]:
    result = copy.deepcopy(groups)
    for message in _flatten(result):
        _compact_commands(message, limit)
        content = message.get("content", "")
        if isinstance(content, str):
            if message.get("role") != "user":
                message["content"] = _excerpt(content, limit)
        else:
            for block in content:
                if block.get("type") != "text":
                    continue
                if message.get("role") != "user":
                    block["text"] = _excerpt(block.get("text", ""), limit)
                elif block.get("attachment"):
                    # Path remains available even if the attachment's text is omitted.
                    label = f"Attachment: {block.get('name', '')}\nPath: {block['path']}\n"
                    original = block.get("text", "")
                    body = original[len(label):].lstrip("\n") if original.startswith(label) else original
                    block["text"] = label + "\n" + _excerpt(body, limit if attachment_limit is None else attachment_limit)
    return result


def _keywords(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]{3,}", text.lower()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(run[index:index + 2] for index in range(len(run) - 1))
    return words


def _summary(groups: list[list[dict]]) -> str:
    lines = []
    for message in _flatten(groups):
        role = message.get("role", "")
        if message.get("tool_calls"):
            names = ", ".join(f"{call['name']}({_excerpt(call['arguments'], 180)})" for call in message["tool_calls"])
            lines.append(f"assistant tools: {names}")
        text = _text(message).strip()
        if text:
            label = message.get("name") or role
            lines.append(f"{label}: {_excerpt(text, 260)}")
    return "Extracted earlier context:\n" + "\n".join(lines)


class ContextBuilder:
    def __init__(self, max_chars: int = 48000):
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars

    @staticmethod
    def size(messages: list[dict], system: str = "", tools: list[dict] | None = None) -> int:
        """Count JSON characters, including instructions and schemas, not model tokens.

        Image/PDF blocks count as metadata; this cannot measure native media tokens.
        """
        return len(json.dumps({"system": system, "tools": tools or [], "messages": messages},
                              ensure_ascii=False, separators=(",", ":")))

    def build(self, messages: list[dict], system: str = "", tools: list[dict] | None = None) -> list[dict]:
        clean = []
        for message in messages:
            item = {key: copy.deepcopy(value) for key, value in message.items() if key != "_raw"}
            if item.get("role") == "assistant" and "reasoning_content" not in item:
                # Sessions saved before reasoning round-trip support kept it only in _raw.
                raw = message.get("_raw") or {}
                choices = raw.get("choices") or []
                source = choices[0].get("message", {}) if choices else {}
                reasoning = source.get("reasoning_content")
                # Older stream assembly could overwrite earlier fragments with null.
                parts = [choice.get("delta", {}).get("reasoning_content")
                         for event in raw.get("stream_events", [])
                         for choice in event.get("choices", []) if choice.get("index", 0) == 0]
                parts = [part for part in parts if isinstance(part, str)]
                if parts:
                    reasoning = "".join(parts)
                if isinstance(reasoning, str):
                    item["reasoning_content"] = reasoning
            clean.append(item)
        groups = _groups(clean)
        clean = _flatten(groups)
        measure = lambda selected: self.size(selected, system, tools)
        if measure([]) > self.max_chars:
            raise ContextBudgetError("System instructions and tool schemas exceed max_chars")
        if measure(clean) <= self.max_chars:
            return clean

        turns: list[list[list[dict]]] = []
        for group in groups:
            if not turns or group[0].get("role") == "user":
                turns.append([])
            turns[-1].append(group)
        if not turns:
            return []

        current = turns[-1]
        has_user = current[0][0].get("role") == "user"
        user_groups = current[:1] if has_user else []
        minimum_user = _flatten(_compact(user_groups, 0))
        minimum_cost = measure(minimum_user)
        if minimum_cost > self.max_chars:
            raise ContextBudgetError("Current user text and attachment metadata exceed max_chars; shorten the text or increase the budget")
        reserve = min(2000, self.max_chars // 8, (self.max_chars - minimum_cost) // 4)
        target = self.max_chars - reserve
        compacted = copy.deepcopy(current)
        for limit in (8000, 4000, 2000, 1000, 500, 256, 128):
            if measure(_flatten(compacted)) <= target:
                break
            compacted = _compact(current, limit)
        if measure(_flatten(compacted)) > target:
            compacted = _compact(current, 128, attachment_limit=0)

        required = {0} if has_user else set()
        tool_groups = [index for index, group in enumerate(current) if group[0].get("tool_calls")]
        if tool_groups:
            required.add(tool_groups[-1])
        retained = list(range(len(current)))
        dropped_current = []
        while measure(_flatten(compacted)) > target:
            removable = next((index for index, original_index in enumerate(retained) if original_index not in required), None)
            if removable is None:
                break
            dropped_current.append(current[retained.pop(removable)])
            del compacted[removable]
        if measure(_flatten(compacted)) > target:
            compacted = _compact(compacted, 0)
        if measure(_flatten(compacted)) > target:
            minimum_cost = measure(_flatten(compacted))
            if minimum_cost > self.max_chars:
                raise ContextBudgetError("Current user message and latest tool call metadata (including reasoning_content) exceed max_chars")
            target = minimum_cost
            reserve = self.max_chars - minimum_cost

        query = _keywords(_text(current[0][0]))
        history = turns[:-1]
        # Recent turns first; remaining turns are ranked by lexical overlap.
        recent = list(range(max(0, len(history) - 2), len(history)))[::-1]
        relevant = sorted(range(max(0, len(history) - 2)),
                          key=lambda index: (len(query & _keywords(_text(history[index][0][0]))), index), reverse=True)
        selected: dict[int, list[list[dict]]] = {}
        for index in recent + relevant:
            candidate = _compact(history[index], 1000)
            proposed = {**selected, index: candidate}
            ordered = [group for key in sorted(proposed) for group in proposed[key]] + compacted
            if measure(_flatten(ordered)) <= target:
                selected[index] = candidate

        ordered = [group for key in sorted(selected) for group in selected[key]] + compacted
        result = _flatten(ordered)
        omitted = [group for index, turn in enumerate(history) if index not in selected for group in turn]
        omitted.extend(dropped_current)
        if omitted:
            summary = _summary(omitted)
            low, high = 0, min(len(summary), reserve)
            while low < high:
                middle = (low + high + 1) // 2
                memory = {"role": "user", "content": [{"type": "text", "text": _excerpt(summary, middle)}]}
                if measure([memory, *result]) <= self.max_chars:
                    low = middle
                else:
                    high = middle - 1
            if low:
                result.insert(0, {"role": "user", "content": [{"type": "text", "text": _excerpt(summary, low)}]})
        return result


__all__ = ["ContextBuilder", "ContextBudgetError"]
