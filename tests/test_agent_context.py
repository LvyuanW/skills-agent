import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.benchmark import parser as benchmark_parser
from skills_agent.cli import parser as cli_parser
from skills_agent.context import ContextBudgetError
from skills_agent.providers.base import ProviderConnectionError
from skills_agent.runtime import create_agent
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_context import Counter


class ScriptedModel(Counter):
    def __init__(self, replies):
        super().__init__()
        self.replies = iter(replies)
        self.calls = []

    def complete(self, system, messages, tools, *, on_event=None, max_output_tokens=None, disable_thinking=False):
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

    def test_entry_points_pass_default_and_custom_context_to_main_and_auxiliary(self):
        entries = [
            (cli_parser, ["run", "--session", "main"]),
            (benchmark_parser, ["--prompt-file", "unused.txt", "--log-dir", str(self.root / "logs")]),
        ]
        for parser, initial in entries:
            for budget, window in [(None, None), (512000, None), (2000000, 1000000)]:
                with self.subTest(entry=initial[0], budget=budget, window=window):
                    argv = [*initial, "--workspace", str(self.root), "--model", "test-model"]
                    if budget is not None:
                        argv += ["--context-tokens", str(budget)]
                    if window is not None:
                        argv += ["--model-context-window", str(window)]
                    model = ScriptedModel([])
                    model.model_context_window = window
                    with patch("skills_agent.runtime.create_provider", return_value=model) as factory:
                        agent = create_agent(parser().parse_args(argv), self.session)
                    expected = 1000000 if budget is None else budget
                    self.assertEqual(agent.context.max_tokens, expected)
                    self.assertEqual(agent.auxiliary.window, min(expected, window or expected))
                    self.assertEqual(factory.call_args.kwargs["model_context_window"], window)
                    self.assertEqual(factory.call_args.kwargs["max_output_tokens"], 32768)

    def test_turn_after_killed_run_recovers_missing_tool_result(self):
        self.session.append({"role": "user", "content": "old task"})
        self.session.append(call("shell", {"name": "inspect", "command": "ls"}))
        raw = (self.session.path / "events.jsonl").read_bytes()
        model = ScriptedModel([final("recovered")])
        self.assertEqual(self.agent(model).run({"role": "user", "content": "next"}), "recovered")
        sent = model.calls[0][1]
        self.assertEqual([message["role"] for message in sent], ["user", "assistant", "tool", "user"])
        self.assertEqual(sent[2]["tool_call_id"], "call-1")
        self.assertEqual(json.loads(sent[2]["content"])["error"],
                         "Execution interrupted; no completed result was recorded.")
        self.assertTrue((self.session.path / "events.jsonl").read_bytes().startswith(raw))
        # 用户消息、被中断的调用、恢复的结果、新用户消息、本轮回答
        self.assertEqual(len(json.loads((self.session.path / "context.json").read_text())["records"]), 5)

    def test_activity_uses_current_listener_and_does_not_log_or_expose_child_work(self):
        model = ScriptedModel([
            call("subagent", {"name": "Inspect workspace", "task": "Read files"}),
            call("shell", {"name": "Private child task", "command": "printf evidence"}),
            final("Child report"), final("done"),
        ])
        agent = self.agent(model)
        events = []
        agent.on_event = events.append
        self.assertEqual(agent.run({"role": "user", "content": "inspect"}), "done")
        self.assertEqual([event["activity"] for event in events if event["type"] == "activity"], [
            {"kind": "tool", "name": "subagent", "task_name": "Inspect workspace"},
            {"kind": "tool", "name": "subagent", "task_name": "Inspect workspace", "phase": "tool"},
            {"kind": "tool", "name": "subagent", "task_name": "Inspect workspace", "phase": "working"},
            None,
        ])
        self.assertNotIn("Private child task", json.dumps(events))
        for path in self.session.path.rglob("events.jsonl"):
            self.assertFalse(any(json.loads(line)["type"] == "activity" for line in path.read_text().splitlines()))

    def test_context_activity_reaches_current_listener_without_session_events(self):
        model = ScriptedModel([])
        agent = self.agent(model, context_tokens=1000)
        events = []
        agent.on_event = events.append
        self.session.append({"role": "user", "content": "task"})
        for index in range(11):
            self.session.append(final(str(index)))
        agent.context.sync()
        before = (self.session.path / "events.jsonl").read_bytes()
        counts = iter([900, 100, 120, 200])
        model.count_tokens = lambda *args: next(counts)
        def compress(*args, **kwargs):
            self.assertEqual(events, [{"type": "activity", "activity": {"kind": "compression"}}])
            return "context summary"
        agent.auxiliary.compress = compress
        agent.context.prepare("sys", [])
        self.assertEqual(events, [{"type": "activity", "activity": {"kind": "compression"}},
                                  {"type": "activity", "activity": None}])
        self.assertEqual((self.session.path / "events.jsonl").read_bytes(), before)

    def test_failed_compression_keeps_model_fragments_in_private_log(self):
        def fail(system, messages, tools, on_event):
            on_event({"type": "thinking_delta", "text": "PRIVATE_SUMMARY_THOUGHT"})
            raise ProviderConnectionError(
                "Summary connection lost", raw={"private": "PRIVATE_SUMMARY_RAW"},
                partial_content=[{"type": "text", "text": "PRIVATE_SUMMARY_FRAGMENT"}])
        model = ScriptedModel([fail, fail, fail])
        agent = self.agent(model, context_tokens=1000)
        events = []
        agent.on_event = events.append
        self.session.append({"role": "user", "content": "old task"})
        for index in range(11):
            self.session.append(final(str(index)))
        agent.context.sync()
        before = (self.session.path / "events.jsonl").read_bytes()
        model.count_tokens = lambda system, messages, tools, **options: 950 if tools else 100
        with self.assertRaisesRegex(ProviderConnectionError, "after 3 attempts"):
            agent.run({"role": "user", "content": "latest task"})
        main_log = (self.session.path / "events.jsonl").read_bytes()
        self.assertTrue(main_log.startswith(before))
        self.assertNotIn("PRIVATE_SUMMARY", main_log.decode())
        persistent = [event for event in events if event.get("type") != "activity"]
        self.assertNotIn("PRIVATE_SUMMARY", json.dumps(persistent))
        self.assertTrue(any(event.get("activity", {}).get("preview") == "PRIVATE_SUMMARY_THOUGHT"
                            for event in events if event.get("activity")))
        self.assertNotIn("PRIVATE_SUMMARY_RAW", json.dumps(events))
        self.assertNotIn("PRIVATE_SUMMARY_FRAGMENT", json.dumps(events))
        self.assertEqual(agent.context.state["summary"], "")
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(self.session.events()[-1]["message"], "Auxiliary connection failed after 3 attempts")
        private = "".join(path.read_text() for path in
                          (self.session.path / "subagents").glob("*/events.jsonl"))
        for fragment in ("PRIVATE_SUMMARY_THOUGHT", "PRIVATE_SUMMARY_RAW", "PRIVATE_SUMMARY_FRAGMENT"):
            self.assertIn(fragment, private)

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
        project = Path(__file__).resolve().parents[1] / "skills_agent"
        self.skills = SkillRegistry([project / "builtin_skills"])
        history_skill = str(project / "context/history_skill/SKILL.md")
        events = []
        def child(system, messages, tools, on_event):
            self.assertIn("Parent history:", system)
            self.assertIn("history retrieval task", system)
            self.assertIn("- history:", system)
            self.assertIn(history_skill, system)
            self.assertIn("- workspace:", system)
            self.assertNotIn("Delegate conversation-history retrieval", system)
            self.assertNotIn("subagent", [tool["name"] for tool in tools])
            prompt = messages[-1]["content"][0]["text"]
            snapshot = json.loads(prompt.split("Read-only working context:\n", 1)[1])
            self.assertNotIn("- history:", snapshot["system"])
            self.assertNotIn(history_skill, snapshot["system"])
            self.assertEqual(snapshot["records"][0]["message"]["content"], "why that decision?")
            self.assertEqual(snapshot["records"][-1]["message"]["tool_calls"][0]["name"], "subagent")
            self.assertIn("timestamp", snapshot["records"][0])
            on_event({"type": "thinking_delta", "text": "PRIVATE_SEARCH_PROCESS"})
            return final("Decision report [event-0]: evidence and uncertainty")
        model = ScriptedModel([call("subagent", {"name": "Recall decision", "task": "Find the user's decision", "history": True}),
                               child, final("User-facing conclusion")])
        agent = self.agent(model, on_event=events.append)
        agent.run({"role": "user", "content": "why that decision?"})
        for system in (model.calls[0][0], model.calls[-1][0], agent._system()):
            self.assertNotIn("- history:", system)
            self.assertNotIn(history_skill, system)
            self.assertNotIn("Session history source:", system)
            self.assertIn("Delegate conversation-history retrieval", system)
        self.assertNotIn("PRIVATE_SEARCH_PROCESS", json.dumps(events))
        self.assertNotIn("PRIVATE_SEARCH_PROCESS", (self.session.path / "events.jsonl").read_text())
        self.assertIn("Decision report", str(model.calls[-1][1]))
        self.assertNotIn("Read-only working context", str(model.calls[-1][1]))
        summaries = [event for event in events if event["type"] == "subagent_summary"]
        self.assertEqual(summaries[0]["name"], "Recall decision")
        self.assertEqual(summaries[0]["status"], "completed")
        self.assertEqual(len(list((self.session.path / "subagents").glob("*/background.json"))), 1)

    def test_ordinary_subtask_keeps_general_skills_without_history_skill(self):
        project = Path(__file__).resolve().parents[1] / "skills_agent"
        self.skills = SkillRegistry([project / "builtin_skills"])
        def child(system, messages, tools, on_event):
            self.assertIn("- workspace:", system)
            self.assertNotIn("- history:", system)
            self.assertNotIn("history_skill", system)
            self.assertNotIn("Parent history:", system)
            self.assertNotIn("Delegate conversation-history retrieval", system)
            self.assertNotIn("subagent", [tool["name"] for tool in tools])
            self.assertNotIn("Read-only working context:", str(messages))
            return final("Workspace report")
        model = ScriptedModel([
            call("subagent", {"name": "Inspect workspace", "task": "Inspect current files"}),
            child, final("done"),
        ])
        self.assertEqual(self.agent(model).run({"role": "user", "content": "inspect"}), "done")
        self.assertFalse(list((self.session.path / "subagents").glob("*/background.json")))

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
