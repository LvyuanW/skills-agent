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
from unittest.mock import patch

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
                          on_event=lambda event: render_event(event, output, full=True))
            self.assertEqual(agent.run(session.make_user_message("original user", [])), "done")
            live = output.getvalue()
            self.assertIn("main text", live)
            self.assertIn("PARENT_OUTPUT", live)
            self.assertIn("sub-agent 处理了：统计文件", live)
            for secret in ("PRIVATE_TASK", "CHILD_THOUGHT", "CHILD_TOOL_OUTPUT", "PRIVATE_CHILD_RESULT"):
                self.assertNotIn(secret, live)
            replay = io.StringIO()
            for event in session.events():
                render_event(event, replay, full=True)
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
                          on_event=lambda event: render_event(event, output, full=True))
            agent.run(session.make_user_message("read output", []))
            self.assertIn("x" * 15000, output.getvalue())
            saved_tool = next(message for message in session.messages() if message["role"] == "tool")
            self.assertEqual(json.loads(saved_tool["content"])["stdout"], "x" * 15000 + "\n")
            self.assertNotIn("x" * 15000, json.dumps(model.observed))

    def test_generated_script_runs_once_and_is_compacted_only_in_model_context(self):
        source = (
            "from pathlib import Path\n"
            "# " + "reference detail " * 1500 + "\n"
            "with Path('runs.txt').open('a') as output:\n"
            "    output.write('executed once\\n')\n"
            "print('generated script completed')\n"
        )
        command = "cat > generated.py <<'PY'\n" + source + "PY\npython3 generated.py"

        class Model:
            def complete(self, system, messages, tools):
                if messages[-1]["role"] == "user":
                    return answer("", [call("shell", {"command": command})])
                self.observed = messages
                return answer("done")

        with TemporaryDirectory() as directory:
            path = Path(directory)
            session = Session(path, "generated")
            output = io.StringIO()
            model = Model()
            agent = Agent(model, session, SkillRegistry([]), path, context_chars=9000,
                          on_event=lambda event: render_event(event, output, full=True))
            self.assertEqual(agent.run(session.make_user_message("write and run a script", [])), "done")
            self.assertEqual((path / "generated.py").read_text(), source)
            self.assertEqual((path / "runs.txt").read_text(), "executed once\n")
            saved_call = next(message for message in session.messages() if message.get("tool_calls"))
            self.assertEqual(json.loads(saved_call["tool_calls"][0]["arguments"]), {"command": command})
            context_call = next(message for message in model.observed if message.get("tool_calls"))
            excerpt = json.loads(context_call["tool_calls"][0]["arguments"])["command"]
            self.assertLess(len(excerpt), len(command))
            self.assertIn("generated.py", excerpt)
            result = next(message for message in model.observed if message["role"] == "tool")
            self.assertEqual(json.loads(result["content"])["stdout"], "generated script completed\n")
            self.assertIn(saved_call["tool_calls"][0]["arguments"], output.getvalue())
            replay = io.StringIO()
            for event in session.events():
                render_event(event, replay, full=True)
            self.assertEqual(output.getvalue(), replay.getvalue())

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
                          on_event=lambda event: render_event(event, output, full=True))
            with self.assertRaises(ProviderError):
                agent.run(session.make_user_message("task", []))
            self.assertIn("  original partial\n", output.getvalue())
            self.assertNotIn("RAW_ONLY", output.getvalue())
            self.assertEqual(len(session.messages()), 1)
            replay = io.StringIO()
            for event in session.events():
                render_event(event, replay, full=True)
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


class CLIInteractionTests(unittest.TestCase):
    def invoke(self, directory, model, lines, *options):
        output, error = io.StringIO(), io.StringIO()
        with patch("skills_agent.runtime.create_provider", return_value=model) as provider, \
                patch("sys.stdin", io.StringIO(lines)), redirect_stdout(output), redirect_stderr(error):
            code = main(["run", "--workspace", directory, "--model", "test", *options])
        provider.assert_called_once()
        return code, output.getvalue(), error.getvalue()

    def test_named_session_opens_without_submitting_a_task(self):
        class Model:
            def complete(self, *args):
                raise AssertionError("Starting a session must wait for input")

        with TemporaryDirectory() as directory:
            code, output, error = self.invoke(directory, Model(), "/exit\n", "--session", "demo")
            self.assertEqual((code, output, error), (0, "", ""))
            self.assertTrue((Path(directory) / ".agent" / "sessions" / "demo").is_dir())

    def test_run_rejects_startup_prompt(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            main(["run", "startup task"])
        self.assertEqual(result.exception.code, 2)

    def test_piped_lines_are_distinct_turns_and_preserve_user_text(self):
        class Model:
            def complete(self, system, messages, tools):
                self.messages = messages
                return answer("response")

        with TemporaryDirectory() as directory:
            model = Model()
            code, output, error = self.invoke(directory, model, "  first turn  \nsecond turn\n")
            self.assertEqual((code, error), (0, ""))
            messages = Session(Path(directory) / ".agent" / "sessions", "default").messages()
            self.assertEqual([message["role"] for message in messages],
                             ["user", "assistant", "user", "assistant"])
            self.assertEqual(messages[0]["content"], [{"type": "text", "text": "  first turn  "}])
            self.assertIn("first turn", json.dumps(model.messages))
            self.assertIn("second turn", json.dumps(model.messages))
            self.assertEqual(output.count("assistant:\nresponse"), 2)
            self.assertNotIn("user:", output)
            self.assertNotIn("first turn", output)
            self.assertNotIn("second turn", output)

    def test_files_and_images_are_queued_for_only_the_next_turn(self):
        class Model:
            def complete(self, *args):
                return answer("attached")

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "data.csv").write_text("a,b\n1,2\n")
            (workspace / "sample image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            code, output, error = self.invoke(
                directory, Model(), '/file "sample image.png"\ninspect these\nfollow up\n/exit\n',
                "--file", "data.csv",
            )
            self.assertEqual((code, error), (0, ""))
            users = [message for message in Session(workspace / ".agent" / "sessions", "default").messages()
                     if message["role"] == "user"]
            self.assertEqual([block["type"] for block in users[0]["content"]], ["text", "text", "image"])
            self.assertEqual(users[0]["content"][1]["name"], "data.csv")
            self.assertEqual(users[0]["content"][2]["name"], "sample image.png")
            self.assertEqual(users[1]["content"], [{"type": "text", "text": "follow up"}])

    def test_file_errors_are_reported_and_session_continues(self):
        class Model:
            def complete(self, *args):
                return answer("done")

        with TemporaryDirectory() as directory:
            code, output, error = self.invoke(
                directory, Model(), "/file missing.txt\n/file\nkeep working\n/exit\n",
                "--file", "also-missing.txt",
            )
            self.assertEqual(code, 0)
            self.assertEqual(error.count("error:"), 3)
            self.assertIn("assistant:\ndone", output)

    def test_provider_error_is_shown_once_and_next_turn_runs(self):
        class Model:
            count = 0

            def complete(self, *args):
                self.count += 1
                if self.count == 1:
                    raise ProviderError("failed request")
                return answer("done")

        with TemporaryDirectory() as directory:
            code, output, error = self.invoke(directory, Model(), "first\ntry again\n/exit\n")
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(output.count("error: failed request"), 1)
            self.assertIn("assistant:\ndone", output)

    def test_ctrl_c_at_prompt_returns_to_prompt(self):
        class Model:
            def complete(self, *args):
                return answer("done")

        with TemporaryDirectory() as directory, \
                patch("builtins.input", side_effect=[KeyboardInterrupt(), "task", "/exit"]):
            code, output, error = self.invoke(directory, Model(), "")
            self.assertEqual((code, error), (0, ""))
            self.assertIn("assistant:\ndone", output)

    def test_ctrl_c_during_turn_returns_to_prompt(self):
        class Model:
            count = 0

            def complete(self, *args):
                self.count += 1
                if self.count == 1:
                    raise KeyboardInterrupt()
                return answer("done")

        with TemporaryDirectory() as directory:
            code, output, error = self.invoke(directory, Model(), "first\nsecond\n/exit\n")
            self.assertEqual((code, error), (0, ""))
            self.assertIn("assistant:\ndone", output)


class CLIProtocolTests(unittest.TestCase):
    def test_cli_reads_references_writes_repairs_script_and_resumes_over_both_protocols(self):
        # Scripted model responses validate transport and execution, not model competence.
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider), TemporaryDirectory() as directory:
                requests = []
                workspace = Path(directory)
                skill = workspace / "skills" / "totals"
                (skill / "scripts").mkdir(parents=True)
                instructions = (
                    "---\nname: totals\ndescription: Calculate totals from task data.\n---\n"
                    "Read scripts/example.py as a reference, then write code for the task.\n"
                )
                reference = "def total(numbers):\n    return sum(numbers)\n"
                (skill / "SKILL.md").write_text(instructions)
                (skill / "scripts" / "example.py").write_text(reference)
                (workspace / "data.json").write_text('{"numbers": [2, 3, 5]}')
                broken_source = (
                    "import json\nfrom pathlib import Path\n"
                    "data = json.loads(Path('data.json').read_text())\n"
                    "result = sum(data['values'])\n"
                    "Path('result.txt').write_text(str(result))\n"
                    "print(f'total={result}')\n"
                )
                fixed_source = broken_source.replace("data['values']", "data['numbers']")
                commands = [
                    "cat skills/totals/SKILL.md skills/totals/scripts/example.py",
                    "cat > task.py <<'PY'\n" + broken_source + "PY\npython3 task.py",
                    "cat > task.py <<'PY'\n" + fixed_source + "PY\npython3 task.py",
                ]
                summaries = ["读取技能参考", "编写计算脚本", "修复数据字段"]
                self.assertFalse((workspace / "task.py").exists())

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_POST(self):
                        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                        requests.append((self.path, data))
                        if len(requests) <= len(commands):
                            arguments = {"command": commands[len(requests) - 1],
                                         "summary": summaries[len(requests) - 1]}
                            identifier = f"shell{len(requests)}"
                            if provider == "openai":
                                response = {"choices": [{"finish_reason": "tool_calls", "message": {
                                    "content": None, "tool_calls": [{"id": identifier, "type": "function", "function": {
                                        "name": "shell", "arguments": json.dumps(arguments),
                                    }}],
                                }}]}
                            else:
                                response = {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": identifier,
                                    "name": "shell", "input": arguments}]}
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
                    first = subprocess.run(command, input="create result\nfollow up\n/exit\n",
                                           capture_output=True, text=True)
                    self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
                    self.assertEqual((workspace / "result.txt").read_text(), "10")
                    self.assertEqual((workspace / "task.py").read_text(), fixed_source)
                    self.assertNotIn("KeyError", first.stdout)
                    self.assertNotIn("total=10", first.stdout)
                    self.assertNotIn("tool_call", first.stdout)
                    self.assertNotIn("cat > task.py", first.stdout)
                    for summary in summaries:
                        self.assertIn(summary, first.stdout)
                    self.assertIn("shell：失败（退出码 1）", first.stdout)
                    self.assertIn("shell：完成", first.stdout)
                    self.assertIn("completed", first.stdout)
                    session = Session(workspace / ".agent" / "sessions", "default")
                    messages = session.messages()
                    saved_commands = [json.loads(call["arguments"])["command"]
                                      for message in messages for call in message.get("tool_calls", [])]
                    self.assertEqual(saved_commands, commands)
                    results = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
                    self.assertEqual(results[0]["stdout"], instructions + reference)
                    self.assertNotEqual(results[1]["exit_code"], 0)
                    self.assertIn("KeyError: 'values'", results[1]["stderr"])
                    self.assertEqual(results[2], {"exit_code": 0, "stdout": "total=10\n", "stderr": ""})
                    for index in range(3):
                        payload = requests[index + 1][1]
                        if provider == "openai":
                            received = [json.loads(message["content"]) for message in payload["messages"]
                                        if message["role"] == "tool"]
                        else:
                            received = [json.loads(block["content"]) for message in payload["messages"]
                                        for block in message["content"] if block["type"] == "tool_result"]
                        self.assertEqual(received[-1], results[index])
                    session_log = workspace / ".agent" / "sessions" / "default" / "events.jsonl"
                    before = session_log.read_bytes()
                    second = subprocess.run(command, input="continue via stdin\n/exit\n",
                                            capture_output=True, text=True)
                    self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
                    self.assertTrue(session_log.read_bytes().startswith(before))
                    self.assertEqual(len(requests), 6)
                    self.assertNotIn("_raw", json.dumps(requests[-1]))
                    self.assertIn("create result", json.dumps(requests[-1]))
                    self.assertIn("follow up", json.dumps(requests[-1]))
                    self.assertIn("continue via stdin", json.dumps(requests[-1]))
                    history = subprocess.run(
                        [sys.executable, "-m", "skills_agent", "history", "--workspace", directory],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(history.returncode, 0, history.stderr)
                    self.assertNotIn("create result", first.stdout)
                    self.assertNotIn("follow up", first.stdout)
                    self.assertIn("create result", second.stdout)
                    self.assertIn("follow up", second.stdout)
                    self.assertNotIn("continue via stdin", second.stdout)
                    self.assertIn("continue via stdin", history.stdout)
                    self.assertNotIn("\033[", first.stdout + second.stdout + history.stdout)
                    recorded = session_log.read_bytes()
                    full_history = subprocess.run(
                        [sys.executable, "-m", "skills_agent", "history", "--full", "--workspace", directory],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(full_history.returncode, 0, full_history.stderr)
                    self.assertIn("KeyError", full_history.stdout)
                    self.assertIn("total=10", full_history.stdout)
                    for message in session.messages():
                        for tool_call in message.get("tool_calls", []):
                            self.assertIn(tool_call["arguments"], full_history.stdout)
                    self.assertEqual(session_log.read_bytes(), recorded)
                    expected = "/v1/chat/completions" if provider == "openai" else "/v1/messages"
                    self.assertTrue(all(path == expected for path, _ in requests))
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
