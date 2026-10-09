from contextlib import closing
from copy import deepcopy
from pathlib import Path

from .base import (
    HTTPProvider, ProviderError, blocks, encoded_file, parse_arguments,
    post_stream, validate_calls, validate_tool_pairs,
)


def _content(content) -> list[dict]:
    result = []
    for block in blocks(content):
        kind = block["type"]
        if kind == "text":
            result.append({"type": "text", "text": block["text"]})
        elif kind == "image":
            result.append({"type": "image_url", "image_url": {
                "url": f"data:{block['mime_type']};base64,{encoded_file(block)}",
            }})
        elif kind == "file" and block["mime_type"] == "application/pdf":
            result.append({"type": "file", "file": {
                "filename": block.get("name") or Path(block["path"]).name,
                "file_data": f"data:application/pdf;base64,{encoded_file(block)}",
            }})
        elif kind == "file":
            result.append({"type": "text", "text": f"File: {block['path']}"})
        else:
            raise ProviderError(f"Unsupported content type: {kind}")
    return result


class OpenAIProvider(HTTPProvider):
    default_base_url = "https://api.openai.com/v1"

    def complete(self, system: str, messages: list[dict], tools: list[dict],
                 *, on_event=None) -> dict:
        validate_tool_pairs(messages)
        converted = [{"role": "system", "content": system}] if system else []
        for message in messages:
            role = message["role"]
            item = {"role": role}
            if role == "tool":
                item.update(content=message["content"], tool_call_id=message["tool_call_id"])
            else:
                item["content"] = _content(message.get("content")) or None
                if message.get("tool_calls"):
                    item["tool_calls"] = [{
                        "id": call["id"], "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    } for call in message["tool_calls"]]
            converted.append(item)
        payload = {
            "model": self.model, "messages": converted,
            "max_completion_tokens": self.max_output_tokens,
            "stream": True, "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": tool["name"], "description": tool["description"],
                "parameters": tool["parameters"],
            }} for tool in tools]
        message = {"role": "assistant", "content": None}
        choice = {"index": 0, "message": message, "finish_reason": None}
        raw = {"choices": [choice], "stream_events": []}
        content, indexed_calls = [], {}
        last_channel = None
        done = False

        def text_delta(value, channel):
            nonlocal last_channel
            if value is None:
                return
            if not isinstance(value, str):
                raise ProviderError("Invalid OpenAI text delta")
            if not value:
                return
            separator = bool(content) and last_channel != channel
            if content and last_channel == channel:
                content[-1]["text"] += value
            else:
                content.append({"type": "text", "text": value})
            last_channel = channel
            if on_event:
                if separator:
                    on_event({"type": "text_delta", "text": "\n"})
                on_event({"type": "text_delta", "text": value})

        try:
            with closing(post_stream(self.base_url + "/chat/completions", payload,
                                     {"Authorization": f"Bearer {self.api_key}"})) as stream:
                for event in stream:
                    if event is None:
                        done = True
                        break
                    raw["stream_events"].append(event)
                    if "error" in event:
                        raise ProviderError("API returned an error")
                    for key, value in event.items():
                        if key != "choices":
                            if key != "usage" or value is not None:
                                raw[key] = deepcopy(value)
                    choices = event["choices"]
                    if not isinstance(choices, list) or len(choices) > 1:
                        raise ProviderError("Invalid OpenAI stream choices")
                    if not choices:
                        continue
                    part = choices[0]
                    if part.get("index", 0) != 0:
                        raise ProviderError("Unexpected OpenAI choice index")
                    delta = part["delta"]
                    if not isinstance(delta, dict):
                        raise ProviderError("Invalid OpenAI stream delta")
                    if choice["finish_reason"] is not None and any(delta.values()):
                        raise ProviderError("OpenAI delta received after finish")
                    for key, value in delta.items():
                        if key == "content":
                            if isinstance(value, list):
                                if message["content"] is None:
                                    message["content"] = []
                                if not isinstance(message["content"], list):
                                    raise ProviderError("Inconsistent OpenAI content deltas")
                                message["content"].extend(deepcopy(value))
                                for index, block in enumerate(value):
                                    kind = block["type"]
                                    if kind not in {"text", "refusal"}:
                                        raise ProviderError("Unsupported OpenAI response content")
                                    text_delta(block[kind], (key, len(raw["stream_events"]), index))
                            elif value is not None:
                                if not isinstance(value, str) or isinstance(message["content"], list):
                                    raise ProviderError("Invalid OpenAI content delta")
                                message["content"] = (message["content"] or "") + value
                                text_delta(value, key)
                        elif key == "refusal":
                            if value is not None:
                                if not isinstance(value, str):
                                    raise ProviderError("Invalid OpenAI refusal delta")
                                message[key] = message.get(key, "") + value
                                text_delta(value, key)
                        elif key == "tool_calls":
                            if value is None:
                                continue
                            if not isinstance(value, list):
                                raise ProviderError("Invalid OpenAI tool delta")
                            for fragment in value:
                                index = fragment["index"]
                                if type(index) is not int or index < 0:
                                    raise ProviderError("Invalid OpenAI tool index")
                                call = indexed_calls.setdefault(index, {
                                    "id": "", "type": None,
                                    "function": {"name": "", "arguments": ""},
                                })
                                message["tool_calls"] = [indexed_calls[i] for i in sorted(indexed_calls)]
                                observed = {"type": "tool_call_delta", "index": index}
                                for name in ("id", "type"):
                                    piece = fragment.get(name)
                                    if piece is not None:
                                        if not isinstance(piece, str):
                                            raise ProviderError("Invalid OpenAI tool delta")
                                        if name == "id":
                                            call[name] += piece
                                            observed[name] = piece
                                        elif piece != "function":
                                            raise ProviderError("Unsupported OpenAI tool call type")
                                        else:
                                            call[name] = piece
                                function = fragment.get("function") or {}
                                for name in ("name", "arguments"):
                                    piece = function.get(name)
                                    if piece is not None:
                                        if not isinstance(piece, str):
                                            raise ProviderError("Invalid OpenAI tool delta")
                                        call["function"][name] += piece
                                        observed[name] = piece
                                if on_event:
                                    on_event(observed)
                        elif isinstance(value, str) and key != "role":
                            message[key] = message.get(key, "") + value
                        else:
                            message[key] = deepcopy(value)
                    reason = part.get("finish_reason")
                    if reason is not None:
                        if choice["finish_reason"] is not None:
                            raise ProviderError("Duplicate OpenAI finish reason")
                        choice["finish_reason"] = reason
            if not done:
                raise ProviderError("OpenAI stream ended before [DONE]")
            reason = choice["finish_reason"]
            if reason not in {"stop", "tool_calls"}:
                raise ProviderError(f"OpenAI response stopped: {reason}")
            if sorted(indexed_calls) != list(range(len(indexed_calls))):
                raise ProviderError("Incomplete OpenAI tool call indices")
            calls = []
            for call in message.get("tool_calls", []):
                if call["type"] != "function":
                    raise ProviderError("Unsupported OpenAI tool call type")
                function = call["function"]
                parse_arguments(function["arguments"])
                calls.append({"id": call["id"], "name": function["name"],
                              "arguments": function["arguments"]})
            if bool(calls) != (reason == "tool_calls"):
                raise ProviderError("OpenAI tool calls do not match finish_reason")
            validate_calls(calls)
            result = {"role": "assistant", "content": content, "_raw": raw}
            if calls:
                result["tool_calls"] = calls
            return result
        except (ProviderError, KeyboardInterrupt) as exc:
            exc.raw = raw
            exc.partial_content = content
            raise
        except (AttributeError, KeyError, IndexError, TypeError):
            raise ProviderError("Invalid OpenAI response", raw, content) from None
