#!/usr/bin/env python3
"""Smoke-only DeepSeek relay that records allowlisted request/usage metadata.

Run on a test host, then temporarily point a sandbox at its bridge IP. No API
key is read from disk: the client's authorization is forwarded in memory only.
Prompts, response text, raw headers, and provider error bodies are never logged.
This relay is deliberately not part of the formal benchmark network path.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_PATHS = {
    "/responses",
    "/v1/responses",
    "/anthropic/v1/messages",
    "/anthropic/v1/messages/count_tokens",
}
_NUMBER_KEYS = {
    "input_tokens", "output_tokens", "total_tokens", "prompt_tokens",
    "completion_tokens", "cached_tokens", "reasoning_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens",
}
_NESTED_USAGE_KEYS = {
    "input_tokens_details", "output_tokens_details", "prompt_tokens_details",
    "completion_tokens_details", "cache_creation",
}


def numeric_usage(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if key in _NUMBER_KEYS and isinstance(item, (int, float)):
            result[key] = item
        elif key in _NESTED_USAGE_KEYS:
            nested = numeric_usage(item)
            if nested:
                result[key] = nested
    return result


def request_metadata(body: dict) -> dict:
    result = {}
    for key in ("model", "max_tokens", "max_output_tokens", "stream"):
        value = body.get(key)
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        result["reasoning"] = {
            k: reasoning[k] for k in ("effort", "summary")
            if isinstance(reasoning.get(k), (str, int))
        }
    for key in ("reasoning_effort", "effort"):
        if isinstance(body.get(key), str):
            result[key] = body[key]
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        result["thinking"] = {
            k: thinking[k] for k in ("type", "budget_tokens")
            if isinstance(thinking.get(k), (str, int))
        }
    output = body.get("output_config")
    if isinstance(output, dict) and isinstance(output.get("effort"), str):
        result["output_config"] = {"effort": output["effort"]}
    tools = body.get("tools")
    if isinstance(tools, list):
        result["tools"] = [
            {key: tool[key] for key in ("type", "name") if isinstance(tool.get(key), str)}
            for tool in tools if isinstance(tool, dict)
        ]

    def has_image(value: object) -> bool:
        if isinstance(value, dict):
            return value.get("type") in {"input_image", "image"} or any(
                has_image(v) for v in value.values()
            )
        if isinstance(value, list):
            return any(has_image(v) for v in value)
        return False

    result["contains_image"] = has_image(body.get("input", body.get("messages")))
    return result


class MetadataServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple, log_path: Path):
        super().__init__(address, Relay)
        self.log_path = log_path
        self.log_lock = threading.Lock()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)

    def record(self, record: dict) -> None:
        with self.log_lock, self.log_path.open("a") as stream:
            stream.write(json.dumps({"unix_time": time.time(), **record}) + "\n")


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server: MetadataServer

    def log_message(self, format: str, *args: object) -> None:
        pass

    def reject(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"Smoke relay request rejected"}}')

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path not in _PATHS:
            self.reject(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 32 * 1024 * 1024:
            self.reject(413)
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self.reject(400)
            return
        if not isinstance(body, dict):
            self.reject(400)
            return
        model = body.get("model", "")
        if not isinstance(model, str) or model.removesuffix("[1m]") != "deepseek-flash":
            self.reject(400)
            return
        request_id = str(uuid.uuid4())
        endpoint = "https://api.deepseek.com" + self.path
        self.server.record({
            "event": "request", "request_id": request_id,
            "upstream_endpoint": "https://api.deepseek.com" + path,
            **request_metadata(body),
        })
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() in {
                "authorization", "x-api-key", "content-type", "accept",
                "anthropic-version", "anthropic-beta", "openai-beta",
            }
        }
        upstream = urllib.request.Request(endpoint, data=raw, headers=headers, method="POST")
        sent_headers = False
        started = time.monotonic()
        try:
            try:
                response = urllib.request.urlopen(upstream, timeout=300)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.end_headers()
                sent_headers = True
                self.server.record({"event": "response", "request_id": request_id, "status": response.status})
                buffer = b""
                sse = "text/event-stream" in response.headers.get("Content-Type", "")

                def parse_event(chunk: bytes) -> None:
                    try:
                        obj = json.loads(chunk)
                    except (ValueError, UnicodeDecodeError):
                        return
                    if not isinstance(obj, dict):
                        return
                    candidates = [obj.get("usage")]
                    for field in ("response", "message"):
                        if isinstance(obj.get(field), dict):
                            candidates.append(obj[field].get("usage"))
                    for candidate in candidates:
                        usage = numeric_usage(candidate)
                        if usage:
                            self.server.record({"event": "usage", "request_id": request_id, "usage": usage})

                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    buffer += chunk
                    if sse:
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            if line.startswith(b"data:"):
                                parse_event(line[5:].strip())
                    elif len(buffer) > 16 * 1024 * 1024:
                        buffer = b""
                if not sse and response.status < 400:
                    parse_event(buffer)
                self.server.record({"event": "finished", "request_id": request_id, "elapsed_seconds": round(time.monotonic() - started, 3)})
        except (OSError, TimeoutError, urllib.error.URLError):
            self.server.record({"event": "transport_error", "request_id": request_id})
            if not sent_headers:
                self.reject(502)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    MetadataServer((args.bind, args.port), args.log).serve_forever()
