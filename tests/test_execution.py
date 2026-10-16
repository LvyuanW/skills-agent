"""Deadlines must bound work, cancellation, and cleanup without losing logs."""

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
                original_wait = execution._wait_for_completion
                handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

                def cancel_once(process, deadline):
                    if not seen:
                        seen.append(process)
                        raise cancellation
                    return original_wait(process, deadline)

                with patch.object(execution, "_wait_for_completion", side_effect=cancel_once):
                    with self.assertRaises(type(cancellation)) as caught:
                        self.run_python("import time; time.sleep(10)", cleanup=callback)
                self.assertIs(caught.exception, cancellation)
                self.assertIsNotNone(seen[0].returncode)
                callback.assert_called_once_with()
                self.assertEqual({sig: signal.getsignal(sig) for sig in handlers}, handlers)

    def test_backend_cleanup_failure_overrides_cancellation_and_retains_cause(self):
        failure = RuntimeError("container still running")
        callback = Mock(side_effect=failure)
        original_wait = execution._wait_for_completion
        seen = []

        def cancel_once(process, deadline):
            if not seen:
                seen.append(process)
                raise KeyboardInterrupt
            return original_wait(process, deadline)

        with patch.object(execution, "_wait_for_completion", side_effect=cancel_once):
            with self.assertRaisesRegex(execution.ToolCleanupError, "container still running") as caught:
                self.run_python("import time; time.sleep(10)", cleanup=callback)
        self.assertIs(caught.exception.__cause__, failure)
        self.assertIsNotNone(seen[0].returncode)
        callback.assert_called_once_with()


class CleanupDeadlineTests(unittest.TestCase):
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

    def test_failed_group_cleanup_never_uses_an_unbounded_final_wait(self):
        process = Mock(pid=123, returncode=None)
        process.wait.side_effect = subprocess.TimeoutExpired("test command", 5)
        with patch.object(execution, "_signal_group") as send_signal, \
                patch.object(execution, "_wait_for_completion", return_value=False):
            with self.assertRaisesRegex(execution.ToolCleanupError, "cleanup deadline"):
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


if __name__ == "__main__":
    unittest.main()
