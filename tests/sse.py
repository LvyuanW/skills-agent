"""Encode scripted model responses as HTTP SSE test fixtures."""

import copy
import json


def frame(value):
    data = "[DONE]" if value is None else json.dumps(value, ensure_ascii=False)
    return f"data: {data}\n\n".encode("utf-8")


def responses_from_chat(response):
    """Translate a simple scripted fixture to a native Responses envelope."""
    choice = response["choices"][0]
    message = choice["message"]
    output = []
    if message.get("reasoning_content"):
        output.append({"type": "reasoning", "id": "reasoning", "encrypted_content": "opaque",
                       "summary": [{"type": "summary_text", "text": message["reasoning_content"]}]})
    if message.get("content"):
        output.append({"type": "message", "id": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": message["content"], "annotations": []}]})
    for call in message.get("tool_calls", []):
        output.append({"type": "function_call", "id": "fc_" + call["id"], "call_id": call["id"],
                       "name": call["function"]["name"], "arguments": call["function"]["arguments"],
                       "status": "completed"})
    result = {"id": "response", "status": "completed", "output": output,
              "usage": {"input_tokens": response.get("usage", {}).get("prompt_tokens", 0),
                        "output_tokens": response.get("usage", {}).get("completion_tokens", 0)}}
    if choice["finish_reason"] == "length":
        result.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    return result


def stream_response(response):
    response = copy.deepcopy(response)
    if "output" in response:
        result = frame({"type": "response.created", "response": {"id": response["id"], "status": "in_progress", "output": []}})
        for index, item in enumerate(response["output"]):
            initial = {**item, "arguments": ""} if item["type"] == "function_call" else item
            result += frame({"type": "response.output_item.added", "output_index": index, "item": initial})
            if item["type"] == "message":
                for block_index, block in enumerate(item["content"]):
                    result += frame({"type": "response.output_text.delta", "output_index": index,
                                     "content_index": block_index, "delta": block["text"]})
            elif item["type"] == "reasoning":
                for block_index, block in enumerate(item["summary"]):
                    result += frame({"type": "response.reasoning_summary_text.delta", "output_index": index,
                                     "summary_index": block_index, "delta": block["text"]})
            elif item["type"] == "function_call":
                result += frame({"type": "response.function_call_arguments.delta", "output_index": index,
                                 "delta": item["arguments"]})
            result += frame({"type": "response.output_item.done", "output_index": index, "item": item})
        return result + frame({"type": "response." + response["status"], "response": response})
    if "choices" in response:
        choice = response["choices"][0]
        delta = choice["message"]
        for index, call in enumerate(delta.get("tool_calls", [])):
            call["index"] = index
        result = frame({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
        result += frame({"choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}]})
        if "usage" in response:
            result += frame({"choices": [], "usage": response["usage"]})
        return result + frame(None)
    content = response.pop("content", [])
    reason = response.pop("stop_reason")
    usage = response.pop("usage", {})
    result = frame({"type": "message_start", "message": {
        **response, "role": "assistant", "content": [], "stop_reason": None,
        "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": 0},
    }})
    for index, block in enumerate(content):
        result += frame({"type": "content_block_start", "index": index, "content_block": block})
        result += frame({"type": "content_block_stop", "index": index})
    result += frame({"type": "message_delta", "delta": {"stop_reason": reason}, "usage": usage})
    return result + frame({"type": "message_stop"})
