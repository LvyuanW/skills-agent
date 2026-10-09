import copy
import json
import tempfile
import unittest
from pathlib import Path

from skills_agent.agent import Agent
from skills_agent.context import ContextBudgetError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_context import Counter


class ScriptedModel(Counter):
    def __init__(self, replies):
        super().__init__()
        self.replies = iter(replies)
        self.calls = []

    def complete(self, system, messages, tools, *, on_event=None, max_output_tokens=None):
        self.calls.append(copy.deepcopy((system, messages, tools)))
        reply = next(self.replies)
        if callable(reply):
            return reply(system, messages, tools, on_event)
        return reply


def call(name, args, number=1):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"name": name, "arguments": json.dumps(args), "id": f"call-{number}"}]}


def final(text):
    return {"role": "assistant", "content": text}


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = Session(self.root, "main")
        self.skills = SkillRegistry([])

    def agent(self, model, **kwargs):
        return Agent(model, self.session, self.skills, self.root, **kwargs)

    def test_long_raw_result_saved_before_report_and_never_replaces_raw_log(self):
        model = ScriptedModel([call("shell", {"name": "inspect log", "command": "ignored"}), final("done")])
        agent = self.agent(model)
        raw = json.dumps({"exit_code": 0, "stdout": "detail " * 9000, "stderr": ""})
        agent.tools.execute = lambda *args: raw
        report_calls = []
        def report(content, task, source, background):
            self.assertEqual(self.session.messages()[-1]["content"], raw)
            self.assertEqual(background["current_user_task"]["content"], "inspect")
            self.assertIn("detail " * 9000, content)
            report_calls.append(source)
            return "Relevant conclusion with evidence"
        agent.auxiliary.tool_report = report
        self.assertEqual(agent.run({"role": "user", "content": "inspect"}), "done")
        self.assertEqual(len(report_calls), 1)
        self.assertEqual(self.session.messages()[2]["content"], raw)
        tool = model.calls[-1][1][-1]
        self.assertEqual(json.loads(tool["content"])["report"], "Relevant conclusion with evidence")
        self.assertEqual(tool["tool_call_id"], "call-1")
        restored = self.agent(model)
        restored.context.sync(restored._select)
        self.assertEqual(restored.context.records, agent.context.records)

    def test_direct_preserves_large_exact_result_and_report_mode_reports_small_result(self):
        for mode in ["direct", "report"]:
            with self.subTest(mode=mode):
                self.session = Session(self.root, mode)
                model = ScriptedModel([call("shell", {"name": "read", "command": "ignored", "result_mode": mode}), final("done")])
                agent = self.agent(model)
                raw = json.dumps({"exit_code": 0, "stdout": "exact " * (9000 if mode == "direct" else 2), "stderr": ""})
                agent.tools.execute = lambda *args: raw
                reports = []
                agent.auxiliary.tool_report = lambda *args: (reports.append(args) or "report")
                agent.run({"role": "user", "content": "read exact source"})
                if mode == "direct":
                    self.assertEqual(model.calls[-1][1][-1]["content"], raw)
                    self.assertFalse(reports)
                else:
                    self.assertEqual(len(reports), 1)

    def test_history_subtask_receives_full_readonly_snapshot_and_returns_only_report(self):
        events = []
        def child(system, messages, tools, on_event):
            self.assertIn("Parent history:", system)
            self.assertIn("history retrieval task", system)
            self.assertNotIn("subagent", [tool["name"] for tool in tools])
            prompt = messages[-1]["content"][0]["text"]
            snapshot = json.loads(prompt.split("Read-only working context:\n", 1)[1])
            self.assertEqual(snapshot["records"][0]["message"]["content"], "why that decision?")
            self.assertEqual(snapshot["records"][-1]["message"]["tool_calls"][0]["name"], "subagent")
            self.assertIn("timestamp", snapshot["records"][0])
            on_event({"type": "thinking_delta", "text": "PRIVATE_SEARCH_PROCESS"})
            return final("Decision report [event-0]: evidence and uncertainty")
        model = ScriptedModel([call("subagent", {"name": "Recall decision", "task": "Find the user's decision", "history": True}),
                               child, final("User-facing conclusion")])
        agent = self.agent(model, on_event=events.append)
        agent.run({"role": "user", "content": "why that decision?"})
        self.assertNotIn("PRIVATE_SEARCH_PROCESS", json.dumps(events))
        self.assertNotIn("PRIVATE_SEARCH_PROCESS", (self.session.path / "events.jsonl").read_text())
        self.assertIn("Decision report", str(model.calls[-1][1]))
        self.assertNotIn("Read-only working context", str(model.calls[-1][1]))
        summaries = [event for event in events if event["type"] == "subagent_summary"]
        self.assertEqual(summaries[0]["name"], "Recall decision")
        self.assertEqual(summaries[0]["status"], "completed")
        self.assertEqual(len(list((self.session.path / "subagents").glob("*/background.json"))), 1)

    def test_complete_history_snapshot_too_large_is_explicit_error(self):
        model = ScriptedModel([])
        agent = self.agent(model, context_tokens=50)
        agent.context.sync()
        with self.assertRaises(ContextBudgetError):
            agent._subagent("history", "read", history=True)
        children = list((self.session.path / "subagents").glob("*/background.json"))
        self.assertEqual(len(children), 1)
        self.assertFalse(model.calls)
        self.assertEqual(self.session.events()[-1]["status"], "failed")

    def test_history_snapshot_preserves_native_media_not_only_paths(self):
        media = {"type": "image", "path": "/read-only/image.png", "mime_type": "image/png"}
        model = ScriptedModel([final("evidence")])
        agent = self.agent(model)
        self.session.append({"role": "user", "content": [{"type": "text", "text": "compare this"}, media]})
        agent.context.sync()
        agent._subagent("Recall image", "Find earlier comparison", history=True)
        content = model.calls[0][1][-1]["content"]
        self.assertIn(media, content)
        self.assertEqual(agent.context.records[0]["message"]["content"][-1], media)

    def test_cancel_during_assistant_render_keeps_protocol_usable_and_never_runs_old_script(self):
        model = ScriptedModel([call("shell", {"name": "old action", "command": "do not run"}), final("new reply")])
        first = True
        def display(event):
            nonlocal first
            if event["type"] == "message" and event["message"].get("tool_calls") and first:
                first = False
                raise KeyboardInterrupt
        agent = self.agent(model, on_event=display)
        executed = []
        agent.tools.execute = lambda *args: executed.append(args)
        with self.assertRaises(KeyboardInterrupt):
            agent.run({"role": "user", "content": "first"})
        self.assertEqual(agent.run({"role": "user", "content": "second"}), "new reply")
        self.assertFalse(executed)
        tools = [m for m in model.calls[-1][1] if m["role"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertIn("interrupted", tools[0]["content"])


if __name__ == "__main__":
    unittest.main()
