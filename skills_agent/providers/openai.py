from pathlib import Path

from .base import (
    HTTPProvider, ProviderError, blocks, encoded_file, parse_arguments,
    post_json, received_text, validate_calls, validate_tool_pairs,
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

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
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
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": tool["name"], "description": tool["description"],
                "parameters": tool["parameters"],
            }} for tool in tools]
        raw = post_json(self.base_url + "/chat/completions", payload,
                        {"Authorization": f"Bearer {self.api_key}"})
        partial_content = []
        try:
            choice = raw["choices"][0]
            message = choice["message"]
            partial_content = received_text(message.get("content"))
            if isinstance(message.get("refusal"), str):
                partial_content.append({"type": "text", "text": message["refusal"]})
            reason = choice["finish_reason"]
            if reason not in {"stop", "tool_calls"}:
                raise ProviderError(f"OpenAI response stopped: {reason}", raw)
            content = []
            for block in blocks(message.get("content")):
                if block["type"] == "text":
                    content.append({"type": "text", "text": block["text"]})
                elif block["type"] == "refusal":
                    content.append({"type": "text", "text": block["refusal"]})
                else:
                    raise ProviderError("Unsupported OpenAI response content", raw)
            if message.get("refusal"):
                content.append({"type": "text", "text": message["refusal"]})
            result = {"role": "assistant", "content": content, "_raw": raw}
            calls = []
            for call in message.get("tool_calls") or []:
                if call["type"] != "function":
                    raise ProviderError("Unsupported OpenAI tool call type", raw)
                function = call["function"]
                parse_arguments(function["arguments"])
                calls.append({"id": call["id"], "name": function["name"],
                              "arguments": function["arguments"]})
            if bool(calls) != (reason == "tool_calls"):
                raise ProviderError("OpenAI tool calls do not match finish_reason", raw)
            validate_calls(calls)
            if calls:
                result["tool_calls"] = calls
            return result
        except ProviderError as exc:
            if exc.raw is None:
                exc.raw = raw
            exc.partial_content = partial_content
            raise
        except (AttributeError, KeyError, IndexError, TypeError):
            raise ProviderError("Invalid OpenAI response", raw, partial_content) from None
