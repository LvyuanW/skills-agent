"""Exercise transient activity in the real CLI, using only local test fixtures."""

import json
import os
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_cli_terminal as terminal_helpers
from tests.test_cli_terminal import Terminal


COMPRESSION_BOOTSTRAP = """
from pathlib import Path
import sys
import time
from skills_agent import cli

gate = Path(sys.argv[1])
original = cli.create_agent
def create_agent(*args, **kwargs):
    agent = original(*args, **kwargs)
    for index in range(11):
        agent.session.append({'role': 'assistant', 'content': f'OLD_REPLY_{index}'})
    agent.context.sync()
    agent.context.max_tokens = 1000
    agent.model.count_tokens = lambda system, messages, tools: 900 if len(messages) > 11 else 100
    def compress(previous, records, current_user, *, output_tokens=None):
        while not gate.exists():
            time.sleep(0.02)
        if gate.read_text() == 'fail':
            raise ValueError('COMPRESSION_FAILED')
        return 'VERIFIED_HISTORY_SUMMARY'
    agent.context.auxiliary.compress = compress
    return agent
cli.create_agent = create_agent
raise SystemExit(cli.main(sys.argv[2:]))
"""


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class ActivityTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    server = terminal_helpers.TerminalTests.server
    response = terminal_helpers.TerminalTests.response
    events = terminal_helpers.TerminalTests.events
    terminal = terminal_helpers.TerminalTests.terminal

    def gate_command(self, gate):
        script = ("from pathlib import Path\nimport time\n"
                  f"gate = Path({str(gate)!r})\n"
                  "while not gate.exists():\n    time.sleep(0.02)\n"
                  "print('PRIVATE_TOOL_OUTPUT')\n")
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

    def activity_line(self, terminal, marker):
        return next((line.rstrip() for line in terminal.screen.display if marker in line), "")

    def assert_animates(self, terminal, marker):
        terminal.wait_for(lambda output: bool(self.activity_line(terminal, marker)))
        first = self.activity_line(terminal, marker)
        terminal.wait_for(lambda output: bool(self.activity_line(terminal, marker))
                          and self.activity_line(terminal, marker) != first, timeout=2)
        second = self.activity_line(terminal, marker)
        self.assertEqual(first.rstrip("."), second.rstrip("."))
        self.assertIn(first[len(first.rstrip(".")):], (".", "..", "..."))
        self.assertIn(second[len(second.rstrip(".")):], (".", "..", "..."))

    def assert_no_persisted_activity(self):
        events = self.events()
        self.assertFalse(any(event.get("type") == "activity" for event in events))
        raw = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("：执行中", raw)
        self.assertNotIn("正在压缩上下文", raw)

    def test_tool_animates_then_selection_freezes_until_return_after_completion(self):
        gate = self.workspace / "release-tool"
        command = self.gate_command(gate)
        terminal, requests = self.terminal(lambda payload, count:
            self.response(command=command, summary="等待文件") if count == 1
            else self.response("TOOL_FINISHED"))
        terminal.send("run the gated tool\n")
        marker = "· shell「等待文件」：执行中"
        self.assert_animates(terminal, marker)
        self.assertNotIn("PRIVATE_TOOL_OUTPUT", terminal.visible)
        terminal.send("\x1bOQ")  # F2 hands mouse selection back to the emulator.
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        frozen = terminal.visible
        # Wait across multiple animation periods without relying on a particular
        # frame's arrival time. Selection must suppress both timers and new data.
        for _ in range(10):
            terminal.read(0.1)
        self.assertEqual(terminal.visible, frozen)
        gate.write_text("release")
        terminal.wait_for(lambda output: any(
            event.get("type") == "message"
            and event["message"].get("role") == "assistant"
            and "TOOL_FINISHED" in json.dumps(event["message"].get("content"))
            for event in self.events()
        ))
        terminal.read(0.1)
        self.assertEqual(terminal.visible, frozen)
        terminal.send("\x1bOQ")
        terminal.prompt("TOOL_FINISHED")
        self.assertNotIn(marker, terminal.visible)
        self.assertNotIn("PRIVATE_TOOL_OUTPUT", terminal.visible)
        self.assertEqual(len(requests), 2)
        self.assert_no_persisted_activity()

    def test_interrupt_clears_tool_activity_and_next_turn_still_works(self):
        gate = self.workspace / "never-release-tool"
        command = self.gate_command(gate)
        terminal, requests = self.terminal(lambda payload, count:
            self.response(command=command, summary="等待取消") if count == 1
            else self.response("AFTER_CANCEL"))
        terminal.send("run until interrupted\n")
        marker = "· shell「等待取消」：执行中"
        self.assert_animates(terminal, marker)
        terminal.send("\x03")
        terminal.prompt("Interrupted")
        self.assertNotIn(marker, terminal.visible)
        terminal.send("continue after interruption\n")
        terminal.prompt("AFTER_CANCEL")
        self.assertEqual(len(requests), 2)
        self.assert_no_persisted_activity()

    def compression_terminal(self):
        gate = self.workspace / "release-compression"
        endpoint, requests = self.server(lambda payload, count: self.response("AFTER_COMPRESSION"))
        start_process = subprocess.Popen

        def start(command, **kwargs):
            return start_process([command[0], "-c", COMPRESSION_BOOTSTRAP, str(gate),
                                  *command[3:]], **kwargs)

        with patch("tests.test_cli_terminal.subprocess.Popen", side_effect=start):
            terminal = Terminal(self.workspace, endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt()
        return terminal, gate, requests

    def test_compression_animates_then_clears_without_persisting_status(self):
        terminal, gate, requests = self.compression_terminal()
        terminal.send("trigger compression\n")
        marker = "· 正在压缩上下文"
        self.assert_animates(terminal, marker)
        self.assertEqual(requests, [])
        gate.write_text("release")
        terminal.prompt("AFTER_COMPRESSION")
        self.assertNotIn(marker, terminal.visible)
        context = json.loads((self.workspace / ".agent/sessions/terminal/context.json").read_text())
        self.assertEqual(context["summary"], "VERIFIED_HISTORY_SUMMARY")
        self.assertEqual(len(requests), 1)
        self.assert_no_persisted_activity()

    def test_compression_failure_clears_activity_and_preserves_history(self):
        terminal, gate, requests = self.compression_terminal()
        terminal.send("trigger compression failure\n")
        marker = "· 正在压缩上下文"
        self.assert_animates(terminal, marker)
        gate.write_text("fail")
        terminal.prompt("COMPRESSION_FAILED")
        self.assertNotIn(marker, terminal.visible)
        self.assertEqual(requests, [])
        context = json.loads((self.workspace / ".agent/sessions/terminal/context.json").read_text())
        self.assertEqual(context["summary"], "")
        self.assertEqual(len(context["records"]), 12)
        self.assert_no_persisted_activity()
