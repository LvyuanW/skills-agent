import codecs
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
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
from unittest.mock import patch

import pyte

from skills_agent.cli import ConsoleRenderer
from tests.sse import stream_response, responses_from_chat
from tests.test_markdown import Output

if os.name == "posix":
    import fcntl
    import pty
    import termios


USER_COLOR = "\x1b[97;40m"
ASSISTANT_COLOR = "\x1b[30;107m"
TOOL_COLOR = "\x1b[34;107m"
RESET = "\x1b[0m"
PROJECT = Path(__file__).resolve().parents[1]


class TerminalScreen(pyte.HistoryScreen):
    def index(self):
        bottom = self.margins.bottom if self.margins else self.lines - 1
        scroll = self.cursor.y == bottom
        super().index()
        if scroll:
            # Real terminals use the active background for newly scrolled rows.
            # pyte defaults them, hiding residual input colors in unused cells.
            blank = self.default_char._replace(bg=self.cursor.attrs.bg)
            self.buffer[bottom].update({x: blank for x in range(self.columns)})


def terminal_screen(text):
    screen = TerminalScreen(80, 24, history=10000)
    pyte.Stream(screen).feed(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    return screen


def terminal_rows(screen):
    return [*screen.history.top, *(screen.buffer[y] for y in range(screen.lines))]


def terminal_text(text):
    return "\n".join("".join(row[x].data for x in range(80)).rstrip()
                     for row in terminal_rows(terminal_screen(text))).rstrip()


def terminal_owner():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    def __init__(self, workspace, endpoint):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        self.output = b""
        self.screen = TerminalScreen(80, 24, history=10000)
        self.screen.write_process_input = self.send
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "skills_agent", "run", "--workspace", str(workspace),
                 "--session", "terminal", "--model", "test-model", "--base-url", endpoint,
                 "--api-format", "responses"],
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
        self.stream.feed(self.decoder.decode(chunk))
        return bool(chunk)

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate(self.text):
            if time.monotonic() >= deadline or not self.read():
                raise AssertionError(f"Expected terminal output did not arrive:\n{self.text}")
        return self.text

    def prompt(self, count):
        def ready(output):
            row = self.screen.buffer[self.screen.cursor.y]
            line = "".join(row[x].data for x in range(self.screen.columns)).rstrip()
            return output.count("\x1b[?2004h") >= count and line == ">"
        return self.wait_for(ready)

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
                if self.path.endswith("/input_tokens"):
                    encoded = b'{"input_tokens":128}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                requests.append(payload)
                encoded = stream_response(responses_from_chat(respond(payload, len(requests))))
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
            arguments = {"command": command, "name": summary or "运行测试脚本"}
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

    def assert_reset(self, text):
        cursor = terminal_screen(text).cursor
        self.assertEqual((cursor.attrs.fg, cursor.attrs.bg), ("default", "default"))
        self.assertFalse(cursor.hidden)

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
        self.assertEqual(plain.count("user:"), 2)
        self.assertNotIn("> " + first, plain)
        self.assertNotIn("> " + second, plain)
        self.assertNotIn("RAW_TERMINAL_OUTPUT", plain)
        self.assertEqual(plain.count("FIRST_FINAL"), 1)
        self.assertEqual(plain.count("SECOND_FINAL"), 1)
        self.assertNotIn("**FIRST_FINAL**", plain)
        self.assertNotIn("`SECOND_FINAL`", plain)
        self.assert_color(text, first, USER_COLOR)
        self.assert_color(text, second, USER_COLOR)
        self.assert_color(text, "shell「运行测试脚本」：调用", TOOL_COLOR)
        self.assert_color(text, "shell「运行测试脚本」：完成", TOOL_COLOR)
        self.assert_color(text, "FIRST_FINAL", ASSISTANT_COLOR)
        self.assert_color(text, "SECOND_FINAL", ASSISTANT_COLOR)
        for marker in ("FIRST_FINAL", "SECOND_FINAL"):
            following = text[text.index(marker) + len(marker):]
            self.assertIn(RESET, following[:following.index("> ")])
        terminal.send("/exit\n")
        finished = terminal.wait_exit()
        self.assertEqual(terminal.process.returncode, 0)
        self.assert_reset(finished)
        self.assertEqual(len(requests), 3)
        users = [event["message"] for event in self.events()
                 if event.get("type") == "message" and event["message"]["role"] == "user"]
        self.assertEqual([item["content"][0]["text"] for item in users], [first, second])

    def test_ctrl_d_exits_without_model_call_and_resets_prompt_color(self):
        terminal, requests = self.terminal(lambda payload, count: self.response("unexpected"))
        self.assert_color(terminal.text, ">", USER_COLOR)
        terminal.send("\x04")
        finished = terminal.wait_exit()
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual(requests, [])
        self.assert_reset(finished)

    def test_input_edits_with_cursor_keys_and_submits_only_edited_text(self):
        terminal, requests = self.terminal(
            lambda payload, count: self.response(f"**EDIT_DONE_{count}**"))
        edits = [
            ("ac\x1b[Db\x1b[Cd", "abcd"),
            ("abXYc\x1b[D\x1b[D\x1b[3~\x7f", "abc"),
            ("middle\x01start \x05 end", "start middle end"),
            ("你X好界\x1b[D\x1b[D\x1b[D\x1b[3~\x1b[C世\x1b[C", "你好世界"),
        ]
        for count, (keys, expected) in enumerate(edits, 1):
            with self.subTest(expected=expected):
                terminal.send(keys + "\n")
                terminal.prompt(count + 1)
                self.assertEqual(len(requests), count)
                self.assertEqual(requests[-1]["input"][-1]["content"],
                                 [{"type": "input_text", "text": expected}])
        users = [event["message"]["content"][0]["text"] for event in self.events()
                 if event.get("type") == "message" and event["message"]["role"] == "user"]
        self.assertEqual(users, [expected for _, expected in edits])
        self.assertNotIn("^[[", terminal.text)

    def test_up_history_is_editable_after_markdown_reply(self):
        terminal, requests = self.terminal(
            lambda payload, count: self.response(f"**HISTORY_DONE_{count}**\n\n- `ready`"))
        terminal.send("previous request\n")
        terminal.prompt(2)
        terminal.send("\x1b[A\x01edited \x05 again\n")
        text = terminal.prompt(3)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": "edited previous request again"}])
        self.assertNotIn("**HISTORY_DONE_1**", terminal_text(text))
        self.assertNotIn("^[[", text)
        self.assert_color(text, "edited previous request again", USER_COLOR)
        self.assert_color(text, "HISTORY_DONE_2", ASSISTANT_COLOR)

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
        self.assertEqual(plain.count("user:"), 2)
        terminal.send("/exit\n")
        self.assert_reset(terminal.wait_exit())
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual(len(requests), 2)
        self.assertTrue(any(event.get("type") == "error" and event["message"] == "Interrupted"
                            for event in self.events()))

    def test_live_messages_match_replay_including_unused_background_cells(self):
        terminal, requests = self.terminal(lambda payload, count: self.response(
            "\n\n".join(f"paragraph {index}" for index in range(20)) if count == 1
            else "**REPLIED** with `Markdown`"))
        terminal.send("fill terminal\n")
        terminal.prompt(2)
        message = "长行中文测试" * 300 + "\n粘贴的第二行"
        terminal.send("\x1b[200~" + message + "\x1b[201~")
        terminal.wait_for(lambda output: "粘贴的第二行" in output)
        terminal.send("\n")
        live = terminal.prompt(3)
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": message}])
        replay = Output()
        with patch.dict(os.environ, {"COLUMNS": "80", "LINES": "24"}):
            renderer = ConsoleRenderer(replay, live=False)
            for event in self.events():
                renderer(event)
            renderer.close()

        def message_rows(text):
            rows = []
            for row in terminal_rows(terminal_screen(text)):
                cells = [row[x] for x in range(80)]
                line = "".join(cell.data for cell in cells).rstrip()
                rows.append((line, [(cell.fg, cell.bg) for cell in cells]))
            messages = [index for index, (line, _) in enumerate(rows) if line and line != ">"]
            return rows[messages[0]:messages[-1] + 1]

        self.assertEqual(message_rows(live), message_rows(replay.getvalue()))
        text = terminal_text(live)
        self.assertEqual(text.count("user:"), 2)
        self.assertEqual(text.count("粘贴的第二行"), 1)
        self.assertNotIn("> fill terminal", text)
        self.assertNotIn("> 长行", text)


if __name__ == "__main__":
    unittest.main()
