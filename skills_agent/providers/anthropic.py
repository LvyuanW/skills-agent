from contextlib import closing
from copy import deepcopy
import json

from .base import (
    DEFAULT_MAX_OUTPUT_TOKENS, HTTPProvider, ProviderError, ProviderConnectionError, ProviderOutputLimitError,
    blocks, encoded_file, parse_arguments, input_token_count, post_json,
    post_stream, stream_ended, stream_error, received_text, validate_calls, request_messages,
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
        elif kind in {"thinking", "redacted_thinking"}:
            # Unsigned reasoning from another provider is not Claude state.
            if kind == "redacted_thinking" or "signature" in block:
                result.append(deepcopy(block))
        else:
            raise ProviderError(f"Unsupported content type: {kind}")
    return result


class AnthropicProvider(HTTPProvider):
    default_base_url = "https://api.anthropic.com/v1"

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS, *, api_format: str = "auto",
                 token_counter: str = "auto", tokenizer_path=None, tokenizer_encoding=None,
                 model_context_window: int | None = None):
        super().__init__(model, api_key, base_url, max_output_tokens,
                         model_context_window=model_context_window)
        if api_format not in {"auto", "messages"} or token_counter not in {"auto", "official"}:
            raise ValueError("Anthropic uses Messages and the official token counter")

    def compact_messages(self, messages: list[dict]) -> list[dict]:
        compacted = deepcopy(messages)
        for message in compacted:
            if message.get("role") != "assistant":
                continue
            for field in ("content", "_anthropic_content"):
                if isinstance(message.get(field), list):
                    message[field] = [b for b in message[field]
                                      if b.get("type") not in {"thinking", "redacted_thinking"}]
        return compacted

    def can_compact(self, messages: list[dict]) -> bool:
        # Locally collecting tool results does not finish Claude's assistant
        # turn: that continuation must return its original thinking intact.
        latest = next((message for message in reversed(messages)
                       if message.get("role") == "assistant"), None)
        if not latest or not latest.get("tool_calls"):
            return True
        content = latest.get("_anthropic_content", blocks(latest.get("content")))
        return not any(block.get("type") in {"thinking", "redacted_thinking"}
                       for block in content)

    def count_tokens(self, system: str, messages: list[dict], tools: list[dict],
                     *, disable_thinking: bool = False) -> int:
        return input_token_count(post_json(self.base_url + "/messages/count_tokens",
                                           self._payload(system, messages, tools,
                                                         disable_thinking=disable_thinking), self._headers()))

    def _headers(self):
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}

    def _payload(self, system: str, messages: list[dict], tools: list[dict],
                 *, disable_thinking: bool = False) -> dict:
        converted = []
        for message in request_messages(messages):
            role = message["role"]
            if role == "tool":
                role = "user"
                content = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                            "content": message["content"]}]
            elif role == "assistant" and "_anthropic_content" in message:
                content = deepcopy(message["_anthropic_content"])
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
        payload = {"model": self.model, "messages": converted}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [{"name": tool["name"], "description": tool["description"],
                                 "input_schema": tool["parameters"]} for tool in tools]
        if disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        return payload

    def complete(self, system: str, messages: list[dict], tools: list[dict], *,
                 on_event=None, max_output_tokens: int | None = None,
                 disable_thinking: bool = False) -> dict:
        payload = self._payload(system, messages, tools, disable_thinking=disable_thinking)
        limit = self.max_output_tokens if max_output_tokens is None else max_output_tokens
        if limit <= 0:
            raise ValueError("max_output_tokens must be positive")
        payload.update(max_tokens=limit, stream=True)
        raw = {"content": [], "stream_events": []}
        active = set()
        inputs = {}
        started = stopped = False
        try:
            with closing(post_stream(self.base_url + "/messages", payload, self._headers())) as stream:
                for event in stream:
                    if not isinstance(event, dict):
                        raise ProviderError("Invalid Anthropic stream event")
                    raw["stream_events"].append(deepcopy(event))
                    kind = event["type"]
                    if kind == "error":
                        failure = stream_error(event, "Anthropic stream returned an error",
                                               self.base_url + "/messages", self._headers())
                        raw["stream_events"][-1] = deepcopy(event)
                        raise failure
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
                        elif block["type"] == "thinking":
                            if not isinstance(block.get("thinking"), str):
                                raise ProviderError("Invalid Anthropic thinking")
                            if block["thinking"] and on_event:
                                on_event({"type": "thinking_delta", "text": block["thinking"]})
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
                                elif delta_kind == "thinking_delta" and value and on_event:
                                    on_event({"type": "thinking_delta", "text": value})
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
                raise stream_ended(stream, "Anthropic stream ended before message_stop")
            reason = raw["stop_reason"]
            if reason in {"max_tokens", "model_context_window_exceeded"}:
                raise ProviderOutputLimitError(f"Anthropic response stopped: {reason}", finish_reason=reason)
            if reason not in {"end_turn", "tool_use", "stop_sequence", "refusal"}:
                raise ProviderError(f"Anthropic response stopped: {reason}", finish_reason=reason)
            content, calls = [], []
            for block in raw["content"]:
                if block["type"] == "text":
                    content.append({"type": "text", "text": block["text"]})
                elif block["type"] == "tool_use":
                    calls.append({"id": block["id"], "name": block["name"],
                                  "arguments": json.dumps(block["input"], ensure_ascii=False)})
                elif block["type"] in {"thinking", "redacted_thinking"}:
                    content.append(deepcopy(block))
                else:
                    raise ProviderError("Unsupported Anthropic response content")
            if bool(calls) != (reason == "tool_use"):
                raise ProviderError("Anthropic tool calls do not match stop_reason")
            validate_calls(calls)
            result = {"role": "assistant", "content": content, "_raw": raw,
                      "_anthropic_content": deepcopy(raw["content"])}
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
