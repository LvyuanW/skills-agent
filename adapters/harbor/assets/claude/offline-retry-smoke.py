#!/usr/bin/env python3
"""Exercise pinned native Claude retries against an isolated, dummy-key mock.

No formal task, live provider, host credential, or old trial container is used.
Only request metadata and hashes are retained; request bodies are not reported.
"""
import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid


ROOT = Path("/data/tb4")
BINARY = ROOT / "releases/claude-code/glibc/package/claude"
PINNED_SHA = "674deba0bba9aee479f4503c438b49303b6fdd304b9a471b14977aaec5215453"
MODEL = "deepseek-v4.1-flash"
IMAGE = "python:3.12-slim-bookworm"
MARKER = "OFFLINE_RETRY_OK"


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, check=True, **kwargs)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def handler_for(state):
    class Mock(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200, json.dumps({"data": [{"id": MODEL, "type": "model"}]}).encode())

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            body = json.loads(raw)
            tools = [tool.get("name", tool.get("type")) for tool in body.get("tools", [])]
            is_main = "count_tokens" not in self.path and "Bash" in tools
            logical = {key: body.get(key) for key in (
                "model", "messages", "system", "tools", "tool_choice",
                "thinking", "output_config", "max_tokens",
            )}
            record = {"path": self.path, "main_request": is_main,
                      "elapsed_seconds": round(time.monotonic() - state["started"], 6),
                      "body_sha256": digest(raw),
                      "logical_payload_sha256": digest(json.dumps(logical, sort_keys=True).encode()),
                      "model": body.get("model"), "thinking": body.get("thinking"),
                      "output_config": body.get("output_config")}
            with state["lock"]:
                if is_main:
                    state["main_requests"] += 1
                count = state["main_requests"]
                state["requests"].append(record)
            if "count_tokens" in self.path:
                record["response_status"] = 200
                self.reply(200, b'{"input_tokens":100}')
                return
            status = state["injected_status"] if is_main and (count == 1 or state["persistent"]) else 200
            record["response_status"] = status
            if status != 200:
                # Deliberately omit x-should-retry / Retry-After: test native defaults.
                kind = "authentication_error" if status == 401 else "api_error"
                self.reply(status, json.dumps({"type": "error", "error": {
                    "type": kind, "message": "Synthetic offline retry regression"}}).encode())
                return
            message = {"id": "msg_offline_retry", "type": "message", "role": "assistant",
                       "model": MODEL, "content": [], "stop_reason": None,
                       "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 0}}
            events = [
                ("message_start", {"type": "message_start", "message": message}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": MARKER}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {
                    "stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 10}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            payload = "".join("event: " + kind + "\ndata: " + json.dumps(event) + "\n\n"
                              for kind, event in events).encode()
            self.reply(200, payload, "text/event-stream")
    return Mock


def case(status, timeout, persistent=False):
    tag = uuid.uuid4().hex[:10]
    network = "tb4-claude-retry-" + tag
    container = "tb4-claude-retry-" + tag
    state = {"started": time.monotonic(), "lock": threading.Lock(),
             "injected_status": status, "persistent": persistent, "main_requests": 0, "requests": []}
    report = {"injected_status": status, "timeout_seconds": timeout,
              "injection_mode": "persistent" if persistent else "once_then_success",
              "live_provider_calls": 0, "external_container_network": False,
              "dummy_key_only": True, "max_retries_override": None}
    server = None
    network_created = False
    fixture_context = tempfile.TemporaryDirectory(prefix="claude-retry-", dir=ROOT / "cache")
    fixture = Path(fixture_context.name)
    try:
        run(["docker", "network", "create", "--internal", network], timeout=20)
        network_created = True
        network_info = json.loads(run(["docker", "network", "inspect", network], timeout=10).stdout)[0]
        if not network_info.get("Internal"):
            raise RuntimeError("Test network must be internal")
        gateway = network_info["IPAM"]["Config"][0]["Gateway"]
        server = ThreadingHTTPServer((gateway, 0), handler_for(state))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        home = fixture / "home"
        home.mkdir()
        (home / ".claude.json").write_text('{"hasCompletedOnboarding":true}')
        env = {
            "HOME": "/app/home", "CLAUDE_CONFIG_DIR": "/app/home/.claude",
            "ANTHROPIC_AUTH_TOKEN": "dummy-offline-no-live-credentials",
            "ANTHROPIC_BASE_URL": f"http://{gateway}:{server.server_port}",
            "ANTHROPIC_MODEL": MODEL + "[1m]",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": MODEL + "[1m]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": MODEL + "[1m]",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODEL,
            "ANTHROPIC_DEFAULT_FABLE_MODEL": MODEL + "[1m]",
            "ANTHROPIC_SMALL_FAST_MODEL": MODEL + "[1m]",
            "CLAUDE_CODE_SUBAGENT_MODEL": MODEL,
            "CLAUDE_CODE_EFFORT_LEVEL": "max", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
            "IS_SANDBOX": "1",
        }
        command = ["docker", "run", "--rm", "--name", container, "--network", network,
                   "--cpus", "2", "--memory", "4g", "--cgroup-parent", "tb4.slice",
                   "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{fixture}:/app", "-w", "/app",
                   "-v", f"{BINARY}:/usr/local/bin/claude:ro"]
        for name, value in env.items():
            command += ["-e", f"{name}={value}"]
        command += [IMAGE, "claude", "-p", "--output-format", "stream-json", "--verbose",
                    "--permission-mode", "bypassPermissions", "--model", MODEL + "[1m]",
                    "--effort", "max", "--disallowedTools", "WebSearch", "--",
                    "Reply OFFLINE_RETRY_OK. Do not use tools."]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        events = []
        for line in completed.stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                events.append(item)
        result = next((event for event in reversed(events) if event.get("type") == "result"), {})
        main = [request for request in state["requests"] if request["main_request"]]
        expected = [401] if status == 401 else [status, 200]
        statuses = [request["response_status"] for request in main]
        same = bool(main) and len({request["logical_payload_sha256"] for request in main}) == 1
        successful = (completed.returncode == 0 and result.get("is_error") is False
                      and MARKER in str(result.get("result", "")))
        passed = statuses == expected and same and (not successful if status == 401 else successful)
        if status == 401:
            passed = passed and completed.returncode != 0 and result.get("is_error") is True
        report.update({"passed": passed, "exit_code": completed.returncode, "timed_out": False,
                       "successful_final_result": successful, "result_is_error": result.get("is_error"),
                       "result_subtype": result.get("subtype"), "main_response_statuses": statuses,
                       "same_logical_payload": same,
                       "native_version": next((event.get("claude_code_version") for event in events
                                               if event.get("claude_code_version")), None),
                       "stdout_event_types": dict(Counter(event.get("type") for event in events)),
                       "stdout_bytes": len(completed.stdout.encode()), "stderr_bytes": len(completed.stderr.encode())})
    except subprocess.TimeoutExpired:
        report.update(passed=False, timed_out=True, error_type="TimeoutExpired")
    except Exception as exc:
        report.update(passed=False, error_type=type(exc).__name__)
    finally:
        # docker-client timeout does not stop its container. Always remove by this unique test name.
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=20)
        if server:
            server.shutdown()
            server.server_close()
        removed = subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=20) if network_created else None
        report["network_removed"] = removed is None or removed.returncode == 0
        container_check = subprocess.run(["docker", "container", "inspect", container], capture_output=True, timeout=10)
        report["container_removed"] = container_check.returncode != 0
        fixture_context.cleanup()
        report["elapsed_seconds"] = round(time.monotonic() - state["started"], 6)
        report["requests"] = state["requests"]
        report["passed"] = bool(report.get("passed") and report["network_removed"] and report["container_removed"])
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--statuses", type=int, nargs="+", choices=(502, 504, 524, 401), default=[502, 504, 524, 401])
    parser.add_argument("--persistent", action="store_true")
    args = parser.parse_args()
    binary_sha = digest(BINARY.read_bytes())
    if binary_sha != PINNED_SHA:
        raise SystemExit("Pinned Claude binary checksum mismatch")
    image_info = json.loads(run(["docker", "image", "inspect", IMAGE], timeout=10).stdout)[0]
    cases = []
    for status in args.statuses:
        result = case(status, args.timeout, args.persistent)
        cases.append(result)
        print(json.dumps({key: result.get(key) for key in (
            "injected_status", "passed", "main_response_statuses", "elapsed_seconds", "timed_out")}), flush=True)
    report = {"kind": "native_claude_offline_retry_regression", "version": "2.1.300",
              "binary_sha256": binary_sha, "script_sha256": digest(Path(__file__).read_bytes()),
              "image_id": image_info["Id"], "live_provider_calls": 0,
              "all_passed": all(result["passed"] for result in cases), "cases": cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()
