"""Exercise folding through a real PTY and mouse input, with a local SSE server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
import unittest

from tests.sse import frame, responses_from_chat, stream_response
from tests import test_cli_terminal as terminal_helpers
from tests.test_cli_terminal import Terminal


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class ThinkingTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    response = terminal_helpers.TerminalTests.response
    events = terminal_helpers.TerminalTests.events
    server = terminal_helpers.TerminalTests.server

    def streaming_server(self, chunks):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if self.path.endswith("/input_tokens"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":128}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in chunks():
                    self.wfile.write(chunk)
                    self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1"

    def open_terminal(self, endpoint):
        terminal = Terminal(self.workspace, endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt()
        return terminal

    def test_stream_preview_folds_at_body_then_mouse_toggles_and_replay_starts_folded(self):
        continue_stream = threading.Event()
        finish_stream = threading.Event()
        self.addCleanup(continue_stream.set)
        self.addCleanup(finish_stream.set)
        first = "FIRST_THOUGHT"
        second = "SECOND_THOUGHT"
        response = self.response("**FINAL_BODY**")
        response["choices"][0]["message"]["reasoning_content"] = first + " " + second
        native = responses_from_chat(response)

        def chunks():
            yield frame({"type": "response.created", "response": {
                "id": native["id"], "status": "in_progress", "output": [],
            }})
            yield frame({"type": "response.output_item.added", "output_index": 0,
                         "item": {"type": "reasoning", "id": "reasoning", "summary": []}})
            yield frame({"type": "response.reasoning_summary_text.delta", "output_index": 0,
                         "summary_index": 0, "delta": first})
            if not continue_stream.wait(5):
                return
            yield frame({"type": "response.reasoning_summary_text.delta", "output_index": 0,
                         "summary_index": 0, "delta": " " + second})
            yield frame({"type": "response.output_item.done", "output_index": 0,
                         "item": native["output"][0]})
            yield frame({"type": "response.output_item.added", "output_index": 1,
                         "item": native["output"][1]})
            yield frame({"type": "response.output_text.delta", "output_index": 1,
                         "content_index": 0, "delta": "**FINAL_BODY**"})
            if not finish_stream.wait(5):
                return
            yield frame({"type": "response.output_item.done", "output_index": 1,
                         "item": native["output"][1]})
            yield frame({"type": "response.completed", "response": native})

        endpoint = self.streaming_server(chunks)
        terminal = self.open_terminal(endpoint)
        terminal.send("inspect changes\n")
        terminal.wait_for(lambda output: first in terminal.visible)
        self.assertIn("▼ 思考", terminal.visible)
        self.assertNotIn("FINAL_BODY", terminal.visible)
        continue_stream.set()
        terminal.wait_for(lambda output: "FINAL_BODY" in terminal.visible)
        self.assertNotIn(first, terminal.visible)
        self.assertNotIn(second, terminal.visible)
        self.assertIn("▶ 思考", terminal.visible)
        self.assertLess(terminal.visible.index("▶ 思考"), terminal.visible.index("FINAL_BODY"))
        self.assertIn("**FINAL_BODY**", terminal.visible, "Active body deltas remain literal plain text")
        self.assertFalse(any(event.get("type") == "message" and event["message"].get("role") == "assistant"
                             for event in self.events()), "Reasoning folds before response.completed")
        finish_stream.set()
        terminal.wait_for(lambda output: any(event.get("type") == "message"
                          and event["message"].get("role") == "assistant" for event in self.events())
                          and "停止" not in terminal.visible)
        terminal.wait_for(lambda output: "▶ 思考" in terminal.visible
                          and first not in terminal.visible and "FINAL_BODY" in terminal.visible
                          and "**FINAL_BODY**" not in terminal.visible)
        terminal.prompt("FINAL_BODY")
        self.assertNotIn(first, terminal.visible)
        self.assertNotIn(second, terminal.visible)
        self.assertNotIn("**FINAL_BODY**", terminal.visible)
        self.assertLess(terminal.visible.index("▶ 思考"), terminal.visible.index("FINAL_BODY"))
        log = self.workspace / ".agent/sessions/terminal/events.jsonl"
        before_toggle = log.read_bytes()
        terminal.click("▶ 思考")
        terminal.wait_for(lambda output: first in terminal.visible)
        self.assertIn(second, terminal.visible)
        self.assertEqual(terminal.visible.count(first), 1)
        terminal.click("▼ 思考")
        terminal.wait_for(lambda output: "▶ 思考" in terminal.visible and first not in terminal.visible)
        self.assertNotIn(first, terminal.visible)
        # Leave it expanded so a restart proves folding is a display preference,
        # not a deletion or rewrite of the original model response.
        terminal.click("▶ 思考")
        terminal.wait_for(lambda output: first in terminal.visible)
        self.assertEqual(log.read_bytes(), before_toggle)
        terminal.send("/exit\n")
        terminal.wait_exit()
        self.assertEqual(terminal.process.returncode, 0)
        replay = self.open_terminal(endpoint)
        self.assertIn("▶ 思考", replay.visible)
        self.assertNotIn(first, replay.visible)
        self.assertIn("FINAL_BODY", replay.visible)
        replay.click("▶ 思考")
        replay.wait_for(lambda output: first in replay.visible)
        self.assertEqual(log.read_bytes(), before_toggle)

    def test_live_thinking_is_eight_plain_tail_lines_and_finished_thinking_expands_as_markdown(self):
        release_body = threading.Event()
        release_completed = threading.Event()
        self.addCleanup(release_body.set)
        self.addCleanup(release_completed.set)
        thought = "\n".join(f"**THOUGHT_{index:02}**" for index in range(14))
        response = self.response("ANSWER_AFTER_TAIL")
        response["choices"][0]["message"]["reasoning_content"] = thought
        native = responses_from_chat(response)

        def chunks():
            yield frame({"type": "response.created", "response": {
                "id": native["id"], "status": "in_progress", "output": [],
            }})
            yield frame({"type": "response.output_item.added", "output_index": 0,
                         "item": {"type": "reasoning", "id": "reasoning", "summary": []}})
            yield frame({"type": "response.reasoning_summary_text.delta", "output_index": 0,
                         "summary_index": 0, "delta": thought})
            if not release_body.wait(10):
                return
            yield frame({"type": "response.output_item.done", "output_index": 0,
                         "item": native["output"][0]})
            yield frame({"type": "response.output_item.added", "output_index": 1,
                         "item": native["output"][1]})
            yield frame({"type": "response.output_text.delta", "output_index": 1,
                         "content_index": 0, "delta": "ANSWER_AFTER_TAIL"})
            if not release_completed.wait(10):
                return
            yield frame({"type": "response.output_item.done", "output_index": 1,
                         "item": native["output"][1]})
            yield frame({"type": "response.completed", "response": native})

        terminal = self.open_terminal(self.streaming_server(chunks))
        terminal.send("inspect bounded reasoning preview\n")
        terminal.wait_for(lambda output: "THOUGHT_13" in terminal.visible)
        for index in range(6):
            self.assertNotIn(f"THOUGHT_{index:02}", terminal.visible)
        for index in range(6, 14):
            self.assertIn(f"**THOUGHT_{index:02}**", terminal.visible,
                          "The live tail is literal text, not Markdown")
        release_body.set()
        terminal.wait_for(lambda output: "ANSWER_AFTER_TAIL" in terminal.visible)
        self.assertIn("▶ 思考", terminal.visible)
        self.assertNotIn("THOUGHT_", terminal.visible)
        self.assertFalse(any(event.get("type") == "message" and event["message"].get("role") == "assistant"
                             for event in self.events()))
        release_completed.set()
        terminal.wait_for(lambda output: any(event.get("type") == "message"
                          and event["message"].get("role") == "assistant" for event in self.events())
                          and "停止" not in terminal.visible)
        terminal.prompt("ANSWER_AFTER_TAIL")
        log = self.workspace / ".agent/sessions/terminal/events.jsonl"
        assistant = next(event["message"] for event in self.events()
                         if event.get("type") == "message" and event["message"].get("role") == "assistant")
        self.assertEqual("".join(block.get("thinking", "") for block in assistant["content"]
                                 if block.get("type") == "thinking"), thought)
        before = log.read_bytes()
        terminal.click("▶ 思考")
        terminal.wait_for(lambda output: "THOUGHT_00" in terminal.visible)
        self.assertIn("THOUGHT_13", terminal.visible)
        self.assertNotIn("**THOUGHT_", terminal.visible)
        self.assertEqual(log.read_bytes(), before)

    def test_first_tool_delta_folds_reasoning_before_arguments_or_response_finish(self):
        release_tool, release_arguments = threading.Event(), threading.Event()
        self.addCleanup(release_tool.set)
        self.addCleanup(release_arguments.set)
        response = self.response(command="printf TOOL_OUTPUT", summary="检查思考结束边界")
        response["choices"][0]["message"]["reasoning_content"] = "THINK_BEFORE_TOOL"
        native = responses_from_chat(response)
        reasoning, call = native["output"]
        requests = []

        def chunks():
            requests.append(True)
            if len(requests) > 1:
                yield stream_response(responses_from_chat(self.response("DONE_AFTER_TOOL")))
                return
            yield frame({"type": "response.created", "response": {
                "id": native["id"], "status": "in_progress", "output": [],
            }})
            yield frame({"type": "response.output_item.added", "output_index": 0,
                         "item": {**reasoning, "summary": []}})
            yield frame({"type": "response.reasoning_summary_text.delta", "output_index": 0,
                         "summary_index": 0, "delta": "THINK_BEFORE_TOOL"})
            if not release_tool.wait(10):
                return
            yield frame({"type": "response.output_item.done", "output_index": 0, "item": reasoning})
            yield frame({"type": "response.output_item.added", "output_index": 1,
                         "item": {**call, "arguments": ""}})
            if not release_arguments.wait(10):
                return
            yield frame({"type": "response.function_call_arguments.delta", "output_index": 1,
                         "delta": call["arguments"]})
            yield frame({"type": "response.output_item.done", "output_index": 1, "item": call})
            yield frame({"type": "response.completed", "response": native})

        terminal = self.open_terminal(self.streaming_server(chunks))
        terminal.send("check tool boundary\n")
        terminal.wait_for(lambda output: "THINK_BEFORE_TOOL" in terminal.visible)
        release_tool.set()
        terminal.wait_for(lambda output: "接收调用" in terminal.visible)
        self.assertIn("▶ 思考", terminal.visible)
        self.assertNotIn("THINK_BEFORE_TOOL", terminal.visible)
        self.assertFalse(any(event.get("type") == "message" and event["message"].get("role") == "assistant"
                             for event in self.events()))
        release_arguments.set()
        terminal.prompt("DONE_AFTER_TOOL")
        self.assertIn("shell「检查思考结束边界」：完成", terminal.visible)

    def test_expanded_long_thinking_remains_scrollable_and_keeps_message_order(self):
        thought = "\n\n".join(f"THOUGHT_{index:03}" for index in range(60))

        def respond(payload, count):
            response = self.response("FINAL_AFTER_LONG_THINKING")
            response["choices"][0]["message"]["reasoning_content"] = thought
            return response

        endpoint, requests = self.server(respond)
        terminal = self.open_terminal(endpoint)
        terminal.send("long thinking\n")
        terminal.prompt("FINAL_AFTER_LONG_THINKING")
        terminal.wait_for(lambda output: "▶ 思考" in terminal.visible)
        terminal.click("▶ 思考")
        terminal.wait_for(lambda output: "THOUGHT_000" in terminal.visible
                          and "FINAL_AFTER_LONG_THINKING" not in terminal.visible)
        self.assertIn("▼ 思考", terminal.visible)
        self.assertLess(terminal.visible.index("long thinking"), terminal.visible.index("THOUGHT_000"))
        self.assertNotIn("FINAL_AFTER_LONG_THINKING", terminal.visible)
        # SGR wheel down scrolls the transcript without editing the input.
        terminal.send("\x1b[<65;2;3M" * 4)
        terminal.wait_for(lambda output: "THOUGHT_000" not in terminal.visible)
        terminal.send("\x1b[1;5F")  # Ctrl-End returns to the latest content.
        terminal.wait_for(lambda output: "FINAL_AFTER_LONG_THINKING" in terminal.visible)
        self.assertIn("THOUGHT_059", terminal.visible)
        self.assertLess(terminal.visible.index("THOUGHT_059"),
                        terminal.visible.index("FINAL_AFTER_LONG_THINKING"))
        terminal.send("\x1b[5~" * 8)
        terminal.wait_for(lambda output: "▼ 思考" in terminal.visible)
        terminal.click("▼ 思考")
        terminal.wait_for(lambda output: "▶ 思考" in terminal.visible
                          and "THOUGHT_" not in terminal.visible)
        self.assertNotIn("THOUGHT_", terminal.visible)
        self.assertIn("FINAL_AFTER_LONG_THINKING", terminal.visible)
        self.assertEqual(len(requests), 1)

    def test_native_selection_freezes_stream_until_f2_restores_mouse_and_completed_response(self):
        continue_stream = threading.Event()
        self.addCleanup(continue_stream.set)
        first = "SELECTABLE_THOUGHT"
        second = "LATER_THOUGHT"
        answer = "COMPLETED_DURING_SELECTION"
        response = self.response(answer)
        response["choices"][0]["message"]["reasoning_content"] = first + " " + second
        native = responses_from_chat(response)

        def chunks():
            yield frame({"type": "response.created", "response": {
                "id": native["id"], "status": "in_progress", "output": [],
            }})
            yield frame({"type": "response.output_item.added", "output_index": 0,
                         "item": {"type": "reasoning", "id": "reasoning", "summary": []}})
            yield frame({"type": "response.reasoning_summary_text.delta", "output_index": 0,
                         "summary_index": 0, "delta": first})
            if not continue_stream.wait(5):
                return
            yield frame({"type": "response.reasoning_summary_text.delta", "output_index": 0,
                         "summary_index": 0, "delta": " " + second})
            yield frame({"type": "response.output_item.done", "output_index": 0,
                         "item": native["output"][0]})
            yield frame({"type": "response.output_item.added", "output_index": 1,
                         "item": native["output"][1]})
            yield frame({"type": "response.output_text.delta", "output_index": 1,
                         "content_index": 0, "delta": answer})
            yield frame({"type": "response.output_item.done", "output_index": 1,
                         "item": native["output"][1]})
            yield frame({"type": "response.completed", "response": native})

        terminal = self.open_terminal(self.streaming_server(chunks))
        terminal.send("select while streaming\n")
        terminal.wait_for(lambda output: first in terminal.visible)
        self.assertIn("▼ 思考", terminal.visible)
        start = len(terminal.output)
        terminal.send("\x1bOQ")  # F2 returns mouse input to the terminal emulator.
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        for mode in (1000, 1003, 1006, 1015):
            self.assertIn(f"\x1b[?{mode}l".encode(), terminal.output[start:])
        terminal.read(0.1)
        frozen = terminal.visible
        continue_stream.set()
        # The model must finish and persist its response while the view remains
        # fixed for native selection; waiting on the screen would hide a stall.
        terminal.wait_for(lambda output: any(
            event.get("type") == "message" and event["message"]["role"] == "assistant"
            for event in self.events()
        ))
        terminal.read(0.2)
        self.assertEqual(terminal.visible, frozen)
        self.assertIn("▼ 思考", terminal.visible)
        self.assertNotIn(second, terminal.visible)
        self.assertNotIn(answer, terminal.visible)
        log = self.workspace / ".agent/sessions/terminal/events.jsonl"
        recorded = log.read_bytes()
        self.assertIn(second.encode(), recorded)
        self.assertIn(answer.encode(), recorded)

        start = len(terminal.output)
        terminal.send("\x1bOQ")
        terminal.prompt(answer)
        for mode in (1000, 1003, 1006, 1015):
            self.assertIn(f"\x1b[?{mode}h".encode(), terminal.output[start:])
        self.assertNotIn("选择文本（F2 返回）", terminal.visible)
        self.assertIn("▶ 思考", terminal.visible)
        self.assertNotIn(first, terminal.visible)
        terminal.click("▶ 思考")
        terminal.wait_for(lambda output: first in terminal.visible and second in terminal.visible)
        self.assertIn("▼ 思考", terminal.visible)
        self.assertIn(answer, terminal.visible)
        self.assertEqual(log.read_bytes(), recorded)
        terminal.send("UNSENT_DRAFT")
        terminal.wait_for(lambda output: "> UNSENT_DRAFT" in terminal.visible)
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        terminal.send("\n")
        terminal.read(0.2)
        self.assertIn("选择文本（F2 返回）", terminal.visible)
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "选择文本（F2 返回）" not in terminal.visible)
        self.assertIn("> UNSENT_DRAFT", terminal.visible)
        self.assertEqual(log.read_bytes(), recorded)


if __name__ == "__main__":
    unittest.main()
