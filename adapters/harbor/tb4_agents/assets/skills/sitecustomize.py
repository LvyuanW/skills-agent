"""Metadata-only auditing for the unmodified skills-agent provider transport.

Loaded only when the adapter prepends this directory to PYTHONPATH. No prompt,
response text, image bytes, URL query, headers, or credentials are persisted.
"""

import json
import os
import time
import uuid
from urllib.parse import urlsplit


def _write(record):
    path = os.environ.get("TB4_API_AUDIT_LOG")
    if not path:
        return
    record.update(timestamp=time.time(), pid=os.getpid())
    data = (json.dumps(record, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)


def _safe_usage(value):
    if not isinstance(value, dict):
        return {}
    return {
        key: _safe_usage(item) if isinstance(item, dict) else item
        for key, item in value.items()
        if key in {
            "prompt_tokens", "completion_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
            "prompt_tokens_details", "completion_tokens_details",
            "cached_tokens", "reasoning_tokens", "input_tokens", "output_tokens",
            "input_tokens_details", "output_tokens_details",
        } and (isinstance(item, dict) or type(item) in (int, float))
    }


def _request(url, payload):
    endpoint = urlsplit(url)
    thinking = payload.get("thinking") or {}
    disabled = thinking.get("type") == "disabled"
    effort = payload.get("reasoning_effort")
    if endpoint.scheme != "https" or endpoint.hostname != "api.deepseek.com":
        raise RuntimeError("TB4 skills-agent only permits the official DeepSeek endpoint")
    if payload.get("model") != "deepseek-flash":
        raise RuntimeError("TB4 skills-agent requires deepseek-flash")
    if not disabled and effort != "max":
        raise RuntimeError("TB4 primary/subagent requests require max effort")
    identifier = str(uuid.uuid4())
    image_count = sum(
        block.get("type") == "image_url"
        for message in payload.get("messages", [])
        for block in message.get("content", [])
        if isinstance(block, dict)
    )
    _write({
        "event": "request", "audit_id": identifier,
        "endpoint": f"{endpoint.scheme}://{endpoint.hostname}{endpoint.path}",
        "model": payload.get("model"), "effort": effort,
        "thinking": "disabled" if disabled else "enabled",
        "max_output_tokens": payload.get("max_tokens", payload.get("max_output_tokens")),
        "tools": [tool.get("function", {}).get("name") for tool in payload.get("tools", [])],
        "image_count": image_count,
    })
    return identifier


def _install():
    from skills_agent.providers import base

    original_stream = base.post_stream
    original_json = base.post_json

    def stream(url, payload, headers):
        identifier = _request(url, payload)
        source = original_stream(url, payload, headers)

        def events():
            try:
                for event in source:
                    if isinstance(event, dict) and event.get("usage"):
                        _write({"event": "usage", "audit_id": identifier,
                                "response_id": event.get("id"),
                                "model": event.get("model"), "usage": _safe_usage(event["usage"])})
                    yield event
            except BaseException as exc:
                _write({"event": "transport_error", "audit_id": identifier,
                        "exception_type": type(exc).__name__})
                raise
            finally:
                source.close()

        return base.DiagnosticStream(events(), source.diagnostic)

    def post(url, payload, headers):
        identifier = _request(url, payload)
        result = original_json(url, payload, headers)
        if result.get("usage"):
            _write({"event": "usage", "audit_id": identifier,
                    "response_id": result.get("id"), "model": result.get("model"),
                    "usage": _safe_usage(result["usage"])})
        return result

    base.post_stream = stream
    base.post_json = post


if os.environ.get("TB4_API_AUDIT_LOG"):
    try:
        _install()
    except Exception:
        # A missing hook must never permit an unaudited paid request.
        os._exit(78)
