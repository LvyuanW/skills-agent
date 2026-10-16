#!/usr/bin/env python3
"""Exercise Codex view_image and Responses serialization with a local mock.

No live provider is contacted and only a literal dummy credential is used. This
checks CLI/container transport compatibility; it is not a model capability test.
"""

import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path("/data/tb4")
REQUESTS = []
COLLABORATION_SCHEMA = None


def has_image(value):
    if isinstance(value, dict):
        return value.get("type") == "input_image" or any(has_image(x) for x in value.values())
    return isinstance(value, list) and any(has_image(x) for x in value)


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        global COLLABORATION_SCHEMA
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        COLLABORATION_SCHEMA = next((x for x in body.get("tools", []) if x.get("name") == "collaboration"), None)
        REQUESTS.append({"model": body.get("model"), "reasoning": body.get("reasoning"),
                         "contains_image": has_image(body.get("input")),
                         "output_types": sorted({x.get("type", "") for x in body.get("input", [])
                                                 if isinstance(x, dict)})})
        index = len(REQUESTS)
        response_id = f"resp_offline_{index}"
        if index == 1:
            item = {"id": "fc_offline_exec", "type": "function_call", "status": "completed",
                    "call_id": "call_offline_exec", "name": "exec_command",
                    "arguments": json.dumps({"cmd": "printf 'TB4_EXEC_TOOL_OK\\n' > /tmp/tb4-exec-marker",
                                              "max_output_tokens": 1000})}
        elif index == 2:
            item = {"id": "fc_offline_image", "type": "function_call", "status": "completed",
                    "call_id": "call_offline_image", "name": "view_image",
                    "arguments": '{"path":"/fixture/smoke.png"}'}
        elif index == 3:
            item = {"id": "ct_offline_patch", "type": "custom_tool_call", "status": "completed",
                    "call_id": "call_offline_patch", "name": "apply_patch",
                    "input": "*** Begin Patch\n*** Add File: /app/offline-marker\n+TB4_APPLY_PATCH_OK\n*** End Patch\n"}
        else:
            item = {"id": "msg_offline_done", "type": "message", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text", "text": "OFFLINE_IMAGE_TOOL_OK", "annotations": []}]}
        response = {"id": response_id, "object": "response", "created_at": int(time.time()),
                    "status": "in_progress", "model": "deepseek-v4.1-flash", "output": []}
        events = [{"type": "response.created", "response": response},
                  {"type": "response.output_item.added", "output_index": 0, "item": item}]
        if index > 3:
            events.append({"type": "response.output_text.delta", "item_id": item["id"],
                           "output_index": 0, "content_index": 0, "delta": "OFFLINE_IMAGE_TOOL_OK"})
        events.append({"type": "response.output_item.done", "output_index": 0, "item": item})
        events.append({"type": "response.completed", "response": {
            **response, "status": "completed", "output": [item],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}}}})
        payload = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                          for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def png():
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)) + chunk(
        b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 32) * 32)) + chunk(b"IEND", b"")


def main():
    server = ThreadingHTTPServer(("172.17.0.1", 8766), Mock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="tb4-codex-offline-", dir=ROOT / "cache") as path:
            fixture = Path(path)
            fixture.chmod(0o755)
            shutil.copyfile(Path(__file__).with_name("models.json"), fixture / "models.json")
            (fixture / "smoke.png").write_bytes(png())
            (fixture / "config.toml").write_text('''model = "deepseek-v4.1-flash"
model_provider = "offline"
model_reasoning_effort = "max"
web_search = "disabled"
model_catalog_json = "/fixture/models.json"
[model_providers.offline]
name = "offline"
base_url = "http://172.17.0.1:8766"
wire_api = "responses"
env_key = "DUMMY_API_KEY"
''')
            command = ["docker", "run", "--rm", "--cpus", "2", "--memory", "4g",
                       "--cgroup-parent", "tb4.slice", "-v", f"{fixture}:/fixture:ro",
                       "-v", f"{ROOT}/artifacts/runtime:/runtime:ro", "-w", "/app",
                       "-e", "DUMMY_API_KEY=offline-no-live-credentials",
                       "-e", "CODEX_HOME=/tmp/offline-codex-home",
                       "python:3.12-slim-bookworm", "bash", "-lc", '''set -eu
mkdir -p /tmp/offline-codex-home /tmp/codex
cp /fixture/config.toml /tmp/offline-codex-home/config.toml
tar -xzf /runtime/codex-0.149.1-linux-x64.tgz -C /tmp/codex
/tmp/codex/package/vendor/x86_64-unknown-linux-musl/bin/codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --json --enable unified_exec "Inspect /fixture/smoke.png with view_image and report its color."
test "$(cat /tmp/tb4-exec-marker)" = TB4_EXEC_TOOL_OK
test "$(cat /app/offline-marker)" = TB4_APPLY_PATCH_OK
''']
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
            passed = (completed.returncode == 0 and len(REQUESTS) == 4
                      and not REQUESTS[0]["contains_image"] and REQUESTS[2]["contains_image"]
                      and "custom_tool_call_output" in REQUESTS[3]["output_types"]
                      and all(r["model"] == "deepseek-v4.1-flash" and r["reasoning"]["effort"] == "max"
                              for r in REQUESTS))
            report = {"kind": "offline_mock_only", "live_provider_calls": 0, "passed": passed,
                      "exit_code": completed.returncode, "request_metadata": REQUESTS,
                      "collaboration_schema": COLLABORATION_SCHEMA,
                      "fixture_png_sha256": hashlib.sha256(png()).hexdigest(),
                      "stdout": completed.stdout, "stderr": completed.stderr}
            destination = ROOT / "logs/codex-offline-image-smoke.json"
            destination.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({k: report[k] for k in ("kind", "live_provider_calls", "passed", "exit_code", "request_metadata")}))
            if not passed:
                raise SystemExit(1)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
