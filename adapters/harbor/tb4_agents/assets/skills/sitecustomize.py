"""Metadata-only auditing for the unmodified skills-agent provider transport.

Loaded only when the adapter prepends this directory to PYTHONPATH. No prompt,
response text, image bytes, URL query, headers, or credentials are persisted.
"""

import json
import os
import ssl
import sys
import time
import uuid
from urllib.parse import urlsplit

ALLOWED_API_BASES = {"https://api.deepseek.com", "https://rehdasu.cn/v1", "https://yuzhiapi.xyz/v1"}
GATEWAY_API_BASES = {"https://rehdasu.cn/v1", "https://yuzhiapi.xyz/v1"}


def _write(record):
    path = os.environ.get("TB4_API_AUDIT_LOG")
    if not path:
        return
    record.update(timestamp=time.time(), pid=os.getpid())
    data = (json.dumps(record, separators=(",", ":")) + "\n").encode()
    # Task containers may run as root while Harbor's host process is tbench.
    # This file contains only the allowlisted public request/usage metadata.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.fchmod(descriptor, 0o644)
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
    allowed_base = os.environ.get("TB4_ALLOWED_API_BASE", "https://yuzhiapi.xyz/v1").rstrip("/")
    thinking = payload.get("thinking") or {}
    disabled = thinking.get("type") == "disabled"
    effort = payload.get("reasoning_effort")
    if allowed_base not in ALLOWED_API_BASES or url != allowed_base + "/chat/completions":
        raise RuntimeError("TB4 skills-agent only permits the explicitly configured DeepSeek endpoint")
    if payload.get("model") != "deepseek-v4.1-flash":
        raise RuntimeError("TB4 skills-agent requires deepseek-v4.1-flash")
    if not disabled and effort != "max":
        raise RuntimeError("TB4 primary/subagent requests require max effort")
    identifier = str(uuid.uuid4())
    image_count = sum(
        block.get("type") == "image_url"
        for message in payload.get("messages", [])
        for block in (message.get("content") or [])
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


def _install_tls_roots():
    """Augment only this interpreter's default urllib context with pinned roots.

    Several task images intentionally omit an OS CA package. The installed
    runtime already includes certifi; avoid changing task packages, files, or
    inherited SSL_CERT_FILE. Explicit caller-provided SSLContext objects remain
    untouched because urllib bypasses its default context factory for them.
    """
    import certifi

    original_context = ssl._create_default_https_context

    def default_https_context(*args, **kwargs):
        context = original_context(*args, **kwargs)
        context.load_verify_locations(cafile=certifi.where())
        return context

    ssl._create_default_https_context = default_https_context


def _install():
    _install_tls_roots()
    from skills_agent.providers import base

    original_stream = base.post_stream
    original_json = base.post_json

    def stream(url, payload, headers):
        identifier = _request(url, payload)
        source = original_stream(url, payload, headers)

        def events():
            previous_usage = None
            try:
                for event in source:
                    if isinstance(event, dict) and event.get("usage"):
                        usage = {"event": "usage", "audit_id": identifier,
                                 "response_id": event.get("id"),
                                 "model": event.get("model"), "usage": _safe_usage(event["usage"])}
                        if usage != previous_usage:
                            _write(dict(usage))
                            previous_usage = usage
                    yield event
            except GeneratorExit:
                # The provider closes the stream after its terminal event.
                # This is normal iterator cleanup, not a transport failure.
                raise
            except BaseException as exc:
                _write({"event": "transport_error", "audit_id": identifier,
                        "exception_type": type(exc).__name__,
                        "http_status": getattr(exc, "http_status", None),
                        "category": getattr(exc, "category", None)})
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
    # providers/__init__.py imports these modules before this hook can patch
    # their module-local from-imports. Replace the same function references.
    from skills_agent.providers import openai, responses, anthropic
    for module in (openai, responses, anthropic):
        if hasattr(module, "post_stream"):
            module.post_stream = stream
        if hasattr(module, "post_json"):
            module.post_json = post
    original_payload = openai.OpenAIProvider._chat_payload

    def chat_payload(self, system, messages, tools, *, disable_thinking=False):
        payload = original_payload(self, system, messages, tools, disable_thinking=disable_thinking)
        # The core intentionally limits its DeepSeek-specific effort option to
        # api.deepseek.com. The user explicitly selected this OpenAI-compatible
        # gateway, so add only that field here; counting and sending both use
        # this same payload method and the existing v41 request encoder.
        allowed = os.environ.get("TB4_ALLOWED_API_BASE", "https://yuzhiapi.xyz/v1").rstrip("/")
        if self.base_url == allowed and allowed in GATEWAY_API_BASES and self.api_format == "chat" and self.token_counter == "deepseek":
            if not disable_thinking:
                if os.environ.get("effort") != "max":
                    raise RuntimeError("The gateway compatibility hook requires effort=max")
                payload["reasoning_effort"] = "max"
                payload["thinking"] = {"type": "enabled"}
        return payload

    openai.OpenAIProvider._chat_payload = chat_payload


if os.environ.get("TB4_API_AUDIT_LOG") and sys.prefix == "/opt/tb4-skills/venv":
    try:
        _install()
    except Exception:
        # A missing hook must never permit an unaudited paid request.
        os._exit(78)
