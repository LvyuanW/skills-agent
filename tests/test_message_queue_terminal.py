"""Queued input, hover withdrawal, and selection through a real terminal."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import nullcontext
import base64
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

from tests import test_cli_terminal as terminal_helpers
from tests.sse import frame, responses_from_chat, stream_response
from tests.test_cli_terminal import Terminal
from tests.test_clipboard_input import BOOTSTRAP, PNG


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class MessageQueueTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    response = terminal_helpers.TerminalTests.response
    events = terminal_helpers.TerminalTests.events

    def gated_terminal(self, clipboard_fixtures=None):
        release = threading.Event()
        requests = []
        response = self.response

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                if self.path.endswith("/input_tokens"):
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":128}')
                    return
                requests.append(payload)
                count = len(requests)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                native = responses_from_chat(response(f"ANSWER_{count}"))
                encoded = stream_response(native)
                if count == 1:
                    # Keep actual model output in flight while typing and
                    # interacting with the queue, rather than delaying a call.
                    completed = frame({"type": "response.completed", "response": native})
                    self.wfile.write(encoded[:-len(completed)])
                    self.wfile.flush()
                    if not release.wait(10):
                        return
                    encoded = completed
                self.wfile.write(encoded)
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        if clipboard_fixtures is not None:
            fixture_path = self.workspace / "clipboard-fixtures.json"
            fixture_path.write_text(json.dumps(clipboard_fixtures))
            start_process = subprocess.Popen

            def start(command, **kwargs):
                return start_process(
                    [command[0], "-c", BOOTSTRAP, str(fixture_path), *command[3:]], **kwargs)

            launch = patch("tests.test_cli_terminal.subprocess.Popen", side_effect=start)
        else:
            launch = nullcontext()
        with launch:
            terminal = Terminal(self.workspace, f"http://127.0.0.1:{server.server_port}/v1")
        self.addCleanup(terminal.close)
        self.addCleanup(release.set)
        terminal.prompt()
        terminal.send("initial request\n")
        terminal.wait_for(lambda output: "ANSWER_1" in terminal.visible)
        return terminal, requests, release

    def user_messages(self):
        return [event["message"]["content"][0]["text"] for event in self.events()
                if event.get("type") == "message" and event["message"]["role"] == "user"]

    def queue_rows(self, terminal):
        return [(y, row) for y, row in enumerate(terminal.screen.display)
                if "排队中" in row or "撤回" in row]

    def test_typing_and_pasting_during_output_queues_fifo_without_early_history(self):
        terminal, requests, release = self.gated_terminal()
        terminal.send("queued alpha\n")
        terminal.wait_for(lambda output: "queued alpha" in terminal.visible
                          and "排队中" in terminal.visible)
        terminal.send("\x1b[200~queued beta\x1b[201~\n")
        terminal.wait_for(lambda output: len(self.queue_rows(terminal)) == 2)
        terminal.send("draft AC\x1b[DB")
        terminal.wait_for(lambda output: "> draft ABC" in terminal.visible)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.user_messages(), ["initial request"])
        rows = self.queue_rows(terminal)
        self.assertIn("queued alpha", rows[0][1])
        self.assertIn("queued beta", rows[1][1])
        draft_row = terminal.screen.cursor.y
        self.assertGreater(draft_row, rows[-1][0])
        self.assertTrue(terminal.screen.display[draft_row].lstrip().startswith("> draft ABC"))
        self.assertTrue(any(set(line.strip()) == {"─"}
                            for line in terminal.screen.display[rows[-1][0] + 1:draft_row]))

        release.set()
        terminal.wait_for(lambda output: "ANSWER_3" in terminal.visible
                          and not self.queue_rows(terminal))
        self.assertEqual(len(requests), 3)
        self.assertEqual(self.user_messages(), ["initial request", "queued alpha", "queued beta"])
        self.assertIn("> draft ABC", terminal.visible)
        self.assertLess(terminal.visible.index("ANSWER_1"), terminal.visible.index("queued alpha"))
        self.assertLess(terminal.visible.index("ANSWER_2"), terminal.visible.index("queued beta"))

    def test_cjk_preview_clips_hover_leaves_and_withdrawal_keeps_draft(self):
        terminal, requests, release = self.gated_terminal()
        pending = "排队预览" + "很长的中文内容" * 20 + "TAIL_MARKER"
        terminal.send(pending + "\n")
        terminal.wait_for(lambda output: "排队中" in terminal.visible)
        rows = self.queue_rows(terminal)
        self.assertEqual(len(rows), 1)
        y, row = rows[0]
        self.assertTrue(row.startswith("排队预览"))
        self.assertIn("…", row)
        self.assertNotIn("TAIL_MARKER", terminal.visible)
        self.assertEqual(terminal.screen.buffer[y][74].data, "排")
        self.assertEqual(terminal.screen.buffer[y][74].fg, "blue")
        self.assertGreater(terminal.screen.cursor.y, y)
        self.assertEqual(terminal.screen.display[terminal.screen.cursor.y].strip(), ">")
        terminal.send("keep this draft")
        terminal.wait_for(lambda output: "> keep this draft" in terminal.visible)

        terminal.send(f"\x1b[<35;78;{y + 1}M")
        terminal.wait_for(lambda output: "撤回" in terminal.visible)
        self.assertEqual(terminal.screen.buffer[y][74].fg, "red")
        terminal.send("\x1b[<35;2;2M")
        terminal.wait_for(lambda output: "排队中" in terminal.visible and "撤回" not in terminal.visible)
        self.assertEqual(terminal.screen.buffer[y][74].fg, "blue")
        terminal.send(f"\x1b[<35;78;{y + 1}M")
        terminal.wait_for(lambda output: "撤回" in terminal.visible)
        terminal.send(f"\x1b[<35;2;{y + 2}M")
        terminal.wait_for(lambda output: "排队中" in terminal.visible and "撤回" not in terminal.visible)
        terminal.send(f"\x1b[<0;78;{y + 1}M\x1b[<0;78;{y + 1}m")
        terminal.wait_for(lambda output: not self.queue_rows(terminal))
        self.assertIn("> keep this draft", terminal.visible)
        self.assertNotIn("排队预览", terminal.visible)
        release.set()
        terminal.wait_for(lambda output: any(
            event.get("type") == "message" and event["message"]["role"] == "assistant"
            for event in self.events()))
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.user_messages(), ["initial request"])

    def test_f2_keeps_queue_frame_stable_while_queued_turn_finishes(self):
        terminal, requests, release = self.gated_terminal()
        terminal.send("queued while selecting\n")
        terminal.wait_for(lambda output: "排队中" in terminal.visible)
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        terminal.read(0.1)
        frozen = terminal.visible
        release.set()
        terminal.wait_for(lambda output: len([
            event for event in self.events() if event.get("type") == "message"
            and event["message"]["role"] == "assistant"
        ]) == 2)
        terminal.read(0.2)
        self.assertEqual(terminal.visible, frozen)
        self.assertIn("排队中", terminal.visible)
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "ANSWER_2" in terminal.visible
                          and not self.queue_rows(terminal))
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.user_messages(), ["initial request", "queued while selecting"])

    def test_ctrl_v_attachments_during_output_stay_with_the_queued_message(self):
        image = self.workspace / "source.png"
        image.write_bytes(PNG)
        document = self.workspace / "notes.pdf"
        document_bytes = b"%PDF-1.7\nqueued document"
        document.write_bytes(document_bytes)
        terminal, requests, release = self.gated_terminal([
            {"image": str(image)}, {"files": [str(document)]},
        ])
        terminal.send("review \x16 and \x16\n")
        terminal.wait_for(lambda output: "排队中" in terminal.visible
                          and "[图片1]" in terminal.visible
                          and "[文件2:notes.pdf]" in terminal.visible)
        terminal.send("no attachments here\n")
        terminal.wait_for(lambda output: len(self.queue_rows(terminal)) == 2)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.user_messages(), ["initial request"])
        release.set()
        terminal.wait_for(lambda output: "ANSWER_3" in terminal.visible
                          and not self.queue_rows(terminal))
        self.assertEqual(len(requests), 3)
        content = requests[1]["input"][-1]["content"]
        self.assertEqual([block["type"] for block in content],
                         ["input_text", "input_text", "input_image", "input_file"])
        self.assertIn("file://", content[1]["text"])
        self.assertEqual(content[2]["image_url"],
                         "data:image/png;base64," + base64.b64encode(PNG).decode())
        self.assertEqual(content[3]["filename"], "notes.pdf")
        self.assertEqual(base64.b64decode(content[3]["file_data"].split(",", 1)[-1]),
                         document_bytes)
        self.assertEqual(requests[2]["input"][-1]["content"],
                         [{"type": "input_text", "text": "no attachments here"}])
        users = [event["message"] for event in self.events()
                 if event.get("type") == "message" and event["message"]["role"] == "user"]
        attachments = [block for block in users[1]["content"] if block.get("path")]
        self.assertEqual([Path(block["path"]).read_bytes() for block in attachments],
                         [PNG, document_bytes])
        self.assertEqual([block for block in users[2]["content"] if block.get("path")], [])

    def test_queue_taller_than_terminal_keeps_editor_and_footer_usable(self):
        terminal, requests, release = self.gated_terminal()
        queued = [f"queued message {number:02}" for number in range(30)]
        terminal.send("\n".join(queued) + "\nEDITABLE_WITH_LONG_QUEUE")
        terminal.wait_for(lambda output: "> EDITABLE_WITH_LONG_QUEUE" in terminal.visible
                          and "排队中" in terminal.visible)
        self.assertNotIn("Window too small", terminal.visible)
        self.assertIn("按 F2 选中文本", terminal.visible)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.user_messages(), ["initial request"])
        release.set()
        terminal.wait_for(lambda output: "ANSWER_31" in terminal.visible
                          and not self.queue_rows(terminal), timeout=15)
        self.assertEqual(self.user_messages(), ["initial request", *queued])
        self.assertIn("> EDITABLE_WITH_LONG_QUEUE", terminal.visible)


if __name__ == "__main__":
    unittest.main()
