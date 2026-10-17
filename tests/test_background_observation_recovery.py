"""Real persisted jobs remain diagnosable after ownership observations fail."""

import hashlib
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
from skills_agent.background import BackgroundResults, RUNTIME_EVENT, UNCERTAINTY_EVENT
from skills_agent.processes import (
    BackgroundJobError, CALL_ENV, CONTEXT_ENV, HELPER, ProcessScope, SCOPE_ENV,
    observe_background_jobs,
)
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final


def notices(messages, event):
    return [message for message in messages if message.get("runtime_event") == event]


def payload(message):
    return json.loads(message["content"][1]["text"])


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX managed processes")
class BackgroundObservationRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root, "observation-recovery")
        self.scope = ProcessScope(self.session.path, allow_persistent=True)
        self.addCleanup(self.scope.close)
        self.owner = hashlib.sha256(str(self.session.path).encode()).hexdigest()
        self.environment = {**os.environ, SCOPE_ENV: str(self.scope.path),
                            CONTEXT_ENV: self.owner, CALL_ENV: "observed-launch"}
        # Never register fixture work in a surrounding Agent's real scope.
        environment = patch.dict(os.environ, self.environment)
        environment.start()
        self.addCleanup(environment.stop)
        self.originals = {}
        self.addCleanup(self.restore)

    def restore(self):
        for path, data in list(self.originals.items()):
            path.write_bytes(data)
            del self.originals[path]

    def keep(self, path):
        self.originals.setdefault(path, path.read_bytes())

    def command(self, *, gated=False):
        gate = self.root / "release"
        script = self.root / "payload.py"
        script.write_text(
            "from pathlib import Path\nimport time\n"
            + (f"deadline = time.monotonic() + 15\n"
               f"while not Path({str(gate)!r}).exists():\n"
               "    if time.monotonic() > deadline: raise RuntimeError('gate not released')\n"
               "    time.sleep(.01)\n" if gated else "")
            + "print('verified output', flush=True)\n",
            encoding="utf-8",
        )
        return shlex.join([sys.executable, str(script)])

    def start(self, *, gated=False, service=False):
        receipt = self.scope.start_background(
            self.command(gated=gated), "observed calculation", self.root, self.environment,
            background_lifecycle="service" if service else "task",
        )
        paths = list((self.scope.path / "runs").glob("*.json"))
        self.assertEqual(len(paths), 1)
        return receipt, paths[0]

    def terminal(self, path):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = json.loads(path.read_text())
            if record["state"] in {"exited", "failed", "stopped", "timed_out"}:
                return record
            time.sleep(.01)
        self.fail("Real fixture did not publish its terminal record")

    def delivery(self):
        return BackgroundResults(self.session, lambda: [self.scope.path])

    def observed_jobs(self):
        return [job for job in observe_background_jobs([self.scope.path], tolerate_unconfirmed=True)
                if not job.get("_verification_only")]

    def test_corrupt_registration_is_pending_then_repaired_result_delivers_once(self):
        receipt, path = self.start()
        self.terminal(path)
        self.keep(path)
        path.write_text("{broken registration")
        with self.assertRaises(BackgroundJobError):
            observe_background_jobs([self.scope.path])
        jobs = self.observed_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]["id"], jobs[0]["state"]), (receipt["job_id"], "unconfirmed"))
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        self.assertEqual(notices(self.session.messages(), RUNTIME_EVENT), [])
        self.restore()
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))
        result = payload(notices(self.session.messages(), RUNTIME_EVENT)[0])
        self.assertEqual((result["exit_code"], result["stdout"]), (0, "verified output\n"))

    def test_unverified_live_supervisor_keeps_task_pending_until_real_exit(self):
        receipt, path = self.start(gated=True)
        self.keep(path)
        record = json.loads(path.read_text())
        record["supervisor_identity"] = None
        path.write_text(json.dumps(record))
        jobs = self.observed_jobs()
        self.assertEqual((jobs[0]["id"], jobs[0]["state"]), (receipt["job_id"], "unconfirmed"))
        self.assertIn("ownership", jobs[0]["error"])
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.restore()
        self.assertEqual(delivery.drain(self.session.append), (0, True))
        (self.root / "release").touch()
        self.terminal(path)
        self.assertEqual(delivery.drain(self.session.append), (1, False))
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))

    def test_known_service_uncertainty_does_not_become_task_completion_gate(self):
        _, path = self.start(gated=True, service=True)
        self.keep(path)
        record = json.loads(path.read_text())
        record["supervisor_identity"] = None
        path.write_text(json.dumps(record))
        jobs = self.observed_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], "unconfirmed")
        self.assertFalse(jobs[0]["wait_on_exit"])
        self.assertEqual(self.delivery().drain(self.session.append), (1, False))
        self.restore()
        (self.root / "release").touch()
        self.terminal(path)

    def test_exit_zero_without_cleanup_confirmation_is_not_completion(self):
        _, path = self.start()
        self.terminal(path)
        self.keep(path)
        record = json.loads(path.read_text())
        record.pop("cleanup_confirmed")
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(BackgroundJobError, "cleanup"):
            observe_background_jobs([self.scope.path])
        delivery = self.delivery()
        self.assertEqual(delivery.drain(self.session.append), (1, True))
        self.assertEqual(notices(self.session.messages(), RUNTIME_EVENT), [])
        self.restore()
        self.assertEqual(delivery.drain(self.session.append), (1, False))

    def test_repaired_launch_directory_clears_only_directory_diagnostic_on_restart(self):
        for kind in ("symlink", "file"):
            with self.subTest(kind=kind):
                session = Session(self.root, f"directory-repair-{kind}")
                scope = ProcessScope(session.path)
                self.addCleanup(scope.close)
                directory = scope.path / "launches"
                if kind == "symlink":
                    foreign = self.root / "foreign-launches"
                    foreign.mkdir()
                    directory.symlink_to(foreign, target_is_directory=True)
                else:
                    directory.write_text("not a directory")
                delivery = BackgroundResults(session, lambda: [scope.path])
                self.assertEqual(delivery.drain(session.append), (1, True))
                directory.unlink()
                directory.mkdir()
                self.assertEqual(delivery.drain(session.append), (0, False))
                restarted = BackgroundResults(session, lambda: [scope.path])
                self.assertEqual(restarted.drain(session.append), (0, False))
                self.assertEqual(notices(session.messages(), RUNTIME_EVENT), [])

    def test_agent_diagnoses_uncertain_start_after_no_tool_reply_then_gets_actual_exit(self):
        command = self.command()
        launched = []
        real_run = subprocess.run
        repair_source = self.root / "repair-source.json"
        repair_script = self.root / "repair.py"
        repair_script.write_text(
            "from pathlib import Path\nimport json, sys\n"
            f"data = Path({str(repair_source)!r}).read_bytes()\n"
            "record = json.loads(data)\n"
            "# Restore the authoritative run and its helper alias together.\n"
            "(Path(record['process_scope']) / 'runs' / (record['token'] + '.json')).write_bytes(data)\n"
            "Path(sys.argv[1]).write_bytes(data)\n",
            encoding="utf-8",
        )

        def uncertain_launch(args, **kwargs):
            if len(args) <= 2 or args[1:3] != [str(HELPER), "start"]:
                return real_run(args, **kwargs)
            launched.append(args)
            result = real_run(args, **kwargs)
            scope_path = Path(kwargs["env"][SCOPE_ENV])
            path, = (scope_path / "runs").glob("*.json")
            record = self.terminal(path)
            repair_source.write_text(json.dumps(record))
            record["cleanup_confirmed"] = False
            path.write_text(json.dumps(record))
            return subprocess.CompletedProcess(args, result.returncode, "", "")

        def premature_answer(system, messages, tools, on_event):
            tool_result = next(message for message in messages if message.get("role") == "tool")
            content = tool_result["content"]
            if isinstance(content, list):
                content = next(block["text"] for block in content if block["type"] == "text")
            receipt = json.loads(content)
            self.assertEqual(receipt["status"], "unconfirmed")
            self.assertFalse(receipt["completed"])
            self.assertIn("cleanup", receipt["error"].lower())
            self.assertTrue(notices(messages, UNCERTAINTY_EVENT))
            return final("The computation is complete.")

        def diagnose(system, messages, tools, on_event):
            observations = notices(messages, UNCERTAINTY_EVENT)
            self.assertGreaterEqual(len(observations), 2)
            target = payload(observations[-1])["process_record_path"]
            return call("shell", {"name": "Confirm registered cleanup", "command":
                                  shlex.join([sys.executable, str(repair_script), target])}, number=2)

        def completed(system, messages, tools, on_event):
            results = notices(messages, RUNTIME_EVENT)
            self.assertEqual(len(results), 1)
            result = payload(results[0])
            self.assertEqual((result["exit_code"], result["stdout"]), (0, "verified output\n"))
            return final("Verified registered exit and cleanup.")

        model = ScriptedModel([
            call("shell", {"name": "Uncertain launch", "command": command, "run_in_background": True}),
            premature_answer, diagnose, completed,
        ])
        agent = Agent(model, self.session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        with patch("skills_agent.processes.subprocess.run", side_effect=uncertain_launch):
            self.assertEqual(agent.run({"role": "user", "content": "Run and verify"}),
                             "Verified registered exit and cleanup.")
        self.assertEqual(len(launched), 1)
        self.assertEqual(len(model.calls), 4)
        self.assertEqual([message["tool_call_id"] for message in self.session.messages()
                          if message.get("role") == "tool"], ["call-1", "call-2"])
        self.assertEqual(len(notices(self.session.messages(), RUNTIME_EVENT)), 1)

    def test_invalid_scope_does_not_abort_constructor_and_can_be_repaired_by_tool(self):
        receipt, record_path = self.start()
        self.terminal(record_path)
        scope_path = self.scope.path / "scope.json"
        self.keep(scope_path)
        saved = self.root / "scope-original.json"
        saved.write_bytes(scope_path.read_bytes())
        scope_path.write_text("{damaged scope")

        def diagnose(system, messages, tools, on_event):
            errors = notices(messages, UNCERTAINTY_EVENT)
            self.assertGreaterEqual(len(errors), 1)
            self.assertTrue(all(payload(error)["wait_on_exit"] for error in errors))
            self.assertTrue(any("scope" in payload(error)["error"].lower() for error in errors))
            return call("shell", {"name": "Restore scope metadata", "command":
                                  shlex.join([sys.executable, "-c",
                                              "from pathlib import Path; import sys; "
                                              "Path(sys.argv[1]).write_bytes(Path(sys.argv[2]).read_bytes())",
                                              str(scope_path), str(saved)])})

        def completed(system, messages, tools, on_event):
            result, = notices(messages, RUNTIME_EVENT)
            self.assertEqual(payload(result)["job_id"], receipt["job_id"])
            return final("Scope and completed result recovered.")

        model = ScriptedModel([diagnose, completed])
        agent = Agent(model, self.session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        self.assertEqual(agent.run({"role": "user", "content": "Diagnose pending work"}),
                         "Scope and completed result recovered.")
        self.assertEqual(len(model.calls), 2)
        self.originals.pop(scope_path)

    def test_repairing_scope_header_cannot_release_a_deleted_run_gate(self):
        receipt, record_path = self.start()
        self.terminal(record_path)
        self.keep(record_path)
        scope_path = self.scope.path / "scope.json"
        self.keep(scope_path)
        scope_path.write_text("{damaged scope")
        delivery = self.delivery()
        delivered, pending = delivery.drain(self.session.append)
        self.assertGreaterEqual(delivered, 1)
        self.assertTrue(pending)
        record_path.unlink()
        scope_path.write_bytes(self.originals.pop(scope_path))
        delivered, pending = delivery.drain(self.session.append)
        self.assertGreaterEqual(delivered, 1)
        self.assertTrue(pending)
        self.assertEqual(set(delivery._known), {receipt["job_id"]})
        self.assertEqual(notices(self.session.messages(), RUNTIME_EVENT), [])
        # The missing run remains unresolved across a session restart too.
        restarted = self.delivery()
        self.assertEqual(restarted.drain(self.session.append), (0, True))
        self.assertEqual(set(restarted._known), {receipt["job_id"]})
        self.restore()
        self.assertEqual(restarted.drain(self.session.append), (1, False))
        self.assertEqual(self.delivery().drain(self.session.append), (0, False))


if __name__ == "__main__":
    unittest.main()
