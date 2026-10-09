"""Encode scripted model responses as HTTP SSE test fixtures."""

import copy
import json


def frame(value):
    data = "[DONE]" if value is None else json.dumps(value, ensure_ascii=False)
    return f"data: {data}\n\n".encode("utf-8")


def stream_response(response):
    response = copy.deepcopy(response)
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
