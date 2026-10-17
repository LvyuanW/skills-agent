"""Lost launch output never reruns work or weakens registered-job ownership."""

import hashlib
import importlib.util
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.background import BackgroundResults, RUNTIME_EVENT
from skills_agent.processes import (
    BackgroundJobError, CALL_ENV, CONTEXT_ENV, HELPER, ProcessScope, SCOPE_ENV,
    _confirmed_launch, observe_background_jobs,
)
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry
from tests.test_agent_context import ScriptedModel, call, final


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX managed processes")
class BackgroundLaunchRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root, "launch-recovery")
        self.scope = ProcessScope(self.session.path)
        self.addCleanup(self.scope.close)
        self.owner = hashlib.sha256(str(self.session.path).encode()).hexdigest()
        # Never inherit the real Agent's registration scope when these tests
        # are themselves invoked through a managed shell.
        self.environment = {**os.environ, SCOPE_ENV: str(self.scope.path),
                            CONTEXT_ENV: self.owner, CALL_ENV: "launch-recovery-call"}
        self.real_run = subprocess.run
        self.launches = 0
        self.restores = []
        self.addCleanup(self.restore_records)

    def restore_records(self):
        while self.restores:
            path, data = self.restores.pop()
            path.write_bytes(data)

    def command(self, *, gated=False):
        script = self.root / f"payload-{self.launches}.py"
        counter = self.root / "executions.txt"
        gate = self.root / "release"
        script.write_text(
            "from pathlib import Path\nimport time\n"
            f"with Path({str(counter)!r}).open('a') as stream: stream.write('executed\\n')\n"
            + (f"deadline = time.monotonic() + 15\n"
               f"while not Path({str(gate)!r}).exists():\n"
               "    if time.monotonic() > deadline: raise RuntimeError('fixture gate not released')\n"
               "    time.sleep(.01)\n" if gated else "")
            + "print('payload complete', flush=True)\n",
            encoding="utf-8",
        )
        return shlex.join([sys.executable, str(script)])

    def start(self, command):
        return self.scope.start_background(command, "recovery fixture", self.root, self.environment)

    def authoritative_record(self):
        paths = list((self.scope.path / "runs").glob("*.json"))
        self.assertEqual(len(paths), 1)
        return paths[0], json.loads(paths[0].read_text())

    def wait_terminal(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            path, record = self.authoritative_record()
            if record["state"] in {"exited", "failed", "stopped", "timed_out"}:
                return path, record
            time.sleep(.01)
        self.fail("Fixture did not reach its terminal registration")

    def launch_wrapper(self, transform):
        def run(args, **kwargs):
            is_launch = (len(args) > 2 and args[1:3] == [str(HELPER), "start"])
            if is_launch:
                self.launches += 1
                result = self.real_run(args, **kwargs)
                return transform(result)
            return self.real_run(args, **kwargs)
        return run

    def assert_delivered_once(self, receipt):
        self.wait_terminal()
        delivery = BackgroundResults(self.session, lambda: [self.scope.path])
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(delivery.drain(self.session.append), (0, False))
        restarted = BackgroundResults(self.session, lambda: [self.scope.path])
        self.assertEqual(restarted.drain(self.session.append), (0, False))
        notices = [message for message in self.session.messages()
                   if message.get("runtime_event") == RUNTIME_EVENT]
        self.assertEqual(len(notices), 1)
        if receipt.get("status") == "unconfirmed":
            self.assertIn(receipt["job_id"], notices[0]["resolved_uncertainty_ids"])
        else:
            self.assertEqual(notices[0]["background_job_id"], receipt["job_id"])
        result = json.loads(notices[0]["content"][1]["text"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "payload complete\n")
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual((self.root / "executions.txt").read_text(), "executed\n")
        self.assertEqual(self.launches, 1)

    def test_empty_launch_output_recovers_live_job_and_delivers_completion_once(self):
        command = self.command(gated=True)
        with patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(
                lambda result: subprocess.CompletedProcess(result.args, result.returncode, "", result.stderr))):
            receipt = self.start(command)
        self.assertEqual(receipt["status"], "started")
        self.assertFalse(receipt["completed"])
        self.assertTrue(receipt["receipt_recovered"])
        jobs = observe_background_jobs([self.scope.path])
        self.assertEqual((jobs[0]["id"], jobs[0]["state"]), (receipt["job_id"], "running"))
        self.assertEqual(jobs[0]["owner_session_id"], self.owner)
        self.assertEqual(jobs[0]["origin_tool_call_id"], "launch-recovery-call")
        (self.root / "release").touch()
        self.assert_delivered_once(receipt)

    def test_agent_preserves_call_pair_and_waits_for_recovered_jobs_real_completion(self):
        command = self.command(gated=True)
        session = Session(self.root, "agent-integration")
        receipts = []

        def progress(system, messages, tools, on_event):
            paired = [message for message in messages
                      if message.get("role") == "tool" and message.get("tool_call_id") == "call-73"]
            self.assertEqual(len(paired), 1)
            receipt = json.loads(paired[0]["content"])
            self.assertTrue(receipt["receipt_recovered"])
            self.assertEqual(receipt["status"], "started")
            receipts.append(receipt)
            self.assertFalse(any(message.get("runtime_event") == RUNTIME_EVENT for message in messages))
            (self.root / "release").touch()
            return final("The computation is still running; I will inspect its result.")

        def completed(system, messages, tools, on_event):
            notices = [message for message in messages if message.get("runtime_event") == RUNTIME_EVENT]
            self.assertEqual(len(notices), 1)
            result = json.loads(notices[0]["content"][1]["text"])
            self.assertEqual((result["job_id"], result["origin_tool_call_id"], result["exit_code"]),
                             (receipts[0]["job_id"], "call-73", 0))
            self.assertEqual(result["stdout"], "payload complete\n")
            return final("Verified completed work")

        model = ScriptedModel([
            call("shell", {"name": "recovery integration", "command": command,
                           "run_in_background": True}, number=73),
            progress, completed,
        ])
        agent = Agent(model, session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        with patch.dict(os.environ, self.environment), \
                patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(
                    lambda result: subprocess.CompletedProcess(result.args, result.returncode, "", result.stderr))):
            self.assertEqual(agent.run({"role": "user", "content": "Run and verify the calculation"}),
                             "Verified completed work")
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(self.launches, 1)
        self.assertEqual((self.root / "executions.txt").read_text(), "executed\n")
        self.assertEqual([message["tool_call_id"] for message in session.messages()
                          if message.get("role") == "tool"], ["call-73"])
        self.assertEqual(sum(message.get("runtime_event") == RUNTIME_EVENT for message in session.messages()), 1)

    def test_corrupt_launch_output_recovers_same_registered_job_without_relaunch(self):
        command = self.command(gated=True)
        with patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(
                lambda result: subprocess.CompletedProcess(result.args, result.returncode, "{broken", ""))):
            receipt = self.start(command)
        self.assertTrue(receipt["receipt_recovered"])
        (self.root / "release").touch()
        self.assert_delivered_once(receipt)

    def test_launcher_timeout_after_registration_recovers_live_job_without_relaunch(self):
        command = self.command(gated=True)

        def timed_out(result):
            raise subprocess.TimeoutExpired(result.args, 12)

        with patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(timed_out)):
            receipt = self.start(command)
        self.assertEqual(receipt["status"], "started")
        self.assertTrue(receipt["receipt_recovered"])
        (self.root / "release").touch()
        self.assert_delivered_once(receipt)

    def test_fast_terminal_job_is_recovered_without_alias(self):
        command = self.command()

        def lost_output(result):
            _, record = self.wait_terminal()
            Path(record["record"]).unlink()
            return subprocess.CompletedProcess(result.args, 0, "", "")

        with patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(lost_output)):
            receipt = self.start(command)
        self.assertEqual(receipt["status"], "started")
        self.assertFalse(receipt["completed"])
        self.assertTrue(receipt["receipt_recovered"])
        self.assert_delivered_once(receipt)

    def test_terminated_real_launcher_recovers_registered_live_job(self):
        command = self.command(gated=True)
        waiting = self.root / "launcher-before-receipt"
        launcher = self.root / "interrupted-launcher.py"
        launcher.write_text(
            "import importlib.util, sys, time\nfrom pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('interrupted_helper', {str(HELPER)!r})\n"
            "helper = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(helper)\n"
            "def hold_receipt(*args, **kwargs):\n"
            f"    Path({str(waiting)!r}).touch()\n"
            "    time.sleep(15)\n"
            "helper.emit = hold_receipt\nsys.exit(helper.main())\n",
            encoding="utf-8",
        )

        def run(args, **kwargs):
            if len(args) <= 2 or args[1:3] != [str(HELPER), "start"]:
                return self.real_run(args, **kwargs)
            self.launches += 1
            process = subprocess.Popen([sys.executable, str(launcher), *args[2:]],
                                       env=kwargs["env"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not waiting.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(waiting.exists(), "Real launcher did not reach its pre-receipt checkpoint")
                # Only this held Popen child is signalled. The supervisor and
                # payload retain their ordinary independent ownership contract.
                process.terminate()
                stdout, stderr = process.communicate(timeout=2)
                self.assertEqual(stdout, "")
                self.assertLess(process.returncode, 0)
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=2)

        with patch("skills_agent.processes.subprocess.run", side_effect=run):
            receipt = self.start(command)
        self.assertTrue(receipt["receipt_recovered"])
        self.assertEqual(receipt["status"], "started")
        (self.root / "release").touch()
        self.assert_delivered_once(receipt)

    def test_registered_supervisor_spawn_failure_returns_receipt_and_one_terminal_result(self):
        spec = importlib.util.spec_from_file_location("launch_recovery_failed_spawn", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)

        def run(args, **kwargs):
            self.launches += 1
            parsed = helper.parser().parse_args(args[2:])
            with patch.dict(os.environ, kwargs["env"]), redirect_stdout(io.StringIO()), \
                    patch.object(helper.subprocess, "Popen", side_effect=OSError("fixture cannot spawn supervisor")):
                status = helper.start(parsed)
            return subprocess.CompletedProcess(args, status, "", "")

        with patch("skills_agent.processes.subprocess.run", side_effect=run):
            receipt = self.start("printf untouched")
        self.assertEqual((receipt["status"], receipt["launch_status"]), ("failed", "failed"))
        self.assertFalse(receipt["completed"])
        self.assertIn("fixture cannot spawn supervisor", receipt["error"])
        self.assertTrue(receipt["receipt_recovered"])
        delivery = BackgroundResults(self.session, lambda: [self.scope.path])
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(delivery.drain(self.session.append), (0, False))
        self.assertEqual(BackgroundResults(self.session, lambda: [self.scope.path]).drain(self.session.append),
                         (0, False))
        result = json.loads(self.session.messages()[0]["content"][1]["text"])
        self.assertEqual((result["job_id"], result["state"], result["exit_code"]),
                         (receipt["job_id"], "failed", 127))
        self.assertEqual((result["stdout"], result["stderr"]), ("", ""))
        self.assertTrue(result["cleanup_confirmed"])
        self.assertFalse((self.root / "executions.txt").exists())
        self.assertEqual(self.launches, 1)

    def starting_registration(self, *, owned=False):
        token, name, pid = "e" * 32, "identity-publication", 424242
        alias = self.scope.path / "jobs" / f"{name}.json"
        artifacts = alias.parent / f"{name}.{token}"
        scope_hash = hashlib.sha256(str(self.scope.path).encode()).hexdigest()
        identity = {"started": "Fri Oct 17 10:00:00 2026",
                    "command": f"python process.py _supervise --token {token} --scope-id {scope_hash}"}
        record = {"version": 1, "token": token, "name": name, "process_scope": str(self.scope.path),
                  "record": str(alias), "artifacts": str(artifacts), "stdout": str(artifacts / "stdout.log"),
                  "stderr": str(artifacts / "stderr.log"), "state": "starting", "returncode": None,
                  "supervisor_pid": pid if owned else None,
                  "supervisor_identity": identity if owned else None}
        path = self.scope.path / "runs" / f"{token}.json"
        path.write_text(json.dumps(record))
        # This fixture has no real process and must never enter scope cleanup.
        self.addCleanup(path.unlink, missing_ok=True)
        return path, record, pid, identity

    def test_identity_published_during_probe_is_reread_and_reprobed(self):
        path, record, pid, identity = self.starting_registration()

        def publish(records):
            if records[0]["supervisor_pid"] is None:
                record.update(state="running", supervisor_pid=pid, supervisor_identity=identity)
                path.write_text(json.dumps(record))
                return {}  # This probe only saw the initial no-PID snapshot.
            return {pid: identity}

        with patch("skills_agent.processes._supervisors", side_effect=publish) as probe:
            recovered = _confirmed_launch(self.scope.path, {"name": record["name"]}, time.monotonic() + 1)
        self.assertEqual((recovered["state"], recovered["supervisor_pid"]), ("running", pid))
        self.assertEqual(probe.call_count, 2)

    def test_owned_starting_supervisor_is_not_success_at_original_startup_deadline(self):
        _, record, pid, identity = self.starting_registration(owned=True)
        with patch("skills_agent.processes._supervisors", return_value={pid: identity}) as probe, \
                patch("skills_agent.processes.time.monotonic", return_value=17), \
                patch("skills_agent.processes.time.sleep") as sleep:
            with self.assertRaises(BackgroundJobError):
                _confirmed_launch(self.scope.path, {"name": record["name"]}, 17)
        probe.assert_called_once()
        sleep.assert_not_called()

    def test_helper_rejected_before_registration_returns_normal_tool_error(self):
        tools = ToolRegistry(self.root)
        tools.process_scope = self.scope
        tools.process_context = self.owner
        tools.tool_call_id = "launch-recovery-call"
        environment = patch.dict(os.environ, self.environment)
        with environment, patch("skills_agent.processes.subprocess.run", return_value=
                                subprocess.CompletedProcess([], 1, "", "helper could not initialize")) as run:
            result = json.loads(tools.execute("shell", json.dumps({
                "name": "rejected", "command": "printf untouched", "run_in_background": True,
            })))
        self.assertIn("error", result)
        self.assertNotIn("job_id", result)
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])
        run.assert_called_once()

    def test_launcher_errors_without_registration_are_normal_failures_without_retry(self):
        for error in (OSError("cannot spawn helper"), subprocess.TimeoutExpired("helper", 12)):
            with self.subTest(error=type(error).__name__), \
                    patch("skills_agent.processes.subprocess.run", side_effect=error) as run:
                with self.assertRaises(ValueError):
                    self.start("printf untouched")
            run.assert_called_once()
            self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])

    def assert_uncertain_launch(self, receipt):
        self.assertEqual(receipt["status"], "unconfirmed")
        self.assertEqual(receipt["state"], "unconfirmed")
        self.assertFalse(receipt["completed"])
        self.assertTrue(receipt["wait_on_exit"])
        self.assertFalse(receipt["cleanup_confirmed"])
        self.assertIsNone(receipt["exit_code"])
        self.assertIn("error", receipt)
        self.assertTrue(receipt["job_id"].startswith("launch-"))
        expectation = Path(receipt["_launch_path"])
        self.assertTrue(expectation.is_file())
        saved = json.loads(expectation.read_text())
        self.assertEqual(saved["id"], receipt["job_id"])
        self.assertEqual(saved["expected"]["command"], ["/bin/sh", "-c", self.started_command])
        self.assertEqual(self.launches, 1)

    def test_untrusted_terminal_registration_returns_durable_uncertainty_then_recovers(self):
        command = self.command()
        self.started_command = command

        def mismatch(result):
            path, record = self.wait_terminal()
            self.restores.append((path, path.read_bytes()))
            record["command"] = ["/bin/sh", "-c", "a different invocation"]
            path.write_text(json.dumps(record))
            return subprocess.CompletedProcess(result.args, 0, "", "")

        with patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(mismatch)):
            receipt = self.start(command)
        self.assert_uncertain_launch(receipt)
        self.assertIn("match", receipt["error"])
        self.restore_records()
        self.assert_delivered_once(receipt)

    def test_unconfirmed_terminal_cleanup_returns_error_and_gate_until_repaired(self):
        command = self.command()
        self.started_command = command

        def unconfirmed(result):
            path, record = self.wait_terminal()
            self.restores.append((path, path.read_bytes()))
            record["cleanup_confirmed"] = False
            path.write_text(json.dumps(record))
            return subprocess.CompletedProcess(result.args, 0, "", "")

        with patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(unconfirmed)):
            receipt = self.start(command)
        self.assert_uncertain_launch(receipt)
        jobs = [job for job in observe_background_jobs([self.scope.path], tolerate_unconfirmed=True)
                if not job.get("_verification_only")]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], "unconfirmed")
        self.assertIn(receipt["job_id"], jobs[0]["resolved_uncertainty_ids"])
        delivery = BackgroundResults(self.session, lambda: [self.scope.path])
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.restore_records()
        self.assert_delivered_once(receipt)

    def test_live_job_without_verified_supervisor_is_pending_until_repaired(self):
        command = self.command(gated=True)
        self.started_command = command

        def unowned(result):
            path, record = self.authoritative_record()
            self.restores.append((path, path.read_bytes()))
            record["supervisor_identity"] = None
            path.write_text(json.dumps(record))
            return subprocess.CompletedProcess(result.args, 0, "", "")

        with patch("skills_agent.processes.subprocess.run", side_effect=self.launch_wrapper(unowned)):
            receipt = self.start(command)
        self.assert_uncertain_launch(receipt)
        jobs = [job for job in observe_background_jobs([self.scope.path], tolerate_unconfirmed=True)
                if not job.get("_verification_only")]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], "unconfirmed")
        self.assertIn(receipt["job_id"], jobs[0]["resolved_uncertainty_ids"])
        self.assertEqual(BackgroundResults(self.session, lambda: [self.scope.path]).drain(self.session.append),
                         (1, True))
        self.restore_records()
        (self.root / "release").touch()
        self.assert_delivered_once(receipt)


if __name__ == "__main__":
    unittest.main()
