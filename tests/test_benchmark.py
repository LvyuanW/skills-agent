from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests.sse import stream_response, responses_from_chat

from skills_agent.benchmark import main


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
                "name": "shell", "arguments": json.dumps({"name": "完成任务脚本", "command": command}),
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


if __name__ == "__main__":
    unittest.main()
