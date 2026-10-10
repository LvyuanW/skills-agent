"""Sub-agent progress exposes stages and elapsed time, never its private work."""

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

from skills_agent.agent import Agent
from skills_agent.cli import ConsoleRenderer
from skills_agent.context import WorkingContext
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.terminal import TerminalView
from tests.test_agent_context import ScriptedModel, call, final


class SubagentProgressBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.session = Session(self.root, "main")
        self.events = []

    def agent(self, replies):
        return Agent(ScriptedModel(replies), self.session, SkillRegistry([]), self.root,
                     on_event=self.events.append)

    def test_child_tool_stages_do_not_forward_child_text_arguments_or_results(self):
        private = ["PRIVATE_THOUGHT", "PRIVATE_TEXT", "PRIVATE_TOOL_NAME", "PRIVATE_OUTPUT"]

        def child(system, messages, tools, on_event):
            on_event({"type": "thinking_delta", "text": private[0]})
            on_event({"type": "text_delta", "text": private[1]})
            reply = call("shell", {"name": private[2], "command": "printf " + private[3]})
            on_event({"type": "tool_call_delta", "index": 0, "name": "shell",
                      "arguments": reply["tool_calls"][0]["arguments"]})
            return reply

        agent = self.agent([
            call("subagent", {"name": "Review patch", "task": "Inspect the patch"}),
            child, final("Evidence report"), final("Reviewed"),
        ])
        self.assertEqual(agent.run({"role": "user", "content": "review"}), "Reviewed")
        activity = [event["activity"] for event in self.events if event["type"] == "activity"]
        public = {"kind": "tool", "name": "subagent", "task_name": "Review patch"}
        self.assertEqual(activity, [public, {**public, "phase": "tool"},
                                    {**public, "phase": "working"}, None])
        self.assertIn("Evidence report", str(agent.model.calls[-1][1]))
        parent_log = (self.session.path / "events.jsonl").read_text()
        child_log = next((self.session.path / "subagents").glob("*/events.jsonl")).read_text()
        for marker in private:
            self.assertNotIn(marker, json.dumps(self.events))
            self.assertNotIn(marker, parent_log)
            self.assertIn(marker, child_log)
        for log in (parent_log, child_log):
            self.assertFalse(any(json.loads(line)["type"] == "activity" for line in log.splitlines()))

    def test_child_context_activity_is_mapped_to_parent_task_and_not_logged(self):
        prepare = WorkingContext.prepare

        def prepare_with_compression(context, system, tools):
            if context.session.path != self.session.path:
                context.on_activity({"kind": "compression", "private": "PRIVATE_CONTEXT"})
                context.on_activity(None)
            return prepare(context, system, tools)

        agent = self.agent([final("Report")])
        with patch.object(WorkingContext, "prepare", prepare_with_compression):
            self.assertEqual(agent._subagent("Review patch", "Inspect the patch"), "Report")
        self.assertEqual([event["activity"] for event in self.events if event["type"] == "activity"], [
            {"kind": "tool", "name": "subagent", "task_name": "Review patch", "phase": "compression"},
            {"kind": "tool", "name": "subagent", "task_name": "Review patch", "phase": "working"},
        ])
        self.assertNotIn("PRIVATE_CONTEXT", json.dumps(self.events))
        for path in self.session.path.rglob("events.jsonl"):
            self.assertFalse(any(json.loads(line)["type"] == "activity"
                                 for line in path.read_text().splitlines()))

    def test_child_failure_and_interruption_return_only_public_summary_events(self):
        for failure, status in ((ValueError("PRIVATE_FAILURE"), "failed"),
                                (KeyboardInterrupt("PRIVATE_INTERRUPT"), "interrupted")):
            with self.subTest(status=status):
                self.events.clear()

                def fail(system, messages, tools, on_event):
                    on_event({"type": "thinking_delta", "text": "PRIVATE_PARTIAL_THOUGHT"})
                    raise failure

                agent = self.agent([fail])
                with self.assertRaises(type(failure)):
                    agent._subagent("Review patch", "Inspect the patch")
                self.assertEqual(len(self.events), 1)
                self.assertEqual(self.events[0]["type"], "subagent_summary")
                self.assertEqual(self.events[0]["name"], "Review patch")
                self.assertEqual(self.events[0]["status"], status)
                self.assertNotIn("PRIVATE", json.dumps(self.events))


class SubagentProgressDisplayTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"TERM": "xterm-256color"})
        environment.start()
        self.addCleanup(environment.stop)
        timer = patch("skills_agent.terminal.monotonic", return_value=100.0)
        self.clock = timer.start()
        self.addCleanup(timer.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())
        self.renderer = ConsoleRenderer(self.view)

    def activity(self, phase=None, name="Review patch"):
        activity = {"kind": "tool", "name": "subagent", "task_name": name}
        if phase is not None:
            activity["phase"] = phase
        self.renderer({"type": "activity", "activity": activity})

    def lines(self):
        content = self.view.control.create_content(100, 20)
        return [fragment_list_to_text(content.get_line(i)).rstrip()
                for i in range(content.line_count)]

    def test_phase_changes_keep_elapsed_time_and_clear_starts_next_call_anew(self):
        self.activity()
        self.assertEqual(self.lines(), ["· sub-agent「Review patch」：执行中. · 0.0 秒"])
        self.clock.return_value = 112.3
        self.activity("tool")
        self.assertRegex(self.lines()[0], r"正在执行工具\.{1,3} · 12\.3 秒$")
        self.clock.return_value = 120.0
        self.activity("compression")
        self.assertRegex(self.lines()[0], r"正在压缩上下文\.{1,3} · 20\.0 秒$")
        self.clock.return_value = 130.0
        self.activity("working")
        self.assertRegex(self.lines()[0], r"执行中\.{1,3} · 30\.0 秒$")
        self.assertEqual(self.view.blocks, [])
        self.renderer({"type": "activity", "activity": None})
        self.assertFalse(any("Review patch" in line for line in self.lines()))
        self.clock.return_value = 200.0
        self.activity()
        self.assertRegex(self.lines()[0], r" · 0\.0 秒$")
        self.clock.return_value = 212.0
        self.activity(name="Other task")
        self.assertRegex(self.lines()[0], r"Other task.* · 0\.0 秒$")

    def test_selection_freezes_phase_and_clock_until_return(self):
        self.activity()
        self.clock.return_value = 105.0
        displayed = self.lines()
        self.view._selecting = True
        self.clock.return_value = 115.5
        self.activity("tool")
        self.assertEqual(self.lines(), displayed)
        self.view._selecting = False
        self.assertRegex(self.lines()[0], r"正在执行工具\.{1,3} · 15\.5 秒$")

    def test_other_activity_has_no_subagent_timer_and_history_does_not_replay_activity(self):
        self.activity()
        self.renderer({"type": "activity", "activity": {
            "kind": "tool", "name": "shell", "task_name": "Read file"}})
        self.clock.return_value = 112.3
        self.assertRegex(self.lines()[0], r"· shell「Read file」：执行中\.{1,3}$")
        self.renderer({"type": "activity", "activity": None})
        historical = ConsoleRenderer(self.view, live=False)
        historical({"type": "activity", "activity": {
            "kind": "tool", "name": "subagent", "task_name": "Review patch", "phase": "tool"}})
        self.assertFalse(any("Review patch" in line for line in self.lines()))

    def test_summary_distinguishes_report_failure_and_interruption(self):
        for status, label in (("completed", "已返回报告"), ("failed", "失败"),
                              ("interrupted", "已中断")):
            with self.subTest(status=status):
                output = StringIO()
                ConsoleRenderer(output)({"type": "subagent_summary", "name": "Review patch",
                                         "status": status})
                self.assertEqual(output.getvalue(), f"· sub-agent「Review patch」：{label}\n")


if __name__ == "__main__":
    unittest.main()
