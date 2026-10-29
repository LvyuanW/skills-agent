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

import pyte
from wcwidth import wcwidth

from tests.sse import stream_response, responses_from_chat

if os.name == "posix":
    import fcntl
    import pty
    import termios


USER_COLOR = "\x1b[97;40m"
ASSISTANT_COLOR = "\x1b[30;107m"
TOOL_COLOR = "\x1b[34;49m"
PROJECT = Path(__file__).resolve().parents[1]


class TerminalScreen(pyte.HistoryScreen):
    @property
    def display(self):
        # pyte leaves the other half of a wide glyph behind when a redraw
        # overwrites/erases just one cell. Real terminals clear that half.
        # Render those incomplete glyphs as blanks rather than treating their
        # empty continuation cells as ordinary characters (which pyte indexes).
        rendered = []
        for y in range(self.lines):
            row, x = [], 0
            while x < self.columns:
                data = self.buffer[y][x].data
                if not data:
                    row.append(" ")
                elif wcwidth(data[0]) == 2 and x + 1 < self.columns:
                    if self.buffer[y][x + 1].data == "":
                        row.append(data)
                        x += 1
                    else:
                        row.append(" ")
                else:
                    row.append(data)
                x += 1
            rendered.append("".join(row))
        return rendered

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


def terminal_text(text):
    screen = terminal_screen(text)
    return "\n".join(screen.display).rstrip()


def terminal_owner():
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    def __init__(self, workspace, endpoint):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        self.output = b""
        self.endpoint = endpoint
        self._prompt_output = 0
        self.screen = TerminalScreen(80, 24, history=10000)
        self.screen.write_process_input = self.send
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "skills_agent", "run", "--workspace", str(workspace),
                 "--sessions-dir", str(workspace / ".agent/sessions"),
                 "--data-dir", str(workspace / ".test-runtime"),
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

    @property
    def visible(self):
        return "\n".join(self.screen.display).rstrip()

    def prompt(self, expected=None):
        if expected is not None:
            self.wait_for(lambda output: expected in self.visible)
        # A full-screen application enables bracketed paste once, rather than
        # once per submitted message. Check the freshly rendered input instead.
        def ready(output):
            row = self.screen.buffer[self.screen.cursor.y]
            line = "".join(row[x].data for x in range(self.screen.columns)).rstrip()
            return len(output) > self._prompt_output and line == ">"
        result = self.wait_for(ready)
        self._prompt_output = len(result)
        return result

    def click(self, marker):
        for y, line in enumerate(self.screen.display):
            if marker in line:
                x = line.index(marker) + 1
                self.send(f"\x1b[<0;{x};{y + 1}M\x1b[<0;{x};{y + 1}m")
                return
        raise AssertionError(f"Clickable marker {marker!r} is not visible:\n{self.visible}")

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


class TerminalScreenTests(unittest.TestCase):
    def screen(self, text):
        screen = TerminalScreen(10, 1)
        pyte.Stream(screen).feed(text)
        return screen

    def test_overwriting_first_half_of_wide_glyph_leaves_a_blank_second_cell(self):
        screen = self.screen("中\x1b[1;1HA")
        self.assertEqual(screen.display, ["A" + " " * 9])

    def test_overwriting_second_half_of_wide_glyph_does_not_hide_new_character(self):
        screen = self.screen("中\x1b[1;2HA")
        self.assertEqual(screen.display, [" A" + " " * 8])

    def test_erasing_one_half_of_wide_glyph_removes_its_remaining_half(self):
        for column in (1, 2):
            with self.subTest(column=column):
                screen = self.screen(f"中\x1b[1;{column}H\x1b[1X")
                self.assertEqual(screen.display, [" " * 10])

    def test_intact_wide_and_combining_characters_keep_their_visible_width(self):
        screen = self.screen("中e\u0301A")
        self.assertEqual(screen.display, ["中éA" + " " * 6])
        self.assertEqual(screen.buffer[0][1].data, "", "A valid continuation is still preserved")


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
            arguments = {"command": command, "name": summary or "运行测试脚本", "run_in_background": False}
            message["tool_calls"] = [{"id": "shell-1", "type": "function", "function": {
                "name": "shell", "arguments": json.dumps(arguments),
            }}]
        return {"choices": [{"finish_reason": "tool_calls" if command is not None else "stop",
                             "message": message}]}

    def terminal(self, respond):
        endpoint, requests = self.server(respond)
        terminal = Terminal(self.workspace, endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt()
        return terminal, requests

    def assert_color(self, text, marker, expected):
        screen = terminal_screen(text)
        rows = [screen.buffer[y] for y in range(screen.lines)]
        colors = {USER_COLOR: ("brightwhite", "black"),
                  ASSISTANT_COLOR: ("black", "brightwhite"),
                  TOOL_COLOR: ("blue", "default")}
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
        terminal.prompt("FIRST_FINAL")
        second = "second unique terminal request"
        terminal.send(second + "\n")
        text = terminal.prompt("SECOND_FINAL")
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
        self.assertNotIn("shell「运行测试脚本」：调用", plain)
        self.assertEqual(plain.count("shell「运行测试脚本」"), 1)
        self.assert_color(text, "shell「运行测试脚本」：完成", TOOL_COLOR)
        self.assert_color(text, "FIRST_FINAL", ASSISTANT_COLOR)
        self.assert_color(text, "SECOND_FINAL", ASSISTANT_COLOR)
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

    def test_timer_updates_above_input_while_waiting_for_model_then_freezes(self):
        release = threading.Event()

        def respond(payload, count):
            release.wait(timeout=5)
            return self.response("TIMED_RESPONSE")

        terminal, requests = self.terminal(respond)
        self.addCleanup(release.set)
        self.assertNotIn("耗时", terminal.visible)
        terminal.send("timed request\n")
        timer_row = terminal.screen.lines - 4

        def timer_text():
            return terminal.screen.display[timer_row].strip()

        terminal.wait_for(lambda output: timer_text().startswith("耗时 "))
        first = timer_text()
        terminal.wait_for(lambda output: timer_text() != first, timeout=2)
        self.assertRegex(timer_text(), r"^耗时 \d+\.\d 秒  停止$")
        self.assertEqual(terminal.screen.display[timer_row + 1].strip(), ">")
        self.assertEqual(terminal.screen.buffer[timer_row][0].bg, "default")
        release.set()
        terminal.prompt("TIMED_RESPONSE")
        # Allow the final response and finish_turn redraws to reach the PTY.
        terminal.read(0.1)
        final = timer_text()
        self.assertRegex(final, r"^耗时 \d+\.\d 秒$")
        for _ in range(3):
            terminal.read(0.1)
        self.assertEqual(timer_text(), final)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("耗时", json.dumps(self.events(), ensure_ascii=False))

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
                terminal.prompt(f"EDIT_DONE_{count}")
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
        terminal.prompt("HISTORY_DONE_1")
        terminal.send("\x1b[A\x01edited \x05 again\n")
        text = terminal.prompt("HISTORY_DONE_2")
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
        terminal.send("cancel this draft")
        terminal.wait_for(lambda output: "cancel this draft" in terminal.visible)
        terminal.send("\x03")
        terminal.prompt()
        self.assertNotIn("cancel this draft", terminal.visible)
        instruction = "cancel running terminal script"
        terminal.send(instruction + "\n")
        heartbeat = self.workspace / "heartbeat"
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            terminal.read()
        self.assertTrue(heartbeat.exists())
        terminal.send("\x03")
        interrupted = terminal.prompt("Interrupted")
        self.assertIn("Interrupted", interrupted)
        size = heartbeat.stat().st_size
        time.sleep(0.1)
        self.assertEqual(heartbeat.stat().st_size, size)
        terminal.send("continue after interrupt\n")
        continued = terminal.prompt("AFTER_INTERRUPT")
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
        terminal.prompt("paragraph 19")
        message = "长行中文测试" * 300 + "\n粘贴的第二行"
        terminal.send("\x1b[200~" + message + "\x1b[201~")
        terminal.wait_for(lambda output: "粘贴的第二行" in output)
        terminal.send("\n")
        terminal.prompt("REPLIED")
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": message}])
        def viewport(terminal):
            return [[(terminal.screen.buffer[y][x].data,
                      terminal.screen.buffer[y][x].fg,
                      terminal.screen.buffer[y][x].bg)
                     for x in range(terminal.screen.columns)]
                    # Elapsed time belongs to this run, not replayed messages.
                    for y in range(terminal.screen.lines) if y != terminal.screen.lines - 4]

        live_view = viewport(terminal)
        self.assertIn("粘贴的第二行", terminal.visible)
        self.assertIn("REPLIED", terminal.visible)
        self.assertNotIn("> 长行", terminal.visible)
        terminal.send("/exit\n")
        terminal.wait_exit()
        replay = Terminal(self.workspace, terminal.endpoint)
        self.addCleanup(replay.close)
        replay.prompt()
        self.assertEqual(live_view, viewport(replay))
        self.assertEqual(replay.visible.count("粘贴的第二行"), 1)


if __name__ == "__main__":
    unittest.main()
