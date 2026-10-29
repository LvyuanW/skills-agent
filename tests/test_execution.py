"""Deadlines must bound work, cancellation, and cleanup without losing logs."""

import errno
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from skills_agent import execution


class TimeoutValidationTests(unittest.TestCase):
    def test_accepts_finite_positive_numbers_within_host_limit(self):
        for value in (0.01, 1, 120, 600, 600.0):
            with self.subTest(value=value):
                self.assertEqual(execution.validate_timeout(value), value)

    def test_rejects_invalid_numbers_including_booleans_and_oversized_integers(self):
        for value in (True, False, 0, -1, math.nan, math.inf, -math.inf,
                      600.01, 10 ** 1000, "1", None, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                    execution.validate_timeout(value)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.logs = self.root / "logs"

    def run_python(self, source, **kwargs):
        return execution.run_command([sys.executable, "-u", "-c", source],
                                     cwd=self.root, log_dir=self.logs, **kwargs)

    def test_normal_exit_preserves_full_output_exit_code_and_raw_logs(self):
        callback = Mock()
        result = self.run_python(
            "import os, sys; "
            "os.write(1, b'full stdout\\xff\\r\\nnext\\rline'); os.write(2, b'full stderr'); sys.exit(7)",
            cleanup=callback,
        )
        self.assertEqual(result, {"exit_code": 7, "stdout": "full stdout\ufffd\nnext\nline", "stderr": "full stderr"})
        callback.assert_not_called()
        self.assertEqual(next(self.logs.glob("*/stdout.log")).read_bytes(), b"full stdout\xff\r\nnext\rline")
        self.assertEqual(next(self.logs.glob("*/stderr.log")).read_bytes(), b"full stderr")

    def test_command_gets_workspace_environment_and_closed_stdin(self):
        result = self.run_python(
            "import os, sys; print(os.getcwd()); print(os.environ['EXECUTION_TEST_VALUE']); "
            "print(repr(sys.stdin.read()))", env={**os.environ, "EXECUTION_TEST_VALUE": "provided"},
        )
        self.assertEqual(result, {"exit_code": 0, "stdout": f"{self.root}\nprovided\n''\n", "stderr": ""})

    def test_normal_exit_retries_a_transient_inspection_timeout_without_signaling(self):
        inspect_group = execution._group_members
        wait_for_main = execution._wait_for_main_exit
        observations = []
        processes = []
        callback = Mock()

        def slow_first_probe(pgid, deadline):
            observations.append((pgid, deadline))
            if len(observations) == 1:
                raise subprocess.TimeoutExpired("ps", execution._PROBE_TIMEOUT_SECONDS)
            return inspect_group(pgid, deadline)

        def remember_process(process, deadline):
            processes.append(process)
            return wait_for_main(process, deadline)

        with patch.object(execution, "_group_members", side_effect=slow_first_probe), \
                patch.object(execution, "_wait_for_main_exit", side_effect=remember_process), \
                patch.object(execution, "_signal_group", wraps=execution._signal_group) as send_signal:
            try:
                result = self.run_python(
                    "import sys; print('complete output'); print('complete error', file=sys.stderr); sys.exit(7)",
                    cleanup=callback,
                )
            finally:
                # Reap this short-lived fixture even when the regression is present.
                for process in processes:
                    if process.returncode is None:
                        process.wait(timeout=1)

        self.assertEqual(result, {
            "exit_code": 7, "stdout": "complete output\n", "stderr": "complete error\n",
        })
        self.assertGreaterEqual(len(observations), 2)
        self.assertLessEqual(max(deadline for _, deadline in observations), observations[0][1])
        send_signal.assert_not_called()
        callback.assert_not_called()
        self.assertEqual(next(self.logs.glob("*/stdout.log")).read_text(), "complete output\n")
        self.assertEqual(next(self.logs.glob("*/stderr.log")).read_text(), "complete error\n")

    def test_timeout_returns_evidence_and_runs_backend_cleanup_once(self):
        callback = Mock()
        started = time.monotonic()
        result = self.run_python(
            "import sys, time; print('partial stdout'); print('partial stderr', file=sys.stderr); time.sleep(10)",
            timeout_seconds=0.3, cleanup=callback,
        )
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result["exit_code"], 124)
        self.assertEqual(result["process_returncode"], -signal.SIGTERM)
        self.assertIs(result["timed_out"], True)
        self.assertEqual(result["timeout_seconds"], 0.3)
        self.assertGreaterEqual(result["duration_seconds"], 0.3)
        self.assertEqual(result["stdout"], "partial stdout\n")
        self.assertEqual(result["stderr"], "partial stderr\n")
        self.assertFalse(result["stdout_truncated"])
        self.assertFalse(result["stderr_truncated"])
        for stream in ("stdout", "stderr"):
            path = Path(result[f"{stream}_path"])
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.is_relative_to(self.logs))
            self.assertEqual(path.read_text(), result[stream])
        callback.assert_called_once_with()

    def test_timeout_bounds_result_size_but_preserves_complete_streams_on_disk(self):
        length = execution.TIMEOUT_OUTPUT_BYTES + 100
        result = self.run_python(
            f"import os, time; os.write(1, b'a' * {length} + b'out-tail'); "
            f"os.write(2, b'b' * {length} + b'err-tail'); time.sleep(10)",
            timeout_seconds=0.3,
        )
        for stream, prefix, suffix in (("stdout", b"a", b"out-tail"), ("stderr", b"b", b"err-tail")):
            self.assertTrue(result[f"{stream}_truncated"])
            self.assertEqual(len(result[stream]), execution.TIMEOUT_OUTPUT_BYTES)
            self.assertTrue(result[stream].endswith(suffix.decode()))
            self.assertEqual(Path(result[f"{stream}_path"]).read_bytes(), prefix * length + suffix)

    def test_start_failure_does_not_run_cleanup_for_a_nonexistent_process(self):
        callback = Mock()
        with self.assertRaises(FileNotFoundError):
            execution.run_command([str(self.root / "nonexistent-command")], cwd=self.root,
                                  log_dir=self.logs, cleanup=callback)
        callback.assert_not_called()

    def test_cancellation_cleans_up_and_restores_handlers_before_propagation(self):
        for cancellation in (KeyboardInterrupt(), SystemExit(12)):
            with self.subTest(cancellation=type(cancellation).__name__):
                callback = Mock()
                seen = []
                original_wait = execution._wait_for_main_exit
                handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

                def cancel_once(process, deadline):
                    if not seen:
                        seen.append(process)
                        raise cancellation
                    return original_wait(process, deadline)

                with patch.object(execution, "_wait_for_main_exit", side_effect=cancel_once):
                    with self.assertRaises(type(cancellation)) as caught:
                        self.run_python("import time; time.sleep(10)", cleanup=callback)
                self.assertIs(caught.exception, cancellation)
                self.assertIsNotNone(seen[0].returncode)
                callback.assert_called_once_with()
                self.assertEqual({sig: signal.getsignal(sig) for sig in handlers}, handlers)

    def test_backend_cleanup_failure_overrides_cancellation_and_retains_cause(self):
        failure = RuntimeError("container still running")
        callback = Mock(side_effect=failure)
        original_wait = execution._wait_for_main_exit
        seen = []

        def cancel_once(process, deadline):
            if not seen:
                seen.append(process)
                raise KeyboardInterrupt
            return original_wait(process, deadline)

        with patch.object(execution, "_wait_for_main_exit", side_effect=cancel_once):
            with self.assertRaisesRegex(execution.ToolCleanupError, "container still running") as caught:
                self.run_python("import time; time.sleep(10)", cleanup=callback)
        self.assertIs(caught.exception.__cause__, failure)
        self.assertIsNotNone(seen[0].returncode)
        callback.assert_called_once_with()


class CleanupDeadlineTests(unittest.TestCase):
    def test_normal_exit_inspection_retries_with_one_bounded_deadline(self):
        process = Mock(pid=123, returncode=None)
        observed_deadlines = []

        def unavailable(pgid, deadline):
            observed_deadlines.append(deadline)
            raise subprocess.TimeoutExpired("ps", 0.01)

        started = time.monotonic()
        with patch.object(execution, "CLEANUP_TIMEOUT_SECONDS", 0.06), \
                patch.object(execution, "_group_members", side_effect=unavailable):
            with self.assertRaises(subprocess.TimeoutExpired):
                execution._finish_main_exit(process)
        self.assertGreater(len(observed_deadlines), 1)
        self.assertEqual(len(set(observed_deadlines)), 1)
        self.assertLess(time.monotonic() - started, 0.8)
        process.wait.assert_not_called()

    def test_unstopped_probe_is_not_cleared_by_a_later_empty_group(self):
        process = Mock(pid=123, returncode=None)
        probe_error = execution.ToolCleanupError("Process inspection could not be stopped")
        with patch.object(execution, "_signal_group"), \
                patch.object(execution, "_wait_for_completion", side_effect=[probe_error, True]):
            with self.assertRaises(execution.ToolCleanupError) as caught:
                execution._stop_process(process)
        self.assertIn("inspection could not be stopped", str(caught.exception))
        self.assertFalse(caught.exception.diagnostics["cleanup_confirmed"])

    def test_reaped_group_is_never_signalled(self):
        with patch.object(execution.os, "killpg") as kill:
            execution._signal_group(Mock(pid=123, returncode=0), signal.SIGKILL)
        kill.assert_not_called()

    def test_expired_inspection_is_an_unfinished_phase_not_cleanup_failure(self):
        process = Mock()
        with patch.object(execution, "_finished", side_effect=[subprocess.TimeoutExpired("ps", 0.01), True]):
            self.assertTrue(execution._wait_for_completion(process, time.monotonic() + 1))
        self.assertGreater(process.wait.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(process.wait.call_args.kwargs["timeout"], 1)

    def test_inspection_timeout_has_a_bounded_kill_and_reap_attempt(self):
        probe = Mock()
        probe.wait.side_effect = subprocess.TimeoutExpired("ps", 0.01)
        with patch.object(execution.subprocess, "Popen", return_value=probe):
            with self.assertRaisesRegex(execution.ToolCleanupError, "inspection could not be stopped"):
                execution._group_members(123, time.monotonic() + 1)
        probe.kill.assert_called_once_with()
        self.assertEqual(probe.wait.call_count, 2)
        for call in probe.wait.call_args_list:
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], execution._PROBE_TIMEOUT_SECONDS)

    def test_failed_probe_signal_still_attempts_reap_and_remains_fatal_when_unconfirmed(self):
        probe = Mock(pid=321, returncode=None)
        probe.wait.side_effect = subprocess.TimeoutExpired("ps", 0.01)
        probe.kill.side_effect = PermissionError(errno.EPERM, "probe signal denied")
        with patch.object(execution.subprocess, "Popen", return_value=probe):
            with self.assertRaises(execution.ToolCleanupError) as caught:
                execution._group_members(123, time.monotonic() + 1)
        self.assertEqual(probe.wait.call_count, 2)
        details = caught.exception.diagnostics
        self.assertEqual(details["original_error"]["type"], "TimeoutExpired")
        self.assertEqual([e["phase"] for e in details["cleanup_errors"]],
                         ["inspection_kill", "inspection_reap"])
        self.assertEqual(details["cleanup_errors"][0]["errno"], errno.EPERM)
        json.dumps(details)

    def test_probe_signal_error_is_resolved_only_when_probe_exit_is_confirmed(self):
        timeout = subprocess.TimeoutExpired("ps", 0.01)
        probe = Mock(pid=321, returncode=None)
        probe.wait.side_effect = [timeout, 0]
        probe.kill.side_effect = PermissionError(errno.EPERM, "probe already exited")
        with patch.object(execution.subprocess, "Popen", return_value=probe):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                execution._group_members(123, time.monotonic() + 1)
        self.assertIs(caught.exception, timeout)
        self.assertEqual(probe.wait.call_count, 2)

    def test_failed_group_cleanup_never_uses_an_unbounded_final_wait(self):
        process = Mock(pid=123, returncode=None)
        process.wait.side_effect = subprocess.TimeoutExpired("test command", 5)
        with patch.object(execution, "_signal_group") as send_signal, \
                patch.object(execution, "_wait_for_completion", return_value=False):
            with self.assertRaisesRegex(execution.ToolCleanupError, "cleanup could not be confirmed"):
                execution._stop_process(process)
        self.assertIn(unittest.mock.call(process, signal.SIGTERM), send_signal.call_args_list)
        self.assertIn(unittest.mock.call(process, signal.SIGKILL), send_signal.call_args_list)
        process.wait.assert_called_once()
        self.assertGreater(process.wait.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(process.wait.call_args.kwargs["timeout"], execution.CLEANUP_TIMEOUT_SECONDS)

    def test_backend_cleanup_still_runs_after_group_cleanup_fails(self):
        failure = execution.ToolCleanupError("group remains")
        callback = Mock()
        with patch.object(execution, "_stop_process", side_effect=failure):
            with self.assertRaisesRegex(execution.ToolCleanupError, "group remains") as caught:
                execution._cleanup(Mock(), callback)
        self.assertIs(caught.exception.__cause__, failure)
        callback.assert_called_once_with()


class ProcessPermissionCleanupTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("TERM_GRACE_SECONDS", 0.02), ("CLEANUP_TIMEOUT_SECONDS", 0.2),
                            ("_POLL_SECONDS", 0.005)):
            setting = patch.object(execution, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def child(self):
        process = Mock(pid=123, returncode=None)

        def reap(*, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, execution.CLEANUP_TIMEOUT_SECONDS)
            process.returncode = 0
            return 0

        process.wait.side_effect = reap
        return process

    def test_last_member_exits_between_observation_and_term_permission_error(self):
        process = self.child()
        live_members = [124]
        observations = []

        def members(pgid, deadline):
            observations.append(list(live_members))
            return list(live_members)

        def signal_after_exit(child, signum):
            self.assertIsNone(child.returncode, "The group ID must still be reserved")
            self.assertEqual(signum, signal.SIGTERM)
            live_members.clear()
            raise PermissionError(errno.EPERM, "only zombies remain")

        with patch.object(execution, "_peek_exit", return_value=True), \
                patch.object(execution, "_group_members", side_effect=members), \
                patch.object(execution, "_signal_group", side_effect=signal_after_exit) as send_signal:
            result = execution._finish_main_exit(process)

        self.assertEqual(observations[0], [124])
        self.assertIn([], observations, "EPERM requires a confirmed empty live group")
        self.assertIs(result["cleanup_confirmed"], True)
        send_signal.assert_called_once_with(process, signal.SIGTERM)
        process.wait.assert_called_once()
        self.assertEqual(process.returncode, 0)

    def test_last_member_exits_before_kill_permission_error(self):
        process = self.child()
        live_members = [124]
        sent = []

        def send_signal(child, signum):
            self.assertIsNone(child.returncode, "Never signal a reaped process group")
            sent.append(signum)
            if signum == signal.SIGKILL:
                live_members.clear()
                raise PermissionError(errno.EPERM, "group became zombie-only before KILL")

        with patch.object(execution, "_peek_exit", return_value=True), \
                patch.object(execution, "_group_members", side_effect=lambda *_: list(live_members)), \
                patch.object(execution, "_signal_group", side_effect=send_signal):
            execution._stop_process(process)

        self.assertEqual(sent, [signal.SIGTERM, signal.SIGKILL])
        process.wait.assert_called_once()
        self.assertEqual(process.returncode, 0)

    def test_permission_error_is_not_success_without_confirmed_group_completion(self):
        for case in ("live_member", "inspection_unavailable", "entry_still_running"):
            with self.subTest(case=case):
                process = self.child()

                def members(*_):
                    if case == "inspection_unavailable":
                        raise subprocess.TimeoutExpired("ps", 0.01)
                    return [124] if case == "live_member" else []

                started = time.monotonic()
                with patch.object(execution, "_peek_exit", return_value=case != "entry_still_running"), \
                        patch.object(execution, "_group_members", side_effect=members), \
                        patch.object(execution, "_signal_group", side_effect=PermissionError(errno.EPERM, "denied")):
                    with self.assertRaises(execution.ToolCleanupError):
                        execution._stop_process(process)
                self.assertLess(time.monotonic() - started, 1.5)

    def test_repeated_signal_errors_preserve_diagnostics_and_do_not_skip_reaping_or_backend_cleanup(self):
        process = self.child()
        callback = Mock()
        sent = []

        def deny_signal(child, signum):
            self.assertIsNone(child.returncode, "Never signal after the leader is reaped")
            sent.append(signum)
            message = "TERM permission denied" if signum == signal.SIGTERM else "KILL permission denied"
            raise PermissionError(errno.EPERM, message)

        with patch.object(execution, "_peek_exit", return_value=True), \
                patch.object(execution, "_group_members", return_value=[124]), \
                patch.object(execution, "_signal_group", side_effect=deny_signal):
            with self.assertRaises(execution.ToolCleanupError) as caught:
                execution._cleanup(process, callback)

        self.assertIn(signal.SIGTERM, sent)
        self.assertIn(signal.SIGKILL, sent)
        process.wait.assert_called_once()
        callback.assert_called_once_with()
        details = caught.exception.diagnostics["cleanup_errors"]
        json.dumps(details)
        for expected in ("TERM permission denied", "KILL permission denied"):
            matches = [item for item in details if expected in item["message"]]
            self.assertTrue(matches, (expected, details))
            self.assertTrue(any(item["type"] == "PermissionError" and item.get("errno") == errno.EPERM
                                for item in matches))
            for item in matches:
                self.assertTrue(item["phase"])
                self.assertIn(expected, item["traceback"])


if __name__ == "__main__":
    unittest.main()
