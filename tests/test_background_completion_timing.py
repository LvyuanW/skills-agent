"""A completion becomes visible only after entering the working context."""

import json
import unittest
from unittest.mock import patch

from skills_agent.background import RUNTIME_EVENT
from tests import test_background_results as result_helpers
from tests.test_agent_context import call, final
from tests.test_background_results import notices


class BackgroundCompletionTimingTests(unittest.TestCase):
    setUp = result_helpers.BackgroundResultsTests.setUp
    agent = result_helpers.BackgroundResultsTests.agent
    job = result_helpers.BackgroundResultsTests.job
    result = result_helpers.BackgroundResultsTests.result

    def test_completion_event_follows_context_sync_and_precedes_model_request(self):
        self.jobs = [self.job(state="exited")]
        agent, model = self.agent([final("Observed completion")])
        displayed = []

        def receive(event):
            if event.get("type") != "message" or event["message"].get("runtime_event") != RUNTIME_EVENT:
                return
            message = event["message"]
            in_context = notices([record["message"] for record in agent.context.records])
            self.assertEqual([item["background_result_id"] for item in in_context],
                             [message["background_result_id"]])
            self.assertEqual(model.calls, [])
            displayed.append(message["background_result_id"])

        agent.on_event = receive
        agent.run({"role": "user", "content": "Inspect completed work"})
        self.assertEqual(displayed, ["job-1-terminal"])
        self.assertEqual(len(notices(model.calls[0][1])), 1)

    def test_failed_context_sync_does_not_display_completion(self):
        self.jobs = [self.job(state="exited")]
        agent, model = self.agent([final("Must not be called")])
        displayed = []
        agent.on_event = displayed.append
        sync = agent.context.sync

        def fail_runtime_sync(*args, **kwargs):
            if self.session.messages()[-1].get("runtime_event") == RUNTIME_EVENT:
                raise RuntimeError("Working context save failed")
            return sync(*args, **kwargs)

        with patch.object(agent.context, "sync", side_effect=fail_runtime_sync):
            with self.assertRaisesRegex(RuntimeError, "Working context save failed"):
                agent.run({"role": "user", "content": "Inspect completed work"})
        self.assertEqual(model.calls, [])
        self.assertEqual(notices([event["message"] for event in displayed if event["type"] == "message"]), [])
        self.assertEqual(len(notices(self.session.messages())), 1, "Durable receipt is still retained")

    def test_ordinary_tool_event_order_is_unchanged(self):
        agent, _ = self.agent([call("shell", {"name": "inspect", "command": "true"}), final("Done")])
        tool_events = []

        def receive(event):
            if event.get("type") == "message" and event["message"].get("role") == "tool":
                self.assertFalse(any(record["message"].get("role") == "tool"
                                     for record in agent.context.records))
                tool_events.append(event)

        agent.on_event = receive
        with patch.object(agent.tools, "execute", return_value=json.dumps({
                "exit_code": 0, "stdout": "ok", "stderr": ""})):
            agent.run({"role": "user", "content": "Inspect"})
        self.assertEqual(len(tool_events), 1)


if __name__ == "__main__":
    unittest.main()
