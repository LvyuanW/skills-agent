"""Derived tool output must not erase a pending job's diagnostic evidence."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skills_agent.agent import Agent
from skills_agent.context import WorkingContext
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call
from tests.test_context import Counter, Summarizer


class BackgroundUncertaintyMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = Session(self.root, "diagnostics")
        self.metadata = {
            "status": "unconfirmed", "state": "unconfirmed", "completed": False,
            "job_id": "launch-pending", "launch_id": "launch-pending", "wait_on_exit": True,
            "error": "Supervisor ownership cannot be confirmed", "supervision_confirmed": False,
            "registration_error": "Cannot save launch expectation",
            "state_dir": "/saved/jobs", "process_record_path": "/saved/jobs/internal.json",
            "launch_record_path": "/saved/launches/internal.json",
        }

    def test_successful_report_keeps_error_gate_and_recovery_paths(self):
        agent = Agent(ScriptedModel([]), self.session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        agent.auxiliary.tool_report = lambda *args: "A summary that omits every diagnostic"
        record = {"event_id": "event-1", "message": {
            "role": "tool", "name": "shell", "tool_call_id": "call-1",
            "content": json.dumps(self.metadata),
        }}
        request = call("shell", {"name": "Start", "command": "worker", "result_mode": "report"})
        selected = agent._select(deepcopy(record), request["tool_calls"][0])
        result = json.loads(selected["message"]["content"])
        self.assertEqual(result["report"], "A summary that omits every diagnostic")
        for field, value in self.metadata.items():
            self.assertEqual(result[field], value, field)

    def test_direct_budget_fallback_keeps_error_gate_and_recovery_paths(self):
        self.session.append({"role": "user", "content": "Diagnose the job"})
        request = call("shell", {"name": "Start", "command": "worker", "result_mode": "direct"})
        self.session.append(request)
        raw = json.dumps({**self.metadata, "details": "LARGE_DIAGNOSTIC " * 2000})
        self.session.append({"role": "tool", "name": "shell", "tool_call_id": "call-1", "content": raw})
        summary = Summarizer()
        context = WorkingContext(self.session, Counter(), summary, max_tokens=500)

        def select(record, tool_call):
            return {**record, "pending_direct": True, "source": {"event_id": record["event_id"]}}

        context.sync(select)
        _, messages = context.prepare("system", [])
        result = json.loads(messages[-1]["content"])
        self.assertIn("does not fit", result["notice"])
        self.assertNotIn("details", result)
        for field, value in self.metadata.items():
            self.assertEqual(result[field], value, field)
        self.assertFalse(summary.calls)
        self.assertEqual(self.session.messages()[-1]["content"], raw)


if __name__ == "__main__":
    unittest.main()
