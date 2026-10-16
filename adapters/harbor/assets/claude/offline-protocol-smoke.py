#!/usr/bin/env python3
"""Native Claude Code + local Anthropic mock, with no external container network.

Use --declare-capabilities only for a second, explicitly labeled experiment.
The baseline deliberately uses the same custom model/effort env as deployment.
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path("/data/tb4")
REQUESTS = []
MAIN_TURNS = 0
SUBAGENT_TEST = False


def image_present(value):
    if isinstance(value, dict):
        return value.get("type") == "image" or any(image_present(x) for x in value.values())
    return isinstance(value, list) and any(image_present(x) for x in value)


def png():
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)) + chunk(
        b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 32) * 32)) + chunk(b"IEND", b"")


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"deepseek-v4.1-flash","type":"model"}]}')

    def do_POST(self):
        global MAIN_TURNS
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        def child_prompt(value):
            if isinstance(value, str):
                return "TB4_CHILD_PROTOCOL_ONLY" in value
            if isinstance(value, dict):
                return any(child_prompt(x) for x in value.values())
            return isinstance(value, list) and any(child_prompt(x) for x in value)
        messages = body.get("messages", [])
        child = bool(messages) and child_prompt(messages[0])
        tools = [x.get("name", x.get("type")) for x in body.get("tools", [])]
        metadata = {"model": body.get("model"), "thinking": body.get("thinking"),
                    "output_config": body.get("output_config"), "max_tokens": body.get("max_tokens"),
                    "contains_image": image_present(body.get("messages")), "tools": tools,
                    "path": self.path, "subagent": child}
        REQUESTS.append(metadata)
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":100}')
            return
        main = "Bash" in tools
        if main and not child:
            MAIN_TURNS += 1
        index = MAIN_TURNS
        offset = 1 if SUBAGENT_TEST else 0
        if main and not child and SUBAGENT_TEST and index == 1:
            block = {"type": "tool_use", "id": "tool_offline_agent", "name": "Agent", "input": {}}
            tool_input = {"description": "Check child model and effort routing", "subagent_type": "general-purpose",
                          "model": "haiku", "prompt": "TB4_CHILD_PROTOCOL_ONLY: reply OFFLINE_CHILD_OK without tools."}
        elif main and not child and index == 1 + offset:
            block = {"type": "tool_use", "id": "tool_offline_bash", "name": "Bash", "input": {}}
            tool_input = {"command": "cat /app/input.txt; printf 'TB4_PREFLIGHT_OK\\n' > /app/answer.txt",
                          "description": "Read input and create synthetic preflight output"}
        elif main and not child and index == 2 + offset:
            block = {"type": "tool_use", "id": "tool_offline_read", "name": "Read", "input": {}}
            tool_input = {"file_path": "/app/smoke.png"}
        else:
            block = {"type": "text", "text": ""}
            tool_input = None
        stop = "tool_use" if tool_input else "end_turn"
        message = {"id": f"msg_offline_{len(REQUESTS)}", "type": "message", "role": "assistant",
                   "model": "deepseek-v4.1-flash", "content": [], "stop_reason": None,
                   "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 0}}
        events = [("message_start", {"type": "message_start", "message": message}),
                  ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block})]
        delta = ({"type": "input_json_delta", "partial_json": json.dumps(tool_input)} if tool_input
                 else {"type": "text_delta", "text": "OFFLINE_PROTOCOL_OK"})
        events.extend([
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": 10}}),
            ("message_stop", {"type": "message_stop"}),
        ])
        payload = "".join("event: " + kind + "\ndata: " + json.dumps(event) + "\n\n" for kind, event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, **kwargs)


def main():
    global SUBAGENT_TEST
    parser = argparse.ArgumentParser()
    parser.add_argument("--declare-capabilities", action="store_true")
    parser.add_argument("--subagent", action="store_true")
    args = parser.parse_args()
    SUBAGENT_TEST = args.subagent
    variant = "declared-capabilities" if args.declare_capabilities else "baseline"
    if args.subagent:
        variant += "-subagent"
    image = "python:3.12-slim-bookworm"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        run(["docker", "pull", image], timeout=180)
    network = "tb4-claude-offline-" + uuid.uuid4().hex[:8]
    run(["docker", "network", "create", "--internal", network])
    server = None
    try:
        gateway = json.loads(run(["docker", "network", "inspect", network]).stdout)[0]["IPAM"]["Config"][0]["Gateway"]
        server = ThreadingHTTPServer((gateway, 8767), Mock)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory(prefix="tb4-claude-offline-", dir=ROOT / "cache") as path:
            fixture = Path(path)
            fixture.chmod(0o777)
            (fixture / "smoke.png").write_bytes(png())
            (fixture / "input.txt").write_text("Write TB4_PREFLIGHT_OK and one newline to answer.txt.\n")
            home = fixture / "home"
            home.mkdir(mode=0o777)
            (home / ".claude.json").write_text('{"hasCompletedOnboarding":true}')
            command = ["docker", "run", "--rm", "--network", network, "--cpus", "2", "--memory", "4g",
                       "--user", f"{os.getuid()}:{os.getgid()}",
                       "--cgroup-parent", "tb4.slice", "-v", f"{fixture}:/app", "-w", "/app",
                       "-v", f"{ROOT}/releases/claude-code/glibc/package/claude:/usr/local/bin/claude:ro"]
            env = {"ANTHROPIC_AUTH_TOKEN": "dummy-no-live-credentials", "ANTHROPIC_BASE_URL": f"http://{gateway}:8767",
                   "ANTHROPIC_MODEL": "deepseek-v4.1-flash[1m]", "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4.1-flash[1m]",
                   "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4.1-flash[1m]", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4.1-flash",
                   "ANTHROPIC_DEFAULT_FABLE_MODEL": "deepseek-v4.1-flash[1m]", "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4.1-flash",
                   "CLAUDE_CODE_EFFORT_LEVEL": "max", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
                   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
                   "IS_SANDBOX": "1", "CLAUDE_CONFIG_DIR": "/app/home/.claude"}
            if args.declare_capabilities:
                capabilities = "effort,max_effort,thinking,adaptive_thinking,interleaved_thinking"
                for family in ("OPUS", "SONNET", "HAIKU", "FABLE"):
                    env[f"ANTHROPIC_DEFAULT_{family}_MODEL_SUPPORTED_CAPABILITIES"] = capabilities
            for key, value in env.items():
                command += ["-e", f"{key}={value}"]
            command += [image, "claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
                        "--model", "deepseek-v4.1-flash[1m]", "--effort", "max", "--disallowedTools", "WebSearch",
                        "--", "Read input.txt, create answer.txt with the requested content, then inspect smoke.png using Read."]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
            answer_ok = (fixture / "answer.txt").exists() and (fixture / "answer.txt").read_bytes() == b"TB4_PREFLIGHT_OK\n"
            verifier_ok = False
            if answer_ok:
                verifier = run(["docker", "run", "--rm", "--network", "none", "--cpus", "1", "--memory", "512m",
                                "--user", f"{os.getuid()}:{os.getgid()}",
                                "--cgroup-parent", "tb4.slice", "-v", f"{fixture}/answer.txt:/app/answer.txt:ro",
                                image, "python", "-c", "from pathlib import Path; assert Path('/app/answer.txt').read_bytes()==b'TB4_PREFLIGHT_OK\\n'"], timeout=30)
                verifier_ok = verifier.returncode == 0
            main_requests = [r for r in REQUESTS if "Bash" in r["tools"] and not r["subagent"]]
            children = [r for r in REQUESTS if r["subagent"]]
            functional = (completed.returncode == 0 and len(main_requests) == 3 + int(args.subagent) and answer_ok and verifier_ok
                          and main_requests[-1]["contains_image"])
            if args.subagent:
                functional = functional and bool(children)
            relevant = main_requests + children
            settings_ok = bool(main_requests) and all(
                r["model"] == "deepseek-v4.1-flash" and (r["output_config"] or {}).get("effort") == "max"
                and (r["thinking"] or {}).get("type") in {"enabled", "adaptive"}
                and "WebSearch" not in r["tools"] for r in relevant)
            report = {"kind": "offline_mock_only", "variant": variant, "live_provider_calls": 0,
                      "external_container_network": False, "functional_passed": functional,
                      "settings_passed": settings_ok, "exit_code": completed.returncode,
                      "answer_ok": answer_ok, "separate_verifier_ok": verifier_ok,
                      "request_metadata": REQUESTS, "stdout": completed.stdout, "stderr": completed.stderr}
            destination = ROOT / f"logs/claude-offline-protocol-{variant}.json"
            destination.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({k: report[k] for k in ("variant", "live_provider_calls", "functional_passed", "settings_passed", "exit_code", "separate_verifier_ok", "request_metadata")}))
            if not functional:
                raise SystemExit(1)
    finally:
        if server:
            server.shutdown()
        run(["docker", "network", "rm", network])


if __name__ == "__main__":
    main()
