import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from skills_agent.agent import Agent
from skills_agent.cli import ConsoleRenderer
from skills_agent.context import record_groups
from skills_agent.context.history import HistoryIndex
from skills_agent.providers import AnthropicProvider, OpenAIProvider
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final


class StopContextTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.session = Session(self.root, "main")

    def agent(self, model, **kwargs):
        return Agent(model, self.session, SkillRegistry([]), self.root, **kwargs)

    @staticmethod
    def interrupted(system, messages, tools, on_event):
        on_event({"type": "thinking_delta", "text": "PRIVATE_THINKING"})
        on_event({"type": "text_delta", "text": "  received body\n"})
        on_event({"type": "tool_call_delta", "index": 0, "name": "shell",
                  "arguments": '{"name":"unfinished","command":"'})
        error = KeyboardInterrupt()
        error.partial_content = [
            {"type": "thinking", "thinking": "PRIVATE_THINKING", "signature": "INCOMPLETE_SIGNATURE"},
            {"type": "text", "text": "  received body\n"},
        ]
        error.raw = {"private": "INCOMPLETE_PROVIDER_STATE"}
        raise error

    def test_stopped_body_is_persisted_once_and_available_to_next_request(self):
        model = ScriptedModel([self.interrupted, final("continued")])
        output = io.StringIO()
        agent = self.agent(model, on_event=ConsoleRenderer(output))
        agent.tools.execute = Mock(side_effect=AssertionError("Partial calls must not execute"))
        with self.assertRaises(KeyboardInterrupt):
            agent.run({"role": "user", "content": "first"})
        agent.tools.execute.assert_not_called()
        self.assertEqual(len(model.calls), 1)  # Stopping never requests a report or summary.
        event = self.session.events()[-1]
        self.assertTrue(event["response_interrupted"])
        self.assertEqual(event["partial_content"][1]["text"], "  received body\n")
        self.assertEqual(len(self.session.messages()), 1)

        state = json.loads((self.session.path / "context.json").read_text())
        self.assertEqual(state["records"], agent.context.records)
        record = state["records"][-1]
        self.assertEqual(record["event_id"], event["event_id"])
        self.assertEqual(record["message"]["content"][0]["text"], "  received body\n")
        self.assertIn("interrupted", record["message"]["content"][-1]["text"])
        for private in ("PRIVATE_THINKING", "INCOMPLETE_", "tool_calls"):
            self.assertNotIn(private, json.dumps(record))
        original = (self.session.path / "events.jsonl").read_bytes()
        agent.context.sync()
        restored = self.agent(model)
        restored.context.sync()
        self.assertEqual(restored.context.records, state["records"])
        self.assertEqual((self.session.path / "events.jsonl").read_bytes(), original)

        reference = HistoryIndex(self.session.path).read(record["event_id"])
        self.assertIn("  received body\n", reference["text"])
        self.assertIn("Interrupted", reference["text"])
        replay = io.StringIO()
        render = ConsoleRenderer(replay)
        for item in self.session.events():
            render(item)
        self.assertEqual(output.getvalue(), replay.getvalue())
        self.assertEqual(output.getvalue().count("received body"), 1)

        self.assertEqual(restored.run({"role": "user", "content": "queued next"}), "continued")
        messages = model.calls[-1][1]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[1], record["message"])
        for payload in (
            OpenAIProvider("test", "secret", api_format="chat")._chat_payload("", messages, []),
            OpenAIProvider("test", "secret")._responses_payload("", messages, []),
            AnthropicProvider("test", "secret")._payload("", messages, []),
        ):
            self.assertIn("received body", json.dumps(payload))
            self.assertNotIn("INCOMPLETE_", json.dumps(payload))

    def test_stopped_thinking_without_body_does_not_create_an_assistant_message(self):
        def stop(system, messages, tools, on_event):
            error = KeyboardInterrupt()
            error.partial_content = [{"type": "thinking", "thinking": "unfinished thought"}]
            raise error
        model = ScriptedModel([stop])
        agent = self.agent(model)
        with self.assertRaises(KeyboardInterrupt):
            agent.run({"role": "user", "content": "task"})
        self.assertEqual(len(agent.context.records), 1)
        self.assertIn("unfinished thought", (self.session.path / "events.jsonl").read_text())
        self.assertNotIn("unfinished thought", (self.session.path / "context.json").read_text())

    def test_stopped_private_report_waits_for_next_selection_without_leaking(self):
        model = ScriptedModel([
            call("shell", {"name": "inspect", "command": "ignored", "result_mode": "report"}),
            final("continued"),
        ])
        output = io.StringIO()
        agent = self.agent(model, on_event=ConsoleRenderer(output))
        agent.tools.execute = Mock(return_value=json.dumps({"stdout": "actual evidence", "exit_code": 0}))
        error = KeyboardInterrupt()
        error.partial_content = [{"type": "text", "text": "PRIVATE_REPORT_DRAFT"}]
        error.raw = {"private": "PRIVATE_REPORT_RAW"}
        agent.auxiliary.tool_report = Mock(side_effect=error)
        with self.assertRaises(KeyboardInterrupt):
            agent.run({"role": "user", "content": "task"})
        self.assertEqual(agent.auxiliary.tool_report.call_count, 1)
        self.assertNotIn("PRIVATE_REPORT", output.getvalue())
        self.assertNotIn("PRIVATE_REPORT", (self.session.path / "events.jsonl").read_text())
        self.assertNotIn("PRIVATE_REPORT", (self.session.path / "context.json").read_text())
        self.assertFalse(self.session.events()[-1].get("response_interrupted"))
        self.assertEqual(len(agent.context.records), 2)  # Full result remains unselected in the raw log.
        self.assertIn("actual evidence", self.session.messages()[-1]["content"])

        agent.auxiliary.tool_report = Mock(return_value="verified report")
        agent.run({"role": "user", "content": "next"})
        agent.auxiliary.tool_report.assert_called_once()
        agent.tools.execute.assert_called_once()
        self.assertIn("verified report", json.dumps(model.calls[-1][1]))
        record_groups(agent.context.records)

    def test_stopped_subagent_body_stays_in_child_context(self):
        model = ScriptedModel([
            call("subagent", {"name": "inspect", "task": "private work"}),
            self.interrupted,
        ])
        agent = self.agent(model)
        with self.assertRaises(KeyboardInterrupt):
            agent.run({"role": "user", "content": "task"})
        self.assertNotIn("received body", (self.session.path / "events.jsonl").read_text())
        self.assertNotIn("received body", json.dumps(agent.context.records))
        children = list((self.session.path / "subagents").glob("*/context.json"))
        self.assertEqual(len(children), 1)
        self.assertIn("received body", children[0].read_text())
        self.assertNotIn("PRIVATE_THINKING", children[0].read_text())


if __name__ == "__main__":
    unittest.main()
