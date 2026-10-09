import json

from .base import (
    HTTPProvider, ProviderError, blocks, encoded_file, parse_arguments,
    post_json, received_text, validate_calls, validate_tool_pairs,
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

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
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
                   "messages": converted}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [{"name": tool["name"], "description": tool["description"],
                                 "input_schema": tool["parameters"]} for tool in tools]
        raw = post_json(self.base_url + "/messages", payload, {
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
        })
        partial_content = received_text(raw.get("content"))
        try:
            reason = raw["stop_reason"]
            if reason not in {"end_turn", "tool_use", "stop_sequence", "refusal"}:
                raise ProviderError(f"Anthropic response stopped: {reason}", raw)
            content, calls = [], []
            for block in raw["content"]:
                if block["type"] == "text":
                    content.append({"type": "text", "text": block["text"]})
                elif block["type"] == "tool_use":
                    if not isinstance(block["input"], dict):
                        raise ProviderError("Tool arguments must be a JSON object", raw)
                    calls.append({"id": block["id"], "name": block["name"],
                                  "arguments": json.dumps(block["input"], ensure_ascii=False)})
                else:
                    raise ProviderError("Unsupported Anthropic response content", raw)
            if bool(calls) != (reason == "tool_use"):
                raise ProviderError("Anthropic tool calls do not match stop_reason", raw)
            validate_calls(calls)
            result = {"role": "assistant", "content": content, "_raw": raw}
            if calls:
                result["tool_calls"] = calls
            return result
        except ProviderError as exc:
            if exc.raw is None:
                exc.raw = raw
            exc.partial_content = partial_content
            raise
        except (AttributeError, KeyError, IndexError, TypeError):
            raise ProviderError("Invalid Anthropic response", raw, partial_content) from None
