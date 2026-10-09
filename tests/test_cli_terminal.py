import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests.sse import stream_response
from tests.test_markdown import terminal_screen, terminal_text

if os.name == "posix":
    import fcntl
    import pty
    import termios


USER_COLOR = "\x1b[97;40m"
ASSISTANT_COLOR = "\x1b[30;107m"
TOOL_COLOR = "\x1b[34;107m"
RESET = "\x1b[0m"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
PROJECT = Path(__file__).resolve().parents[1]


def terminal_owner():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    def __init__(self, workspace, endpoint):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        self.output = b""
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "skills_agent", "run", "--workspace", str(workspace),
                 "--session", "terminal", "--model", "test-model", "--base-url", endpoint],
                stdin=slave, stdout=slave, stderr=slave, cwd=PROJECT,
                env={"PATH": os.defpath, "PYTHONPATH": str(PROJECT), "TERM": "xterm-256color"},
                start_new_session=True, preexec_fn=terminal_owner,
            )
        finally:
            os.close(slave)

    @property
    def text(self):
        return self.output.decode("utf-8", errors="replace")

    def send(self, text):
        os.write(self.master, text.encode())

    def read(self, timeout=0.1):
        if not select.select([self.master], [], [], timeout)[0]:
            return True
        try:
            chunk = os.read(self.master, 65536)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return False
            raise
        self.output += chunk
        return bool(chunk)

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate(self.text):
            if time.monotonic() >= deadline or not self.read():
                raise AssertionError(f"Expected terminal output did not arrive:\n{self.text}")
        return self.text

    def prompt(self, count):
        return self.wait_for(lambda output: ANSI.sub("", output).count("> ") >= count)

    def wait_exit(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self.read():
                break
        self.process.wait(timeout=max(0.1, deadline - time.monotonic()))
        return self.text

    def close(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            self.send("/exit\n")
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        os.close(self.master)


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class TerminalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)

    def server(self, respond):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                encoded = stream_response(respond(payload, len(requests)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1", requests

    def response(self, text="", *, command=None, summary=None):
        message = {"content": text or None}
        if command is not None:
            arguments = {"command": command}
            if summary is not None:
                arguments["summary"] = summary
            message["tool_calls"] = [{"id": "shell-1", "type": "function", "function": {
                "name": "shell", "arguments": json.dumps(arguments),
            }}]
        return {"choices": [{"finish_reason": "tool_calls" if command is not None else "stop",
                             "message": message}]}

    def terminal(self, respond):
        endpoint, requests = self.server(respond)
        terminal = Terminal(self.workspace, endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt(1)
        return terminal, requests

    def assert_color(self, text, marker, expected):
        screen = terminal_screen(text)
        rows = [*screen.history.top, *(screen.buffer[y] for y in range(screen.lines))]
        colors = {USER_COLOR: ("brightwhite", "black"),
                  ASSISTANT_COLOR: ("black", "brightwhite"),
                  TOOL_COLOR: ("blue", "brightwhite")}
        for row in rows:
            chars = [row[x] for x in range(screen.columns) if row[x].data]
            line = "".join(char.data for char in chars)
            if marker in line:
                cell = chars[line.index(marker)]
                self.assertEqual((cell.fg, cell.bg), colors[expected])
                return
        self.fail(f"Marker {marker!r} is not displayed in the terminal")

    def events(self):
        raw = (self.workspace / ".agent/sessions/terminal/events.jsonl").read_text()
        self.assertNotIn("\x1b", raw)
        self.assertNotIn("\\u001b", raw)
        return [json.loads(line) for line in raw.splitlines()]

    def test_real_tty_shows_each_request_once_with_colors_and_resets(self):
        def respond(payload, count):
            if count == 1:
                return self.response(command="printf RAW_TERMINAL_OUTPUT", summary="运行测试脚本")
            return self.response("**FIRST_FINAL**" if count == 2 else "`SECOND_FINAL`")

        terminal, requests = self.terminal(respond)
        first = "first unique terminal request"
        terminal.send(first + "\n")
        terminal.prompt(2)
        second = "second unique terminal request"
        terminal.send(second + "\n")
        text = terminal.prompt(3)
        plain = terminal_text(text)
        self.assertEqual(plain.count(first), 1)
        self.assertEqual(plain.count(second), 1)
        self.assertNotIn("user:", plain)
        self.assertNotIn("RAW_TERMINAL_OUTPUT", plain)
        self.assertEqual(plain.count("FIRST_FINAL"), 1)
        self.assertEqual(plain.count("SECOND_FINAL"), 1)
        self.assertNotIn("**FIRST_FINAL**", plain)
        self.assertNotIn("`SECOND_FINAL`", plain)
        self.assert_color(text, first, USER_COLOR)
        self.assert_color(text, second, USER_COLOR)
        self.assert_color(text, "shell：运行测试脚本", TOOL_COLOR)
        self.assert_color(text, "shell：完成", TOOL_COLOR)
        self.assert_color(text, "FIRST_FINAL", ASSISTANT_COLOR)
        self.assert_color(text, "SECOND_FINAL", ASSISTANT_COLOR)
        for marker in ("FIRST_FINAL", "SECOND_FINAL"):
            following = text[text.index(marker) + len(marker):]
            self.assertIn(RESET, following[:following.index("> ")])
        terminal.send("/exit\n")
        finished = terminal.wait_exit()
        self.assertEqual(terminal.process.returncode, 0)
        self.assertTrue(finished.rstrip().endswith(RESET))
        self.assertEqual(len(requests), 3)
        users = [event["message"] for event in self.events()
                 if event.get("type") == "message" and event["message"]["role"] == "user"]
        self.assertEqual([item["content"][0]["text"] for item in users], [first, second])

    def test_ctrl_d_exits_without_model_call_and_resets_prompt_color(self):
        terminal, requests = self.terminal(lambda payload, count: self.response("unexpected"))
        terminal.send("\x04")
        finished = terminal.wait_exit()
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual(requests, [])
        self.assertIn(USER_COLOR, finished)
        self.assertTrue(finished.rstrip().endswith(RESET))

    def test_ctrl_c_resets_colors_cancels_script_and_keeps_conversation_open(self):
        script = (
            "from pathlib import Path\nimport time\n"
            "with Path('heartbeat').open('w') as output:\n"
            "    for _ in range(500):\n"
            "        output.write('x'); output.flush(); time.sleep(0.02)\n"
        )
        command = shlex.join([sys.executable, "-u", "-c", script])

        def respond(payload, count):
            return (self.response(command=command, summary="运行等待中断的脚本") if count == 1
                    else self.response("AFTER_INTERRUPT"))

        terminal, requests = self.terminal(respond)
        terminal.send("\x03")
        idle = terminal.prompt(2)
        self.assertIn(RESET, idle[idle.index("> ") + 2:idle.rindex("> ")])
        instruction = "cancel running terminal script"
        terminal.send(instruction + "\n")
        heartbeat = self.workspace / "heartbeat"
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            terminal.read()
        self.assertTrue(heartbeat.exists())
        terminal.send("\x03")
        interrupted = terminal.prompt(3)
        self.assertIn("Interrupted", interrupted)
        size = heartbeat.stat().st_size
        time.sleep(0.1)
        self.assertEqual(heartbeat.stat().st_size, size)
        terminal.send("continue after interrupt\n")
        continued = terminal.prompt(4)
        self.assert_color(continued, "AFTER_INTERRUPT", ASSISTANT_COLOR)
        plain = terminal_text(continued)
        self.assertEqual(plain.count(instruction), 1)
        self.assertEqual(plain.count("continue after interrupt"), 1)
        self.assertNotIn("user:", plain)
        terminal.send("/exit\n")
        self.assertTrue(terminal.wait_exit().rstrip().endswith(RESET))
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual(len(requests), 2)
        self.assertTrue(any(event.get("type") == "error" and event["message"] == "Interrupted"
                            for event in self.events()))


if __name__ == "__main__":
    unittest.main()
