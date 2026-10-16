"""Document delivery boundaries while a request retries or a batch is collected."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.background import RUNTIME_EVENT
from skills_agent.providers.base import ProviderConnectionError, validate_tool_pairs
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final


def notices(messages):
    return [message for message in messages if message.get("runtime_event") == RUNTIME_EVENT]


class BackgroundDeliveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root, "main")
        self.jobs = []
        observer = patch("skills_agent.background.observe_background_jobs",
                         side_effect=lambda paths: deepcopy(self.jobs))
        observer.start()
        self.addCleanup(observer.stop)
        results = patch("skills_agent.background.completion_result", side_effect=lambda job: {
            "job_id": job["id"], "state": job["state"], "exit_code": 0,
            "stdout": "completed", "stderr": "", "cleanup_confirmed": True,
        })
        results.start()
        self.addCleanup(results.stop)
        monitor = patch("skills_agent.agent.BackgroundJobMonitor")
        monitor.start()
        self.addCleanup(monitor.stop)

    def job(self, identifier, state="running"):
        return {"id": identifier, "name": identifier, "state": state,
                "owner_session_id": hashlib.sha256(str(self.session.path).encode()).hexdigest(),
                "kind": "job", "wait_on_exit": True}

    def agent(self, replies):
        model = ScriptedModel(replies)
        agent = Agent(model, self.session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        return agent, model

    def test_retry_includes_completion_without_adopting_partial_stream_or_replaying_tool(self):
        self.jobs = [self.job("finished-during-retry")]
        accepted_tool = call("shell", {"name": "inspect", "command": "inspect"})

        def fail_request(system, messages, tools, on_event):
            self.assertEqual(notices(messages), [])
            self.assertEqual([message["role"] for message in messages], ["user", "assistant", "tool"])
            self.assertIn("SAVED_TOOL_RESULT", messages[-1]["content"])
            on_event({"type": "text_delta", "text": "FAILED_STREAM_PREFIX"})
            on_event({"type": "tool_call_delta", "index": 0, "name": "shell",
                      "arguments": '{"name":"UNFINISHED_TOOL",'})
            # Providers must receive isolated copies even when the next retry
            # rebuilds the request to include a newly completed background job.
            messages.append(final("MUTATED_ATTEMPT_INPUT"))
            raise ProviderConnectionError(
                "temporary connection failure", raw={"partial": "RAW_FAILED_ATTEMPT"},
                partial_content=[{"type": "text", "text": "FAILED_STREAM_PREFIX"}],
            )

        def finish_during_backoff(seconds):
            self.assertEqual(seconds, 5)
            self.jobs[0]["state"] = "exited"
            self.assertEqual(notices(self.session.messages()), [])

        def retry_response(system, messages, tools, on_event):
            self.assertEqual(len(notices(messages)), 1)
            self.assertEqual(len(notices(self.session.messages())), 1)
            self.assertEqual([message["role"] for message in messages],
                             ["user", "assistant", "tool", "user"])
            self.assertIn("SAVED_TOOL_RESULT", messages[-2]["content"])
            self.assertNotIn("FAILED_STREAM_PREFIX", json.dumps(messages))
            self.assertNotIn("UNFINISHED_TOOL", json.dumps(messages))
            self.assertNotIn("MUTATED_ATTEMPT_INPUT", json.dumps(messages))
            validate_tool_pairs(messages)
            return final("Handled completion")

        def execute(name, arguments):
            self.assertEqual(notices(self.session.messages()), [])
            self.assertEqual((name, arguments),
                             ("shell", accepted_tool["tool_calls"][0]["arguments"]))
            return json.dumps({"exit_code": 0, "stdout": "SAVED_TOOL_RESULT", "stderr": ""})

        agent, model = self.agent([accepted_tool, fail_request, retry_response])
        with patch("skills_agent.providers.retry.time.sleep", side_effect=finish_during_backoff), \
                patch.object(agent.tools, "execute", side_effect=execute) as executed:
            self.assertEqual(agent.run({"role": "user", "content": "Complete work"}), "Handled completion")
        executed.assert_called_once()
        # One accepted tool round, one failed attempt and its successful retry;
        # completion delivery must not require another main model request.
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(notices(model.calls[1][1]), [])
        self.assertEqual(len(notices(model.calls[2][1])), 1)
        self.assertEqual([message["role"] for message in model.calls[2][1]],
                         ["user", "assistant", "tool", "user"])
        validate_tool_pairs(model.calls[2][1])
        self.assertEqual(len(notices(self.session.messages())), 1)
        self.assertEqual(len([message for message in self.session.messages() if message["role"] == "tool"]), 1)
        self.assertNotIn("FAILED_STREAM_PREFIX", json.dumps(self.session.messages()))
        self.assertNotIn("FAILED_STREAM_PREFIX", json.dumps(agent.context.records))
        self.assertNotIn("UNFINISHED_TOOL", json.dumps(agent.context.records))
        self.assertNotIn("MUTATED_ATTEMPT_INPUT", json.dumps(agent.context.records))
        retries = [event for event in self.session.events() if event["type"] == "retry"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["partial_content"], [{"type": "text", "text": "FAILED_STREAM_PREFIX"}])

    def test_completion_after_collection_snapshot_rebuilds_before_main_request(self):
        self.jobs = [self.job("first", "exited"), self.job("second")]
        agent, model = self.agent([final("Both results observed")])
        append = agent._message

        def complete_second_after_first_append(message, **kwargs):
            result = append(message, **kwargs)
            if message.get("background_job_id") == "first":
                self.jobs[1]["state"] = "exited"
            return result

        with patch.object(agent, "_message", side_effect=complete_second_after_first_append):
            self.assertEqual(agent.run({"role": "user", "content": "Inspect both jobs"}),
                             "Both results observed")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual([message["background_job_id"] for message in notices(model.calls[0][1])],
                         ["first", "second"])
        self.assertEqual(len(notices(self.session.messages())), 2)

    def test_retry_keeps_request_dirty_until_completed_result_rebuild_succeeds(self):
        self.jobs = [self.job("finished-during-retry")]

        def fail_request(system, messages, tools, on_event):
            self.assertEqual(notices(messages), [])
            raise ProviderConnectionError("main request failed")

        def finish_during_backoff(seconds):
            self.assertEqual(seconds, 5)
            self.jobs[0]["state"] = "exited"

        def retry_response(system, messages, tools, on_event):
            self.assertEqual(len(notices(messages)), 1,
                             "A saved delivery receipt must not acknowledge an unsent old request snapshot")
            return final("Observed the completed job")

        agent, model = self.agent([fail_request, retry_response])
        prepare = agent.context.prepare
        preparations = 0

        def fail_first_result_rebuild(*args, **kwargs):
            nonlocal preparations
            preparations += 1
            if preparations == 2:
                # prepare can call remote provider compaction. The observation
                # was already appended before that request failed transiently.
                self.assertEqual(len(notices(self.session.messages())), 1)
                raise ProviderConnectionError("context rebuild failed transiently")
            return prepare(*args, **kwargs)

        with patch("skills_agent.providers.retry.time.sleep", side_effect=finish_during_backoff), \
                patch.object(agent.context, "prepare", side_effect=fail_first_result_rebuild):
            self.assertEqual(agent.run({"role": "user", "content": "Observe the job"}),
                             "Observed the completed job")
        self.assertEqual(preparations, 3)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(notices(self.session.messages())), 1)


if __name__ == "__main__":
    unittest.main()
