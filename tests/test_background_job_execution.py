import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from skills_agent.processes import (
    BackgroundJobError, CALL_ENV, CONTEXT_ENV, HELPER, SCOPE_ENV, ProcessScope,
    completion_result, observe_background_jobs,
)


spec = importlib.util.spec_from_file_location("background_execution_helper", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
class BackgroundJobExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.state = self.root / "state"
        self.scope = ProcessScope(self.root, allow_persistent=True)
        self.addCleanup(self.scope.close)
        self.environment = {**os.environ, SCOPE_ENV: str(self.scope.path),
                            CONTEXT_ENV: "owner-session", CALL_ENV: "start-call"}

    def cli(self, operation, name="worker", *extra):
        return subprocess.run([sys.executable, str(HELPER), operation, "--state-dir", str(self.state),
                               "--name", name, *extra], env=self.environment, text=True,
                              capture_output=True, timeout=8)

    def start(self, code, *extra, name="worker"):
        result = self.cli("start", name, *extra, "--", sys.executable, "-c", code)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)["process"]

    def finished(self, name="worker"):
        result = self.cli("wait", name, "--timeout", "5")
        self.assertIn(result.returncode, (0, 7, 124), result.stderr or result.stdout)
        jobs = observe_background_jobs([self.scope.path])
        return next(job for job in reversed(jobs) if job["name"] == name)

    def test_completion_carries_origin_and_keeps_complete_logs_with_bounded_tails(self):
        launched = self.start("import sys; print('a'*100000); print('last-error', file=sys.stderr); sys.exit(7)")
        job = self.finished()
        result = completion_result(job, max_bytes=128)
        self.assertEqual((job["kind"], job["wait_on_exit"], job["job_timeout_seconds"]), ("job", True, 3600))
        self.assertEqual(job["id"], launched["id"])
        self.assertEqual(result["origin_tool_call_id"], "start-call")
        self.assertEqual(result["owner_session_id"], "owner-session")
        self.assertEqual((result["exit_code"], result["process_returncode"]), (7, 7))
        self.assertTrue(result["stdout_truncated"])
        self.assertEqual(result["stdout_bytes"], 100001)
        self.assertEqual(len(result["stdout"]), 128)
        self.assertEqual(Path(result["stdout_path"]).stat().st_size, 100001)
        self.assertEqual(result["stderr"], "last-error\n")
        self.assertTrue(result["cleanup_confirmed"])

    def test_independent_deadline_kills_term_ignoring_process_group(self):
        descendant = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('child-ready',flush=True); time.sleep(30)"
        code = ("import subprocess,sys,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                f"p=subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
                "print('parent-ready',p.pid,flush=True); time.sleep(30)")
        started = time.monotonic()
        self.start(code, "--job-timeout", ".4")
        job = self.finished()
        result = completion_result(job)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(job["state"], "timed_out")
        self.assertEqual(result["exit_code"], 124)
        self.assertEqual(result["process_returncode"], -signal.SIGKILL)
        self.assertEqual(result["stop_reason"], "timeout")
        self.assertTrue(result["forced"])
        self.assertTrue(result["cleanup_confirmed"])
        self.assertIn("child-ready", result["stdout"])
        self.assertEqual(helper.group_members(job["child_pid"]), [])

    def test_service_is_distinct_and_wait_timeout_does_not_stop_it(self):
        self.start("import time; time.sleep(30)", "--service", "--job-timeout", "7200", "--persistent")
        wait = self.cli("wait", "worker", "--timeout", ".05")
        self.assertEqual(wait.returncode, 124)
        job = observe_background_jobs([self.scope.path])[0]
        self.assertEqual((job["kind"], job["wait_on_exit"], job["job_timeout_seconds"]), ("service", False, 7200))
        self.assertEqual(job["state"], "running")
        self.assertFalse(job["timed_out"])
        stopped = self.cli("stop", "worker", "--grace", ".1", "--timeout", "3")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        result = completion_result(observe_background_jobs([self.scope.path])[0])
        self.assertEqual(result["stop_reason"], "requested")
        self.assertFalse(result["timed_out"])

    def test_service_has_no_implicit_job_deadline(self):
        record = self.start("import time; time.sleep(30)", "--service")
        self.assertIsNone(record["job_timeout_seconds"])
        self.assertIsNone(record["deadline_at"])

    def test_timeout_validation_and_persistence_policy(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                result = self.cli("start", "bad", "--job-timeout", value, "--", sys.executable, "-c", "pass")
                self.assertNotEqual(result.returncode, 0)
        result = self.cli("start", "bad", "--persistent", "--", sys.executable, "-c", "pass")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --service", result.stderr)
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])

    def test_name_reuse_keeps_distinct_results_and_scope_success_survives_attach(self):
        self.start("print('first')")
        first = self.finished()
        self.start("print('second')")
        second = self.finished()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(completion_result(first)["stdout"], "first\n")
        self.assertEqual(completion_result(second)["stdout"], "second\n")
        self.scope.mark_successful()
        restored = ProcessScope.attach(self.scope.path)
        self.assertEqual(restored.path, self.scope.path)
        self.assertTrue(restored.successful)

    def test_completion_rejects_fifo_and_symlink_without_blocking(self):
        self.start("print('output')")
        job = self.finished()
        path = Path(job["stdout_path"])
        path.unlink()
        os.mkfifo(path)
        before = time.monotonic()
        with self.assertRaises(BackgroundJobError):
            completion_result(job)
        with self.assertRaises(ValueError):
            helper.tail(path, 100)
        self.assertLess(time.monotonic() - before, .5)
        path.unlink()
        path.symlink_to(self.root / "outside")
        with self.assertRaises(BackgroundJobError):
            completion_result(job)

    def test_strict_observer_rejects_policy_and_exit_status_corruption(self):
        self.start("pass")
        job = self.finished()
        path = Path(job["_record_path"])
        original = json.loads(path.read_text())
        cases = ({"wait_on_exit": False}, {"kind": "unknown"}, {"owner_session_id": []},
                 {"returncode": None}, {"returncode": True}, {"cleanup_confirmed": False},
                 {"state": "unknown"}, {"exit_code": []})
        for change in cases:
            with self.subTest(change=change):
                path.write_text(json.dumps({**original, **change}))
                try:
                    with self.assertRaises(BackgroundJobError):
                        observe_background_jobs([self.scope.path])
                    with self.assertRaises(BackgroundJobError):
                        completion_result(job)
                finally:
                    path.write_text(json.dumps(original))

    def test_strict_observer_rejects_missing_supervisor_identity(self):
        self.start("import time; time.sleep(30)")
        with patch("skills_agent.processes._supervisors", return_value={}):
            with self.assertRaisesRegex(BackgroundJobError, "ownership"):
                observe_background_jobs([self.scope.path])

    def test_start_failure_still_produces_a_reliable_empty_completion(self):
        result = self.cli("start", "missing", "--", str(self.root / "nonexistent-command"))
        self.assertEqual(result.returncode, 127)
        job = observe_background_jobs([self.scope.path])[0]
        result = completion_result(job)
        self.assertEqual(result["exit_code"], 127)
        self.assertEqual((result["stdout"], result["stderr"]), ("", ""))
        self.assertTrue(result["cleanup_confirmed"])

    def test_foreign_scope_alias_cannot_be_recovered_observed_or_closed(self):
        foreign = ProcessScope(self.root / "foreign")
        self.addCleanup(foreign.close)
        alias = self.root / "local" / "process-scopes" / "alias"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(foreign.path, target_is_directory=True)
        with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
            ProcessScope.attach(alias)
        with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
            observe_background_jobs([alias])
        stale = ProcessScope.__new__(ProcessScope)
        stale.path, stale.successful = alias, False
        with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
            stale.close()
        self.assertFalse((foreign.path / "closed").exists())

    def test_symlinked_parent_and_runs_are_not_scope_ownership(self):
        foreign = ProcessScope(self.root / "foreign")
        self.addCleanup(foreign.close)
        local = self.root / "local"
        local.mkdir()
        (local / "process-scopes").symlink_to(foreign.path.parent, target_is_directory=True)
        alias = local / "process-scopes" / foreign.path.name
        with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
            ProcessScope.attach(alias)
        with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
            observe_background_jobs([alias])
        with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
            ProcessScope(local)
        own = ProcessScope(self.root / "other")
        (own.path / "runs").rmdir()
        (own.path / "runs").symlink_to(foreign.path / "runs", target_is_directory=True)
        try:
            with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
                ProcessScope.attach(own.path)
            with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
                observe_background_jobs([own.path])
            with self.assertRaisesRegex(BackgroundJobError, "symbolic"):
                own.close()
            self.assertFalse((foreign.path / "closed").exists())
        finally:
            (own.path / "runs").unlink()
            (own.path / "runs").mkdir()
            own.close()


class BackgroundSupervisorBoundaryTests(unittest.TestCase):
    def fixture(self, root):
        root = root.resolve()
        name, token = "job", "a" * 32
        artifacts = root / f"{name}.{token}"
        artifacts.mkdir()
        path = root / f"{name}.json"
        record = {"version": 1, "name": name, "token": token, "record": str(path), "artifacts": str(artifacts),
                  "state": "starting", "command": ["unused"], "cwd": str(root), "returncode": None,
                  "stdout": str(artifacts / "stdout.log"), "stderr": str(artifacts / "stderr.log"),
                  "job_timeout_seconds": .001, "timed_out": False, "cleanup_confirmed": False}
        helper.save_record(path, record)
        return path, argparse.Namespace(record=str(path), token=token, scope_id=None)

    def test_already_completed_command_is_not_reclassified_when_observation_is_late(self):
        with tempfile.TemporaryDirectory() as directory:
            path, args = self.fixture(Path(directory))
            child = Mock(pid=123, returncode=None)
            child.wait.return_value = 0
            with patch.object(helper, "pid_identity", return_value={"started": "now", "command": "supervisor"}), \
                    patch.object(helper.signal, "signal"), patch.object(helper.subprocess, "Popen", return_value=child), \
                    patch.object(helper, "peek_exit", return_value=True), patch.object(helper, "group_members", return_value=[]), \
                    patch.object(helper.time, "monotonic", side_effect=[0, 2]), patch.object(helper, "signal_group") as signal_group:
                self.assertEqual(helper.supervise(args), 0)
            record = json.loads(path.read_text())
            self.assertEqual(record["state"], "exited")
            self.assertFalse(record["timed_out"])
            signal_group.assert_not_called()

    def test_cleanup_failure_never_becomes_a_terminal_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path, args = self.fixture(Path(directory))
            child = Mock(pid=123, returncode=None)
            with patch.object(helper, "pid_identity", return_value={"started": "now", "command": "supervisor"}), \
                    patch.object(helper.signal, "signal"), patch.object(helper.subprocess, "Popen", return_value=child), \
                    patch.object(helper, "peek_exit", side_effect=RuntimeError("probe failed")), \
                    patch.object(helper, "kill_and_confirm", side_effect=RuntimeError("cleanup unconfirmed")):
                with self.assertRaisesRegex(RuntimeError, "probe failed"):
                    helper.supervise(args)
            record = json.loads(path.read_text())
            self.assertEqual(record["state"], "unknown")
            self.assertFalse(record["cleanup_confirmed"])
            self.assertIsNone(record["returncode"])
            self.assertIn("cleanup unconfirmed", record["error"])

    def test_reaped_child_is_never_signalled_or_waited_again(self):
        child = Mock(pid=123, returncode=0)
        with patch.object(helper.os, "killpg") as kill, patch.object(helper, "peek_exit") as peek:
            helper.signal_group(child, signal.SIGKILL)
            self.assertEqual(helper.kill_and_confirm(child), 0)
        kill.assert_not_called()
        peek.assert_not_called()
        child.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
