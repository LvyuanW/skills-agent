"""Turn explicit shell feedback into persisted native image observations."""

import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .images import MAX_REQUEST_IMAGES, image_bytes


def _image_path(value):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("Image path must be nonempty text")
    if value.startswith("file:"):
        url = urlsplit(value)
        if url.scheme != "file" or url.netloc not in {"", "localhost"} or url.query or url.fragment:
            raise ValueError("Image file:// URL must identify a local absolute path without query or fragment")
        value = unquote(url.path, errors="strict")
        if "\0" in value:
            raise ValueError("Image path must not contain NUL")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Image paths must be absolute host paths or local file:// URLs")
    return path


def _source_url(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Image source_url must be a file:// or HTTP(S) URL")
    parsed = urlsplit(value)
    if parsed.scheme == "file":
        _image_path(value)
    elif parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Image source_url must be a file:// or HTTP(S) URL")
    return value


def validate_images(value):
    """Validate declarations before execution without opening future output files."""
    if not isinstance(value, list):
        raise ValueError("images must be an array of image objects")
    if len(value) > MAX_REQUEST_IMAGES:
        raise ValueError(f"images can contain at most {MAX_REQUEST_IMAGES} items; compose additional views first")
    images = []
    for index, image in enumerate(value, 1):
        if not isinstance(image, dict) or set(image) - {"path", "label", "source_url"}:
            raise ValueError(f"Image {index} must be an object containing path and optional label/source_url")
        item = {"path": str(_image_path(image.get("path")))}
        if "label" in image:
            if not isinstance(image["label"], str):
                raise ValueError(f"Image {index} label must be text")
            item["label"] = image["label"]
        if "source_url" in image:
            item["source_url"] = _source_url(image["source_url"])
        images.append(item)
    return images


def image_mime(data: bytes) -> str:
    """Identify supported image headers, without claiming full pixel validation."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and len(data) >= 4:
        return "image/jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 13:
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 16 and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Expected a PNG, JPEG, GIF or WebP image header")


def _marked_feedback(value):
    return ((isinstance(value, dict) and "skills_agent_feedback" in value)
            or (isinstance(value, list) and any(isinstance(item, dict) and "skills_agent_feedback" in item
                                              for item in value)))


def _malformed_feedback(stdout):
    """Recognize a misplaced envelope, without interpreting arbitrary log objects."""
    try:
        value, _ = json.JSONDecoder().raw_decode(stdout.lstrip())
        if _marked_feedback(value):
            return True
    except (ValueError, TypeError, RecursionError):
        pass
    for line in stdout.splitlines():
        try:
            if _marked_feedback(json.loads(line)):
                return True
        except (ValueError, TypeError, RecursionError):
            pass
    return False


def feedback_content(result: str, session, *, images=None):
    """Attach declared images after execution; retain single-envelope compatibility."""
    try:
        execution = json.loads(result)
    except (ValueError, TypeError):
        return result
    if not isinstance(execution, dict):
        return result
    errors, media = [], []
    attachments = []
    if images is not None or "images" in execution:
        try:
            declared = validate_images(images if images is not None else execution["images"])
            attachments = [{"type": "image", **item} for item in declared]
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
    else:
        stdout = execution.get("stdout")
        if not isinstance(stdout, str) or execution.get("stdout_truncated"):
            return result
        malformed = False
        try:
            payload = json.loads(stdout)
        except (ValueError, TypeError, RecursionError):
            if not _malformed_feedback(stdout):
                return result
            malformed = True
            payload = None
        if not malformed and not _marked_feedback(payload):
            return result
        if not isinstance(payload, dict):
            errors.append("Legacy image feedback must be one complete JSON object; declare shell.images instead")
        elif type(payload["skills_agent_feedback"]) is not int or payload["skills_agent_feedback"] != 1:
            errors.append("Unsupported skills_agent_feedback version; expected 1")
        elif not isinstance(payload.get("attachments"), list):
            errors.append("Feedback attachments must be a list")
        elif len(payload["attachments"]) > MAX_REQUEST_IMAGES:
            errors.append(f"A request can contain at most {MAX_REQUEST_IMAGES} images; "
                          "use the images skill to compose the required sources into a labeled image within 1 MB")
        else:
            attachments = payload["attachments"]
    for index, attachment in enumerate(attachments, 1):
        try:
            if not isinstance(attachment, dict) or attachment.get("type") != "image":
                raise ValueError("Only image feedback attachments are supported")
            path = _image_path(attachment.get("path")).resolve(strict=True)
            if not path.is_file():
                raise ValueError("Image feedback must refer to a regular file")
            label = attachment.get("label", path.name)
            if not isinstance(label, str):
                raise ValueError("Image feedback label must be text")
            data = image_bytes({"path": str(path)})
            mime = image_mime(data)
            source_url = _source_url(attachment.get("source_url") or path.as_uri())
            metadata = session.save_attachment(path, data=data)
            media.extend([
                {"type": "text", "text": f"Image feedback: {label}\nSource: {path}\n"
                 f"Snapshot: {metadata['path']}\nImage URL: {metadata['url']}\n"
                 f"Source URL: {source_url}"},
                {"type": "image", **metadata, "mime_type": mime,
                 "source_path": str(path), "source_url": source_url, "label": label},
            ])
        except (OSError, ValueError) as exc:
            errors.append(f"Feedback attachment {index}: {exc}")
    count = sum(block["type"] == "image" for block in media)
    execution["feedback_image_count"] = count
    if errors:
        execution["feedback_errors"] = [*errors, f"Actually attached {count} image(s). Correct the image declarations "
                                        "and reattach using shell.images; do not rerun completed computation."]
    result = json.dumps(execution, ensure_ascii=False)
    return [{"type": "text", "text": result}, *media] if media else result


def replace_result_text(message: dict, text: str) -> None:
    """Reports and direct-result fallbacks replace text while retaining native media."""
    content = message.get("content")
    media = ([block for block in content if block.get("type") in {"image", "file"}
              or (block.get("type") == "text" and isinstance(block.get("image_reference"), dict))]
             if isinstance(content, list) else [])
    message["content"] = [{"type": "text", "text": text}, *media] if media else text
