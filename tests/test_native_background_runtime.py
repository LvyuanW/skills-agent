import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from skills_agent.processes import (
    BackgroundJobError, CALL_ENV, CONTEXT_ENV, HELPER, SCOPE_ENV, ProcessScope,
    completion_result, observe_background_jobs,
)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
class NativeBackgroundRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.scope = ProcessScope(self.root)
        self.addCleanup(self.scope.close)
        self.environment = {**os.environ, CONTEXT_ENV: "owner-session", CALL_ENV: "shell-call"}

    def launch(self, code, *, name="后台数据处理", timeout=None, lifecycle="task"):
        return self.scope.start_background(shlex.join([sys.executable, "-c", code]), name,
                                           self.root, self.environment, timeout,
                                           background_lifecycle=lifecycle)

    def enable_services(self):
        self.scope = ProcessScope(self.root / "persistent", allow_persistent=True)
        self.addCleanup(self.scope.close)

    def finished(self, receipt):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            job = next(job for job in observe_background_jobs([self.scope.path])
                       if job["id"] == receipt["job_id"])
            if job["state"] in {"exited", "failed", "stopped", "timed_out"}:
                return job, completion_result(job)
            time.sleep(.02)
        self.fail("Managed background job did not finish")

    def helper(self, receipt, operation, *extra):
        return subprocess.run([sys.executable, str(HELPER), operation, "--state-dir", receipt["state_dir"],
                               "--name", receipt["name"], *extra], capture_output=True, text=True, timeout=5)

    def test_receipt_returns_before_completion_and_environment_and_logs_survive(self):
        gate = self.root / "release"
        code = ("from pathlib import Path\nimport os,time\n"
                f"while not Path({str(gate)!r}).exists(): time.sleep(.02)\n"
                f"print(os.environ[{CONTEXT_ENV!r}])\nprint(os.getcwd())\n")
        receipt = self.launch(code)
        self.assertEqual(receipt["launch_status"], "started")
        self.assertFalse(receipt["completed"])
        self.assertEqual(receipt["state"], "running")
        self.assertEqual((receipt["kind"], receipt["wait_on_exit"], receipt["background_lifecycle"], receipt["persistent"]),
                         ("job", True, "task", False))
        self.assertEqual((receipt["job_timeout_seconds"], receipt["startup_timeout_seconds"]), (3600, 10))
        for key in ("process_record_path", "stdout_path", "stderr_path", "state_dir"):
            self.assertTrue(Path(receipt[key]).is_absolute(), key)
        self.assertEqual(json.loads(self.helper(receipt, "status").stdout)["process"]["state"], "running")
        gate.touch()
        job, result = self.finished(receipt)
        self.assertEqual(result["stdout"], f"owner-session\n{self.root}\n")
        self.assertEqual(result["origin_tool_call_id"], "shell-call")
        self.assertEqual(result["owner_session_id"], "owner-session")
        self.assertEqual(result["job_id"], receipt["job_id"])
        self.assertFalse(result["cleanup_required"])
        self.assertTrue(result["cleanup_confirmed"])
        logs = json.loads(self.helper(receipt, "logs").stdout)
        self.assertIn("owner-session", logs["observations"]["logs"]["stdout"]["tail"])

    def test_fast_failure_is_a_started_job_with_a_separate_failure_result(self):
        receipt = self.launch("import sys; print('failed detail', file=sys.stderr); sys.exit(7)")
        self.assertEqual(receipt["status"], "started")
        self.assertFalse(receipt["completed"])
        _, result = self.finished(receipt)
        self.assertEqual((result["exit_code"], result["process_returncode"]), (7, 7))
        self.assertEqual(result["stderr"], "failed detail\n")

    def test_job_deadline_is_independent_and_cleans_the_group(self):
        receipt = self.launch("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)", timeout=.2)
        _, result = self.finished(receipt)
        self.assertEqual((result["state"], result["exit_code"]), ("timed_out", 124))
        self.assertEqual(result["process_returncode"], -signal.SIGKILL)
        self.assertTrue(result["cleanup_required"])
        self.assertTrue(result["cleanup_forced"])
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual(result["cleanup_reason"], "timeout")

    def test_main_exit_records_term_and_kill_cleanup_without_hiding_zero(self):
        for ignore in (False, True):
            with self.subTest(ignore_term=ignore):
                child = ("import signal,time; " +
                         ("signal.signal(signal.SIGTERM,signal.SIG_IGN); " if ignore else "") +
                         "print('child-ready',flush=True); time.sleep(30)")
                parent = ("import subprocess,sys; "
                          f"p=subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.PIPE,text=True); "
                          "print(p.stdout.readline().strip(),flush=True)")
                receipt = self.launch(parent)
                job, result = self.finished(receipt)
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(result["process_returncode"], 0)
                self.assertTrue(result["cleanup_required"])
                self.assertTrue(result["cleanup_confirmed"])
                self.assertEqual(result["cleanup_forced"], ignore)
                self.assertEqual(result["cleanup_reason"], "main_process_exited")
                self.assertGreaterEqual(result["cleanup_process_count"], 1)
                self.assertIn("does not mean all work completed naturally", result["cleanup_message"])
                self.assertIn("child-ready", result["stdout"])
                probe = subprocess.run(["ps", "-e", "-o", "pgid=", "-o", "stat="], capture_output=True, text=True)
                self.assertFalse(any(parts[0] == str(job["child_pid"]) and not parts[1].startswith("Z")
                                     for line in probe.stdout.splitlines() if len(parts := line.split()) == 2))

    def test_arbitrary_display_names_reuse_safely_and_long_deadlines_are_allowed(self):
        label = "分析 / 中文 task 1 🧪"
        first = self.launch("print('one')", name=label)
        second = self.launch("print('two')", name=label, timeout=7200)
        self.assertEqual(first["display_name"], label)
        self.assertNotEqual(first["name"], second["name"])
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertRegex(first["name"], r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
        _, result = self.finished(first)
        self.assertEqual(result["display_name"], label)
        _, result = self.finished(second)
        self.assertEqual(result["job_timeout_seconds"], 7200)

    def test_existing_management_cli_can_stop_runtime_started_jobs(self):
        receipt = self.launch("import time; time.sleep(30)")
        stopped = self.helper(receipt, "stop", "--grace", ".1", "--timeout", "3")
        self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
        _, result = self.finished(receipt)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["stop_reason"], "requested")
        self.assertTrue(result["cleanup_confirmed"])

    def test_start_requires_an_open_registered_scope_and_valid_deadline(self):
        for timeout in (True, 0, -1, float("nan"), float("inf"), "1", 10 ** 1000):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "positive finite"):
                self.launch("pass", timeout=timeout)
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])
        fake = ProcessScope.__new__(ProcessScope)
        fake.path = self.root / "unregistered"
        with self.assertRaises(BackgroundJobError):
            fake.start_background("true", "bad", self.root, self.environment)
        self.scope.close()
        with self.assertRaisesRegex(BackgroundJobError, "closed"):
            self.launch("pass")
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])

    def test_scope_environment_cannot_be_overridden_by_a_caller(self):
        self.environment[SCOPE_ENV] = str(self.root / "not-a-scope")
        receipt = self.launch(f"import os; print(os.environ[{SCOPE_ENV!r}])")
        _, result = self.finished(receipt)
        self.assertEqual(result["stdout"].strip(), str(self.scope.path))

    def test_service_policy_rejects_before_registering_or_starting(self):
        with self.assertRaisesRegex(ValueError, "services are not allowed"):
            self.launch("import time; time.sleep(30)", lifecycle="service")
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])
        self.assertFalse((self.scope.path / "jobs").exists())
        with self.assertRaisesRegex(ValueError, "background_lifecycle"):
            self.launch("pass", lifecycle="detached")

    def test_service_defaults_to_no_deadline_and_survives_successful_scope_exit(self):
        self.enable_services()
        receipt = self.launch("import time; print('service-ready',flush=True); time.sleep(30)", lifecycle="service")
        self.assertEqual((receipt["kind"], receipt["wait_on_exit"], receipt["persistent"], receipt["background_lifecycle"]),
                         ("service", False, True, "service"))
        self.assertIsNone(receipt["job_timeout_seconds"])
        job = observe_background_jobs([self.scope.path])[0]
        self.assertIsNone(job["job_timeout_seconds"])
        self.assertIsNone(job["deadline_at"])
        self.assertEqual(job["background_lifecycle"], "service")
        self.scope.mark_successful()
        self.scope.close(preserve_persistent=self.scope.successful)
        job = observe_background_jobs([self.scope.path])[0]
        self.assertEqual(job["state"], "running")
        stopped = self.helper(receipt, "stop", "--grace", ".1", "--timeout", "3")
        self.assertEqual(stopped.returncode, 0, stopped.stderr or stopped.stdout)
        _, result = self.finished(receipt)
        self.assertEqual(result["background_lifecycle"], "service")
        self.assertTrue(result["persistent"])
        self.assertFalse(result["wait_on_exit"])

    def test_service_has_explicit_deadline_when_requested(self):
        self.enable_services()
        receipt = self.launch("import time; time.sleep(30)", lifecycle="service", timeout=.2)
        self.assertEqual(receipt["job_timeout_seconds"], .2)
        _, result = self.finished(receipt)
        self.assertEqual(result["background_lifecycle"], "service")
        self.assertEqual(result["state"], "timed_out")
        self.assertEqual(result["exit_code"], 124)
        self.assertTrue(result["cleanup_confirmed"])
        for timeout in (True, 0, -1, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "positive finite"):
                self.launch("pass", lifecycle="service", timeout=timeout)

    def test_failed_or_cancelled_scope_cleans_service_instead_of_preserving_it(self):
        self.enable_services()
        receipt = self.launch("import time; time.sleep(30)", lifecycle="service")
        self.scope.close()
        _, result = self.finished(receipt)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["stop_reason"], "scope_cleanup")
        self.assertTrue(result["cleanup_required"])
        self.assertTrue(result["cleanup_confirmed"])

    def test_legacy_records_derive_lifecycle_from_kind(self):
        self.enable_services()
        receipt = self.launch("print('done')", lifecycle="service")
        job, _ = self.finished(receipt)
        record_path = Path(job["_record_path"])
        record = json.loads(record_path.read_text())
        record.pop("background_lifecycle")
        record_path.write_text(json.dumps(record))
        job = observe_background_jobs([self.scope.path])[0]
        self.assertEqual(job["background_lifecycle"], "service")
        self.assertEqual(completion_result(job)["background_lifecycle"], "service")

    def test_identity_probe_race_uses_same_run_terminal_record(self):
        receipt = self.launch("print('done')")
        job, _ = self.finished(receipt)
        terminal = json.loads(Path(receipt["process_record_path"]).read_text())
        stale = {**terminal, "state": "running", "ended_at": None, "returncode": None}
        spec = importlib.util.spec_from_file_location("native_background_helper", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with patch.object(helper, "supervisor_owned", return_value=False):
            observed = helper.observed_record(stale)
            self.assertEqual(observed["state"], "exited")
            self.assertEqual(observed["returncode"], 0)
            self.assertEqual(observed["id"], job["id"])
            # A reused public alias must not confirm completion of an older run.
            stale["token"] = "f" * 32
            self.assertEqual(helper.observed_record(stale)["state"], "orphaned")

    def test_owner_cleanup_stops_failed_child_services_and_keeps_parent_scope_open(self):
        self.enable_services()
        parent_owner = "parent-session"
        child_owner = "failed child / ../ 中文"
        self.environment[CONTEXT_ENV] = parent_owner
        parent = self.launch("import time; time.sleep(30)", lifecycle="service", name="parent service")
        self.environment[CONTEXT_ENV] = child_owner
        child = self.launch("import time; time.sleep(30)", lifecycle="service", name="failed child service")
        self.scope.close_owner(child_owner)
        self.assertFalse((self.scope.path / "closed").exists())
        marker = self.scope.path / "closed-owners" / hashlib.sha256(child_owner.encode()).hexdigest()
        self.assertTrue(marker.is_file())
        self.assertEqual(list(marker.parent.iterdir()), [marker])
        _, result = self.finished(child)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["stop_reason"], "owner_cleanup")
        self.assertTrue(result["cleanup_confirmed"])
        parent_job = next(job for job in observe_background_jobs([self.scope.path]) if job["id"] == parent["job_id"])
        self.assertEqual(parent_job["state"], "running")
        with self.assertRaisesRegex(BackgroundJobError, "owner.*closed"):
            self.launch("print('late child must not start')", lifecycle="service")
        self.assertEqual(len(list((self.scope.path / "runs").glob("*.json"))), 2)
        # Other owners can still register and complete new work in this scope.
        self.environment[CONTEXT_ENV] = parent_owner
        later = self.launch("print('parent work')")
        _, result = self.finished(later)
        self.assertEqual(result["stdout"], "parent work\n")
        self.scope.mark_successful()
        self.scope.close(preserve_persistent=True)
        parent_job = next(job for job in observe_background_jobs([self.scope.path]) if job["id"] == parent["job_id"])
        self.assertEqual(parent_job["state"], "running")
        self.assertEqual(self.helper(parent, "stop", "--grace", ".1", "--timeout", "3").returncode, 0)

    def test_owner_can_be_closed_before_its_first_start(self):
        self.scope.close_owner(self.environment[CONTEXT_ENV])
        self.assertFalse((self.scope.path / "closed").exists())
        with self.assertRaisesRegex(BackgroundJobError, "owner.*closed"):
            self.launch("print('must not start')")
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])

    def test_owner_cleanup_errors_are_fatal_background_errors(self):
        for owner in ("", "  ", None, True):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                self.scope.close_owner(owner)
        failed = subprocess.CompletedProcess([], 1, stdout="cleanup could not be confirmed", stderr="")
        with patch("skills_agent.processes.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(BackgroundJobError, "could not be confirmed"):
                self.scope.close_owner("failed-child")
        with patch("skills_agent.processes.subprocess.run", side_effect=subprocess.TimeoutExpired("cleanup", 7)):
            with self.assertRaisesRegex(BackgroundJobError, "cleanup was not confirmed"):
                self.scope.close_owner("failed-child")

    def test_cleanup_identity_probe_race_requires_same_registration_and_confirmed_terminal(self):
        receipt = self.launch("print('done')")
        job, _ = self.finished(receipt)
        record_path = Path(job["_record_path"])
        terminal = json.loads(record_path.read_text())
        stale = {**terminal, "state": "running", "ended_at": None, "returncode": None,
                 "cleanup_confirmed": False}
        spec = importlib.util.spec_from_file_location("cleanup_background_helper", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        original_load = helper.load_json
        for owner in (None, self.environment[CONTEXT_ENV]):
            for changed in (False, True):
                with self.subTest(owner=owner, changed=changed):
                    reads = 0

                    def racing_load(path):
                        nonlocal reads
                        value = original_load(path)
                        if Path(path) == record_path:
                            reads += 1
                            if reads == 1:
                                return stale
                            if changed:
                                return {**value, "token": "0" * 32}
                        return value

                    args = argparse.Namespace(scope=str(self.scope.path), owner_session_id=owner,
                                              preserve_persistent=False)
                    output = io.StringIO()
                    with patch.object(helper, "load_json", side_effect=racing_load), \
                            patch.object(helper, "supervisor_owned", return_value=False), redirect_stdout(output):
                        result = helper.cleanup(args)
                    self.assertGreaterEqual(reads, 2)
                    self.assertEqual(result, 1 if changed else 0, output.getvalue())
                    if changed:
                        self.assertIn("Ownership registration changed", output.getvalue())

    def test_startup_confirmation_has_ten_seconds_and_distinct_twelve_second_transport_bound(self):
        with patch("skills_agent.processes.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("background launch", 12)) as launch:
            with self.assertRaisesRegex(BackgroundJobError, "launch could not be confirmed"):
                self.launch("print('work')", timeout=7200)
        args = launch.call_args.args[0]
        self.assertEqual(args[args.index("--startup-timeout") + 1], "10")
        self.assertEqual(args[args.index("--job-timeout") + 1], "7200")
        self.assertEqual(launch.call_args.kwargs["timeout"], 12)
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])
        spec = importlib.util.spec_from_file_location("startup_background_helper", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        parsed = helper.parser().parse_args(["start", "--state-dir", str(self.root), "--name", "test", "--", "true"])
        self.assertEqual(parsed.startup_timeout, 10)

    def test_legacy_starting_record_gets_ten_second_confirmation_window(self):
        receipt = self.launch("print('done')")
        job, _ = self.finished(receipt)
        path = Path(job["_record_path"])
        original = path.read_text()
        record = json.loads(original)
        record.update(state="starting", supervisor_pid=None, returncode=None, cleanup_confirmed=False,
                      created_at=(datetime.now(timezone.utc) - timedelta(seconds=9)).isoformat())
        record.pop("startup_timeout_seconds")
        path.write_text(json.dumps(record))
        try:
            observed = observe_background_jobs([self.scope.path])
            self.assertEqual(observed[0]["state"], "starting")
        finally:
            path.write_text(original)


if __name__ == "__main__":
    unittest.main()
