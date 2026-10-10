"""Render real streamed compression phases in the CLI without remote model calls."""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from tests import test_cli_terminal as terminal_helpers
from tests.test_cli_terminal import Terminal


BOOTSTRAP = r"""
import json
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
    agent.model.count_tokens = lambda system, messages, tools, **options: (
        900 if len(messages) > 11 and '<historical_summary>' not in system else 100)
    complete = agent.model.complete
    attempts = 0
    def wait(name):
        while not (gate / name).exists():
            time.sleep(0.02)
    def streaming(system, messages, tools, *, on_event=None, max_output_tokens=None, disable_thinking=False):
        nonlocal attempts
        content = messages[0]['content']
        if system.startswith('Summarize only'):
            assert disable_thinking
            assert max_output_tokens <= 8192
            attempts += 1
            payload = json.loads(content[0]['text'] if isinstance(content, list) else content)
            reference = payload['records'][0]['event_id']
            if attempts == 1:
                on_event({'type':'thinking_delta', 'text':'较早思考 ' * 100 + 'THOUGHT_TAIL'})
                wait('summary')
                on_event({'type':'text_delta', 'text':'ABANDONED_PREVIEW_SUMMARY [' + reference + ']'})
                (gate / 'summary-emitted').write_text('yes')
                wait('finish')
            return {'role':'assistant', 'content':[{'type':'text', 'text':'SAVED_SUMMARY [' + reference + ']'}]}
        if system.startswith('Independently verify'):
            payload = json.loads(content[0]['text'] if isinstance(content, list) else content)
            on_event({'type':'thinking_delta', 'text':'PRIVATE_REVIEW_THOUGHT'})
            text = json.dumps({'passed':True, 'issues':[], 'checked_event_ids':[
                record['event_id'] for record in payload['records']], 'checked_previous_summary':True})
            return {'role':'assistant', 'content':[{'type':'text','text':text}]}
        return complete(system, messages, tools, on_event=on_event, max_output_tokens=max_output_tokens)
    agent.model.complete = streaming
    return agent
cli.create_agent = create_agent
raise SystemExit(cli.main(sys.argv[2:]))
"""


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class CompressionProgressTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    server = terminal_helpers.TerminalTests.server
    response = terminal_helpers.TerminalTests.response
    events = terminal_helpers.TerminalTests.events

    def terminal(self):
        gate = self.workspace / "compression-gates"
        gate.mkdir()
        endpoint, requests = self.server(lambda payload, count: self.response("AFTER_COMPRESSION"))
        start_process = subprocess.Popen

        def start(command, **kwargs):
            return start_process([command[0], "-c", BOOTSTRAP, str(gate), *command[3:]], **kwargs)

        with patch("tests.test_cli_terminal.subprocess.Popen", side_effect=start):
            terminal = Terminal(self.workspace, endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt()
        return terminal, gate, requests

    def activity_lines(self, terminal):
        return [line.rstrip() for line in terminal.screen.display if "正在压缩上下文" in line]

    def cell(self, terminal, marker):
        for y in range(terminal.screen.lines):
            row = terminal.screen.buffer[y]
            for x in range(terminal.screen.columns):
                if "".join(row[c].data for c in range(x, terminal.screen.columns)).startswith(marker):
                    return x, y, row[x]
        self.fail(f"Marker {marker!r} is not visible:\n{terminal.visible}")

    def assert_private(self):
        main = json.dumps(self.events(), ensure_ascii=False)
        self.assertNotIn("THOUGHT_TAIL", main)
        self.assertNotIn("ABANDONED_PREVIEW_SUMMARY", main)
        self.assertNotIn("PRIVATE_REVIEW_THOUGHT", main)
        self.assertFalse(any(event.get("type") == "activity" for event in self.events()))
        private = "\n".join(path.read_text() for path in
                            (self.workspace / ".agent/sessions/terminal/subagents").glob("*/events.jsonl"))
        self.assertIn("THOUGHT_TAIL", private)
        self.assertIn("ABANDONED_PREVIEW_SUMMARY", private)

    def test_streamed_stages_stay_on_one_row_freeze_for_selection_and_disappear(self):
        terminal, gate, requests = self.terminal()
        terminal.send("trigger compression\n")
        terminal.wait_for(lambda output: "THOUGHT_TAIL" in terminal.visible)
        lines = self.activity_lines(terminal)
        self.assertEqual(len(lines), 1)
        self.assertIn("思考", lines[0])
        self.assertIn("…", lines[0])
        self.assertEqual(self.cell(terminal, "THOUGHT_TAIL")[2].fg, "brightblack")
        self.assertEqual(self.cell(terminal, "THOUGHT_TAIL")[2].bg, "default")
        self.assertEqual(self.cell(terminal, "正在压缩上下文")[2].fg, "blue")
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "选择文本（F2 返回）" in terminal.visible)
        frozen = terminal.visible
        (gate / "summary").write_text("release")
        terminal.wait_for(lambda output: (gate / "summary-emitted").exists())
        terminal.read(0.1)
        self.assertEqual(terminal.visible, frozen)
        terminal.send("\x1bOQ")
        terminal.wait_for(lambda output: "ABANDONED_PREVIEW_SUMMARY" in terminal.visible)
        self.assertEqual(len(self.activity_lines(terminal)), 1)
        self.assertNotIn("THOUGHT_TAIL", terminal.visible)
        self.assertIn("摘要", self.activity_lines(terminal)[0])
        (gate / "finish").write_text("release")
        terminal.wait_for(lambda output: "AFTER_COMPRESSION" in terminal.visible
                          and not self.activity_lines(terminal) and "停止" not in terminal.visible)
        context = json.loads((self.workspace / ".agent/sessions/terminal/context.json").read_text())
        self.assertIn("SAVED_SUMMARY", context["summary"])
        self.assertEqual(len(requests), 1)
        self.assertNotIn("PRIVATE_REVIEW_THOUGHT", terminal.text)
        self.assert_private()

    def test_stop_discards_partial_summary_and_queued_message_runs_afterward(self):
        terminal, gate, requests = self.terminal()
        terminal.send("compress until stopped\n")
        terminal.wait_for(lambda output: "THOUGHT_TAIL" in terminal.visible)
        (gate / "summary").write_text("release")
        terminal.wait_for(lambda output: "ABANDONED_PREVIEW_SUMMARY" in terminal.visible)
        terminal.send("queued continuation\n")
        terminal.wait_for(lambda output: "排队中" in terminal.visible)
        context = json.loads((self.workspace / ".agent/sessions/terminal/context.json").read_text())
        self.assertEqual(context["summary"], "")
        x, y, _ = self.cell(terminal, "停止")
        terminal.send(f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m")
        terminal.wait_for(lambda output: "AFTER_COMPRESSION" in terminal.visible
                          and not self.activity_lines(terminal) and "排队中" not in terminal.visible
                          and "停止" not in terminal.visible)
        self.assertIsNone(terminal.process.poll())
        self.assertEqual(len(requests), 1)
        self.assertIn("queued continuation", json.dumps(requests[0]))
        context = json.loads((self.workspace / ".agent/sessions/terminal/context.json").read_text())
        self.assertIn("SAVED_SUMMARY", context["summary"])
        self.assertNotIn("ABANDONED_PREVIEW_SUMMARY", context["summary"])
        self.assert_private()


if __name__ == "__main__":
    unittest.main()
