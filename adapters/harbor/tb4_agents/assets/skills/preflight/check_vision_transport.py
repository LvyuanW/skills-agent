"""Offline reconstruction of the native image payload; makes no API requests."""

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import zlib

from skills_agent.providers.openai import OpenAIProvider


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rgb_values(data):
    offset, compressed = 8, b""
    while offset < len(data):
        length = struct.unpack("!I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        value = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color, compression, filtering, interlace = struct.unpack("!2I5B", value)
            assert (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)
        if kind == b"IDAT":
            compressed += value
        offset += length + 12
    raw = zlib.decompress(compressed)
    colors = set()
    for row in range(height):
        line = raw[row * (width * 3 + 1):(row + 1) * (width * 3 + 1)]
        assert line[0] == 0
        colors.update(tuple(line[index:index + 3]) for index in range(1, len(line), 3))
    return {"width": width, "height": height, "rgb_values": sorted(colors)}


def main():
    trial = Path(sys.argv[1])
    fixture = Path(__file__).parent / "vision_task/environment/swatch.png"
    records = [json.loads(line) for line in (trial / "agent/api-audit.jsonl").read_text().splitlines()]
    parent_events = [json.loads(line) for line in (trial / "agent/skills/session/events.jsonl").read_text().splitlines()]
    snapshots, decoded = [], []
    model = OpenAIProvider("deepseek-v4.1-flash", "NOT_A_REAL_KEY", base_url="https://yuzhiapi.xyz/v1", api_format="chat")
    for source in (trial / "agent/skills/session/subagents").glob("*/events.jsonl"):
        messages, seen_image = [], False
        for line in source.read_text().splitlines():
            event = json.loads(line)
            if event.get("type") != "message":
                continue
            message = deepcopy(event["message"])
            if message.get("role") == "assistant" and seen_image:
                payload = model._chat_payload("", messages, [])
                for item in payload["messages"]:
                    for block in item.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "image_url":
                            decoded.append(base64.b64decode(block["image_url"]["url"].split(";base64,", 1)[1]))
                break
            content = message.get("content") or []
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "image":
                    path = trial / "agent" / Path(block["path"]).relative_to("/logs/agent")
                    block["path"] = str(path)
                    snapshots.append(path.read_bytes())
                    seen_image = True
            messages.append(message)
    assert snapshots and decoded
    expected = digest(fixture.read_bytes())
    assert all(digest(data) == expected for data in [*snapshots, *decoded])
    result = json.loads((trial / "controller-result.json").read_text())
    report = {
        "model": "deepseek-v4.1-flash", "trial_path": str(trial),
        "network_requests_by_this_check": 0,
        "proof_method": "offline reconstruction from actual native events using the unmodified payload/image encoder",
        "fixture_sha256": expected, "snapshot_sha256": [digest(data) for data in snapshots],
        "reconstructed_image_url_decoded_sha256": [digest(data) for data in decoded],
        "image_bytes_unchanged": True, "pixel_values": rgb_values(fixture.read_bytes()),
        "ordinary_child_completed": sum(event.get("type") == "subagent_summary" and event.get("status") == "completed" for event in parent_events),
        "real_requests_with_image_url": sum(event.get("event") == "request" and event.get("image_count", 0) > 0 for event in records),
        "real_api_request_count": sum(event.get("event") == "request" for event in records),
        "all_reasoning_requests_max": all(event.get("effort") == "max" for event in records if event.get("event") == "request" and event.get("thinking") != "disabled"),
        "auxiliary_thinking_disabled_requests": sum(event.get("event") == "request" and event.get("thinking") == "disabled" for event in records),
        "transport_error_count": sum(event.get("event") == "transport_error" for event in records),
        "verifier_environment_mode": result.get("verifier_environment_mode"),
        "verifier_reward": result.get("verifier_result", {}).get("rewards", {}).get("reward"),
        "answer_file": (trial / "artifacts/app/answer.txt").read_text(),
        "semantic_color_answer_correct": (trial / "artifacts/app/answer.txt").read_bytes() == b"blue\n",
        "interpretation": "Native image transport and ordinary-child execution are checked separately from the semantic color answer.",
        "agent_result": result.get("agent_result"),
    }
    destination = Path(os.environ.get("TB4_PREFLIGHT_EVIDENCE_DIR", "/data/tb4/artifacts/skills-native-preflight")) / "vision-transport-report.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
