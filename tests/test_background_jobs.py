"""UI observations are transient; explicit services can outlive a completed turn."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from skills_agent.agent import Agent
from skills_agent.processes import BackgroundJobMonitor, HELPER, ProcessScope
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final


class BackgroundJobObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scope = ProcessScope(self.root)
        self.events = []
        self.monitor = BackgroundJobMonitor(self.root, self.events.append)
        self.addCleanup(self.monitor.close)

    def record(self, **overrides):
        token = uuid4().hex
        started = datetime.now(timezone.utc) - timedelta(seconds=20)
        scope_id = hashlib.sha256(str(self.scope.path).encode()).hexdigest()
        command = f"python process.py _supervise --token {token} --scope-id {scope_id}"
        record = {"version": 1, "token": token, "process_scope": str(self.scope.path),
                  "name": "extract", "state": "running", "supervisor_pid": 45678,
                  "child_pid": 12345, "returncode": None, "persistent": False,
                  "created_at": started.isoformat(), "started_at": started.isoformat(),
                  "supervisor_identity": {"started": "Fri Oct 16 12:00:00 2026", "command": command},
                  "command": ["PRIVATE_COMMAND"], "error": "PRIVATE_ERROR", **overrides}
        path = self.scope.path / "runs" / f"{token}.json"
        path.write_text(json.dumps(record))
        return path, record

    def test_only_registered_jobs_are_observed_without_exposing_commands_or_tokens(self):
        self.monitor.refresh()
        self.assertEqual(self.events, [])
        path, record = self.record()
        foreign, value = self.record(name="foreign")
        value["process_scope"] = "/another/session"
        foreign.write_text(json.dumps(value))
        before = path.read_bytes()
        with patch("skills_agent.processes._supervisors", return_value={45678: record["supervisor_identity"]}):
            self.monitor.refresh()
            self.monitor.refresh()
        self.assertEqual(len(self.events), 1)
        self.assertEqual([job["name"] for job in self.events[0]["jobs"]], ["extract"])
        encoded = json.dumps(self.events)
        for private in (record["token"], str(self.scope.path), "PRIVATE_COMMAND", "PRIVATE_ERROR"):
            self.assertNotIn(private, encoded)
        self.assertEqual(path.read_bytes(), before)

    def test_exit_status_and_elapsed_time_freeze_after_completion(self):
        path, record = self.record()
        with patch("skills_agent.processes._supervisors", return_value={45678: record["supervisor_identity"]}):
            self.monitor.refresh()
        first = self.events[-1]["jobs"][0]
        ended = datetime.fromisoformat(record["started_at"]) + timedelta(seconds=8)
        record.update(state="exited", returncode=7, ended_at=ended.isoformat(), cleanup_confirmed=True)
        path.write_text(json.dumps(record))
        self.monitor.refresh()
        self.monitor.refresh()
        self.assertEqual(len(self.events), 2)
        job = self.events[-1]["jobs"][0]
        self.assertEqual((job["state"], job["returncode"], job["elapsed_seconds"]), ("exited", 7, 8))
        self.assertEqual(job["id"], first["id"])

    def test_normal_exit_during_identity_probe_does_not_flash_orphaned(self):
        path, record = self.record()

        def exited(records):
            latest = {**record, "state": "exited", "returncode": 0, "cleanup_confirmed": True,
                      "ended_at": datetime.now(timezone.utc).isoformat()}
            path.write_text(json.dumps(latest))
            return {}

        with patch("skills_agent.processes._supervisors", side_effect=exited):
            self.monitor.refresh()
        job = self.events[-1]["jobs"][0]
        self.assertEqual((job["state"], job["returncode"]), ("exited", 0))
        self.assertFalse(self.monitor._pending)

    def test_absent_or_unverifiable_supervisor_never_claims_running_elapsed_time(self):
        self.record()
        for identities, expected in (({}, "orphaned"), (None, "unknown")):
            with self.subTest(state=expected), patch("skills_agent.processes._supervisors", return_value=identities):
                self.monitor.refresh()
                job = self.events[-1]["jobs"][0]
                self.assertEqual(job["state"], expected)
                self.assertIsNone(job["elapsed_seconds"])

    def test_confirmed_start_failure_and_reused_names_keep_distinct_jobs(self):
        self.record(state="failed", returncode=127, ended_at=datetime.now(timezone.utc).isoformat())
        self.record(state="failed", returncode=1, ended_at=datetime.now(timezone.utc).isoformat())
        with patch("skills_agent.processes.subprocess.run") as probe:
            self.monitor.refresh()
        probe.assert_not_called()
        jobs = self.events[-1]["jobs"]
        self.assertEqual([job["name"] for job in jobs], ["extract", "extract"])
        self.assertEqual(len({job["id"] for job in jobs}), 2)
        self.assertEqual({job["returncode"] for job in jobs}, {1, 127})

    def test_invalid_fields_and_unreadable_records_do_not_break_observation(self):
        self.record(state=[], supervisor_pid={})
        bad, _ = self.record()
        bad.write_text("{")
        self.monitor.refresh()
        self.assertEqual(len(self.events[-1]["jobs"]), 1)
        self.assertEqual(self.events[-1]["jobs"][0]["state"], "unknown")
        with patch.object(self.monitor, "_snapshot", side_effect=OSError("unreadable")):
            self.monitor.start_turn()
            self.monitor.end_turn()
            self.monitor.close()
        self.assertIsNone(self.monitor._thread)

    def test_display_exception_does_not_escape_refresh_or_close(self):
        self.record(state="failed", returncode=127)
        with patch.object(self.monitor, "on_event", side_effect=RuntimeError("display closed")):
            self.monitor.refresh()
            self.monitor.close()

    def test_ps_checks_only_supervisor_identity_in_one_bounded_query(self):
        _, record = self.record()
        identity = record["supervisor_identity"]
        output = f"45678 {identity['started']} {identity['command']}\n"
        with patch("skills_agent.processes.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")) as probe:
            self.monitor.refresh()
        self.assertEqual(self.events[-1]["jobs"][0]["state"], "running")
        argv = probe.call_args.args[0]
        self.assertEqual(argv[argv.index("-p") + 1], "45678")
        self.assertNotIn("12345", argv)
        self.assertEqual(probe.call_args.kwargs["timeout"], 1)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX managed processes")
class BackgroundJobAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root, "session")

    def command(self, name, *, service=False):
        gate = self.root / f"{name}.gate"
        program = (f"from pathlib import Path; import time; gate=Path({str(gate)!r}); "
                   "exec('while not gate.exists(): time.sleep(.02)'); raise SystemExit(7)")
        return shlex.join([sys.executable, str(HELPER), "start", "--state-dir", str(self.root / "state"),
                           "--name", name, *(["--service"] if service else []),
                           "--", sys.executable, "-c", program])

    def test_streaming_and_idle_turns_receive_live_state_without_history_events(self):
        events, later = [], []
        running, ended = threading.Event(), threading.Event()

        def receive(event):
            events.append(deepcopy(event))
            if event.get("type") == "background_jobs" and any(job["state"] == "running" for job in event["jobs"]):
                running.set()

        def streaming(system, messages, tools, on_event):
            on_event({"type": "text_delta", "text": "Streaming while extraction runs"})
            self.assertTrue(running.wait(4), "A model call must not block background observations")
            return final("Started")

        def interrupted(*args):
            raise KeyboardInterrupt

        model = ScriptedModel([call("shell", {"name": "start", "command": self.command("extract", service=True)}),
                               streaming, interrupted])
        agent = Agent(model, self.session, SkillRegistry([]), self.root, on_event=receive)
        self.addCleanup(agent.close)
        self.assertEqual(agent.run(self.session.make_user_message("Start extraction", [])), "Started")
        self.assertIsNotNone(agent._background_jobs._thread)
        self.assertTrue(agent._background_jobs._thread.is_alive())
        with self.assertRaises(KeyboardInterrupt):
            agent.run(self.session.make_user_message("An interrupted follow-up", []))
        self.assertTrue(agent._background_jobs._thread.is_alive(), "Prior successful turns still own live jobs")

        def switched(event):
            later.append(deepcopy(event))
            if event.get("type") == "background_jobs" and any(job["returncode"] == 7 for job in event["jobs"]):
                ended.set()

        agent.on_event = switched
        (self.root / "extract.gate").touch()
        self.assertTrue(ended.wait(4), "Jobs must remain observable between CLI turns through the current listener")
        agent.close()
        self.assertIsNone(agent._background_jobs._thread)
        self.assertFalse(any(event.get("type") == "background_jobs" for event in self.session.events()))
        self.assertTrue(any(event.get("type") == "background_jobs" for event in later))

    def test_subagent_jobs_share_parent_scope_and_only_one_monitor(self):
        events = []
        model = ScriptedModel([
            call("subagent", {"name": "child", "task": "Start an ongoing service"}),
            call("shell", {"name": "start", "command": self.command("child-job", service=True)}, number=2),
            final("Child started"), final("Parent finished"),
        ])
        with patch("skills_agent.agent.BackgroundJobMonitor", wraps=BackgroundJobMonitor) as monitors:
            agent = Agent(model, self.session, SkillRegistry([]), self.root, on_event=events.append)
            self.addCleanup(agent.close)
            agent.run(self.session.make_user_message("Delegate extraction", []))
        self.assertEqual(monitors.call_count, 1)
        jobs = [event for event in events if event.get("type") == "background_jobs"][-1]["jobs"]
        self.assertEqual((jobs[0]["name"], jobs[0]["state"]), ("child-job", "running"))
        agent.close()
        self.assertIsNone(agent._background_jobs._thread)

    def test_close_publishes_cleanup_terminal_state_and_leaves_no_monitor(self):
        events = []
        model = ScriptedModel([call("shell", {"name": "start", "command": self.command("close-job", service=True)}), final("Started")])
        agent = Agent(model, self.session, SkillRegistry([]), self.root, on_event=events.append)
        self.addCleanup(agent.close)
        agent.run(self.session.make_user_message("Start a job", []))
        agent.close()
        jobs = [event for event in events if event.get("type") == "background_jobs"][-1]["jobs"]
        self.assertEqual((jobs[0]["state"], jobs[0]["returncode"]), ("stopped", -15))
        self.assertIsNone(agent._background_jobs._thread)


if __name__ == "__main__":
    unittest.main()
