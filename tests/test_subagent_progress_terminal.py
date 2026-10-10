"""Check private child progress in a real terminal with local model fixtures."""

import json
import os
import re
import shlex
import sys
import threading
import unittest

from tests import test_cli_terminal as terminal_helpers


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class SubagentProgressTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    server = terminal_helpers.TerminalTests.server
    response = terminal_helpers.TerminalTests.response
    events = terminal_helpers.TerminalTests.events
    terminal = terminal_helpers.TerminalTests.terminal

    task_name = "独立复核"
    private_markers = ("PRIVATE_CHILD_TEXT", "PRIVATE_CHILD_THINKING",
                       "PRIVATE_CHILD_TOOL", "PRIVATE_CHILD_OUTPUT",
                       "PRIVATE_CHILD_REPORT", "PRIVATE_CHILD_INSTRUCTIONS")

    def delegation(self):
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None,
            "tool_calls": [{"id": "child-1", "type": "function", "function": {
                "name": "subagent", "arguments": json.dumps({
                    "name": self.task_name, "task": "PRIVATE_CHILD_INSTRUCTIONS",
                }),
            }}],
        }}]}

    def child_tool(self, gate):
        script = ("from pathlib import Path\nimport time\n"
                  f"gate = Path({str(gate)!r})\n"
                  "while not gate.exists():\n    time.sleep(0.02)\n"
                  "print('PRIVATE_CHILD_OUTPUT')\n")
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        response = self.response("PRIVATE_CHILD_TEXT", command=command,
                                 summary="PRIVATE_CHILD_TOOL")
        response["choices"][0]["message"]["reasoning_content"] = "PRIVATE_CHILD_THINKING"
        return response

    def activity_line(self, terminal, stage):
        marker = f"· sub-agent「{self.task_name}」：{stage}"
        return next((line.rstrip() for line in terminal.screen.display
                     if marker in line), "")

    def elapsed(self, terminal, stage):
        match = re.search(r"\.{1,3} · (\d+\.\d) 秒$", self.activity_line(terminal, stage))
        return float(match[1]) if match else -1

    def assert_private(self, terminal):
        for marker in self.private_markers:
            self.assertNotIn(marker, terminal.text)

    def assert_not_persisted(self):
        events = self.events()
        self.assertFalse(any(event.get("type") == "activity" for event in events))
        raw = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("正在执行工具", raw)
        self.assertNotIn("正在压缩上下文", raw)

    def test_child_stages_share_elapsed_time_and_selection_freezes_only_display(self):
        gate = self.workspace / "release-child-tool"
        release_report = threading.Event()
        child_waiting = threading.Event()

        def respond(payload, count):
            if count == 1:
                return self.delegation()
            if count == 2:
                return self.child_tool(gate)
            if count == 3:
                child_waiting.set()
                release_report.wait(timeout=10)
                return self.response("PRIVATE_CHILD_REPORT")
            return self.response("PARENT_FINISHED")

        terminal, requests = self.terminal(respond)
        self.addCleanup(release_report.set)
        terminal.send("review this task\n")
        terminal.wait_for(lambda output: self.elapsed(terminal, "正在执行工具") >= 0.3)
        tool_elapsed = self.elapsed(terminal, "正在执行工具")
        self.assert_private(terminal)
        gate.write_text("release")
        terminal.wait_for(lambda output: child_waiting.is_set()
                          and self.elapsed(terminal, "执行中") >= tool_elapsed)
        # Returning from a child tool must not reset the subtask timer.
        terminal.wait_for(lambda output: self.elapsed(terminal, "执行中") >= tool_elapsed + 0.2)
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        frozen = terminal.visible
        for _ in range(5):
            terminal.read(0.1)
        self.assertEqual(terminal.visible, frozen)
        release_report.set()
        terminal.wait_for(lambda output: any(
            event.get("type") == "message"
            and event["message"].get("role") == "assistant"
            and "PARENT_FINISHED" in json.dumps(event["message"].get("content"))
            for event in self.events()
        ))
        terminal.read(0.1)
        self.assertEqual(terminal.visible, frozen)
        terminal.send("\x1bOQ")
        terminal.prompt("PARENT_FINISHED")
        self.assertIn(f"sub-agent「{self.task_name}」：已返回报告", terminal.visible)
        self.assertFalse(self.activity_line(terminal, "执行中"))
        self.assertFalse(self.activity_line(terminal, "正在执行工具"))
        self.assertEqual(len(requests), 4)
        self.assert_private(terminal)
        self.assert_not_persisted()

    def test_interrupt_clears_child_progress_and_next_turn_still_works(self):
        gate = self.workspace / "never-release-child-tool"

        def respond(payload, count):
            if count == 1:
                return self.delegation()
            if count == 2:
                return self.child_tool(gate)
            return self.response("AFTER_CHILD_CANCEL")

        terminal, requests = self.terminal(respond)
        terminal.send("review until interrupted\n")
        terminal.wait_for(lambda output: self.elapsed(terminal, "正在执行工具") >= 0.1)
        terminal.send("\x03")
        terminal.prompt("Interrupted")
        self.assertIn(f"sub-agent「{self.task_name}」：已中断", terminal.visible)
        self.assertFalse(self.activity_line(terminal, "正在执行工具"))
        terminal.send("continue after child interruption\n")
        terminal.prompt("AFTER_CHILD_CANCEL")
        self.assertEqual(len(requests), 3)
        self.assert_private(terminal)
        self.assert_not_persisted()
