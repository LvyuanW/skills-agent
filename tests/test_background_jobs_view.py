"""Background status stays independent of foreground output and selection."""

from io import StringIO
import json
import unittest
from unittest.mock import patch

from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer
from skills_agent.terminal import TerminalView


class BackgroundJobsViewTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        clock = patch("skills_agent.terminal.monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())
        self.addCleanup(self.view.close)
        self.renderer = ConsoleRenderer(self.view)

    def job(self, identifier, state="running", elapsed=2.0, code=None, name="提取特征"):
        return {"id": identifier, "name": name, "state": state,
                "elapsed_seconds": elapsed, "returncode": code}

    def update(self, *jobs):
        self.renderer({"type": "background_jobs", "jobs": list(jobs)})

    def completion(self, identifier="one", *, state="exited", code=0,
                   name="提取特征", elapsed=4.2, **values):
        result = {"job_id": identifier, "name": name, "state": state,
                  "kind": "job", "exit_code": code,
                  "duration_seconds": elapsed, "timed_out": state == "timed_out",
                  "stdout": "PRIVATE_BACKGROUND_STDOUT", "stderr": "PRIVATE_BACKGROUND_STDERR",
                  **values}
        return {"type": "message", "message": {
            "role": "user", "runtime_event": "background_job_result",
            "background_result_id": f"{identifier}-terminal", "background_job_id": identifier,
            "content": [{"type": "text", "text": "[Runtime background job result — PRIVATE_RUNTIME_WRAPPER]"},
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        }}

    def lines(self):
        content = self.view.control.create_content(100, 30)
        return [fragment_list_to_text(content.get_line(index)).rstrip()
                for index in range(content.line_count)]

    def test_concurrent_jobs_do_not_interrupt_stream_or_foreground_activity(self):
        self.renderer({"type": "text_delta", "text": "任务"})
        self.update(self.job("first"), self.job("second", name="训练"))
        self.renderer({"type": "text_delta", "text": "进行中"})
        self.view.set_activity("· shell「检查」：执行中")
        self.now += 1.2
        lines = self.lines()
        self.assertEqual(lines[:2], ["assistant:", "任务进行中"])
        self.assertIn("· 后台「提取特征」：运行中 · 3.2 秒", lines)
        self.assertIn("· 后台「训练」：运行中 · 3.2 秒", lines)
        self.assertTrue(any("· shell「检查」：执行中" in line for line in lines))
        self.view.set_activity(None)
        self.update(self.job("first", "exited", elapsed=3.2, code=0),
                    self.job("second", elapsed=3.2, name="训练"))
        self.view.finish_turn()
        self.now += 2.0
        self.assertIn("· 后台「提取特征」：完成 · 3.2 秒", self.lines())
        self.assertIn("· 后台「训练」：运行中 · 5.2 秒", self.lines())
        with patch.object(self.view, "flush") as flush:
            self.view._animate()
            flush.assert_called_once()

    def test_same_name_jobs_keep_distinct_identity_and_terminal_times_freeze(self):
        self.update(self.job("scope-a", "exited", elapsed=4.2, code=0),
                    self.job("scope-b", "exited", elapsed=1.3, code=7))
        expected = ["· 后台「提取特征」：完成 · 4.2 秒",
                    "· 后台「提取特征」：失败（退出码 7） · 1.3 秒"]
        self.assertEqual(self.lines(), expected)
        self.now += 400
        self.assertEqual(self.lines(), expected)
        rows = self.view._lines(100)
        self.assertIn("ansired", rows[1][0][0])
        with patch.object(self.view, "flush") as flush:
            self.view._animate()
            flush.assert_not_called()

    def test_service_display_name_and_residual_cleanup_remain_visible(self):
        self.update({**self.job("service", name="internal-generated-name"),
                     "display_name": "项目服务", "kind": "service"},
                    {**self.job("partial", "exited", code=0), "cleanup_required": True})
        self.assertEqual(self.lines(), ["· 服务「项目服务」：运行中 · 2.0 秒",
                                       "· 后台「提取特征」：入口已退出（已清理残留进程） · 2.0 秒"])
        self.assertIn("ansired", self.view._lines(100)[1][0][0])

    def test_selection_freezes_timer_completion_and_new_job_until_resumed(self):
        self.update(self.job("one"))
        initial = self.lines()
        self.view._selecting = True
        self.now += 3
        self.update(self.job("one", "exited", elapsed=5.0, code=0),
                    self.job("two", name="新作业", elapsed=0.0))
        self.now += 2
        self.assertEqual(self.lines(), initial)
        self.view._selecting = False
        self.assertEqual(self.lines(), ["· 后台「提取特征」：完成 · 5.0 秒",
                                       "· 后台「新作业」：运行中 · 2.0 秒"])

    def test_unknown_and_stopped_states_never_look_like_success(self):
        self.update(self.job("lost", "orphaned", elapsed=8.0),
                    self.job("unknown", "unknown", elapsed=None),
                    self.job("stopped", "stopped", elapsed=3.0, code=-15),
                    self.job("failed", "failed", elapsed=0.1),
                    self.job("no-code", "exited", elapsed=2.0))
        self.assertEqual(self.lines(), ["· 后台「提取特征」：失去进程归属 · 8.0 秒",
                                       "· 后台「提取特征」：状态未知",
                                       "· 后台「提取特征」：已停止 · 3.0 秒",
                                       "· 后台「提取特征」：失败 · 0.1 秒",
                                       "· 后台「提取特征」：已退出（退出码未知） · 2.0 秒"])

    def test_replay_plain_output_and_closed_view_ignore_transient_status(self):
        ConsoleRenderer(self.view, live=False)({"type": "background_jobs", "jobs": [self.job("one")]})
        self.assertEqual(self.lines(), [""])
        output = StringIO()
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "A"})
        renderer({"type": "background_jobs", "jobs": [self.job("one")]})
        renderer({"type": "text_delta", "text": "B"})
        renderer.close()
        self.assertEqual(output.getvalue(), "assistant:\nAB\n")
        self.view.close()
        self.update(self.job("one"))
        self.assertEqual(self.lines(), [""])

    def test_runtime_completion_appears_in_chat_between_assistant_output(self):
        self.renderer({"type": "text_delta", "text": "正在"})
        self.renderer(self.completion())
        self.renderer({"type": "text_delta", "text": "处理"})
        self.assertEqual(self.lines(), ["assistant:", "正在",
                                       "· 后台任务「提取特征」：完成 · 4.2 秒；结果已加入上下文",
                                       "assistant:", "处理"])
        self.assertNotIn("user:", "\n".join(self.lines()))

    def test_completion_is_in_plain_and_replayed_chat_without_private_output(self):
        for full in (False, True):
            with self.subTest(full=full):
                output = StringIO()
                renderer = ConsoleRenderer(output, live=False, full=full)
                renderer(self.completion())
                renderer.close()
                self.assertIn("后台任务「提取特征」：完成", output.getvalue())
                self.assertIn("结果已加入上下文", output.getvalue())
                hidden = ["PRIVATE_RUNTIME_WRAPPER", "user:", "background_job_result"]
                if not full:
                    hidden.extend(("PRIVATE_BACKGROUND_STDOUT", "PRIVATE_BACKGROUND_STDERR"))
                for private in hidden:
                    self.assertNotIn(private, output.getvalue())

    def test_same_name_completion_records_remain_distinct_and_do_not_replace_status(self):
        self.update(self.job("first"), self.job("second"))
        self.renderer(self.completion("first"))
        self.renderer(self.completion("second", code=7, elapsed=3.1))
        lines = self.lines()
        completed = [line for line in lines if "结果已加入上下文" in line]
        self.assertEqual(len(completed), 2)
        self.assertIn("完成", completed[0])
        self.assertIn("失败（退出码 7）", completed[1])
        self.assertEqual(sum("运行中" in line for line in lines), 2)
        self.assertIn("ansired", self.view._lines(100)[1][0][0])

    def test_completion_obeys_selection_freeze_and_keeps_service_and_cleanup_warning(self):
        self.renderer({"type": "text_delta", "text": "等待"})
        self.view._selecting = True
        before = self.lines()
        self.renderer(self.completion(kind="service", display_name="项目服务", cleanup_required=True))
        self.assertEqual(self.lines(), before)
        self.view._selecting = False
        rendered = "\n".join(self.lines())
        self.assertIn("后台服务「项目服务」", rendered)
        self.assertIn("结果已加入上下文", rendered)
        self.assertIn("清理残留进程", rendered)
        self.assertNotIn("PRIVATE_", rendered)

    def test_job_timeout_is_a_frozen_error_state(self):
        self.update(self.job("timeout", "timed_out", elapsed=2.5, code=-9))
        expected = ["· 后台「提取特征」：已超时 · 2.5 秒"]
        self.assertEqual(self.lines(), expected)
        self.now += 20
        self.assertEqual(self.lines(), expected)
        self.assertFalse(self.view._running_jobs())
        self.assertIn("ansired", self.view._lines(100)[0][0][0])


if __name__ == "__main__":
    unittest.main()
