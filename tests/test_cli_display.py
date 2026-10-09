from contextlib import redirect_stdout, redirect_stderr
import copy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.cli import main, render_event
from skills_agent.session import Session


def message(role, content, **fields):
    return {"type": "message", "message": {"role": role, "content": content, **fields}}


def call(name="shell", arguments=None, identifier="c1"):
    return {"id": identifier, "name": name,
            "arguments": json.dumps(arguments or {"command": "printf RAW_COMMAND"})}


class DisplayTests(unittest.TestCase):
    def render(self, events, **options):
        output = io.StringIO()
        for event in events:
            render_event(event, output, **options)
        return output.getvalue()

    def test_terminal_roles_have_distinct_colors_and_plain_output_has_none(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True

        events = [
            message("user", "user text"),
            message("assistant", "first line\nsecond line"),
            message("assistant", "", tool_calls=[call(arguments={"command": "pwd", "summary": "检查目录"})]),
            message("tool", '{"exit_code":0,"stdout":"","stderr":""}', name="shell", tool_call_id="c1"),
            {"type": "subagent_summary", "goal": "统计文件", "status": "completed"},
        ]
        original = copy.deepcopy(events)
        for full in (False, True):
            with self.subTest(full=full):
                terminal = TTY()
                for event in events:
                    render_event(event, terminal, full=full)
                text = terminal.getvalue()
                self.assertRegex(text, r"\x1b\[97;40muser text +\x1b\[0m\n")
                self.assertRegex(text, r"\x1b\[30;107mfirst line second line *\x1b\[0m\n")
                self.assertIn("\033[34;49msub-agent 处理了：统计文件\033[0m\n", text)
                self.assertIn("\033[34;49m" + ("tool_call shell:" if full else "· shell：检查目录"), text)
                self.assertNotIn("\033[", self.render(events, full=full))
        self.assertEqual(events, original)

    def test_compact_replay_preserves_event_objects_and_original_log_bytes(self):
        command = "cat > generated.py <<'PY'\n" + "print('RAW_SOURCE')\n" * 500 + "PY"
        stdout = "RAW_OUTPUT\n" * 500
        events = [
            message("user", "  original request\nsecond line  "),
            message("assistant", "intermediate details " * 500, tool_calls=[call(arguments={
                "command": command, "summary": "生成并运行分析脚本",
            })], reasoning_content="RAW_REASONING", _raw={"reasoning_content": "RAW_REASONING"}),
            message("tool", json.dumps({"exit_code": 0, "stdout": stdout, "stderr": ""}),
                    name="shell", tool_call_id="c1"),
            message("assistant", [{"type": "text", "text": "  final answer\nunchanged  "}]),
        ]
        original = copy.deepcopy(events)
        with TemporaryDirectory() as directory:
            session = Session(Path(directory), "display")
            for event in events:
                session.append_event(event)
            path = session.path / "events.jsonl"
            before = path.read_bytes()
            compact = self.render(events)
            self.assertEqual(self.render(session.events()), compact)
            self.assertIn("shell：生成并运行分析脚本", compact)
            self.assertIn("完成，输出 500 行", compact)
            self.assertIn("  original request\nsecond line  ", compact)
            self.assertIn("  final answer\nunchanged  ", compact)
            self.assertIn("思考:\nRAW_REASONING\n", compact)
            self.assertIn("intermediate details", compact)
            for hidden in ("RAW_SOURCE", "RAW_OUTPUT"):
                self.assertNotIn(hidden, compact)
            detailed = self.render(session.events(), full=True)
            self.assertIn(events[1]["message"]["tool_calls"][0]["arguments"], detailed)
            self.assertIn(events[2]["message"]["content"], detailed)
            self.assertEqual(detailed.count("RAW_REASONING"), 1)
            self.assertEqual(events, original)
            self.assertEqual(path.read_bytes(), before)

    def test_long_final_answer_is_never_summarized(self):
        final = "  Final explanation\n\n```python\n" + "print('keep this line')\n" * 1000 + "```\n  "
        for content in (final, [{"type": "text", "text": final}]):
            with self.subTest(content_type=type(content).__name__):
                self.assertEqual(self.render([message("assistant", content)]), "assistant:\n" + final + "\n")

    def test_public_body_with_tool_calls_is_shown_and_raw_private_fields_are_hidden(self):
        text = "I need to think through these inputs.\n```python\n" + "print('INTERMEDIATE_CODE')\n" * 500 + "```"
        event = message("assistant", [{"type": "text", "text": text}], tool_calls=[call(), call(identifier="c2")],
                        _raw={"reasoning_content": "PRIVATE_REASONING", "thinking": "PRIVATE_THINKING"})
        output = self.render([event])
        self.assertEqual(output, "assistant:\n" + text + "\n" + "· shell：接收调用\n" * 2)
        self.assertNotIn("RAW_COMMAND", output)
        self.assertNotIn("PRIVATE_", output)
        detailed = self.render([event], full=True)
        self.assertIn(text, detailed)
        self.assertIn("RAW_COMMAND", detailed)
        self.assertNotIn("PRIVATE_", detailed)

    def test_missing_or_malformed_summary_uses_safe_progress_fallback(self):
        for arguments in ("not json", "[]", '{"summary": 42}', '{"summary": "   "}'):
            with self.subTest(arguments=arguments):
                tool_call = call()
                tool_call["arguments"] = arguments
                event = message("assistant", "Read inputs", tool_calls=[tool_call])
                self.assertEqual(self.render([event]), "assistant:\nRead inputs\n· shell：接收调用\n")
        self.assertEqual(self.render([message("assistant", [], tool_calls=[call()])]),
                         "· shell：接收调用\n")

    def test_failure_status_is_visible_without_dumping_tool_output(self):
        cases = [
            ({"exit_code": 7, "stdout": "RAW_STDOUT\n", "stderr": "RAW_STDERR\n"}, "失败（退出码 7），输出 2 行"),
            ({"error": "invalid argument\n" + "detail " * 500}, "失败：invalid argument"),
            ({"exit_code": 0, "stdout": "", "stderr": ""}, "完成"),
        ]
        for result, expected in cases:
            with self.subTest(result=result):
                output = self.render([message("tool", json.dumps(result), name="shell", tool_call_id="c1")])
                self.assertIn(expected, output)
                self.assertNotIn("RAW_STD", output)
                self.assertLess(len(output), 130)
                self.assertEqual(len(output.splitlines()), 1)

    def test_result_metadata_displays_name_without_prior_call(self):
        output = self.render([message("tool", '{"exit_code":0,"stdout":"","stderr":""}',
                                     name="shell", task_name="检查配置", tool_call_id="c1")])
        self.assertEqual(output, "· shell「检查配置」：完成\n")

    def test_non_json_or_non_object_tool_result_does_not_break_display(self):
        for content in ("RAW_LEGACY_OUTPUT", "[]", "null", '"RAW_TEXT"'):
            with self.subTest(content=content):
                self.assertEqual(self.render([message("tool", content, name="shell", tool_call_id="c1")]),
                                 "· shell：结果已记录\n")

    def test_partial_error_output_is_bounded_and_full_history_keeps_it(self):
        partial = "  received partial\n" + "unfinished " * 1000
        event = {"type": "error", "message": "response truncated", "partial_content": [
            {"type": "text", "text": partial},
        ], "_raw": {"reasoning_content": "PRIVATE_REASONING"}}
        original = copy.deepcopy(event)
        compact = self.render([event])
        self.assertIn(f"回答未完成，已保存 {len(partial)} 字符", compact)
        self.assertNotIn("received partial", compact)
        self.assertNotIn("unfinished", compact)
        self.assertIn("error: response truncated", compact)
        self.assertNotIn("assistant:", compact)
        self.assertLess(len(compact), 160)
        detailed = self.render([event], full=True)
        self.assertIn(partial, detailed)
        self.assertNotIn("PRIVATE_REASONING", compact + detailed)
        self.assertEqual(event, original)

    def test_child_details_stay_private_in_both_display_modes(self):
        events = [
            message("assistant", "Inspect inputs", tool_calls=[
                call(), call("subagent", {"goal": "统计文件", "task": "PRIVATE_TASK"}, "c2"),
            ], _raw={"reasoning_content": "PRIVATE_REASONING"}),
            {"type": "subagent_summary", "goal": "统计文件", "status": "completed"},
            message("tool", '{"result": "PRIVATE_CHILD_RESULT"}', name="subagent", tool_call_id="c2"),
            message("assistant", "Result"),
        ]
        for full in (False, True):
            with self.subTest(full=full):
                output = self.render(events, full=full)
                self.assertIn("sub-agent 处理了：统计文件", output)
                self.assertIn("Result", output)
                self.assertNotIn("PRIVATE_", output)
                self.assertNotIn("tool_call subagent", output)

    def test_live_tty_hides_new_user_text_but_shows_attachments_and_replays_history(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True

        class Model:
            def complete(self, *args, **kwargs):
                return {"role": "assistant", "content": "done"}

            def count_tokens(self, *args):
                return 128

            def count_text(self, text):
                return len(text) // 4

            def compact_messages(self, messages):
                return copy.deepcopy(messages)

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            attachment = workspace / "input.txt"
            attachment.write_text("ATTACHMENT_BODY")
            session = Session(workspace / ".agent/sessions", "default")
            session.append({"role": "user", "content": "prior user message"})
            session.append({"role": "assistant", "content": "prior answer"})
            output, error = io.StringIO(), io.StringIO()
            with patch("skills_agent.runtime.create_provider", return_value=Model()), \
                    patch("sys.stdin", TTY()), patch("builtins.input", side_effect=["new user message", "/exit"]), \
                    redirect_stdout(output), redirect_stderr(error):
                status = main(["run", "--workspace", directory, "--model", "test", "--file", "input.txt"])
            self.assertEqual((status, error.getvalue()), (0, ""))
            live = output.getvalue()
            self.assertIn("prior user message", live)
            self.assertIn("[附件] input.txt", live)
            self.assertIn("done", live)
            self.assertNotIn("new user message", live)
            self.assertNotIn("ATTACHMENT_BODY", live)
            replay = io.StringIO()
            with redirect_stdout(replay):
                self.assertEqual(main(["history", "--workspace", directory]), 0)
            self.assertIn("new user message", replay.getvalue())
            self.assertIn("[附件] input.txt", replay.getvalue())
            self.assertNotIn("ATTACHMENT_BODY", replay.getvalue())
            detailed = io.StringIO()
            with redirect_stdout(detailed):
                self.assertEqual(main(["history", "--full", "--workspace", directory]), 0)
            self.assertIn("ATTACHMENT_BODY", detailed.getvalue())


if __name__ == "__main__":
    unittest.main()
