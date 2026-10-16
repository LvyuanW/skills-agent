"""Real CLI/PTYS must await managed work and feed its result back to the model."""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import unittest

from skills_agent.processes import HELPER
from tests import test_cli_terminal as terminal_helpers
from tests.background_jobs_fixture import BackgroundJobsFixture, FINISHED, WAITING, response
from tests.test_cli_terminal import Terminal


TASK = "Finish the extraction and save its observed result."
PROGRESS = "Extraction is running. Let me monitor briefly."
DONE = "DELIVERABLE_WRITTEN"
PRIVATE_STDOUT = "PRIVATE_COMPLETION_STDOUT"
PRIVATE_STDERR = "PRIVATE_COMPLETION_STDERR"


class ResultJobFixture:
    """The local model writes a deliverable only from a received job notification."""

    def __init__(self, workspace, *, exit_code=0):
        self.workspace = workspace
        self.state = workspace / "job-state"
        self.gate = workspace / "release-extraction"
        self.output = workspace / "observed-result.json"
        self.exit_code = exit_code
        self.received = []

    def helper(self, operation, *arguments):
        return [sys.executable, str(HELPER), operation, "--state-dir", str(self.state),
                "--name", "extract", *arguments]

    def start_command(self):
        program = ("from pathlib import Path\nimport sys,time\n"
                   f"gate=Path({str(self.gate)!r})\n"
                   "while not gate.exists():\n    time.sleep(.02)\n"
                   f"print({PRIVATE_STDOUT!r}, flush=True)\n"
                   f"print({PRIVATE_STDERR!r}, file=sys.stderr, flush=True)\n"
                   f"raise SystemExit({self.exit_code})\n")
        return shlex.join(self.helper("start", "--job-timeout", "30", "--", sys.executable, "-c", program))

    def record(self):
        try:
            return json.loads((self.state / "extract.json").read_text())
        except FileNotFoundError:
            return {}

    def respond(self, payload, count):
        if count == 1:
            return response(command=self.start_command())
        if count == 2:
            # A genuine final protocol response, with no pending tool calls.
            return response(PROGRESS)
        if count == 3:
            # Search only user-role context, never the old assistant command
            # that happens to contain the same stdout literals in its script.
            texts = [block.get("text", "") for item in payload["input"]
                     if item.get("role") == "user"
                     for block in item.get("content", []) if block.get("type") == "input_text"]
            notice = next((text for text in texts if PRIVATE_STDOUT in text), "")
            code = re.search(r'"exit_code"\s*:\s*(-?\d+)', notice)
            observed = {"stdout_received": PRIVATE_STDOUT in notice,
                        "stderr_received": PRIVATE_STDERR in notice,
                        "exit_code": int(code[1]) if code else None}
            self.received.append(observed)
            if not all((observed["stdout_received"], observed["stderr_received"],
                        observed["exit_code"] == self.exit_code)):
                return response("MISSING_BACKGROUND_RESULT")
            program = ("from pathlib import Path\n"
                       f"Path({str(self.output)!r}).write_text({json.dumps(observed)!r})\n")
            return response(command=shlex.join([sys.executable, "-c", program]), name="保存真实后台结果")
        return response(DONE)

    def close(self):
        if self.record().get("state") in {"starting", "running", "stopping"}:
            subprocess.run(self.helper("stop", "--grace", "0.1", "--timeout", "3"),
                           capture_output=True, timeout=5, check=False)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "PTY and managed POSIX jobs")
class BackgroundResultsTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    server = terminal_helpers.TerminalTests.server
    events = terminal_helpers.TerminalTests.events

    @staticmethod
    def job_line(terminal):
        return next((line.rstrip() for line in terminal.screen.display
                     if "后台「extract」" in line and "结果已加入上下文" not in line), "")

    @staticmethod
    def turn_line(terminal):
        return next((line.strip() for line in terminal.screen.display if line.strip().startswith("耗时 ")), "")

    def begin(self, *, exit_code=0):
        fixture = ResultJobFixture(self.workspace, exit_code=exit_code)
        self.addCleanup(fixture.close)
        endpoint, requests = self.server(fixture.respond)
        terminal = Terminal(self.workspace, endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt()
        terminal.send(TASK + "\n")
        terminal.wait_for(lambda output: PROGRESS in terminal.visible and len(requests) >= 2)
        terminal.wait_for(lambda output: "运行中" in self.job_line(terminal))
        self.assertIn("shell「启动后台作业」：完成", terminal.visible)
        return terminal, fixture, requests

    def assert_waiting(self, terminal, fixture, requests):
        before = self.turn_line(terminal)
        self.assertRegex(before, r"^耗时 \d+\.\d 秒  停止$")
        terminal.wait_for(lambda output: self.turn_line(terminal) != before, timeout=2)
        self.assertRegex(self.turn_line(terminal), r"^耗时 \d+\.\d 秒  停止$")
        deadline = time.monotonic() + .4
        while time.monotonic() < deadline:
            terminal.read(.1)
        self.assertEqual(len(requests), 2, "Unchanged running state must not spin the model loop")
        self.assertEqual(fixture.record().get("state"), "running")
        self.assertFalse(fixture.output.exists(), "Progress-only final must not count as a delivered task")
        self.assertNotIn("结果已加入上下文", terminal.visible)
        os.kill(fixture.record()["child_pid"], 0)

    def assert_delivered(self, terminal, fixture, requests):
        terminal.wait_for(lambda output: DONE in terminal.visible and "停止" not in self.turn_line(terminal))
        self.assertEqual(len(requests), 4)
        self.assertEqual(json.loads(fixture.output.read_text()), {
            "stdout_received": True, "stderr_received": True, "exit_code": fixture.exit_code,
        })
        notices = [event["message"] for event in self.events()
                   if event.get("type") == "message"
                   and event["message"].get("runtime_event") == "background_job_result"]
        self.assertEqual(len(notices), 1, "Each terminal run must enter the durable context exactly once")
        self.assertEqual(notices[0]["role"], "user")
        summary = next((line for line in terminal.screen.display if "结果已加入上下文" in line), "")
        self.assertIn("后台任务「extract」", summary)
        self.assertIn("完成" if fixture.exit_code == 0 else f"失败（退出码 {fixture.exit_code}）", summary)
        self.assertEqual(terminal.visible.count("结果已加入上下文"), 1)
        self.assertLess(terminal.visible.index("结果已加入上下文"), terminal.visible.index(DONE))
        original_users = [event for event in self.events() if event.get("type") == "message"
                          and event["message"].get("role") == "user"
                          and not event["message"].get("runtime_event")]
        self.assertEqual(len(original_users), 1)
        self.assertNotIn(PRIVATE_STDOUT, terminal.text)
        self.assertNotIn(PRIVATE_STDERR, terminal.text)
        self.assertNotIn("background_job_result", terminal.text)
        # The notification's text must not become a black user-message block.
        first_line = notices[0]["content"][0]["text"].splitlines()[0]
        self.assertTrue(first_line)
        self.assertNotIn(first_line, terminal.text)

    def test_progress_only_final_waits_for_real_result_then_writes_deliverable(self):
        terminal, fixture, requests = self.begin()
        self.assert_waiting(terminal, fixture, requests)
        fixture.gate.touch()
        self.assert_delivered(terminal, fixture, requests)
        self.assertIn("完成", self.job_line(terminal))
        self.assertEqual(fixture.record()["returncode"], 0)

    def test_failed_job_notifies_actual_exit_code_and_model_can_continue(self):
        terminal, fixture, requests = self.begin(exit_code=7)
        self.assert_waiting(terminal, fixture, requests)
        fixture.gate.touch()
        self.assert_delivered(terminal, fixture, requests)
        self.assertIn("失败（退出码 7）", self.job_line(terminal))
        self.assertEqual(fixture.record()["returncode"], 7)

    def test_selection_freezes_waiting_view_while_completion_resumes_the_model(self):
        terminal, fixture, requests = self.begin()
        self.assert_waiting(terminal, fixture, requests)
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        frozen = terminal.visible
        fixture.gate.touch()
        terminal.wait_for(lambda output: len(requests) == 4 and fixture.output.exists())
        for _ in range(3):
            terminal.read(.1)
        self.assertEqual(terminal.visible, frozen)
        terminal.send("\x1bOQ")
        self.assert_delivered(terminal, fixture, requests)
        self.assertIn("完成", self.job_line(terminal))

    def test_completion_during_stream_is_displayed_only_after_response_and_before_next_response(self):
        fixture = BackgroundJobsFixture(self.workspace, [("streaming-extract", 0)])
        self.addCleanup(fixture.close)
        self.addCleanup(fixture.release_model.set)
        terminal = Terminal(self.workspace, fixture.endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt()
        terminal.send(TASK + "\n")
        terminal.wait_for(lambda output: fixture.streaming.is_set() and WAITING in terminal.visible)
        fixture.gate("streaming-extract").touch()
        terminal.wait_for(lambda output: fixture.record("streaming-extract").get("state") == "exited")
        terminal.wait_for(lambda output: any("后台「streaming-extract」：完成" in line
                                           for line in terminal.screen.display))
        self.assertEqual(len(fixture.requests), 2)
        self.assertNotIn("结果已加入上下文", terminal.visible)
        self.assertFalse(any(event.get("type") == "message"
                             and event["message"].get("runtime_event") == "background_job_result"
                             for event in self.events()))
        fixture.release_model.set()
        terminal.prompt("NEXT_TURN_DONE")
        self.assertEqual(terminal.visible.count("结果已加入上下文"), 1)
        self.assertLess(terminal.visible.index(FINISHED), terminal.visible.index("结果已加入上下文"))
        self.assertLess(terminal.visible.index("结果已加入上下文"), terminal.visible.index("NEXT_TURN_DONE"))

    def test_interrupt_while_waiting_stops_job_without_another_model_call(self):
        terminal, fixture, requests = self.begin()
        self.assert_waiting(terminal, fixture, requests)
        terminal.send("\x03")
        terminal.wait_for(lambda output: "Interrupted" in terminal.visible
                          and "停止" not in self.turn_line(terminal))
        terminal.wait_for(lambda output: "已停止" in self.job_line(terminal))
        for _ in range(3):
            terminal.read(.1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(fixture.record()["state"], "stopped")
        self.assertEqual(fixture.record()["returncode"], -15)
        self.assertFalse(fixture.output.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(fixture.record()["child_pid"], 0)
        self.assertNotIn(PRIVATE_STDOUT, terminal.text)
        self.assertNotIn(PRIVATE_STDERR, terminal.text)


if __name__ == "__main__":
    unittest.main()
