from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from tests.sse import frame, stream_response


PROJECT = Path(__file__).resolve().parents[1]
ENVIRONMENT = {"PATH": os.defpath, "PYTHONPATH": str(PROJECT)}
FIRST_BODY = "BODY_BEFORE_GATE"
LAST_BODY = " BODY_AFTER_GATE"
FINAL = "FINAL_STREAM_ANSWER"
SUMMARY = "PREPARE_TEST_ARTIFACT"
COMMAND = "printf STREAM_TOOL_OUTPUT; printf result > result.txt"


def gated_response(provider):
    arguments = json.dumps({"command": COMMAND, "summary": SUMMARY})
    split = arguments.index('"summary"')
    if provider == "openai":
        def chunk(delta, reason=None):
            return {"choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}

        beginning = [
            chunk({"role": "assistant", "reasoning_content": "PRIVATE_REASONING"}),
            chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {
                "name": "shell", "arguments": arguments[:split],
            }}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": arguments[split:]}}]}),
            chunk({"content": FIRST_BODY}),
        ]
        ending = [chunk({"content": LAST_BODY, "reasoning_content": None}), chunk({}, "tool_calls"), None]
        final = {"choices": [{"finish_reason": "stop", "message": {"content": FINAL}}]}
    else:
        beginning = [
            {"type": "message_start", "message": {"id": "msg_1", "role": "assistant", "content": [],
                "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": {
                "type": "thinking", "thinking": "PRIVATE_REASONING", "signature": "private"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {
                "type": "tool_use", "id": "call_1", "name": "shell", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {
                "type": "input_json_delta", "partial_json": arguments[:split]}},
            {"type": "content_block_delta", "index": 1, "delta": {
                "type": "input_json_delta", "partial_json": arguments[split:]}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": FIRST_BODY}},
        ]
        ending = [
            {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": LAST_BODY}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 10}},
            {"type": "message_stop"},
        ]
        final = {"stop_reason": "end_turn", "content": [{"type": "text", "text": FINAL}]}
    return b"".join(map(frame, beginning)), b"".join(map(frame, ending)), stream_response(final)


class StreamIntegrationTests(unittest.TestCase):
    def test_both_protocols_show_text_before_server_finishes_and_replay_arrival_order(self):
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider), TemporaryDirectory() as directory:
                workspace = Path(directory)
                initial, remaining, final = gated_response(provider)
                release, waiting, completed = threading.Event(), threading.Event(), threading.Event()
                requests, errors = [], []

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_POST(self):
                        requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        try:
                            if len(requests) == 1:
                                self.wfile.write(initial)
                                self.wfile.flush()
                                waiting.set()
                                if not release.wait(timeout=10):
                                    errors.append("Test never observed initial text to release the stream")
                                    return
                                self.wfile.write(remaining)
                                self.wfile.flush()
                                completed.set()
                            else:
                                self.wfile.write(final)
                                self.wfile.flush()
                        except OSError:
                            # A failed assertion may terminate the client while cleanup releases the server.
                            pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                process = None
                reader = None
                pieces = queue.Queue()
                seen = bytearray()
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "skills_agent", "run", "--workspace", directory,
                         "--session", "gated", "--provider", provider, "--model", "test-model",
                         "--base-url", f"http://127.0.0.1:{server.server_port}/v1"],
                        cwd=PROJECT, env=ENVIRONMENT, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )

                    def read_stdout():
                        while True:
                            piece = process.stdout.read(1)
                            pieces.put(piece)
                            if not piece:
                                return

                    reader = threading.Thread(target=read_stdout, daemon=True)
                    reader.start()
                    process.stdin.write(b"run gated stream\n/exit\n")
                    process.stdin.close()
                    self.assertTrue(waiting.wait(timeout=5), "CLI never reached the local model")
                    deadline = time.monotonic() + 5
                    while FIRST_BODY.encode() not in seen:
                        timeout = deadline - time.monotonic()
                        if timeout <= 0:
                            self.fail(f"CLI buffered the initial body: {seen.decode(errors='replace')}")
                        try:
                            piece = pieces.get(timeout=timeout)
                        except queue.Empty:
                            self.fail(f"CLI buffered the initial body: {seen.decode(errors='replace')}")
                        if not piece:
                            self.fail(f"CLI exited before displaying initial body: {seen.decode(errors='replace')}")
                        seen.extend(piece)
                    self.assertFalse(release.is_set())
                    self.assertFalse(completed.is_set())
                    self.assertIsNone(process.poll())
                    self.assertFalse((workspace / "result.txt").exists())
                    log = workspace / ".agent/sessions/gated/events.jsonl"
                    pending = [json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual(pending[-1], {"type": "text_delta", "text": FIRST_BODY})
                    self.assertFalse(any(event.get("type") == "message"
                                         and event["message"]["role"] == "assistant" for event in pending))

                    release.set()
                    self.assertEqual(process.wait(timeout=5), 0)
                    reader.join(timeout=5)
                    self.assertFalse(reader.is_alive())
                    while not pieces.empty():
                        seen.extend(pieces.get_nowait())
                    output = seen.decode()
                    self.assertEqual(process.stderr.read(), b"")
                    self.assertEqual(errors, [])
                    self.assertTrue(completed.is_set())
                    self.assertEqual(len(requests), 2)
                    self.assertTrue(all(request["stream"] for request in requests))
                    if provider == "openai":
                        assistant = next(message for message in requests[1]["messages"]
                                         if message["role"] == "assistant")
                        self.assertEqual(assistant["reasoning_content"], "PRIVATE_REASONING")
                    self.assertEqual((workspace / "result.txt").read_text(), "result")
                    self.assertNotIn("user:", output)
                    self.assertNotIn("PRIVATE_REASONING", output)
                    self.assertNotIn("STREAM_TOOL_OUTPUT", output)
                    for marker in (FIRST_BODY, LAST_BODY, FINAL):
                        self.assertEqual(output.count(marker), 1)
                    self.assertLess(output.index(SUMMARY), output.index(FIRST_BODY))
                    self.assertLess(output.index(FIRST_BODY), output.index(LAST_BODY))
                    self.assertLess(output.index(LAST_BODY), output.index(FINAL))
                    saved = log.read_bytes()
                    for full in (False, True):
                        command = [sys.executable, "-m", "skills_agent", "history", "--workspace", directory,
                                   "--session", "gated"] + (["--full"] if full else [])
                        history = subprocess.run(command, cwd=PROJECT, env=ENVIRONMENT,
                                                 capture_output=True, text=True, timeout=5)
                        self.assertEqual(history.returncode, 0, history.stderr)
                        replay = history.stdout
                        first_marker = "tool_call shell:" if full else SUMMARY
                        self.assertLess(replay.index(first_marker), replay.index(FIRST_BODY))
                        for marker in (FIRST_BODY, LAST_BODY, FINAL):
                            self.assertEqual(replay.count(marker), 1)
                        self.assertNotIn("PRIVATE_REASONING", replay)
                        self.assertEqual("STREAM_TOOL_OUTPUT" in replay, full)
                        if not full:
                            self.assertEqual(replay, "user:\nrun gated stream\n" + output)
                        self.assertEqual(log.read_bytes(), saved)
                finally:
                    release.set()
                    if process is not None:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        if reader is not None:
                            reader.join(timeout=5)
                        process.stdout.close()
                        process.stderr.close()
                    server.shutdown()
                    server.server_close()
                    server_thread.join()


if __name__ == "__main__":
    unittest.main()
