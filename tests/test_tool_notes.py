"""Tool status rows stay readable as streamed calls become recorded results."""

import copy
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer
from skills_agent.session import Session
from skills_agent.terminal import TerminalView
from tests.test_cli_terminal import terminal_screen


def call(identifier="shell-1", name="shell", task="检查文件"):
    return {"id": identifier, "name": name, "arguments": json.dumps({
        "name": task, "command": "PRIVATE_COMMAND",
    }, ensure_ascii=False)}


def assistant(*calls, streamed=False, content=""):
    return {"type": "message", "streamed": streamed, "message": {
        "role": "assistant", "content": content, "tool_calls": list(calls),
    }}


def result(identifier="shell-1", name="shell", value=None, *, blocks=False):
    content = json.dumps(value if value is not None else {
        "exit_code": 0, "stdout": "PRIVATE_STDOUT", "stderr": "",
    })
    return {"type": "message", "message": {
        "role": "tool", "name": name, "tool_call_id": identifier,
        "content": [{"type": "text", "text": content}] if blocks else content,
    }}


class TTY(StringIO):
    def isatty(self):
        return True


class ToolNoteTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "TERM": "xterm-256color", "COLUMNS": "100", "LINES": "24",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.view = self.make_view()
        self.renderer = ConsoleRenderer(self.view)

    def make_view(self):
        return TerminalView(input=DummyInput(), output=DummyOutput())

    def lines(self, view=None):
        content = (view or self.view).control.create_content(100, 24)
        return [fragment_list_to_text(content.get_line(index)).rstrip()
                for index in range(content.line_count)]

    def assert_role(self, marker, role, view=None):
        target = view or self.view
        matching = [block for block in target.blocks if marker in block.get("text", "")]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["role"], role)
        color = "ansired" if role == "error" else "ansiblue"
        rows = [row for row in target._lines(100)
                if marker in fragment_list_to_text(row)]
        self.assertEqual(len(rows), 1)
        self.assertTrue(all(color in style for style, text, *rest in rows[0] if text.strip()))

    def test_fragmented_interleaved_calls_update_own_rows_through_reversed_results(self):
        self.renderer({"type": "tool_call_delta", "index": 0, "name": "sh"})
        self.renderer({"type": "tool_call_delta", "index": 0, "name": "ell",
                       "arguments": '{"command":"PRIVATE_COMMAND",'})
        self.assertEqual(self.lines(), ["· shell：接收调用"])
        self.renderer({"type": "text_delta", "text": "工具参数仍在接收"})
        sandbox = call("sandbox-1", "sandbox", "检查容器")
        self.renderer({"type": "tool_call_delta", "index": 1, "name": "sandbox",
                       "arguments": sandbox["arguments"]})
        self.renderer({"type": "tool_call_delta", "index": 0,
                       "arguments": '"name":"检查文件"}'})
        self.assertEqual(self.lines(), [
            "· shell「检查文件」：调用", "assistant:", "工具参数仍在接收",
            "· sandbox「检查容器」：调用",
        ])
        self.renderer(assistant(call(), sandbox, streamed=True, content="工具参数仍在接收"))
        self.renderer(result("sandbox-1", "sandbox"))
        self.renderer(result(value={"exit_code": 7, "stdout": "", "stderr": "PRIVATE_STDERR"}))
        self.assertEqual(self.lines(), [
            "· shell「检查文件」：失败（退出码 7），输出 1 行", "assistant:", "工具参数仍在接收",
            "· sandbox「检查容器」：完成，输出 1 行",
        ])
        self.assert_role("失败（退出码 7）", "error")
        self.assert_role("sandbox「检查容器」", "tool")
        self.assertNotIn("PRIVATE_", "\n".join(self.lines()))

    def test_history_keeps_one_final_row_per_call_and_preserves_saved_events(self):
        first, second = call("first"), call("second")
        events = [
            {"type": "tool_call_delta", "index": 0, "name": "shell",
             "arguments": '{"command":"PRIVATE_COMMAND",'},
            {"type": "tool_call_delta", "index": 0, "arguments": '"name":"检查文件"}'},
            assistant(first, streamed=True), result("first"),
            assistant(second), result("second", value={"error": "缺少参数"}),
        ]
        original = copy.deepcopy(events)
        with TemporaryDirectory() as directory:
            session = Session(Path(directory), "tool-notes")
            for event in events:
                session.append_event(event)
            path = session.path / "events.jsonl"
            saved = path.read_bytes()
            renderer = ConsoleRenderer(self.view, live=False)
            for event in session.events():
                renderer(event)
            renderer.close()
            self.assertEqual(self.lines(), [
                "· shell「检查文件」：完成，输出 1 行", "· shell「检查文件」：失败：缺少参数",
            ])
            self.assert_role("失败：缺少参数", "error")
            self.assertEqual(path.read_bytes(), saved)
        self.assertEqual(events, original)

    def test_partial_stream_with_known_call_id_does_not_merge_an_earlier_call(self):
        first = call("first", task="第一个检查")
        second = call("second", task="第二个检查")
        self.renderer({"type": "tool_call_delta", "index": 4, "id": "sec", "name": "shell",
                       "arguments": '{"name":"第二个检查",'})
        self.renderer({"type": "tool_call_delta", "index": 4, "id": "ond",
                       "arguments": '"command":"PRIVATE_COMMAND"}'})
        self.renderer(assistant(first, second, streamed=True))
        self.renderer(result("first"))
        self.renderer(result("second", value={"exit_code": 2, "stdout": "", "stderr": ""}))
        self.assertCountEqual(self.lines(), [
            "· shell「第一个检查」：完成，输出 1 行", "· shell「第二个检查」：失败（退出码 2）",
        ])
        self.assert_role("第一个检查", "tool")
        self.assert_role("第二个检查", "error")

    def test_tool_activity_occupies_existing_row_until_final_result(self):
        self.renderer(assistant(call()))
        self.renderer({"type": "activity", "activity": {
            "kind": "tool", "name": "shell", "task_name": "检查文件",
        }})
        active = self.lines()
        self.assertEqual(len(active), 1)
        self.assertRegex(active[0], r"^· shell「检查文件」：执行中\.{1,3}$")
        self.renderer({"type": "activity", "activity": None})
        self.renderer(result())
        self.assertEqual(self.lines(), ["· shell「检查文件」：完成，输出 1 行"])
        self.renderer({"type": "activity", "activity": {"kind": "compression"}})
        self.assertEqual(len(self.lines()), 2)
        self.assertIn("正在压缩上下文", self.lines()[1])
        self.renderer({"type": "activity", "activity": None})
        self.assertEqual(self.lines(), ["· shell「检查文件」：完成，输出 1 行"])

    def test_failed_results_change_cached_blue_rows_to_red_for_all_failure_kinds(self):
        for value in (
            {"exit_code": 3, "stdout": "PRIVATE_STDOUT", "stderr": ""},
            {"error": "参数无效"},
            {"exit_code": 0, "stdout": "", "feedback_errors": ["PRIVATE_FEEDBACK"]},
        ):
            for blocks in (False, True):
                with self.subTest(value=value, blocks=blocks):
                    view = self.make_view()
                    renderer = ConsoleRenderer(view)
                    renderer(assistant(call()))
                    self.assert_role("检查文件", "tool", view)
                    renderer(result(value=value, blocks=blocks))
                    self.assertEqual(len(self.lines(view)), 1)
                    self.assert_role("检查文件", "error", view)
                    self.assertNotIn("PRIVATE_", "\n".join(self.lines(view)))

    def test_changing_only_tool_row_role_invalidates_cached_color(self):
        key = object()
        self.view.upsert_tool(key, "保持原文", "tool")
        self.assert_role("保持原文", "tool")
        self.view.upsert_tool(key, "保持原文", "error")
        self.assertEqual(self.lines(), ["保持原文"])
        self.assert_role("保持原文", "error")

    def test_subagent_summary_and_early_validation_failure_finalize_without_private_details(self):
        for status, label, role in (("completed", "已返回报告", "tool"),
                                    ("failed", "失败", "error"),
                                    ("interrupted", "已中断", "error"),
                                    (None, "失败", "error")):
            with self.subTest(status=status):
                view = self.make_view()
                renderer = ConsoleRenderer(view)
                child = {"id": "child-1", "name": "subagent", "arguments": json.dumps({
                    "name": "检查文档", "task": "PRIVATE_CHILD_TASK",
                }, ensure_ascii=False)}
                renderer(assistant(child))
                if status is not None:
                    renderer({"type": "subagent_summary", "name": "检查文档", "status": status,
                              "report": "PRIVATE_CHILD_REPORT", "error": "PRIVATE_CHILD_ERROR"})
                renderer(result("child-1", "subagent", value={
                    "error": "PRIVATE_CHILD_ERROR", "report": "PRIVATE_CHILD_REPORT",
                }))
                self.assertEqual(self.lines(view), [f"· sub-agent「检查文档」：{label}"])
                self.assert_role("检查文档", role, view)
                self.assertNotIn("PRIVATE_", "\n".join(self.lines(view)))

    def test_interruption_and_retry_finalize_partial_call_before_index_is_reused(self):
        events = [
            ({"type": "error", "message": "Interrupted"}, "中断"),
            ({"type": "retry", "category": "connection_error", "message": "lost connection",
              "retry_in_seconds": 1, "attempt": 1}, "重试"),
        ]
        for failure, marker in events:
            with self.subTest(kind=failure["type"]):
                view = self.make_view()
                renderer = ConsoleRenderer(view)
                renderer({"type": "tool_call_delta", "index": 0, "name": "shell",
                          "arguments": '{"command":"PRIVATE_COMMAND",'})
                renderer(failure)
                first = self.lines(view)[0]
                self.assertIn(marker, first)
                self.assert_role(first, "error", view)
                renderer({"type": "tool_call_delta", "index": 0, "name": "shell",
                          "arguments": call("next")["arguments"]})
                renderer(assistant(call("next"), streamed=True))
                renderer(result("next"))
                self.assertEqual(self.lines(view)[0], first)
                self.assertEqual(sum("完成，输出 1 行" in line for line in self.lines(view)), 1)
                self.assertFalse(any("接收调用" in line or "：调用" in line for line in self.lines(view)))

    def test_plain_tty_failures_are_red_in_compact_and_full_transcripts(self):
        for full in (False, True):
            for value in ({"exit_code": 2}, {"error": "bad argument"},
                          {"exit_code": 0, "feedback_errors": ["missing image"]}):
                with self.subTest(full=full, value=value):
                    output = TTY()
                    ConsoleRenderer(output, full=full, live=False)(result(value=value))
                    screen = terminal_screen(output.getvalue())
                    marker = "tool shell:" if full else "· shell"
                    for row in screen.buffer.values():
                        text = "".join(row[x].data for x in range(screen.columns))
                        if marker in text:
                            self.assertEqual(row[text.index(marker)].fg, "red")
                            break
                    else:
                        self.fail(f"missing tool result: {output.getvalue()!r}")

    def test_plain_output_and_full_terminal_view_keep_append_only_call_details(self):
        current = call()
        events = [
            {"type": "tool_call_delta", "index": 0, "name": "shell",
             "arguments": '{"command":"PRIVATE_COMMAND",'},
            {"type": "tool_call_delta", "index": 0, "arguments": '"name":"检查文件"}'},
            assistant(current, streamed=True), result(),
        ]
        output = StringIO()
        renderer = ConsoleRenderer(output)
        for event in events:
            renderer(event)
        self.assertEqual(output.getvalue().splitlines(), [
            "· shell：接收调用", "· shell「检查文件」：调用", "· shell「检查文件」：完成，输出 1 行",
        ])
        renderer = ConsoleRenderer(self.view, full=True)
        for event in events:
            renderer(event)
        self.assertTrue(any("tool_call shell:" in line for line in self.lines()))
        self.assertIn("PRIVATE_COMMAND", "\n".join(self.lines()))
        self.assertIn("PRIVATE_STDOUT", "\n".join(self.lines()))


if __name__ == "__main__":
    unittest.main()
