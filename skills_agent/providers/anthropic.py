from contextlib import closing
from copy import deepcopy
import json

from .base import (
    HTTPProvider, ProviderError, blocks, encoded_file, parse_arguments,
    post_stream, received_text, validate_calls, validate_tool_pairs,
)


def _content(content) -> list[dict]:
    result = []
    for block in blocks(content):
        kind = block["type"]
        if kind == "text":
            if block["text"]:
                result.append({"type": "text", "text": block["text"]})
        elif kind == "image" or (kind == "file" and block["mime_type"] == "application/pdf"):
            result.append({"type": "image" if kind == "image" else "document", "source": {
                "type": "base64", "media_type": block["mime_type"],
                "data": encoded_file(block),
            }})
        elif kind == "file":
            result.append({"type": "text", "text": f"File: {block['path']}"})
        else:
            raise ProviderError(f"Unsupported content type: {kind}")
    return result


class AnthropicProvider(HTTPProvider):
    default_base_url = "https://api.anthropic.com/v1"

    def complete(self, system: str, messages: list[dict], tools: list[dict], *,
                 on_event=None) -> dict:
        validate_tool_pairs(messages)
        converted = []
        for message in messages:
            role = message["role"]
            if role == "tool":
                role = "user"
                content = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                            "content": message["content"]}]
            else:
                content = _content(message.get("content"))
                for call in message.get("tool_calls", []):
                    content.append({"type": "tool_use", "id": call["id"],
                                    "name": call["name"],
                                    "input": parse_arguments(call["arguments"])})
            if role not in {"user", "assistant"}:
                raise ProviderError(f"Unsupported Anthropic message role: {role}")
            if role == "assistant" and not content:
                continue
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"].extend(content)
            else:
                converted.append({"role": role, "content": content})
        payload = {"model": self.model, "max_tokens": self.max_output_tokens,
                   "messages": converted, "stream": True}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [{"name": tool["name"], "description": tool["description"],
                                 "input_schema": tool["parameters"]} for tool in tools]
        raw = {"content": [], "stream_events": []}
        active = set()
        inputs = {}
        started = stopped = False
        try:
            with closing(post_stream(self.base_url + "/messages", payload, {
                "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
            })) as stream:
                for event in stream:
                    if not isinstance(event, dict):
                        raise ProviderError("Invalid Anthropic stream event")
                    raw["stream_events"].append(deepcopy(event))
                    kind = event["type"]
                    if kind == "error":
                        raise ProviderError("Anthropic stream returned an error")
                    if kind == "message_start":
                        if started or event["message"].get("content") != []:
                            raise ProviderError("Invalid Anthropic message start")
                        raw.update(deepcopy(event["message"]))
                        started = True
                    elif kind == "content_block_start":
                        index = event["index"]
                        if not started or index != len(raw["content"]):
                            raise ProviderError("Invalid Anthropic content block index")
                        block = deepcopy(event["content_block"])
                        raw["content"].append(block)
                        active.add(index)
                        if block["type"] == "text":
                            if not isinstance(block["text"], str):
                                raise ProviderError("Invalid Anthropic text")
                            if on_event:
                                if any(b["type"] == "text" for b in raw["content"][:-1]):
                                    on_event({"type": "text_delta", "text": "\n"})
                                if block["text"]:
                                    on_event({"type": "text_delta", "text": block["text"]})
                        elif block["type"] == "tool_use":
                            inputs[index] = []
                            if on_event:
                                call = {"type": "tool_call_delta", "index": index,
                                        "id": block["id"], "name": block["name"]}
                                if block.get("input"):
                                    call["arguments"] = json.dumps(block["input"], ensure_ascii=False)
                                on_event(call)
                    elif kind == "content_block_delta":
                        index, delta = event["index"], event["delta"]
                        if index not in active:
                            raise ProviderError("Anthropic delta has no open content block")
                        block = raw["content"][index]
                        delta_kind = delta["type"]
                        fields = {"text_delta": ("text", "text"),
                                  "thinking_delta": ("thinking", "thinking"),
                                  "signature_delta": ("thinking", "signature"),
                                  "input_json_delta": ("tool_use", "partial_json")}
                        if delta_kind in fields:
                            block_kind, field = fields[delta_kind]
                            value = delta[field]
                            if block["type"] != block_kind or not isinstance(value, str):
                                raise ProviderError("Invalid Anthropic content delta")
                            if delta_kind == "input_json_delta":
                                inputs[index].append(value)
                                if on_event:
                                    on_event({"type": "tool_call_delta", "index": index,
                                              "arguments": value})
                            else:
                                block[field] = block.get(field, "") + value
                                if delta_kind == "text_delta" and value and on_event:
                                    on_event({"type": "text_delta", "text": value})
                    elif kind == "content_block_stop":
                        index = event["index"]
                        if index not in active:
                            raise ProviderError("Anthropic stop has no open content block")
                        block = raw["content"][index]
                        if block["type"] == "tool_use":
                            if inputs[index]:
                                block["input"] = parse_arguments("".join(inputs[index]))
                            if not isinstance(block["input"], dict):
                                raise ProviderError("Tool arguments must be a JSON object")
                        active.remove(index)
                    elif kind == "message_delta":
                        if not started:
                            raise ProviderError("Anthropic delta has no message start")
                        raw.update(deepcopy(event["delta"]))
                        raw.setdefault("usage", {}).update(deepcopy(event.get("usage", {})))
                    elif kind == "message_stop":
                        if not started or active:
                            raise ProviderError("Incomplete Anthropic content blocks")
                        stopped = True
                        break
            if not stopped:
                raise ProviderError("Anthropic stream ended before message_stop")
            reason = raw["stop_reason"]
            if reason not in {"end_turn", "tool_use", "stop_sequence", "refusal"}:
                raise ProviderError(f"Anthropic response stopped: {reason}")
            content, calls = [], []
            for block in raw["content"]:
                if block["type"] == "text":
                    content.append({"type": "text", "text": block["text"]})
                elif block["type"] == "tool_use":
                    calls.append({"id": block["id"], "name": block["name"],
                                  "arguments": json.dumps(block["input"], ensure_ascii=False)})
                elif block["type"] not in {"thinking", "redacted_thinking"}:
                    raise ProviderError("Unsupported Anthropic response content")
            if bool(calls) != (reason == "tool_use"):
                raise ProviderError("Anthropic tool calls do not match stop_reason")
            validate_calls(calls)
            result = {"role": "assistant", "content": content, "_raw": raw}
            if calls:
                result["tool_calls"] = calls
            return result
        except (ProviderError, KeyboardInterrupt) as exc:
            exc.raw = raw
            exc.partial_content = received_text(raw.get("content"))
            raise
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("Invalid Anthropic stream", raw,
                                received_text(raw.get("content"))) from None
