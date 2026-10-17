"""Background progress belongs to its first transcript position, not a footer."""

import json
import unittest
from unittest.mock import patch

from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer
from skills_agent.terminal import TerminalView


class BackgroundJobPositionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        clock = patch("skills_agent.terminal.monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())
        self.addCleanup(self.view.close)
        self.renderer = ConsoleRenderer(self.view)

    def job(self, identifier="one", *, state="running", elapsed=2.0,
            code=None, name="提取特征"):
        return {"id": identifier, "name": name, "state": state,
                "elapsed_seconds": elapsed, "returncode": code}

    def update(self, *jobs):
        self.renderer({"type": "background_jobs", "jobs": list(jobs)})

    def body(self, text):
        self.renderer({"type": "text_delta", "text": text})
        self.renderer.close()

    def completion(self, identifier="one", *, code=0, elapsed=4.2, renderer=None):
        result = {"job_id": identifier, "name": "提取特征", "state": "exited",
                  "kind": "job", "exit_code": code, "duration_seconds": elapsed,
                  "stdout": "PRIVATE_BACKGROUND_STDOUT", "stderr": ""}
        event = {"type": "message", "message": {
            "role": "user", "runtime_event": "background_job_result",
            "background_result_id": f"{identifier}-terminal",
            "background_job_id": identifier,
            "content": [
                {"type": "text", "text": "[Runtime background job result — PRIVATE_WRAPPER]"},
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)},
            ],
        }}
        (renderer or self.renderer)(event)
        return event

    def lines(self):
        content = self.view.control.create_content(140, 30)
        return [fragment_list_to_text(content.get_line(index)).rstrip()
                for index in range(content.line_count)]

    def test_running_status_is_anchored_before_later_assistant_and_tool_output(self):
        self.body("启动前正文")
        before = len(self.view.blocks)
        self.update(self.job())
        self.assertEqual(len(self.view.blocks), before + 1)
        status_block = self.view.blocks[before]

        self.body("启动后正文")
        self.renderer({"type": "message", "message": {
            "role": "tool", "name": "shell", "tool_call_id": "foreground",
            "task_name": "检查前台", "content": json.dumps({
                "exit_code": 0, "stdout": "ok", "stderr": "",
            }),
        }})
        self.now += 3
        self.update(self.job(elapsed=5.0))
        self.assertIs(self.view.blocks[before], status_block)
        self.assertEqual(sum(block is status_block for block in self.view.blocks), 1)

        lines = self.lines()
        status = next(index for index, line in enumerate(lines) if "后台「提取特征」" in line)
        foreground = next(index for index, line in enumerate(lines) if "shell「检查前台」" in line)
        self.assertLess(lines.index("启动前正文"), status)
        self.assertLess(status, lines.index("启动后正文"))
        self.assertLess(lines.index("启动后正文"), foreground)
        self.assertIn("5.0 秒", lines[status])
        self.assertEqual(sum("后台「提取特征」" in line for line in lines), 1)

    def test_timer_refresh_updates_the_same_historical_block(self):
        self.update(self.job())
        self.assertEqual(len(self.view.blocks), 1)
        status_block = self.view.blocks[0]
        self.body("后台运行期间的新消息")
        self.now += 4.5
        self.assertIn("· 后台「提取特征」：运行中 · 6.5 秒", self.lines())
        self.assertIs(self.view.blocks[0], status_block)
        self.assertEqual(self.lines()[-1], "后台运行期间的新消息")

        self.update(self.job(elapsed=6.5))
        self.now += 1
        self.assertIs(self.view.blocks[0], status_block)
        self.assertIn("· 后台「提取特征」：运行中 · 7.5 秒", self.lines())
        self.assertEqual(self.lines()[-1], "后台运行期间的新消息")

    def test_completion_stays_at_delivery_boundary_and_later_turn_follows_it(self):
        self.body("先启动任务")
        self.update(self.job())
        self.body("结果投递前仍在工作")
        self.update(self.job(state="exited", elapsed=4.2, code=0))
        self.completion()
        self.body("下一轮正文")
        self.view.finish_turn()

        lines = self.lines()
        completion = next(index for index, line in enumerate(lines) if "结果已加入上下文" in line)
        self.assertLess(lines.index("结果投递前仍在工作"), completion)
        self.assertLess(completion, lines.index("下一轮正文"))
        self.assertEqual(lines[-1], "下一轮正文")
        self.assertEqual(sum("结果已加入上下文" in line for line in lines), 1)
        self.assertFalse(any("· 后台「提取特征」" in line for line in lines))
        self.assertFalse(any("运行中" in line for line in lines))
        self.assertFalse(any("PRIVATE_" in line for line in lines))

        for turn in range(3):
            self.update(self.job(state="exited", elapsed=4.2, code=0))
            self.body(f"后续第 {turn + 1} 轮正文")
            self.view.finish_turn()
        later = self.lines()
        self.assertEqual(later[-1], "后续第 3 轮正文")
        self.assertEqual(sum("结果已加入上下文" in line for line in later), 1)
        self.assertFalse(any("· 后台「提取特征」" in line for line in later))

    def test_terminal_status_remains_visible_until_its_result_is_delivered(self):
        self.body("开始")
        self.update(self.job())
        self.body("模型仍在输出")
        self.update(self.job(state="exited", elapsed=4.2, code=0))
        self.now += 500
        lines = self.lines()
        status = lines.index("· 后台「提取特征」：完成 · 4.2 秒")
        self.assertLess(status, lines.index("模型仍在输出"))
        self.assertFalse(any("结果已加入上下文" in line for line in lines))

        self.update()
        self.assertIn("· 后台「提取特征」：完成 · 4.2 秒", self.lines())
        self.completion()
        self.assertEqual(sum("结果已加入上下文" in line for line in self.lines()), 1)

    def test_missing_snapshot_does_not_erase_an_unreported_lost_job(self):
        self.update(self.job())
        self.body("仍需处理异常")
        self.update(self.job(state="orphaned", elapsed=8.0))
        self.update()
        lines = self.lines()
        warning = next(index for index, line in enumerate(lines) if "失去进程归属" in line)
        self.assertLess(warning, lines.index("仍需处理异常"))
        self.assertIn("8.0 秒", lines[warning])
        self.assertFalse(any("结果已加入上下文" in line for line in lines))

    def test_disappeared_running_job_keeps_an_unknown_status_in_its_original_place(self):
        self.update(self.job())
        self.body("记录暂不可用之后的正文")
        self.update()
        lines = self.lines()
        status = next(index for index, line in enumerate(lines) if "后台「提取特征」" in line)
        self.assertLess(status, lines.index("记录暂不可用之后的正文"))
        self.assertIn("未知", lines[status])
        self.assertNotIn("完成", lines[status])
        self.assertFalse(any("结果已加入上下文" in line for line in lines))

    def test_same_name_jobs_and_late_snapshots_do_not_resurrect_completed_job(self):
        self.update(self.job("first"))
        self.body("两次启动之间")
        self.update(self.job("first"), self.job("second", elapsed=1.0))
        self.body("两项均已启动")
        self.completion("first")
        self.update(self.job("first"), self.job("second", elapsed=2.0))
        self.assertEqual(sum("运行中" in line for line in self.lines()), 1)

        event = self.completion("second", code=7, elapsed=3.1)
        self.renderer(event)
        self.body("两个任务完成后的新消息")
        self.update(self.job("first"), self.job("second", state="exited", elapsed=3.1, code=7))
        self.update()
        lines = self.lines()
        completed = [line for line in lines if "结果已加入上下文" in line]
        self.assertEqual(len(completed), 2)
        self.assertIn("完成", completed[0])
        self.assertIn("失败（退出码 7）", completed[1])
        self.assertFalse(any("运行中" in line for line in lines))
        self.assertEqual(lines[-1], "两个任务完成后的新消息")

    def test_replayed_completion_prevents_first_live_snapshot_from_resurrecting_job(self):
        replay = ConsoleRenderer(self.view, live=False)
        self.completion(renderer=replay)
        replay.close()
        self.renderer({"type": "message", "message": {
            "role": "tool", "name": "shell", "tool_call_id": "old-start",
            "content": json.dumps({"job_id": "one", "completed": False,
                                   "name": "提取特征", "state": "running", "kind": "job"}),
        }})
        self.update(self.job())
        self.body("恢复会话后的正文")
        lines = self.lines()
        self.assertEqual(sum("结果已加入上下文" in line for line in lines), 1)
        self.assertFalse(any("· 后台「提取特征」" in line for line in lines))
        self.assertEqual(lines[-1], "恢复会话后的正文")

    def test_selection_freezes_job_updates_and_completion_until_resumed(self):
        self.body("之前的正文")
        self.update(self.job())
        before = self.lines()
        self.view._selecting = True
        self.now += 3
        self.update(self.job(state="exited", elapsed=5.0, code=0),
                    self.job("two", name="新作业", elapsed=0.0))
        self.completion(elapsed=5.0)
        self.body("恢复后看到的新正文")
        self.now += 2
        self.assertEqual(self.lines(), before)

        self.view._selecting = False
        lines = self.lines()
        completion = next(index for index, line in enumerate(lines) if "结果已加入上下文" in line)
        self.assertLess(completion, lines.index("恢复后看到的新正文"))
        self.assertIn("· 后台「新作业」：运行中 · 2.0 秒", lines)
        self.assertFalse(any("后台「提取特征」：运行中" in line for line in lines))
        self.assertEqual(lines[-1], "恢复后看到的新正文")


if __name__ == "__main__":
    unittest.main()
