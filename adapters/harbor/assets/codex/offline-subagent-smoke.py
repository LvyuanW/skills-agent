#!/usr/bin/env python3
"""Adversarial Codex child-model routing test using an isolated local mock."""

import json
import argparse
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path("/data/tb4")
REQUESTS = []
OUTPUTS = []
STAGE = 0
FORK_ALL_TEST = False


def marker(value):
    if isinstance(value, str):
        return "TB4_OFFLINE_CODEX_CHILD" in value
    if isinstance(value, dict):
        return any(marker(x) for x in value.values())
    return isinstance(value, list) and any(marker(x) for x in value)


def marker_paths(value, path=""):
    if isinstance(value, str):
        return [path] if "TB4_OFFLINE_CODEX_CHILD" in value else []
    if isinstance(value, dict):
        return sum((marker_paths(x, path + "." + k) for k, x in value.items()), [])
    if isinstance(value, list):
        return sum((marker_paths(x, path + f"[{i}]") for i, x in enumerate(value)), [])
    return []


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        global STAGE
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        inputs = body.get("input", [])
        child = any("content" in x and marker(x["content"]) for x in inputs if isinstance(x, dict))
        REQUESTS.append({"model": body.get("model"), "reasoning": body.get("reasoning"), "child": child,
                         "marker_paths": marker_paths(body),
                         "message_roles": [x.get("role") for x in inputs if x.get("role")]})
        for item in inputs:
            if item.get("type") == "function_call_output":
                entry = {"call_id": item.get("call_id"), "output": item.get("output")}
                if entry not in OUTPUTS:
                    OUTPUTS.append(entry)
        if not child:
            STAGE += 1
        response_id = f"resp_offline_{len(REQUESTS)}"
        if not child and STAGE <= 3:
            model, effort = [("deepseek-v4-pro", "max"), ("deepseek-v4.1-flash", "low"), ("deepseek-v4.1-flash", "max")][STAGE - 1]
            arguments = {"task_name": f"probe_{STAGE}", "message": "TB4_OFFLINE_CODEX_CHILD: reply OFFLINE_CHILD_OK and finish.",
                         "fork_turns": "none", "model": model, "reasoning_effort": effort}
            if STAGE == 3 and FORK_ALL_TEST:
                arguments["fork_turns"] = "all"
                arguments.pop("model")
                arguments.pop("reasoning_effort")
            item = {"id": f"fc_{STAGE}", "type": "function_call", "status": "completed",
                    "call_id": f"call_spawn_{STAGE}", "name": "spawn_agent", "namespace": "collaboration",
                    "arguments": json.dumps(arguments)}
        elif not child and STAGE == 4:
            item = {"id": "fc_wait", "type": "function_call", "status": "completed", "call_id": "call_wait",
                    "name": "wait_agent", "namespace": "collaboration", "arguments": '{"timeout_ms":10000}'}
        else:
            item = {"id": f"msg_{len(REQUESTS)}", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "OFFLINE_CHILD_OK" if child else "OFFLINE_ROUTING_DONE", "annotations": []}]}
        response = {"id": response_id, "object": "response", "created_at": int(time.time()),
                    "status": "in_progress", "model": body.get("model"), "output": []}
        events = [{"type": "response.created", "response": response},
                  {"type": "response.output_item.added", "output_index": 0, "item": item},
                  {"type": "response.output_item.done", "output_index": 0, "item": item},
                  {"type": "response.completed", "response": {**response, "status": "completed", "output": [item],
                   "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                             "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}}]
        payload = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def command(args):
    return subprocess.run(args, capture_output=True, text=True, check=True)


def main():
    global FORK_ALL_TEST
    parser = argparse.ArgumentParser()
    parser.add_argument("--fork-all", action="store_true")
    options = parser.parse_args()
    FORK_ALL_TEST = options.fork_all
    network = "tb4-codex-offline-child-" + uuid.uuid4().hex[:8]
    command(["docker", "network", "create", "--internal", network])
    server = None
    try:
        gateway = json.loads(command(["docker", "network", "inspect", network]).stdout)[0]["IPAM"]["Config"][0]["Gateway"]
        server = ThreadingHTTPServer((gateway, 8768), Mock)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory(prefix="tb4-codex-routing-", dir=ROOT / "cache") as path:
            fixture = Path(path)
            fixture.chmod(0o755)
            shutil.copyfile(Path(__file__).with_name("models.json"), fixture / "models.json")
            (fixture / "config.toml").write_text(f'''model = "deepseek-v4.1-flash"
model_provider = "offline"
model_reasoning_effort = "max"
web_search = "disabled"
model_catalog_json = "/fixture/models.json"
[model_providers.offline]
name = "offline"
base_url = "http://{gateway}:8768"
wire_api = "responses"
env_key = "DUMMY_API_KEY"
''')
            args = ["docker", "run", "--rm", "--network", network, "--cpus", "2", "--memory", "4g", "--cgroup-parent", "tb4.slice",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{fixture}:/fixture", "-v", f"{ROOT}/artifacts/runtime:/runtime:ro", "-w", "/app",
                    "-e", "DUMMY_API_KEY=offline-no-live-credentials", "-e", "CODEX_HOME=/tmp/offline-codex-home",
                    "python:3.12-slim-bookworm", "bash", "-lc", '''set -eu
mkdir -p /tmp/offline-codex-home /tmp/codex
cp /fixture/config.toml /tmp/offline-codex-home/config.toml
tar -xzf /runtime/codex-0.149.1-linux-x64.tgz -C /tmp/codex
/tmp/codex/package/vendor/x86_64-unknown-linux-musl/bin/codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --json --enable unified_exec "Exercise the offline child-routing preflight."
mkdir /fixture/captured
cp -R /tmp/offline-codex-home/sessions /fixture/captured/sessions
''']
            completed = subprocess.run(args, capture_output=True, text=True, timeout=120)
            children = [r for r in REQUESTS if r["child"]]
            safe = bool(children) and all(r["model"] == "deepseek-v4.1-flash" and r["reasoning"]["effort"] == "max" for r in REQUESTS)
            sys.path.insert(0, str(ROOT / "control"))
            from adapters.harbor.tb4_agents.codex import DeepSeekCodex
            from harbor.models.agent.context import AgentContext
            agent = DeepSeekCodex(logs_dir=fixture / "captured")
            context = AgentContext()
            agent.populate_context_post_run(context)
            accounting_ok = context.n_input_tokens == len(REQUESTS) and context.n_output_tokens == len(REQUESTS)
            report = {"kind": "offline_mock_only", "live_provider_calls": 0, "external_container_network": False,
                      "exit_code": completed.returncode, "all_requests_flash_max": safe, "child_requests": len(children),
                      "valid_child_fork_turns": "all" if options.fork_all else "none",
                      "token_accounting_matches_mock_requests": accounting_ok,
                      "aggregated_context": context.model_dump(),
                      "request_metadata": REQUESTS, "tool_results": OUTPUTS, "stdout": completed.stdout, "stderr": completed.stderr}
            filename = "codex-offline-subagent-fork-all-smoke.json" if options.fork_all else "codex-offline-subagent-smoke.json"
            (ROOT / "logs" / filename).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({k: report[k] for k in ("kind", "live_provider_calls", "exit_code", "all_requests_flash_max", "child_requests", "token_accounting_matches_mock_requests", "aggregated_context", "request_metadata", "tool_results")}))
            if not safe or completed.returncode or not accounting_ok:
                raise SystemExit(1)
    finally:
        if server:
            server.shutdown()
        command(["docker", "network", "rm", network])


if __name__ == "__main__":
    main()
