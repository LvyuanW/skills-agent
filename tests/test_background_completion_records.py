"""Consumed background outcomes appear as separate tool-like transcript records."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer, main, render_event
from skills_agent.session import Session
from skills_agent.terminal import TerminalView


def completed(identifier="job-1", **values):
    result = {
        "job_id": identifier, "name": "internal-name", "display_name": "提取特征",
        "kind": "job", "state": "exited", "exit_code": 0, "process_returncode": 0,
        "duration_seconds": 4.25, "timed_out": False, "cleanup_required": False,
        "command": "PRIVATE_COMMAND", "stdout": "PRIVATE_STDOUT", "stderr": "PRIVATE_STDERR",
        **values,
    }
    return {"type": "message", "message": {
        "role": "user", "runtime_event": "background_job_result",
        "background_result_id": f"{identifier}-terminal", "background_job_id": identifier,
        "job_owner_session_id": "PRIVATE_OWNER",
        "content": [
            {"type": "text", "text": "[Runtime background job result — observation, not a new user request.]"},
            {"type": "text", "text": json.dumps(result, ensure_ascii=False)},
        ],
    }}


def assistant(text):
    return {"type": "message", "message": {"role": "assistant", "content": text}}


from tests.runtime_paths import isolated_runtime, runtime_sessions

_runtime_environment = isolated_runtime()
setUpModule = _runtime_environment.start
tearDownModule = _runtime_environment.stop


class BackgroundCompletionRecordTests(unittest.TestCase):
    def test_compact_direct_render_is_named_tool_record_without_private_details(self):
        event = completed()
        before = deepcopy(event)
        for echo in (False, True):
            with self.subTest(echo=echo):
                output = StringIO()
                render_event(event, output, echo_user=echo)
                self.assertEqual(output.getvalue(),
                                 "· 后台任务「提取特征」：完成 · 4.2 秒；结果已加入上下文\n")
                self.assertNotIn("PRIVATE", output.getvalue())
                self.assertNotIn("user:", output.getvalue())
                self.assertNotIn("Runtime", output.getvalue())
                self.assertNotIn("internal-name", output.getvalue())
        self.assertEqual(event, before)

    def test_failure_timeout_stopped_and_residual_cleanup_are_red(self):
        for fields, status in (
            ({"exit_code": 7}, "失败（退出码 7）"),
            ({"state": "timed_out", "timed_out": True, "exit_code": 124}, "超时"),
            ({"state": "stopped", "exit_code": 143}, "已停止"),
            ({"cleanup_required": True}, "入口已退出（退出码 0），已清理残留进程"),
            ({"state": "failed", "exit_code": None}, "失败"),
        ):
            with self.subTest(fields=fields):
                view = TerminalView(input=DummyInput(), output=DummyOutput())
                ConsoleRenderer(view)(completed(**fields))
                self.assertEqual(len(view.blocks), 1)
                record = view.blocks[0]
                self.assertEqual(record["role"], "error")
                self.assertIn(status, record["text"])
                self.assertIn("结果已加入上下文", record["text"])

    def test_success_is_blue_and_service_delegation_use_readable_labels(self):
        view = TerminalView(input=DummyInput(), output=DummyOutput())
        ConsoleRenderer(view)(completed(kind="service", delegated_task_name="分析样本"))
        record = view.blocks[0]
        self.assertEqual(record["role"], "tool")
        self.assertIn("后台服务「提取特征」（子任务「分析样本」）", record["text"])

    def test_duration_accepts_only_finite_numbers(self):
        for duration, expected in ((0, "0.0 秒"), (-1, "0.0 秒"), (12.34, "12.3 秒"),
                                   (float("inf"), None), (float("nan"), None),
                                   (True, None), ("12", None), (None, None)):
            with self.subTest(duration=duration):
                output = StringIO()
                render_event(completed(duration_seconds=duration), output)
                if expected:
                    self.assertIn(expected, output.getvalue())
                else:
                    self.assertNotIn(" 秒", output.getvalue())

    def test_same_name_jobs_remain_separate_and_duplicates_do_not_repeat(self):
        for full in (False, True):
            for rich in (False, True):
                with self.subTest(full=full, rich=rich):
                    output = (TerminalView(input=DummyInput(), output=DummyOutput())
                              if rich else StringIO())
                    renderer = ConsoleRenderer(output, full=full, live=False)
                    for event in (completed(), completed(), completed("job-2"), completed("job-2")):
                        renderer(event)
                    text = ("\n".join(block.get("text", "") for block in output.blocks)
                            if rich else output.getvalue())
                    self.assertEqual(text.count("结果已加入上下文"), 2)

    def test_missing_result_id_falls_back_to_job_id(self):
        event = completed()
        del event["message"]["background_result_id"]
        output = StringIO()
        renderer = ConsoleRenderer(output)
        renderer(event)
        renderer(event)
        self.assertEqual(output.getvalue().count("结果已加入上下文"), 1)

    def test_completion_is_inserted_between_assistant_messages_and_not_start_receipt(self):
        view = TerminalView(input=DummyInput(), output=DummyOutput())
        renderer = ConsoleRenderer(view)
        renderer({"type": "message", "message": {"role": "tool", "name": "shell", "tool_call_id": "call-1",
                  "content": json.dumps({"job_id": "job-1", "completed": False, "kind": "job"})}})
        renderer(assistant("等待结果"))
        renderer(completed())
        renderer(assistant("检查结果"))
        records = [block for block in view.blocks if block.get("kind") == "line"]
        self.assertEqual(len(records), 2)
        self.assertIn("启动回执", records[0]["text"])
        self.assertIn("结果已加入上下文", records[1]["text"])
        rendered = "\n".join(block.get("text", "") for block in view.blocks)
        self.assertLess(rendered.index("等待结果"), rendered.index("结果已加入上下文"))
        self.assertLess(rendered.index("结果已加入上下文"), rendered.index("检查结果"))

    def test_completion_finishes_partial_plain_stream_before_its_own_record(self):
        output = StringIO()
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "等待"})
        renderer(completed())
        renderer({"type": "text_delta", "text": "检查"})
        renderer.close()
        text = output.getvalue()
        self.assertIn("等待\n· 后台任务", text)
        self.assertIn("结果已加入上下文\nassistant:\n检查", text)

    def test_full_expands_result_and_image_links_without_user_or_runtime_envelope(self):
        event = completed()
        event["message"]["content"].append({"type": "image", "path": "/result.png", "data": "PRIVATE_BYTES"})
        output = StringIO()
        render_event(event, output, full=True)
        text = output.getvalue()
        self.assertIn("后台任务「提取特征」", text)
        self.assertIn("PRIVATE_STDOUT", text)
        self.assertIn("[图片] /result.png", text)
        self.assertNotIn("user:", text)
        self.assertNotIn("Runtime background", text)
        self.assertNotIn("PRIVATE_BYTES", text)
        self.assertNotIn("PRIVATE_OWNER", text)

    def test_history_replays_completion_once_in_order_and_does_not_change_log(self):
        for full in (False, True):
            with self.subTest(full=full), TemporaryDirectory() as directory:
                root = Path(directory)
                session = Session(runtime_sessions(root), "background-records")
                for event in (assistant("之前"), completed(), completed(), assistant("之后")):
                    session.append_event(event)
                path = session.path / "events.jsonl"
                before = path.read_bytes()
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["history", "--workspace", str(root), "--session", "background-records", *(["--full"] if full else [])]), 0)
                text = output.getvalue()
                self.assertEqual(text.count("结果已加入上下文"), 1)
                self.assertLess(text.index("之前"), text.index("结果已加入上下文"))
                self.assertLess(text.index("结果已加入上下文"), text.index("之后"))
                self.assertNotIn("user:", text)
                self.assertNotIn("Runtime background", text)
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
