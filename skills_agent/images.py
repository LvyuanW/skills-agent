"""Project immutable image evidence into the next model request."""

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse


MAX_REQUEST_IMAGES = 10
MAX_IMAGE_BYTES = 1_000_000


class ImageRequestError(ValueError):
    """An image cannot be attached within the request's image limits."""


def image_bytes(block: dict) -> bytes:
    path = block.get("path")
    if not isinstance(path, (str, Path)) or not str(path):
        raise ImageRequestError("Image requires a local snapshot path")
    path = Path(path).expanduser()
    if not path.is_file():
        raise ImageRequestError(f"Image snapshot is not a regular file: {path}")
    with path.open("rb") as source:
        data = source.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageRequestError(f"Image exceeds {MAX_IMAGE_BYTES} bytes: {path}; "
                                "use the images skill to compress a copy before attaching")
    return data


def image_reference(block: dict) -> dict:
    """Keep a stable snapshot URL without reading the image file."""
    if block.get("type") == "text" and isinstance(block.get("image_reference"), dict):
        return deepcopy(block)
    path = block.get("path")
    url = Path(path).expanduser().absolute().as_uri() if path else block.get("url")
    if not isinstance(url, str) or urlparse(url).scheme not in {"file", "http", "https"}:
        raise ImageRequestError("Image requires a local snapshot path or retrievable URL")
    label = block.get("label") or block.get("name") or (Path(path).name if path else "image")
    reference = {"url": url, "name": label}
    if path:
        reference["path"] = str(path)
    source_url = block.get("source_url")
    if not source_url and block.get("source_path"):
        source = str(block["source_path"])
        source_url = source if urlparse(source).scheme in {"http", "https", "file"} else Path(source).absolute().as_uri()
    text = f"Image: {label}\nImage URL: {url}"
    if isinstance(source_url, str) and urlparse(source_url).scheme in {"file", "http", "https"} and source_url != url:
        reference["source_url"] = source_url
        text += f"\nSource URL: {source_url}"
    return {"type": "text", "text": text, "image_reference": reference}


def _same_reference(block, reference):
    return (isinstance(block, dict) and isinstance(block.get("image_reference"), dict)
            and block["image_reference"].get("url") == reference["image_reference"]["url"])


def project_images(messages: list[dict]) -> list[dict]:
    """Keep at most ten images after the last assistant; retain all references.

    Projection never reads files or changes the original history. A model turn
    ends at every assistant message, including one that requests more tools.
    """
    projected = deepcopy(messages)
    boundary = max((index for index, message in enumerate(projected)
                    if message.get("role") == "assistant"), default=-1)
    active = [(index, position) for index, message in enumerate(projected)
              if index > boundary and message.get("role") in {"user", "tool"}
              and isinstance(message.get("content"), list)
              for position, block in enumerate(message["content"])
              if isinstance(block, dict) and block.get("type") == "image"]
    retained = set(active[-MAX_REQUEST_IMAGES:])
    invalidated = False
    for index, message in enumerate(projected):
        parts = message.get("content")
        if isinstance(parts, list):
            content = []
            for position, block in enumerate(parts):
                if not isinstance(block, dict) or block.get("type") != "image":
                    content.append(block)
                    continue
                reference = image_reference(block)
                keep = (index, position) in retained
                over_limit = index > boundary and message.get("role") in {"user", "tool"} and not keep
                omitted = f"\nImage not attached: request limit is {MAX_REQUEST_IMAGES}; newest images retained."
                if over_limit:
                    reference["text"] += omitted
                    reference["image_reference"]["omitted_reason"] = "request_limit"
                if content and _same_reference(content[-1], reference):
                    if over_limit and content[-1]["image_reference"].get("omitted_reason") != "request_limit":
                        content[-1]["text"] += omitted
                        content[-1]["image_reference"]["omitted_reason"] = "request_limit"
                else:
                    content.append(reference)
                if keep:
                    content.append(block)
                elif index <= boundary:
                    invalidated = True
            message["content"] = content
        if invalidated and message.get("role") == "assistant":
            # Claude's signed thinking belongs to the original preceding input.
            # Keep tool_use blocks and IDs, but do not replay that stale state.
            for field in ("content", "_anthropic_content"):
                if isinstance(message.get(field), list):
                    message[field] = [block for block in message[field]
                                      if not isinstance(block, dict) or
                                      block.get("type") not in {"thinking", "redacted_thinking"}]
    return projected
