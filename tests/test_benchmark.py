from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests.sse import stream_response, responses_from_chat

from skills_agent.benchmark import main
from skills_agent.processes import HELPER


class BenchmarkTests(unittest.TestCase):
    def run_server(self, reply):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/input_tokens"):
                    body = b'{"input_tokens":128}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                requests.append(payload)
                body = stream_response(responses_from_chat(reply(len(requests))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1", requests

    def command(self, root, endpoint):
        return [sys.executable, "-m", "skills_agent.benchmark", "--prompt-file", str(root / "prompt.txt"),
                "--workspace", str(root / "workspace"), "--log-dir", str(root / "logs"),
                "--model", "test-model", "--base-url", endpoint, "--api-format", "responses"]

    def response(self, command=None, text="completed", reason=None):
        message = {"content": text}
        if command:
            message["tool_calls"] = [{"id": "task", "type": "function", "function": {
                "name": "shell", "arguments": json.dumps({"name": "完成任务脚本", "command": command, "run_in_background": False}),
            }}]
        return {"choices": [{"finish_reason": reason or ("tool_calls" if command else "stop"),
                             "message": message}], "usage": {"prompt_tokens": 20, "completion_tokens": 10}}

    def test_one_task_exits_writes_requested_output_and_preserves_prompt_and_logs(self):
        script = "mkdir -p output\nprintf 'task artifact' > output/result.txt"
        endpoint, requests = self.run_server(lambda count: self.response(script) if count == 1 else self.response())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            prompt = "Write output/result.txt.\nPreserve this second instruction line.\n"
            (root / "prompt.txt").write_text(prompt)
            process = subprocess.run(self.command(root, endpoint), input="unused stdin", text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0]["input"][-1]["content"][0]["text"], prompt)
            self.assertEqual((root / "workspace/output/result.txt").read_text(), "task artifact")
            events = [json.loads(line) for line in (root / "logs/session/events.jsonl").read_text().splitlines()]
            self.assertEqual(events[0]["message"]["content"][0]["text"], prompt)
            self.assertEqual(events[-1]["message"]["content"][0]["text"], "completed")
            self.assertFalse((root / "workspace/.agent").exists())
            before = (root / "logs/session/events.jsonl").read_bytes()
            second = subprocess.run(self.command(root, endpoint), text=True, capture_output=True)
            self.assertEqual(second.returncode, 1)
            self.assertIn("session", second.stderr)
            self.assertEqual((root / "logs/session/events.jsonl").read_bytes(), before)
            self.assertEqual(len(requests), 2)

    def test_model_failure_returns_nonzero_and_keeps_received_partial_text(self):
        endpoint, _ = self.run_server(lambda count: self.response(text="original partial", reason="length"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            (root / "prompt.txt").write_text("task")
            process = subprocess.run(self.command(root, endpoint), text=True, capture_output=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(process.stdout.count("original partial"), 1)
            self.assertIn("回答未完成", process.stdout)
            events = [json.loads(line) for line in (root / "logs/session/events.jsonl").read_text().splitlines()]
            self.assertEqual(events[-1]["type"], "error")
            self.assertEqual(events[-1]["partial_content"], [{"type": "text", "text": "original partial"}])
            self.assertIn("_raw", events[-1])

    def test_config_only_model_settings_reach_the_request(self):
        endpoint, requests = self.run_server(lambda count: self.response())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (root / "prompt.txt").write_text("task")
            (workspace / "skills-agent.toml").write_text(
                f'model = "from-config"\nbase_url = "{endpoint}"\napi_format = "responses"\n'
                'max_output_tokens = 64000\nmodel_context_window = 1000000\n')
            command = [sys.executable, "-m", "skills_agent.benchmark",
                       "--workspace", str(workspace), "--prompt-file", str(root / "prompt.txt"),
                       "--log-dir", str(root / "logs")]
            process = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["model"], "from-config")
            self.assertEqual(requests[0]["max_output_tokens"], 64000)
            context = json.loads((root / "logs/session/context.json").read_text())
            self.assertEqual(context["last_usage"]["limit"], 936000)

    def test_empty_prompt_has_no_session_or_model_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prompt.txt").write_text(" \n")
            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(["--prompt-file", str(root / "prompt.txt"), "--workspace", directory,
                               "--log-dir", str(root / "logs")])
            self.assertEqual(status, 1)
            self.assertFalse((root / "logs/session").exists())

    @unittest.skipUnless(sys.platform != "win32", "POSIX process groups")
    def test_sigterm_stops_agent_and_running_script(self):
        script = (
            "python3 -u - <<'PY'\n"
            "from pathlib import Path\nimport time\n"
            "with Path('heartbeat').open('w') as f:\n"
            "    while True:\n        f.write('x'); f.flush(); time.sleep(0.02)\nPY"
        )
        endpoint, _ = self.run_server(lambda count: self.response(script))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            (root / "prompt.txt").write_text("run script")
            process = subprocess.Popen(self.command(root, endpoint), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                heartbeat = root / "workspace/heartbeat"
                deadline = time.monotonic() + 5
                while not heartbeat.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(heartbeat.exists())
                process.terminate()
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, stdout + stderr)
                self.assertIn("Interrupted", stdout)
                size = heartbeat.stat().st_size
                time.sleep(0.1)
                self.assertEqual(heartbeat.stat().st_size, size)
                events = [json.loads(line) for line in (root / "logs/session/events.jsonl").read_text().splitlines()]
                self.assertEqual({k: events[-1][k] for k in ("type", "message")}, {"type": "error", "message": "Interrupted"})
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "Managed POSIX processes")
    def test_background_writer_stops_before_benchmark_returns_on_every_exit_path(self):
        for outcome, expected in (("complete", 0), ("failure", 1), ("sigterm", 130)):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                workspace.mkdir()
                state = root / "process-state"
                heartbeat = workspace / "heartbeat"
                (root / "prompt.txt").write_text("start a background writer")
                writer = "import pathlib,time; p=pathlib.Path('heartbeat'); exec(\"while True:\\n with p.open('a') as f: f.write('x')\\n time.sleep(.02)\")"
                start = [sys.executable, str(HELPER), "start", "--state-dir", str(state),
                         "--name", "writer", "--service", "--", sys.executable, "-c", writer]
                script = shlex.join(start)
                if outcome == "sigterm":
                    script += "\nsleep 30"
                endpoint, _ = self.run_server(lambda count: self.response(script) if count == 1 else
                                              self.response(reason="length" if outcome == "failure" else None))
                process = subprocess.Popen(self.command(root, endpoint), stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True)
                try:
                    if outcome == "sigterm":
                        deadline = time.monotonic() + 5
                        while not heartbeat.exists() and process.poll() is None and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertTrue(heartbeat.exists())
                        process.terminate()
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, expected, stdout + stderr)
                    record = json.loads((state / "writer.json").read_text())
                    self.assertEqual(record["state"], "stopped")
                    for pid in (record["child_pid"], record["supervisor_pid"]):
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                    size = heartbeat.stat().st_size if heartbeat.exists() else 0
                    time.sleep(.1)
                    self.assertEqual(heartbeat.stat().st_size if heartbeat.exists() else 0, size)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=10)
                    if (state / "writer.json").exists():
                        subprocess.run([sys.executable, str(HELPER), "stop", "--state-dir", str(state),
                                        "--name", "writer", "--grace", ".1", "--timeout", "3"],
                                       capture_output=True, timeout=5)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "Managed POSIX processes")
    def test_cli_sigterm_exits_session_and_stops_its_background_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "process-state"
            script = shlex.join([sys.executable, str(HELPER), "start", "--state-dir", str(state),
                                 "--name", "writer", "--service", "--", sys.executable, "-c", "import time; time.sleep(30)"])
            endpoint, requests = self.run_server(lambda count: self.response(script) if count == 1 else self.response())
            argv = [sys.executable, "-m", "skills_agent", "run", "--workspace", str(root),
                    "--model", "test-model", "--base-url", endpoint, "--api-format", "responses"]
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                process.stdin.write("start a service\n")
                process.stdin.flush()
                deadline = time.monotonic() + 5
                while len(requests) < 2 and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(len(requests), 2)
                events_path = root / ".agent/sessions/default/events.jsonl"
                while time.monotonic() < deadline:
                    events = [json.loads(line) for line in events_path.read_text().splitlines()]
                    if any(event.get("type") == "message" and event["message"].get("role") == "assistant"
                           and not event["message"].get("tool_calls") for event in events):
                        break
                    time.sleep(.02)
                time.sleep(.05)  # The completed CLI turn is now waiting for its next input.
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 143, stdout + stderr)
                record = json.loads((state / "writer.json").read_text())
                self.assertEqual(record["state"], "stopped")
                with self.assertRaises(ProcessLookupError):
                    os.kill(record["child_pid"], 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=10)
                if (state / "writer.json").exists():
                    subprocess.run([sys.executable, str(HELPER), "stop", "--state-dir", str(state),
                                    "--name", "writer", "--grace", ".1", "--timeout", "3"],
                                   capture_output=True, timeout=5)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "Managed POSIX processes")
    def test_cleanup_failure_is_logged_and_cannot_report_benchmark_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            (root / "prompt.txt").write_text("task")
            state = root / "process-state"
            start = shlex.join([sys.executable, str(HELPER), "start", "--state-dir", str(state),
                                "--name", "writer", "--", sys.executable, "-c", "import time; time.sleep(30)"])
            corrupt = ("import json,os,pathlib; p=next((pathlib.Path(os.environ['SKILLS_AGENT_PROCESS_SCOPE'])/'runs').glob('*.json')); "
                       "r=json.loads(p.read_text()); r['supervisor_identity']={'started':'incorrect','command':'incorrect'}; p.write_text(json.dumps(r))")
            script = start + "\n" + shlex.join([sys.executable, "-c", corrupt])
            endpoint, _ = self.run_server(lambda count: self.response(script) if count == 1 else self.response())
            try:
                result = subprocess.run(self.command(root, endpoint), capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                events = [json.loads(line) for line in (root / "logs/session/events.jsonl").read_text().splitlines()]
                self.assertEqual(events[-1]["type"], "error")
                self.assertIn("cleanup was not confirmed", events[-1]["message"])
                self.assertIn("cleanup", result.stdout)
            finally:
                if (state / "writer.json").exists():
                    stopped = subprocess.run([sys.executable, str(HELPER), "stop", "--state-dir", str(state),
                                              "--name", "writer", "--grace", ".1", "--timeout", "3"],
                                             capture_output=True, timeout=5)
                    self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "Managed POSIX processes")
    def test_cli_sigterm_during_a_tool_stops_background_work_and_records_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "process-state"
            script = shlex.join([sys.executable, str(HELPER), "start", "--state-dir", str(state),
                                 "--name", "writer", "--", sys.executable, "-c", "import time; time.sleep(30)"]) + "\nsleep 30"
            endpoint, _ = self.run_server(lambda count: self.response(script))
            process = subprocess.Popen([sys.executable, "-m", "skills_agent", "run", "--workspace", str(root),
                                        "--model", "test-model", "--base-url", endpoint, "--api-format", "responses"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                process.stdin.write("start\n")
                process.stdin.flush()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    path = state / "writer.json"
                    if path.exists() and json.loads(path.read_text())["state"] == "running":
                        break
                    time.sleep(.02)
                self.assertTrue(path.exists())
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 143, stdout + stderr)
                self.assertIn("Interrupted", stdout)
                record = json.loads(path.read_text())
                self.assertEqual(record["state"], "stopped")
                with self.assertRaises(ProcessLookupError):
                    os.kill(record["child_pid"], 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=10)
                if (state / "writer.json").exists():
                    subprocess.run([sys.executable, str(HELPER), "stop", "--state-dir", str(state),
                                    "--name", "writer", "--grace", ".1", "--timeout", "3"],
                                   capture_output=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
