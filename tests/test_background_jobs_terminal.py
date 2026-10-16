"""Observe managed background jobs while the real CLI receives a local SSE stream."""

import json
import os
import re
import unittest

from tests import test_cli_terminal as terminal_helpers
from tests.background_jobs_fixture import (BackgroundJobsFixture, FINISHED, PRIVATE_ERROR,
                                           PRIVATE_OUTPUT, WAITING, response)
from tests.test_cli_terminal import Terminal


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "PTY and managed POSIX jobs")
class BackgroundJobsTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    events = terminal_helpers.TerminalTests.events

    def start(self, jobs, *, services=()):
        fixture = BackgroundJobsFixture(self.workspace, jobs, services=services)
        self.addCleanup(fixture.close)
        terminal = Terminal(self.workspace, fixture.endpoint)
        self.addCleanup(terminal.close)
        self.addCleanup(fixture.release_model.set)
        terminal.prompt()
        terminal.send("run background jobs\n")
        terminal.wait_for(lambda output: fixture.streaming.is_set() and WAITING in terminal.visible)
        self.assertIn("shell「启动后台作业」：完成", terminal.visible)
        self.assertEqual(len(fixture.requests), 2)
        return terminal, fixture

    def line(self, terminal, name):
        return next((line.rstrip() for line in terminal.screen.display
                     if any(f"{label}「{name}」" in line for label in ("后台", "服务"))), "")

    def elapsed(self, terminal, name):
        match = re.search(r"(\d+\.\d) 秒", self.line(terminal, name))
        return float(match[1]) if match else -1

    def assert_running_timer(self, terminal, name):
        terminal.wait_for(lambda output: "运行中" in self.line(terminal, name)
                          and self.elapsed(terminal, name) >= 0)
        before = self.elapsed(terminal, name)
        terminal.wait_for(lambda output: self.elapsed(terminal, name) >= before + .2, timeout=3)

    def assert_clean(self, terminal):
        self.assertNotIn(PRIVATE_OUTPUT, terminal.text)
        self.assertNotIn(PRIVATE_ERROR, terminal.text)
        events = self.events()
        self.assertFalse(any(event.get("type") in {"background_jobs", "jobs"} for event in events))
        self.assertNotIn("后台「", json.dumps(events, ensure_ascii=False))

    def test_concurrent_jobs_update_while_streaming_and_keep_terminal_results(self):
        terminal, fixture = self.start([("extract-ok", 0), ("fit-failed", 7)])
        self.assert_running_timer(terminal, "extract-ok")
        self.assert_running_timer(terminal, "fit-failed")
        fixture.gate("extract-ok").touch()
        terminal.wait_for(lambda output: "完成" in self.line(terminal, "extract-ok"))
        completed = self.line(terminal, "extract-ok")
        self.assert_running_timer(terminal, "fit-failed")
        self.assertEqual(self.line(terminal, "extract-ok"), completed)
        fixture.gate("fit-failed").touch()
        terminal.wait_for(lambda output: "失败" in self.line(terminal, "fit-failed")
                          and "7" in self.line(terminal, "fit-failed"))
        failed = self.line(terminal, "fit-failed")
        self.assertEqual(fixture.record("extract-ok")["returncode"], 0)
        self.assertEqual(fixture.record("fit-failed")["returncode"], 7)
        fixture.release_model.set()
        terminal.prompt(FINISHED)
        terminal.read(.1)
        self.assertEqual(self.line(terminal, "extract-ok"), completed)
        self.assertEqual(self.line(terminal, "fit-failed"), failed)
        self.assertEqual(terminal.visible.count("后台「extract-ok」"), 1)
        self.assertEqual(terminal.visible.count("后台「fit-failed」"), 1)
        self.assert_clean(terminal)

    def test_selection_freezes_job_timer_and_completion_until_return(self):
        terminal, fixture = self.start([("frozen-extract", 0)])
        self.assert_running_timer(terminal, "frozen-extract")
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        frozen = terminal.visible
        for _ in range(5):
            terminal.read(.1)
        self.assertEqual(terminal.visible, frozen)
        fixture.gate("frozen-extract").touch()
        terminal.wait_for(lambda output: fixture.record("frozen-extract").get("state") == "exited")
        fixture.release_model.set()
        terminal.wait_for(lambda output: any(
            event.get("type") == "message" and event["message"].get("role") == "assistant"
            and FINISHED in json.dumps(event["message"].get("content")) for event in self.events()))
        terminal.read(.2)
        self.assertEqual(terminal.visible, frozen)
        terminal.send("\x1bOQ")
        terminal.prompt(FINISHED)
        terminal.wait_for(lambda output: "完成" in self.line(terminal, "frozen-extract"))
        self.assertNotIn("运行中", self.line(terminal, "frozen-extract"))
        self.assert_clean(terminal)

    def test_timer_continues_at_idle_prompt_and_explicit_stop_keeps_final_state(self):
        terminal, fixture = self.start([("idle-worker", 0)], services={"idle-worker"})
        self.assert_running_timer(terminal, "idle-worker")
        fixture.release_model.set()
        terminal.prompt(FINISHED)
        self.assert_running_timer(terminal, "idle-worker")
        fixture.after_stream = [response(command=fixture.stop_command("idle-worker"), name="停止后台作业"),
                                response("JOB_STOPPED")]
        terminal.send("stop this background job\n")
        terminal.prompt("JOB_STOPPED")
        terminal.wait_for(lambda output: "已停止" in self.line(terminal, "idle-worker"))
        final = self.line(terminal, "idle-worker")
        for _ in range(3):
            terminal.read(.1)
        self.assertEqual(self.line(terminal, "idle-worker"), final)
        self.assertEqual(fixture.record("idle-worker")["state"], "stopped")
        self.assertEqual(fixture.record("idle-worker")["returncode"], -15)
        self.assert_clean(terminal)

    def test_interruption_retains_stopped_job_in_final_display(self):
        terminal, fixture = self.start([("cancel-worker", 0)])
        self.assert_running_timer(terminal, "cancel-worker")
        terminal.send("\x03")
        terminal.prompt("Interrupted")
        terminal.wait_for(lambda output: "已停止" in self.line(terminal, "cancel-worker"))
        self.assertEqual(fixture.record("cancel-worker")["state"], "stopped")
        self.assertNotIn("运行中", self.line(terminal, "cancel-worker"))
        self.assert_clean(terminal)


if __name__ == "__main__":
    unittest.main()
