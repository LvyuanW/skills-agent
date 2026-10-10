"""Stop through the real terminal without losing output or queued messages."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import unittest

from tests import test_cli_terminal as terminal_helpers
from tests.sse import frame, responses_from_chat, stream_response
from tests.test_cli_terminal import Terminal


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class StopTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    response = terminal_helpers.TerminalTests.response
    events = terminal_helpers.TerminalTests.events
    server = terminal_helpers.TerminalTests.server
    terminal = terminal_helpers.TerminalTests.terminal

    def cell(self, terminal, marker):
        # The elapsed label contains wide characters. Mouse coordinates count
        # terminal cells, not Python string positions.
        for y in range(terminal.screen.lines):
            row = terminal.screen.buffer[y]
            for x in range(terminal.screen.columns):
                if "".join(row[c].data for c in range(x, terminal.screen.columns)).startswith(marker):
                    return x, y, row[x]
        self.fail(f"Marker {marker!r} is not visible:\n{terminal.visible}")

    def click_stop(self, terminal):
        x, y, _ = self.cell(terminal, "停止")
        terminal.send(f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m")

    def streaming_terminal(self):
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
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if len(requests) == 1:
                    # No completion or tool-arguments completion ever arrives.
                    # Stop must retain the body, without sending unfinished
                    # provider reasoning or invoking this partial call.
                    events = [
                        {"type": "response.created", "response": {
                            "id": "partial-response", "status": "in_progress", "output": []}},
                        {"type": "response.reasoning_summary_text.delta", "output_index": 0,
                         "summary_index": 0, "delta": "PRIVATE_INCOMPLETE_REASONING"},
                        {"type": "response.output_text.delta", "output_index": 1,
                         "content_index": 0, "delta": "BODY_RECEIVED_BEFORE_STOP"},
                        {"type": "response.output_item.added", "output_index": 2, "item": {
                            "type": "function_call", "id": "partial-item", "call_id": "partial-call",
                            "name": "shell", "arguments": ""}},
                        {"type": "response.function_call_arguments.delta", "output_index": 2,
                         "delta": '{"name":"unfinished","command":"touch SHOULD_NOT_RUN"'},
                    ]
                    self.wfile.write(b"".join(frame(event) for event in events))
                    self.wfile.flush()
                    release.wait(10)
                    return
                self.wfile.write(stream_response(responses_from_chat(response("QUEUED_REPLY"))))
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        terminal = Terminal(self.workspace, f"http://127.0.0.1:{server.server_port}/v1")
        self.addCleanup(terminal.close)
        self.addCleanup(release.set)
        terminal.prompt()
        return terminal, requests

    def test_stop_stream_preserves_partial_body_and_sends_queued_message(self):
        terminal, requests = self.streaming_terminal()
        terminal.send("first request\n")
        terminal.wait_for(lambda output: "BODY_RECEIVED_BEFORE_STOP" in terminal.visible
                          and "停止" in terminal.visible)
        x, y, cell = self.cell(terminal, "停止")
        self.assertEqual(cell.fg, "blue")
        self.assertIn("耗时", terminal.screen.display[y])
        terminal.send(f"\x1b[<35;{x + 1};{y + 1}M")
        terminal.wait_for(lambda output: self.cell(terminal, "停止")[2].fg == "red")
        terminal.send("\x1b[<35;2;2M")
        terminal.wait_for(lambda output: self.cell(terminal, "停止")[2].fg == "blue")
        terminal.send("queued next request\n")
        terminal.wait_for(lambda output: "排队中" in terminal.visible)
        self.assertEqual(len(requests), 1)

        self.click_stop(terminal)
        terminal.wait_for(lambda output: "QUEUED_REPLY" in terminal.visible
                          and "排队中" not in terminal.visible and "停止" not in terminal.visible)
        self.assertIsNone(terminal.process.poll())
        self.assertEqual(len(requests), 2)
        history = requests[1]["input"]
        self.assertEqual(history[-1]["content"],
                         [{"type": "input_text", "text": "queued next request"}])
        history_json = json.dumps(history)
        self.assertEqual(history_json.count("BODY_RECEIVED_BEFORE_STOP"), 1)
        self.assertIn("interrupt", history_json.lower())
        self.assertNotIn("PRIVATE_INCOMPLETE_REASONING", history_json)
        self.assertFalse(any(item.get("type") == "function_call" for item in history))
        self.assertFalse((self.workspace / "SHOULD_NOT_RUN").exists())
        errors = [event for event in self.events() if event.get("type") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["message"], "Interrupted")
        self.assertEqual([block["text"] for block in errors[0]["partial_content"]
                          if block["type"] == "text"], ["BODY_RECEIVED_BEFORE_STOP"])
        self.assertIn("PRIVATE_INCOMPLETE_REASONING", json.dumps(errors[0]))

    def test_stop_shell_kills_descendants_and_continues_queued_turn(self):
        pids = self.workspace / "shell-pids.json"
        source = (
            "import json, os, subprocess, sys, time; from pathlib import Path; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"Path({str(pids)!r}).write_text(json.dumps([os.getpgrp(), child.pid])); "
            "time.sleep(60)"
        )
        command = shlex.join([sys.executable, "-c", source])
        terminal, requests = self.terminal(lambda payload, count:
            self.response(command=command, summary="等待取消") if count == 1
            else self.response("AFTER_STOPPED_SHELL"))

        def cleanup_group():
            if pids.exists():
                try:
                    os.killpg(json.loads(pids.read_text())[0], signal.SIGKILL)
                except ProcessLookupError:
                    pass

        self.addCleanup(cleanup_group)
        terminal.send("start shell\n")
        terminal.wait_for(lambda output: pids.exists() and "：执行中" in terminal.visible
                          and "停止" in terminal.visible)
        terminal.send("continue after shell\n")
        terminal.wait_for(lambda output: "排队中" in terminal.visible)
        self.click_stop(terminal)
        terminal.wait_for(lambda output: "AFTER_STOPPED_SHELL" in terminal.visible
                          and "排队中" not in terminal.visible and "停止" not in terminal.visible)
        self.assertIsNone(terminal.process.poll())
        self.assertEqual(len(requests), 2)
        results = [item for item in requests[1]["input"]
                   if item.get("type") == "function_call_output"]
        self.assertEqual(len(results), 1)
        self.assertIn("interrupted", results[0]["output"].lower())
        child_pid = json.loads(pids.read_text())[1]

        def child_stopped(_):
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)],
                                    capture_output=True, text=True).stdout.strip()
            # An orphaned zombie has stopped; init may reap it later.
            return not status or status.startswith("Z")

        terminal.wait_for(child_stopped)


if __name__ == "__main__":
    unittest.main()
