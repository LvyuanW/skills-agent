"""Turn explicit shell feedback into persisted native image observations."""

import json
from pathlib import Path


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


def feedback_content(result: str, session):
    """Keep ordinary results unchanged; copy images only from the versioned envelope."""
    try:
        execution = json.loads(result)
        stdout = execution.get("stdout") if isinstance(execution, dict) else None
        payload = json.loads(stdout) if isinstance(stdout, str) else None
    except (ValueError, TypeError):
        return result
    if not isinstance(payload, dict) or "skills_agent_feedback" not in payload:
        return result
    errors, media = [], []
    if type(payload["skills_agent_feedback"]) is not int or payload["skills_agent_feedback"] != 1:
        errors.append("Unsupported skills_agent_feedback version; expected 1")
    elif not isinstance(payload.get("attachments"), list):
        errors.append("Feedback attachments must be a list")
    else:
        for index, attachment in enumerate(payload["attachments"], 1):
            try:
                if not isinstance(attachment, dict) or attachment.get("type") != "image":
                    raise ValueError("Only image feedback attachments are supported")
                supplied = attachment.get("path")
                if not isinstance(supplied, str) or not supplied:
                    raise ValueError("Image feedback requires a nonempty path")
                path = Path(supplied).expanduser()
                if not path.is_absolute():
                    raise ValueError("Image feedback paths must be absolute host paths")
                path = path.resolve(strict=True)
                if not path.is_file():
                    raise ValueError("Image feedback must refer to a regular file")
                label = attachment.get("label", path.name)
                if not isinstance(label, str):
                    raise ValueError("Image feedback label must be text")
                data = path.read_bytes()
                mime = image_mime(data)
                metadata = session.save_attachment(path, data=data)
                media.extend([
                    {"type": "text", "text": f"Image feedback: {label}\nSource: {path}\n"
                     f"Snapshot: {metadata['path']}"},
                    {"type": "image", **metadata, "mime_type": mime,
                     "source_path": str(path), "label": label},
                ])
            except (OSError, ValueError) as exc:
                errors.append(f"Feedback attachment {index}: {exc}")
    if errors:
        execution["feedback_errors"] = errors
        result = json.dumps(execution, ensure_ascii=False)
    return [{"type": "text", "text": result}, *media] if media else result


def replace_result_text(message: dict, text: str) -> None:
    """Reports and direct-result fallbacks replace text while retaining native media."""
    content = message.get("content")
    media = ([block for block in content if block.get("type") in {"image", "file"}]
             if isinstance(content, list) else [])
    message["content"] = [{"type": "text", "text": text}, *media] if media else text
