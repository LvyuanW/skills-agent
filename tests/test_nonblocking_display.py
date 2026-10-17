"""Blocked UI work must not delay SSE consumption, tools, or the next request."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import shlex
import sys
import threading
import unittest

from skills_agent.agent import Agent
from skills_agent.cli import ConsoleRenderer
from skills_agent.display import DisplayQueue
from skills_agent.providers.openai import OpenAIProvider
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests import test_cli_terminal as terminal_helpers
from tests.sse import frame, responses_from_chat, stream_response


class NonblockingDisplayTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    response = terminal_helpers.TerminalTests.response

    def test_stalled_renderer_does_not_hold_sse_tool_execution_or_next_model_request(self):
        entered, release = threading.Event(), threading.Event()
        next_request, agent_done, ui_wake, ui_stop = (threading.Event() for _ in range(4))
        artifact = self.workspace / "actual-tool-result.txt"
        command = shlex.join([sys.executable, "-c",
                              f"from pathlib import Path; Path({str(artifact)!r}).write_text('observed')"])
        first = self.response(command=command, summary="写入验收文件")
        first["choices"][0]["message"]["reasoning_content"] = "PRESERVED_REASONING"
        native = responses_from_chat(first)
        reasoning, call = native["output"]
        requests, server_errors = [], []
        fixture = self

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
                if len(requests) > 1:
                    next_request.set()
                    self.wfile.write(stream_response(responses_from_chat(fixture.response("VERIFIED_FINAL"))))
                    self.wfile.flush()
                    return
                self.wfile.write(frame({"type": "response.created", "response": {
                    "id": native["id"], "status": "in_progress", "output": [],
                }}))
                self.wfile.write(frame({"type": "response.output_item.added", "output_index": 0,
                                       "item": {**reasoning, "summary": []}}))
                self.wfile.write(frame({"type": "response.reasoning_summary_text.delta", "output_index": 0,
                                       "summary_index": 0, "delta": "PRESERVED_REASONING"}))
                self.wfile.flush()
                # The command is sent only after UI work is demonstrably stuck.
                if not entered.wait(5):
                    server_errors.append("The UI never received its first thinking event")
                    return
                self.wfile.write(frame({"type": "response.output_item.done", "output_index": 0,
                                       "item": reasoning}))
                self.wfile.write(frame({"type": "response.output_item.added", "output_index": 1,
                                       "item": {**call, "arguments": ""}}))
                self.wfile.write(frame({"type": "response.function_call_arguments.delta", "output_index": 1,
                                       "delta": call["arguments"]}))
                self.wfile.write(frame({"type": "response.output_item.done", "output_index": 1,
                                       "item": call}))
                self.wfile.write(frame({"type": "response.completed", "response": native}))
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        output, delivered, failures, answers = StringIO(), [], [], []
        renderer = ConsoleRenderer(output)

        def blocked_renderer(event):
            if event.get("type") == "thinking_delta":
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("Test did not release the stalled UI")
            delivered.append(event)
            renderer(event)

        queue = DisplayQueue(wake=ui_wake.set)

        def consume():
            try:
                while not ui_stop.is_set():
                    ui_wake.wait(.1)
                    ui_wake.clear()
                    queue.drain()
                queue.drain()
            except BaseException as error:
                failures.append(error)

        session = Session(self.workspace / "sessions", "nonblocking")
        provider = OpenAIProvider("test-model", "local-test", f"http://127.0.0.1:{server.server_port}/v1",
                                  api_format="responses")
        agent = Agent(provider, session, SkillRegistry([]), self.workspace,
                      on_event=lambda event: queue.post_event(blocked_renderer, event))

        def run():
            try:
                answers.append(agent.run({"role": "user", "content": "Write and verify the result"}))
            except BaseException as error:
                failures.append(error)
            finally:
                agent_done.set()

        consumer = threading.Thread(target=consume, daemon=True)
        producer = threading.Thread(target=run, daemon=True)
        consumer.start()
        producer.start()
        try:
            self.assertTrue(entered.wait(5), "UI must be blocked before the rest of the response is sent")
            self.assertFalse(release.is_set())
            self.assertTrue(next_request.wait(5), "A stalled UI must not prevent the next API request")
            self.assertTrue(agent_done.wait(5))
            self.assertEqual(failures, [])
            self.assertEqual(artifact.read_text(), "observed", "The real shell ran while rendering was blocked")
            self.assertEqual(answers, ["VERIFIED_FINAL"])
            self.assertFalse(any(event.get("type") == "message"
                                 and event["message"].get("role") == "tool" for event in delivered))
            self.assertTrue(any(item.get("type") == "function_call_output" for item in requests[1]["input"]))
        finally:
            release.set()
            producer.join(5)
            queue.close()
            ui_stop.set()
            ui_wake.set()
            consumer.join(5)
            renderer.close()
            agent.close()
            server.shutdown()
            server.server_close()
            server_thread.join(5)
        self.assertFalse(producer.is_alive())
        self.assertFalse(consumer.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(server_errors, [])
        messages = [event["message"] for event in delivered if event.get("type") == "message"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(messages[1]["tool_calls"][0]["id"], messages[2]["tool_call_id"])
        self.assertIn("PRESERVED_REASONING", output.getvalue())
        self.assertIn("VERIFIED_FINAL", output.getvalue())
        self.assertLess(output.getvalue().index("写入验收文件"), output.getvalue().index("VERIFIED_FINAL"))
        self.assertEqual("".join(block.get("thinking", "") for block in session.messages()[1]["content"]
                                 if block.get("type") == "thinking"), "PRESERVED_REASONING")


if __name__ == "__main__":
    unittest.main()
