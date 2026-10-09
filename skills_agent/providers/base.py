import base64
import json
from pathlib import Path
from typing import Protocol
from urllib import error, request


class ProviderError(RuntimeError):
    def __init__(self, message: str, raw: dict | None = None,
                 partial_content: list[dict] | None = None):
        super().__init__(message)
        self.raw = raw
        self.partial_content = partial_content if partial_content is not None else []


class Provider(Protocol):
    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> dict: ...


def post_json(url: str, payload: dict, headers: dict) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=120) as response:
            data = json.load(response)
    except error.HTTPError as exc:
        raise ProviderError(f"API HTTP error {exc.code}") from None
    except (error.URLError, OSError):
        raise ProviderError("API connection failed") from None
    except (ValueError, UnicodeError):
        raise ProviderError("API returned invalid JSON") from None
    if not isinstance(data, dict):
        raise ProviderError("API response must be a JSON object")
    if "error" in data:
        raise ProviderError("API returned an error")
    return data


def blocks(content: str | list | None) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    return content


def received_text(content) -> list[dict]:
    """Extract received text for error display, without exposing tool inputs."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    result = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        key = "text" if kind == "text" else "refusal" if kind == "refusal" else None
        if key and isinstance(block.get(key), str):
            result.append({"type": "text", "text": block[key]})
    return result


def encoded_file(block: dict) -> str:
    return base64.b64encode(Path(block["path"]).read_bytes()).decode("ascii")


def parse_arguments(arguments: str) -> dict:
    try:
        value = json.loads(arguments)
    except (ValueError, TypeError):
        raise ProviderError("Tool arguments must be valid JSON") from None
    if not isinstance(value, dict):
        raise ProviderError("Tool arguments must be a JSON object")
    return value


def validate_calls(calls: list[dict]) -> None:
    if any(not isinstance(call.get("id"), str) or not call["id"]
           or not isinstance(call.get("name"), str) or not call["name"] for call in calls):
        raise ProviderError("Tool calls require nonempty IDs and names")
    if len({call["id"] for call in calls}) != len(calls):
        raise ProviderError("Duplicate tool call IDs")


def validate_tool_pairs(messages: list[dict]) -> None:
    pending: set[str] = set()
    for message in messages:
        if message["role"] == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise ProviderError("Tool result has no matching pending call")
            pending.remove(call_id)
            continue
        if pending:
            raise ProviderError("Tool calls must be followed by all matching results")
        calls = message.get("tool_calls", [])
        validate_calls(calls)
        pending = {call["id"] for call in calls}
    if pending:
        raise ProviderError("Tool calls are missing results")


class HTTPProvider:
    default_base_url: str

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 max_output_tokens: int = 4096):
        if not model or max_output_tokens <= 0:
            raise ValueError("model and positive max_output_tokens are required")
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.max_output_tokens = max_output_tokens
