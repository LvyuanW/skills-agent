"""Persistent background results enter the model only at valid message boundaries."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.background import BackgroundResults, RUNTIME_EVENT
from skills_agent.context import record_groups
from skills_agent.processes import BackgroundJobError, ProcessScope
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final


def notices(messages):
    return [message for message in messages if message.get("runtime_event") == RUNTIME_EVENT]


def observation(message):
    return json.loads(message["content"][1]["text"])


class BackgroundResultsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root, "main")
        self.jobs = []
        observer = patch("skills_agent.background.observe_background_jobs",
                         side_effect=lambda paths: deepcopy(self.jobs))
        self.observe = observer.start()
        self.addCleanup(observer.stop)
        results = patch("skills_agent.background.completion_result", side_effect=self.result)
        self.results = results.start()
        self.addCleanup(results.stop)
        monitor = patch("skills_agent.agent.BackgroundJobMonitor")
        monitor.start()
        self.addCleanup(monitor.stop)

    def agent(self, replies, **kwargs):
        model = ScriptedModel(replies)
        agent = Agent(model, self.session, SkillRegistry([]), self.root, **kwargs)
        self.addCleanup(agent.close)
        return agent, model

    def job(self, *, owner=None, state="running", wait=True, identifier="job-1", **values):
        return {"id": identifier, "name": "compute", "state": state,
                "owner_session_id": owner or hashlib.sha256(str(self.session.path).encode()).hexdigest(),
                "origin_tool_call_id": "call-1", "kind": "job" if wait else "service",
                "wait_on_exit": wait, "exit_code": 0, "returncode": 0,
                "_scope_path": str(self.session.path / "process-scopes" / "test"), **values}

    def result(self, job):
        return {"job_id": job["id"], "name": job["name"], "state": job["state"],
                "owner_session_id": job.get("owner_session_id"),
                "origin_tool_call_id": job.get("origin_tool_call_id"),
                "exit_code": job["exit_code"], "process_returncode": job["returncode"],
                "timed_out": job["state"] == "timed_out", "cleanup_confirmed": True,
                "stdout": job.get("stdout", "Finished numerical work"), "stderr": "",
                "stdout_truncated": job.get("stdout_truncated", False), "stderr_truncated": False,
                "stdout_path": str(self.root / "stdout.log"), "stderr_path": str(self.root / "stderr.log")}

    def test_progress_without_call_waits_then_delivers_once_without_replaying_tool(self):
        agent, model = self.agent([
            call("shell", {"name": "launch", "command": "registered launch"}),
            final("Computation is running; I will inspect its result."), final("Verified completed result"),
        ])

        def launch(*args):
            self.assertEqual(agent.tools.process_context, agent._background_results.owner)
            self.assertEqual(agent.tools.tool_call_id, "call-1")
            self.jobs.append(self.job())
            return json.dumps({"exit_code": 0, "stdout": "started", "stderr": ""})

        def finish(seconds):
            self.assertEqual(seconds, 0.1)
            self.jobs[0]["state"] = "exited"

        with patch.object(agent.tools, "execute", side_effect=launch) as execute, \
                patch("skills_agent.background.time.sleep", side_effect=finish) as sleep:
            self.assertEqual(agent.run({"role": "user", "content": "Compute and verify"}),
                             "Verified completed result")
        execute.assert_called_once()
        sleep.assert_called_once()
        self.assertIsNone(agent.tools.tool_call_id)
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(len(notices(model.calls[2][1])), 1)
        message = notices(self.session.messages())[0]
        self.assertEqual(message["background_result_id"], "job-1-terminal")
        self.assertEqual(observation(message)["exit_code"], 0)
        self.assertEqual([m.get("tool_call_id") for m in self.session.messages() if m["role"] == "tool"],
                         ["call-1"])

    def test_completion_during_model_call_does_not_mutate_live_request_or_history(self):
        self.jobs = [self.job()]

        def finish_during_response(system, messages, tools, on_event):
            before = deepcopy(messages)
            self.jobs[0]["state"] = "exited"
            self.assertEqual(notices(self.session.messages()), [])
            self.assertEqual(messages, before)
            return final("I am waiting for the computation.")

        agent, model = self.agent([finish_during_response, final("Now the result is verified")])
        agent.run({"role": "user", "content": "Inspect"})
        self.assertEqual(len(notices(model.calls[0][1])), 0)
        self.assertEqual(len(notices(model.calls[1][1])), 1)
        self.assertEqual([m["role"] for m in self.session.messages()],
                         ["user", "assistant", "user", "assistant"])

    def test_completion_during_context_count_is_included_before_main_request(self):
        self.jobs = [self.job()]
        agent, model = self.agent([final("Observed completion")])
        count = model.count_tokens

        def finish_during_count(*args):
            self.jobs[0]["state"] = "exited"
            return count(*args)

        with patch.object(model, "count_tokens", side_effect=finish_during_count):
            agent.run({"role": "user", "content": "Check work"})
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(notices(model.calls[0][1])), 1)

    def test_results_wait_until_every_tool_in_the_assistant_group_is_paired(self):
        reply = call("shell", {"name": "first", "command": "first"})
        reply["tool_calls"] += call("shell", {"name": "second", "command": "second"}, 2)["tool_calls"]
        agent, model = self.agent([reply, final("Completed")])

        def execute(name, arguments):
            self.assertEqual(notices(self.session.messages()), [])
            if not self.jobs:
                self.jobs.append(self.job(state="exited"))
            return json.dumps({"exit_code": 0, "stdout": "ok", "stderr": ""})

        with patch.object(agent.tools, "execute", side_effect=execute):
            agent.run({"role": "user", "content": "Two calls"})
        self.assertEqual([m["role"] for m in model.calls[1][1]],
                         ["user", "assistant", "tool", "tool", "user"])
        self.assertEqual(len(record_groups(agent.context.records)), 4)

    def test_running_service_does_not_block_but_its_later_result_reaches_next_turn(self):
        self.jobs = [self.job(wait=False)]
        agent, model = self.agent([final("Service ready"), final("Observed stopped service")])
        with patch("skills_agent.background.time.sleep") as sleep:
            self.assertEqual(agent.run({"role": "user", "content": "Start service"}), "Service ready")
            sleep.assert_not_called()
        self.jobs[0].update(state="stopped", returncode=-15, exit_code=143)
        agent.run({"role": "user", "content": "Check service"})
        self.assertEqual(observation(notices(model.calls[1][1])[0])["exit_code"], 143)

    def test_cancel_does_not_call_model_again_and_next_turn_observes_closed_scope(self):
        agent, model = self.agent([final("Working"), final("Handled interrupted job")])
        self.jobs = [self.job()]
        closed = []

        def close(scope, **kwargs):
            closed.append(scope.path)
            self.jobs[0].update(state="stopped", returncode=-15, exit_code=143)

        with patch("skills_agent.background.time.sleep", side_effect=KeyboardInterrupt), \
                patch.object(ProcessScope, "close", close):
            with self.assertRaises(KeyboardInterrupt):
                agent.run({"role": "user", "content": "Compute"})
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(agent._process_scopes, [])
            self.assertIn(closed[0], agent._background_scope_paths())
            agent.run({"role": "user", "content": "Resume after cancellation"})
        self.assertEqual(len(notices(self.session.messages())), 1)
        self.assertEqual(observation(notices(model.calls[1][1])[0])["state"], "stopped")

    def test_durable_receipt_prevents_replay_after_append_then_context_failure(self):
        self.jobs = [self.job(state="exited")]
        delivery = BackgroundResults(self.session, lambda: [])

        def append_then_fail(message):
            self.session.append(message)
            raise RuntimeError("context sync interrupted after durable append")

        with self.assertRaisesRegex(RuntimeError, "context sync"):
            delivery.drain(append_then_fail)
        restored = BackgroundResults(self.session, lambda: [])
        self.assertEqual(restored.drain(self.session.append), (0, False))
        self.assertEqual(len(notices(self.session.messages())), 1)
        self.results.assert_called_once()

    def test_terminal_failure_and_timeout_are_observations_not_repeated_tool_results(self):
        self.jobs = [self.job(state="exited", exit_code=7, returncode=7),
                     self.job(state="timed_out", identifier="job-2", exit_code=124, returncode=-9)]
        agent, model = self.agent([final("Adapted to failures")])
        agent.run({"role": "user", "content": "Continue"})
        outputs = [observation(message) for message in notices(model.calls[0][1])]
        self.assertEqual([result["exit_code"] for result in outputs], [7, 124])
        self.assertTrue(outputs[1]["timed_out"])
        self.assertEqual(outputs[1]["process_returncode"], -9)
        self.assertEqual(outputs[1]["stdout_path"], str(self.root / "stdout.log"))
        self.assertFalse(any(m["role"] == "tool" for m in self.session.messages()))

    def test_child_never_consumes_parent_or_legacy_root_results(self):
        child = Session(self.session.path / "subagents", "child")
        delivery = BackgroundResults(child, lambda: [], accept_handoffs=False)
        legacy = self.job(identifier="legacy", state="exited")
        legacy["owner_session_id"] = None
        self.jobs = [self.job(state="exited"), legacy,
                     self.job(owner=delivery.owner, identifier="child-job", state="exited")]
        self.assertEqual(delivery.drain(child.append), (1, False))
        self.assertEqual([m["background_job_id"] for m in notices(child.messages())], ["child-job"])
        self.assertEqual(self.session.messages(), [])

    def handoff(self, child, owner, name="Delegate review", status="completed", reported=True):
        self.session.append_event({"type": "subagent_summary", "name": name, "status": status,
                                   "child_session_id": child.path.name,
                                   "child_session_path": str(child.path),
                                   "background_owner_session_id": owner})
        if status == "completed" and reported:
            self.session.append({"role": "tool", "name": "subagent", "tool_call_id": "call-1",
                                 "child_session_id": child.path.name,
                                 "content": json.dumps({"result": "Child's completed report"})})

    def test_finished_child_hands_off_service_once_but_consumed_job_is_not_repeated(self):
        child = Session(self.session.path / "subagents", "child")
        child_delivery = BackgroundResults(child, lambda: [], accept_handoffs=False)
        child_job = self.job(owner=child_delivery.owner, state="exited", identifier="child-job")
        service = self.job(owner=child_delivery.owner, wait=False, identifier="child-service")
        self.jobs = [child_job, service]
        child_delivery.drain(child.append)
        self.handoff(child, child_delivery.owner)
        service.update(state="exited", exit_code=7, returncode=7)
        parent = BackgroundResults(self.session, lambda: [])
        self.assertEqual(parent.drain(self.session.append), (1, False))
        message = notices(self.session.messages())[0]
        self.assertEqual(message["background_job_id"], "child-service")
        self.assertEqual(message["job_owner_session_id"], child_delivery.owner)
        self.assertEqual(observation(message)["delegated_task_name"], "Delegate review")
        restored = BackgroundResults(self.session, lambda: [])
        self.assertEqual(restored.drain(self.session.append), (0, False))

    def test_failed_child_receipt_does_not_hide_result_without_a_delivered_report(self):
        for status in ("failed", "interrupted"):
            with self.subTest(status=status):
                child = Session(self.session.path / "subagents", status)
                child_delivery = BackgroundResults(child, lambda: [], accept_handoffs=False)
                self.jobs = [self.job(owner=child_delivery.owner, state="exited", identifier=status)]
                child_delivery.drain(child.append)
                self.handoff(child, child_delivery.owner, status=status)
                parent = BackgroundResults(self.session, lambda: [])
                self.assertEqual(parent.drain(self.session.append), (1, False))
                message = notices(self.session.messages())[-1]
                self.assertEqual(message["background_job_id"], status)
                self.assertEqual(observation(message)["stdout"], "Finished numerical work")
                restored = BackgroundResults(self.session, lambda: [])
                self.assertEqual(restored.drain(self.session.append), (0, False))

    def test_completed_summary_without_canonical_report_replays_result_even_with_reused_call_id(self):
        child = Session(self.session.path / "subagents", "unreported-child")
        child_delivery = BackgroundResults(child, lambda: [], accept_handoffs=False)
        self.jobs = [self.job(owner=child_delivery.owner, state="exited")]
        child_delivery.drain(child.append)
        self.handoff(child, child_delivery.owner, reported=False)
        # Another child used the same provider call ID; it cannot acknowledge
        # this child's report if a crash happened between summary and append.
        self.session.append({"role": "tool", "name": "subagent", "tool_call_id": "call-1",
                             "child_session_id": "another-child",
                             "content": json.dumps({"result": "Unrelated report"})})
        parent = BackgroundResults(self.session, lambda: [])
        self.assertEqual(parent.drain(self.session.append), (1, False))
        self.assertEqual(notices(self.session.messages())[0]["background_job_id"], "job-1")
        self.assertEqual(BackgroundResults(self.session, lambda: []).drain(self.session.append), (0, False))

    def test_successful_subagent_canonical_result_acknowledges_its_exact_child_session(self):
        agent, _ = self.agent([call("subagent", {"name": "Review", "task": "Check evidence"}),
                               final("Child verified evidence"), final("Delivered")])
        agent.run({"role": "user", "content": "Delegate review"})
        summary = next(event for event in self.session.events() if event["type"] == "subagent_summary")
        report = next(message for message in self.session.messages() if message["role"] == "tool")
        self.assertEqual(report["child_session_id"], summary["child_session_id"])
        self.assertEqual(json.loads(report["content"])["result"], "Child verified evidence")

    def test_unhanded_child_required_job_fails_closed_without_model_call(self):
        self.jobs = [self.job(owner="unknown-child")]
        agent, model = self.agent([final("Must not claim completion")])
        with self.assertRaisesRegex(BackgroundJobError, "handoff was not recorded"):
            agent.run({"role": "user", "content": "Resume"})
        self.assertEqual(model.calls, [])

    def test_disappearing_active_registration_is_not_treated_as_completion(self):
        self.jobs = [self.job()]
        delivery = BackgroundResults(self.session, lambda: [])
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        self.jobs.clear()
        with self.assertRaisesRegex(BackgroundJobError, "disappeared"):
            delivery.drain(self.session.append)

    def test_feedback_copies_complete_image_envelope_and_preserves_status_and_logs(self):
        image = self.root / "plot.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 4 + b"IHDR" + b"\0" * 8)
        envelope = json.dumps({"skills_agent_feedback": 1, "attachments": [
            {"type": "image", "path": str(image), "label": "Plot"}]})
        self.jobs = [self.job(state="exited", stdout=envelope)]
        delivery = BackgroundResults(self.session, lambda: [])
        delivery.drain(self.session.append)
        message = notices(self.session.messages())[0]
        snapshot = next(block for block in message["content"] if block["type"] == "image")
        self.assertNotEqual(snapshot["path"], str(image))
        self.assertEqual(Path(snapshot["path"]).read_bytes(), image.read_bytes())
        self.assertEqual(observation(message)["stdout_path"], str(self.root / "stdout.log"))
        self.jobs.append(self.job(state="exited", identifier="truncated", stdout=envelope, stdout_truncated=True))
        delivery.drain(self.session.append)
        message = notices(self.session.messages())[1]
        self.assertFalse(any(block["type"] == "image" for block in message["content"]))
        self.assertTrue(observation(message)["stdout_truncated"])

    def test_runtime_user_observation_does_not_replace_current_task_or_protection(self):
        self.jobs = [self.job(state="exited")]
        agent, _ = self.agent([final("Done")])
        agent.run({"role": "user", "content": "Original user task"})
        self.assertEqual(agent.context.latest_user()["content"], "Original user task")
        older, kept = agent.context._protected()
        self.assertTrue(any(item["message"].get("content") == "Original user task" for item in kept))
        captured = []
        with patch.object(agent.auxiliary, "_fits", side_effect=[False, True]), \
                patch.object(agent.auxiliary, "_report", side_effect=lambda log, payload: captured.append(payload) or "report"):
            agent.auxiliary.tool_report("output", {}, {}, {"messages": self.session.messages()})
        self.assertEqual(captured[0]["background"]["current_user_task"]["content"], "Original user task")

    def test_recovery_attaches_successful_scope_without_repeating_terminal_receipt(self):
        scope = ProcessScope(self.session.path, allow_persistent=True)
        scope.mark_successful()
        self.jobs = [self.job(state="exited")]
        BackgroundResults(self.session, lambda: [scope.path]).drain(self.session.append)
        agent, model = self.agent([final("Recovered")], allow_persistent_processes=True)
        self.assertEqual([item.path for item in agent._process_scopes], [scope.path])
        self.assertTrue(agent._process_scopes[0].successful)
        agent.run({"role": "user", "content": "Continue"})
        self.assertEqual(len(notices(model.calls[0][1])), 1)
        self.assertEqual(len(notices(self.session.messages())), 1)


if __name__ == "__main__":
    unittest.main()
