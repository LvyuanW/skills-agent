"""Unconfirmed supervision is recoverable model input, never a final outcome."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.background import BackgroundResults, RUNTIME_EVENT, UNCERTAINTY_EVENT
from skills_agent.processes import BackgroundJobError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final


def uncertainties(messages):
    return [message for message in messages if message.get("runtime_event") == UNCERTAINTY_EVENT]


def results(messages):
    return [message for message in messages if message.get("runtime_event") == RUNTIME_EVENT]


def payload(message):
    return json.loads(message["content"][1]["text"])


class BackgroundUncertaintyTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root, "main")
        self.owner = hashlib.sha256(str(self.session.path).encode()).hexdigest()
        self.jobs = []
        observer = patch("skills_agent.background.observe_background_jobs",
                         side_effect=lambda paths, **kwargs: deepcopy(self.jobs))
        self.observer = observer.start()
        self.addCleanup(observer.stop)
        completion = patch("skills_agent.background.completion_result", side_effect=self.result)
        self.completion = completion.start()
        self.addCleanup(completion.stop)
        monitor = patch("skills_agent.agent.BackgroundJobMonitor")
        monitor.start()
        self.addCleanup(monitor.stop)

    def job(self, *, identifier="job-1", state="unconfirmed", **values):
        return {"id": identifier, "name": "compute", "state": state, "kind": "job",
                "owner_session_id": self.owner, "origin_tool_call_id": "call-1",
                "wait_on_exit": True, "error": "Supervisor identity cannot be confirmed",
                "_scope_path": str(self.session.path / "process-scopes" / "scope"),
                "_record_path": str(self.root / f"{identifier}.json"), **values}

    def result(self, job):
        return {"job_id": job["id"], "state": job["state"], "exit_code": 7,
                "process_returncode": 7, "stdout": "diagnostic result", "stderr": "",
                "cleanup_confirmed": True}

    def delivery(self):
        return BackgroundResults(self.session, lambda: [])

    def agent(self, replies):
        model = ScriptedModel(replies)
        agent = Agent(model, self.session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        return agent, model

    def test_uncertainty_is_reported_once_and_keeps_completion_gate(self):
        self.jobs = [self.job()]
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        notice = uncertainties(self.session.messages())[0]
        self.assertFalse(payload(notice)["completed"])
        self.assertTrue(payload(notice)["wait_on_exit"])
        self.assertEqual(payload(notice)["state"], "unconfirmed")
        self.assertIn("cannot be confirmed", payload(notice)["error"])
        self.assertEqual(results(self.session.messages()), [])
        self.completion.assert_not_called()
        self.assertTrue(self.observer.call_args.kwargs["tolerate_unconfirmed"])

    def test_known_service_uncertainty_is_reported_without_adding_completion_gate(self):
        self.jobs = [self.job(kind="service", wait_on_exit=False)]
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(delivery.drain(self.session.append), (0, False))
        self.assertFalse(payload(uncertainties(self.session.messages())[0])["wait_on_exit"])
        with patch("skills_agent.background.time.sleep") as sleep:
            self.assertEqual(delivery.wait(self.session.append), 0)
        sleep.assert_not_called()

    def test_restart_deduplicates_unchanged_error_but_preserves_gate(self):
        self.jobs = [self.job()]
        self.assertEqual(self.delivery().drain(self.session.append), (1, True))
        self.assertEqual(self.delivery().drain(self.session.append), (0, True))
        self.assertEqual(len(uncertainties(self.session.messages())), 1)

    def test_uncertain_registration_deleted_then_restart_cannot_remove_gate(self):
        self.jobs = [self.job()]
        delivery = self.delivery()
        delivery.drain(self.session.append)
        self.jobs.clear()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertIn("disappeared", payload(uncertainties(self.session.messages())[-1])["error"])
        restored = self.delivery()
        self.assertEqual(restored.drain(self.session.append), (0, True))
        self.assertEqual(results(self.session.messages()), [])

    def test_disappearing_running_job_returns_diagnostic_then_reappears(self):
        self.jobs = [self.job(state="running")]
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        self.jobs.clear()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.jobs = [self.job(state="exited")]
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(payload(results(self.session.messages())[0])["exit_code"], 7)
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))

    def test_missing_terminal_log_is_recoverable_and_real_result_delivers_once(self):
        self.jobs = [self.job(state="exited")]
        delivery = self.delivery()
        self.completion.side_effect = BackgroundJobError("Completed background job log is missing")
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        self.assertIn("log is missing", payload(uncertainties(self.session.messages())[0])["error"])
        self.completion.side_effect = self.result
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))
        self.assertEqual(len(results(self.session.messages())), 1)

    def test_no_tool_answer_gets_gate_reminder_without_passive_wait(self):
        self.jobs = [self.job()]
        delivery = self.delivery()
        delivery.drain(self.session.append)
        with patch("skills_agent.background.time.sleep") as sleep:
            self.assertEqual(delivery.wait(self.session.append), 1)
        sleep.assert_not_called()
        self.assertEqual(len(uncertainties(self.session.messages())), 2)
        self.assertEqual(delivery.drain(self.session.append), (0, True))

    def test_agent_can_diagnose_after_no_tool_reply_then_receive_actual_result(self):
        self.jobs = [self.job()]
        agent, model = self.agent([
            final("The task is complete."),
            call("shell", {"name": "Inspect supervision", "command": "read registered state"}),
            final("Confirmed the failed job and recovered its output."),
        ])

        def diagnose(*args):
            self.jobs[0].update(state="exited")
            return json.dumps({"exit_code": 0, "stdout": "Confirmed exit and cleanup", "stderr": ""})

        with patch.object(agent.tools, "execute", side_effect=diagnose) as execute, \
                patch("skills_agent.background.time.sleep") as sleep:
            self.assertEqual(agent.run({"role": "user", "content": "Run and verify"}),
                             "Confirmed the failed job and recovered its output.")
        execute.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(len(uncertainties(model.calls[0][1])), 1)
        self.assertEqual(len(uncertainties(model.calls[1][1])), 2)
        self.assertEqual(len(results(model.calls[2][1])), 1)
        self.assertEqual([message["tool_call_id"] for message in self.session.messages()
                          if message["role"] == "tool"], ["call-1"])

    def test_unhanded_child_result_is_not_misattributed_and_handoff_recovers(self):
        child = Session(self.session.path / "subagents", "child")
        child_owner = hashlib.sha256(str(child.path).encode()).hexdigest()
        self.jobs = [self.job(state="exited", owner_session_id=child_owner)]
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.completion.assert_not_called()
        self.assertEqual(results(self.session.messages()), [])
        self.session.append_event({"type": "subagent_summary", "status": "failed",
                                   "name": "Compute", "child_session_id": child.path.name,
                                   "child_session_path": str(child.path),
                                   "background_owner_session_id": child_owner})
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        notice = results(self.session.messages())[0]
        self.assertEqual(notice["job_owner_session_id"], child_owner)
        self.assertEqual(payload(notice)["delegated_task_name"], "Compute")

    def test_changed_uncertainty_is_observed_without_reporting_terminal_success(self):
        self.jobs = [self.job()]
        delivery = self.delivery()
        delivery.drain(self.session.append)
        self.jobs[0]["error"] = "Cleanup has not been confirmed"
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        self.assertEqual(len(uncertainties(self.session.messages())), 2)
        self.assertEqual(results(self.session.messages()), [])

    def test_matching_launch_expectation_replaced_by_terminal_run_durably(self):
        launch_id = "launch-unconfirmed"
        self.jobs = [self.job(identifier=launch_id)]
        delivery = self.delivery()
        delivery.drain(self.session.append)
        self.jobs = [self.job(state="exited", resolved_uncertainty_ids=[launch_id])]
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(results(self.session.messages())[0]["resolved_uncertainty_ids"], [launch_id])
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))

    def test_registered_running_run_replaces_launch_gate_without_false_disappearance(self):
        launch_id = "launch-unconfirmed"
        self.jobs = [self.job(identifier=launch_id)]
        delivery = self.delivery()
        delivery.drain(self.session.append)
        self.jobs = [self.job(state="running", resolved_uncertainty_ids=[launch_id])]
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        self.assertEqual(len(uncertainties(self.session.messages())), 1)
        self.jobs[0]["state"] = "exited"
        self.assertEqual(delivery.drain(self.session.append), (1, False))

    def test_canonical_uncertain_run_replaces_launch_gate_across_restart(self):
        launch_id = "launch-unconfirmed"
        self.jobs = [self.job(identifier=launch_id)]
        self.delivery().drain(self.session.append)
        self.jobs = [self.job(resolved_uncertainty_ids=[launch_id])]
        self.assertEqual(self.delivery().drain(self.session.append), (1, True))
        restored = self.delivery()
        self.assertEqual(restored.drain(self.session.append), (0, True))
        self.assertEqual(set(restored._known), {"job-1"})
        self.jobs[0]["state"] = "exited"
        self.assertEqual(restored.drain(self.session.append), (1, False))

    def test_durable_uncertainty_receipt_survives_failed_context_sync(self):
        self.jobs = [self.job()]
        delivery = self.delivery()

        def append_then_fail(message):
            self.session.append(message)
            raise RuntimeError("context sync failed")

        with self.assertRaisesRegex(RuntimeError, "context sync failed"):
            delivery.drain(append_then_fail)
        self.assertEqual(self.delivery().drain(self.session.append), (0, True))
        self.assertEqual(len(uncertainties(self.session.messages())), 1)

    def test_shell_uncertain_receipt_retains_gate_when_manifest_could_not_be_saved(self):
        receipt = self.job(identifier="launch-unconfirmed")
        receipt.update(job_id=receipt["id"], status="unconfirmed", completed=False,
                       registration_error="No space left on device")
        self.session.append({"role": "tool", "name": "shell", "tool_call_id": "call-1",
                             "content": [{"type": "text", "text": json.dumps(receipt)}]})
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertEqual(payload(uncertainties(self.session.messages())[0])["registration_error"],
                         "No space left on device")
        self.assertEqual(self.delivery().drain(self.session.append), (0, True))

    def test_command_stdout_cannot_fabricate_registration_gate(self):
        fake = {"job_id": "fake", "status": "unconfirmed"}
        self.session.append({"role": "tool", "name": "shell", "tool_call_id": "call-1",
                             "content": json.dumps({"exit_code": 0, "stdout": json.dumps(fake),
                                                    "stderr": ""})})
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))

    def test_later_uncertainty_cannot_reopen_an_already_delivered_terminal_gate(self):
        self.jobs = [self.job(state="exited")]
        self.assertEqual(self.delivery().drain(self.session.append), (1, False))
        self.jobs[0].update(state="unconfirmed", error="Historical registration became unreadable")
        restored = self.delivery()
        self.assertEqual(restored.drain(self.session.append), (0, False))
        self.assertEqual(restored.wait(self.session.append), 0)
        self.assertEqual(len(results(self.session.messages())), 1)
        self.assertFalse(uncertainties(self.session.messages()))

    def test_started_receipt_preserves_missing_registration_gate_after_restart(self):
        scope = self.session.path / "process-scopes" / "missing"
        receipt = {"status": "started", "completed": False, "job_id": "registered-job",
                   "name": "compute", "kind": "job", "wait_on_exit": True,
                   "state_dir": str(scope / "jobs"),
                   "process_record_path": str(scope / "jobs" / "compute.json")}
        self.session.append({"role": "tool", "name": "shell", "tool_call_id": "call-1",
                             "content": json.dumps(receipt)})
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        notice = uncertainties(self.session.messages())[0]
        self.assertEqual(payload(notice)["process_scope_path"], str(scope))
        self.assertEqual(payload(notice)["process_record_path"], receipt["process_record_path"])
        self.assertIn("disappeared", payload(notice)["error"])
        self.assertEqual(self.delivery().drain(self.session.append), (0, True))

    def handoff(self, child, owner, *, status="failed"):
        self.session.append_event({"type": "subagent_summary", "name": "Compute",
                                   "status": status, "child_session_id": child.path.name,
                                   "child_session_path": str(child.path),
                                   "background_owner_session_id": owner})

    def test_failed_or_interrupted_child_hands_off_uncertainty_without_registration(self):
        for status in ("failed", "interrupted"):
            with self.subTest(status=status):
                child = Session(self.session.path / "subagents", status)
                child_delivery = BackgroundResults(child, lambda: [], accept_handoffs=False)
                job = self.job(identifier=f"{status}-job", owner_session_id=child_delivery.owner)
                child_delivery._report_uncertainty(job, child.append)
                self.handoff(child, child_delivery.owner, status=status)
                delivery = self.delivery()
                self.assertEqual(delivery.drain(self.session.append), (1, True))
                self.assertIn(job["id"], delivery._known)
                self.assertEqual(delivery._known[job["id"]]["owner_session_id"], child_delivery.owner)
                self.assertEqual(self.delivery().drain(self.session.append), (0, True))

    def test_failed_child_hands_off_launch_receipt_when_manifest_was_not_saved(self):
        child = Session(self.session.path / "subagents", "lost-launch")
        child_delivery = BackgroundResults(child, lambda: [], accept_handoffs=False)
        receipt = self.job(identifier="launch-lost", owner_session_id=child_delivery.owner)
        receipt.update(status="unconfirmed", completed=False, job_id=receipt["id"],
                       registration_error="Expectation directory is a symbolic link")
        child.append({"role": "tool", "name": "shell", "tool_call_id": "call-1",
                      "content": json.dumps(receipt)})
        self.handoff(child, child_delivery.owner)
        self.assertEqual(self.delivery().drain(self.session.append), (1, True))
        notice = uncertainties(self.session.messages())[0]
        self.assertEqual(payload(notice)["registration_error"], receipt["registration_error"])
        self.assertEqual(payload(notice)["owner_session_id"], child_delivery.owner)
        self.assertEqual(self.delivery().drain(self.session.append), (0, True))

    def test_child_terminal_receipt_resolves_prior_uncertainty_before_failed_handoff(self):
        child = Session(self.session.path / "subagents", "resolved-child")
        child_delivery = BackgroundResults(child, lambda: [], accept_handoffs=False)
        job = self.job(identifier="resolved-job", owner_session_id=child_delivery.owner)
        child_delivery._report_uncertainty(job, child.append)
        child.append({"role": "user", "runtime_event": RUNTIME_EVENT,
                      "background_result_id": "resolved-job-terminal", "background_job_id": job["id"],
                      "content": [{"type": "text", "text": json.dumps({"job_id": job["id"],
                                   "state": "exited", "exit_code": 7, "cleanup_confirmed": True})}]})
        self.handoff(child, child_delivery.owner)
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))
        self.assertEqual(uncertainties(self.session.messages()), [])

    def test_launch_diagnostic_keeps_expectation_and_public_process_references(self):
        self.jobs = [self.job(launch_id="launch-1", launch_record_path=str(self.root / "launch.json"),
                              process_record_path=str(self.root / "jobs" / "compute.json"))]
        self.assertEqual(self.delivery().drain(self.session.append), (1, True))
        notice = payload(uncertainties(self.session.messages())[0])
        self.assertEqual(notice["launch_id"], "launch-1")
        self.assertEqual(notice["launch_record_path"], str(self.root / "launch.json"))
        self.assertEqual(notice["process_record_path"], str(self.root / "jobs" / "compute.json"))

    def test_verified_scope_clears_only_scope_blocker_across_restart(self):
        self.jobs = [self.job(identifier="invalid-scope")]
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.jobs = [{"id": "scope-verification", "state": "verified_scope", "_verification_only": True,
                      "resolved_uncertainty_ids": ["invalid-scope"]}]
        self.assertEqual(delivery.drain(self.session.append), (0, False))
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))
        self.assertEqual(results(self.session.messages()), [])
        self.jobs = [self.job(identifier="missing-job")]
        delivery.drain(self.session.append)
        self.jobs = [{"id": "scope-verification", "state": "verified_scope", "_verification_only": True,
                      "resolved_uncertainty_ids": ["invalid-scope"]}]
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertIn("missing-job", delivery._known)
        self.assertNotIn("invalid-scope", delivery._known)


if __name__ == "__main__":
    unittest.main()
