from contextlib import redirect_stdout, redirect_stderr
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest

from skills_agent.agent import Agent
from skills_agent.cli import main, render_event
from skills_agent.providers import ProviderError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry


def answer(text, calls=None):
    message = {"role": "assistant", "content": [{"type": "text", "text": text}] if text else []}
    if calls:
        message["tool_calls"] = calls
    return message


def call(name, values, identifier="c1"):
    return {"id": identifier, "name": name, "arguments": json.dumps(values)}


class AgentTests(unittest.TestCase):
    def test_empty_response_is_recorded_but_does_not_report_success(self):
        class Model:
            def complete(self, *args):
                return answer("")

        with TemporaryDirectory() as directory:
            path = Path(directory)
            session = Session(path, "empty")
            agent = Agent(Model(), session, SkillRegistry([]), path)
            with self.assertRaisesRegex(RuntimeError, "neither an answer"):
                agent.run(session.make_user_message("task", []))
            self.assertEqual(session.messages()[-1], answer(""))
            self.assertEqual(session.events()[-1]["type"], "error")

    def test_child_conversation_and_result_are_private_in_live_and_replayed_output(self):
        class Model:
            def complete(self, system, messages, tools):
                is_child = not any(tool["name"] == "subagent" for tool in tools)
                if messages[-1]["role"] == "user":
                    if is_child:
                        return answer("CHILD_THOUGHT", [call("shell", {"command": "printf CHILD_TOOL_OUTPUT"})])
                    return answer("main text", [
                        call("shell", {"command": "printf PARENT_OUTPUT"}, "c0"),
                        call("subagent", {"goal": "统计文件", "task": "PRIVATE_TASK"}),
                    ])
                if is_child:
                    return answer("PRIVATE_CHILD_RESULT")
                assert "PRIVATE_CHILD_RESULT" in messages[-1]["content"]
                return answer("done")

        with TemporaryDirectory() as directory:
            path = Path(directory)
            session = Session(path, "main")
            output = io.StringIO()
            agent = Agent(Model(), session, SkillRegistry([]), path,
                          on_event=lambda event: render_event(event, output))
            self.assertEqual(agent.run(session.make_user_message("original user", [])), "done")
            live = output.getvalue()
            self.assertIn("main text", live)
            self.assertIn("PARENT_OUTPUT", live)
            self.assertIn("sub-agent 处理了：统计文件", live)
            for secret in ("PRIVATE_TASK", "CHILD_THOUGHT", "CHILD_TOOL_OUTPUT", "PRIVATE_CHILD_RESULT"):
                self.assertNotIn(secret, live)
            replay = io.StringIO()
            for event in session.events():
                render_event(event, replay)
            self.assertEqual(live, replay.getvalue())
            child_logs = list((session.path / "subagents").glob("*/events.jsonl"))
            self.assertEqual(len(child_logs), 1)
            self.assertIn("CHILD_TOOL_OUTPUT", child_logs[0].read_text())
            self.assertIn("PRIVATE_CHILD_RESULT", (session.path / "events.jsonl").read_text())

    def test_tool_output_is_saved_and_displayed_in_full(self):
        class Model:
            def complete(self, system, messages, tools):
                if messages[-1]["role"] == "user":
                    return answer("", [call("shell", {
                        "command": "python3 -c 'print(\"x\" * 15000)'",
                    })])
                self.observed = messages
                return answer("done")

        with TemporaryDirectory() as directory:
            path = Path(directory)
            session = Session(path, "full")
            output = io.StringIO()
            model = Model()
            agent = Agent(model, session, SkillRegistry([]), path, context_chars=7000,
                          on_event=lambda event: render_event(event, output))
            agent.run(session.make_user_message("read output", []))
            self.assertIn("x" * 15000, output.getvalue())
            saved_tool = next(message for message in session.messages() if message["role"] == "tool")
            self.assertEqual(json.loads(saved_tool["content"])["stdout"], "x" * 15000 + "\n")
            self.assertNotIn("x" * 15000, json.dumps(model.observed))

    def test_failed_provider_response_is_recorded_without_executing_tools(self):
        raw = {"incomplete": "original response"}

        class Model:
            def complete(self, *args):
                raise ProviderError("truncated", raw)

        with TemporaryDirectory() as directory:
            path = Path(directory)
            session = Session(path, "failure")
            agent = Agent(Model(), session, SkillRegistry([]), path)
            with self.assertRaises(ProviderError):
                agent.run(session.make_user_message("task", []))
            self.assertEqual(session.events()[-1]["_raw"], raw)
            self.assertEqual(len(session.messages()), 1)

    def test_partial_response_is_displayed_and_replayed_but_not_sent_as_history(self):
        class Model:
            def complete(self, *args):
                error = ProviderError("truncated", {"private": "RAW_ONLY"})
                error.partial_content = [{"type": "text", "text": "  original partial\n"}]
                raise error

        with TemporaryDirectory() as directory:
            path = Path(directory)
            session = Session(path, "partial")
            output = io.StringIO()
            agent = Agent(Model(), session, SkillRegistry([]), path,
                          on_event=lambda event: render_event(event, output))
            with self.assertRaises(ProviderError):
                agent.run(session.make_user_message("task", []))
            self.assertIn("  original partial\n", output.getvalue())
            self.assertNotIn("RAW_ONLY", output.getvalue())
            self.assertEqual(len(session.messages()), 1)
            replay = io.StringIO()
            for event in session.events():
                render_event(event, replay)
            self.assertEqual(output.getvalue(), replay.getvalue())

    def test_history_does_not_require_model_credentials(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            session = Session(path / ".agent" / "sessions", "default")
            session.append({"role": "user", "content": "  unchanged\ntext  "})
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["history", "--workspace", directory])
            self.assertEqual(code, 0)
            self.assertIn("  unchanged\ntext  ", output.getvalue())

    def test_missing_attachment_is_reported(self):
        import os
        from unittest.mock import patch
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_MODEL": "test"}):
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(["run", "--workspace", directory, "--file", directory + "/absent", "task"])
            self.assertEqual(code, 1)
            self.assertIn("error:", error.getvalue())


class CLIProtocolTests(unittest.TestCase):
    def test_cli_executes_real_script_and_resumes_over_both_http_protocols(self):
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider), TemporaryDirectory() as directory:
                requests = []
                workspace = Path(directory)
                (workspace / "task.py").write_text("from pathlib import Path\nPath('result.txt').write_text('created')\nprint('script done')\n")

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_POST(self):
                        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                        requests.append((self.path, data))
                        if len(requests) == 1:
                            if provider == "openai":
                                response = {"choices": [{"finish_reason": "tool_calls", "message": {
                                    "content": None, "tool_calls": [{"id": "shell1", "type": "function", "function": {
                                        "name": "shell", "arguments": json.dumps({"command": "python3 task.py"}),
                                    }}],
                                }}]}
                            else:
                                response = {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "shell1",
                                    "name": "shell", "input": {"command": "python3 task.py"}}]}
                        elif provider == "openai":
                            response = {"choices": [{"finish_reason": "stop", "message": {"content": "completed"}}]}
                        else:
                            response = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "completed"}]}
                        encoded = json.dumps(response).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(encoded)))
                        self.end_headers()
                        self.wfile.write(encoded)

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    command = [sys.executable, "-m", "skills_agent", "run", "--workspace", directory,
                               "--provider", provider, "--model", "test-model", "--base-url",
                               f"http://127.0.0.1:{server.server_port}/v1"]
                    first = subprocess.run([*command, "create result"], capture_output=True, text=True)
                    self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
                    self.assertEqual((workspace / "result.txt").read_text(), "created")
                    self.assertIn("script done", first.stdout)
                    self.assertIn("completed", first.stdout)
                    session_log = workspace / ".agent" / "sessions" / "default" / "events.jsonl"
                    before = session_log.read_bytes()
                    second = subprocess.run(command, input="continue via stdin\n", capture_output=True, text=True)
                    self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
                    self.assertTrue(session_log.read_bytes().startswith(before))
                    self.assertEqual(len(requests), 3)
                    self.assertNotIn("_raw", json.dumps(requests[-1]))
                    self.assertIn("create result", json.dumps(requests[-1]))
                    self.assertIn("continue via stdin", json.dumps(requests[-1]))
                    expected = "/v1/chat/completions" if provider == "openai" else "/v1/messages"
                    self.assertTrue(all(path == expected for path, _ in requests))
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
