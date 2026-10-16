"""Local SSE and real managed processes for background-job terminal integration tests."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shlex
import subprocess
import sys
import threading

from skills_agent.processes import HELPER
from tests.sse import frame, responses_from_chat, stream_response


PRIVATE_OUTPUT = "PRIVATE_BACKGROUND_STDOUT"
PRIVATE_ERROR = "PRIVATE_BACKGROUND_STDERR"
WAITING = "MODEL_STREAM_WAIT"
FINISHED = "MODEL_STREAM_DONE"


def response(text="", *, command=None, name="启动后台作业"):
    message = {"content": text or None}
    if command is not None:
        message["tool_calls"] = [{"id": "background-shell", "type": "function", "function": {
            "name": "shell", "arguments": json.dumps({"name": name, "command": command, "run_in_background": False}),
        }}]
    return {"choices": [{"finish_reason": "tool_calls" if command is not None else "stop",
                         "message": message}]}


class BackgroundJobsFixture:
    def __init__(self, workspace, jobs, *, services=()):
        self.workspace = workspace
        self.state = workspace / "background-state"
        self.jobs = jobs
        self.services = set(services)
        self.requests = []
        self.release_model = threading.Event()
        self.streaming = threading.Event()
        self.after_stream = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                if self.path.endswith("/input_tokens"):
                    encoded = b'{"input_tokens":128}'
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                fixture.requests.append(payload)
                count = len(fixture.requests)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    if count == 1:
                        self.wfile.write(stream_response(responses_from_chat(
                            response(command=fixture.start_command()))))
                    elif count == 2:
                        complete = responses_from_chat(response(WAITING + "\n" + FINISHED))
                        item = complete["output"][0]
                        prefix = [
                            {"type": "response.created", "response": {
                                "id": complete["id"], "status": "in_progress", "output": []}},
                            {"type": "response.output_item.added", "output_index": 0,
                             "item": {**item, "status": "in_progress", "content": []}},
                            {"type": "response.output_text.delta", "output_index": 0,
                             "content_index": 0, "delta": WAITING + "\n"},
                        ]
                        self.wfile.write(b"".join(frame(event) for event in prefix))
                        self.wfile.flush()
                        fixture.streaming.set()
                        if not fixture.release_model.wait(20):
                            return
                        self.wfile.write(frame({"type": "response.output_text.delta", "output_index": 0,
                                                "content_index": 0, "delta": FINISHED}))
                        self.wfile.write(frame({"type": "response.output_item.done", "output_index": 0,
                                                "item": item}))
                        self.wfile.write(frame({"type": "response.completed", "response": complete}))
                    else:
                        reply = fixture.after_stream[count - 3] if count - 3 < len(fixture.after_stream) else response("NEXT_TURN_DONE")
                        self.wfile.write(stream_response(responses_from_chat(reply)))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Interruption deliberately closes an unfinished SSE stream.
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def gate(self, name):
        return self.workspace / f"release-{name}"

    def record(self, name):
        path = self.state / f"{name}.json"
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return {}

    def helper(self, operation, name, *args):
        return [sys.executable, str(HELPER), operation, "--state-dir", str(self.state),
                "--name", name, *args]

    def start_command(self):
        commands = []
        for name, exit_code in self.jobs:
            code = ("from pathlib import Path\nimport sys,time\n"
                    f"print({PRIVATE_OUTPUT!r}, flush=True)\n"
                    f"print({PRIVATE_ERROR!r}, file=sys.stderr, flush=True)\n"
                    f"gate = Path({str(self.gate(name))!r})\n"
                    "while not gate.exists():\n    time.sleep(.02)\n"
                    f"raise SystemExit({exit_code})\n")
            commands.append(self.helper("start", name, *(["--service"] if name in self.services else []),
                                        "--", sys.executable, "-c", code))
        # One foreground shell starts all detached workers, then really returns.
        script = "import subprocess\n" + "\n".join(
            f"subprocess.run({command!r}, check=True)" for command in commands)
        return shlex.join([sys.executable, "-c", script])

    def stop_command(self, name):
        return shlex.join(self.helper("stop", name, "--grace", "0.1", "--timeout", "3"))

    def close(self):
        self.release_model.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for name, _ in self.jobs:
            if self.record(name).get("state") in {"starting", "running", "stopping"}:
                subprocess.run(self.helper("stop", name, "--grace", "0.1", "--timeout", "3"),
                               capture_output=True, timeout=5, check=False)
