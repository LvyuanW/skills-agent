"""An uncertainty observation must not retire or finish a background job."""

import io
import json
import unittest
from unittest.mock import patch

from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer, render_event
from skills_agent.terminal import TerminalView


def observation(*, completed=False):
    result = {"job_id": "worker-one", "name": "提取特征", "kind": "job",
              "state": "exited" if completed else "unconfirmed",
              "completed": completed, "exit_code": 0 if completed else None,
              "cleanup_confirmed": completed, "wait_on_exit": True}
    kind = "result" if completed else "uncertainty"
    if not completed:
        result["error"] = "Supervisor ownership could not be confirmed"
        result["process_record_path"] = "/saved/worker.json"
    return {"type": "message", "message": {
        "role": "user", "runtime_event": f"background_job_{kind}",
        f"background_{kind}_id": "same-id-for-distinct-observations",
        "background_job_id": "worker-one", "content": [
            {"type": "text", "text": f"[Runtime background job {kind} — PRIVATE_WRAPPER]"},
            {"type": "text", "text": json.dumps(result, ensure_ascii=False)},
        ],
    }}


class BackgroundUncertaintyUITests(unittest.TestCase):
    def setUp(self):
        clock = patch("skills_agent.terminal.monotonic", return_value=100.0)
        clock.start()
        self.addCleanup(clock.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())
        self.addCleanup(self.view.close)
        self.renderer = ConsoleRenderer(self.view)

    def update(self, state):
        self.renderer({"type": "background_jobs", "jobs": [{
            "id": "worker-one", "name": "提取特征", "state": state,
            "elapsed_seconds": 12.3, "returncode": None,
        }]})

    def body(self, text):
        self.renderer({"type": "text_delta", "text": text})
        self.renderer.close()

    def lines(self):
        content = self.view.control.create_content(180, 30)
        return [fragment_list_to_text(content.get_line(index)).rstrip()
                for index in range(content.line_count)]

    def test_uncertainty_preserves_live_row_and_adds_diagnostic_at_delivery_boundary(self):
        self.update("running")
        anchored = self.view._background_rows["worker-one"]
        self.body("等待期间的正文")
        self.update("unconfirmed")
        event = observation()
        self.renderer(event)
        self.renderer(event)
        self.body("继续诊断")

        lines = self.lines()
        warning = next(index for index, line in enumerate(lines) if "错误已加入上下文" in line)
        self.assertLess(lines.index("等待期间的正文"), warning)
        self.assertLess(warning, lines.index("继续诊断"))
        self.assertIs(self.view._background_rows["worker-one"], anchored)
        self.assertNotIn("worker-one", self.view._finished_background_jobs)
        self.assertEqual(sum("错误已加入上下文" in line for line in lines), 1)
        self.assertTrue(any("后台「提取特征」：监管状态未确认" in line for line in lines))
        self.assertFalse(any("结果已加入上下文" in line for line in lines))
        self.assertFalse(any("PRIVATE_WRAPPER" in line for line in lines))

    def test_recovered_job_can_update_then_deliver_actual_completion(self):
        self.update("unconfirmed")
        self.renderer(observation())
        self.body("恢复归属后")
        self.update("running")
        self.assertTrue(any("后台「提取特征」：运行中" in line for line in self.lines()))
        self.renderer(observation(completed=True))
        self.body("检查最终产物")
        lines = self.lines()
        completion = next(index for index, line in enumerate(lines) if "结果已加入上下文" in line)
        self.assertLess(lines.index("恢复归属后"), completion)
        self.assertLess(completion, lines.index("检查最终产物"))
        self.assertIn("worker-one", self.view._finished_background_jobs)
        self.assertNotIn("worker-one", self.view._background_rows)
        self.assertFalse(any("· 后台「提取特征」" in line for line in lines))
        self.assertEqual(sum("错误已加入上下文" in line for line in lines), 1)
        self.assertEqual(sum("结果已加入上下文" in line for line in lines), 1)

    def test_missing_snapshot_keeps_unconfirmed_row_without_claiming_completion(self):
        self.update("unconfirmed")
        self.renderer(observation())
        self.renderer({"type": "background_jobs", "jobs": []})
        self.assertTrue(any("后台「提取特征」：监管状态未确认" in line for line in self.lines()))
        self.assertFalse(any("结果已加入上下文" in line for line in self.lines()))

    def test_confirmed_result_retires_resolved_launch_identity(self):
        self.renderer({"type": "background_jobs", "jobs": [{
            "id": "launch-unknown", "name": "启动待确认", "state": "unconfirmed",
            "elapsed_seconds": None, "returncode": None,
        }]})
        self.assertIn("launch-unknown", self.view._background_rows)
        event = observation(completed=True)
        event["message"]["resolved_uncertainty_ids"] = ["launch-unknown"]
        self.renderer(event)
        self.assertNotIn("launch-unknown", self.view._background_rows)
        self.assertIn("launch-unknown", self.view._finished_background_jobs)
        self.assertFalse(any("启动待确认" in line for line in self.lines()))

    def test_compact_and_full_history_label_uncertainty_as_diagnostic(self):
        for full in (False, True):
            with self.subTest(full=full):
                output = io.StringIO()
                render_event(observation(), output, full=full)
                text = output.getvalue()
                self.assertIn("监管状态未确认", text)
                self.assertIn("仍需诊断和恢复", text)
                self.assertNotIn("结果已加入上下文", text)
                self.assertNotIn("PRIVATE_WRAPPER", text)
                self.assertEqual("/saved/worker.json" in text, full)
                self.assertEqual("Supervisor ownership could not be confirmed" in text, full)


if __name__ == "__main__":
    unittest.main()
