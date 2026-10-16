import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from skills_agent.processes import HELPER, ProcessScope


spec = importlib.util.spec_from_file_location("process_helper_start_tests", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "Managed POSIX processes")
class ProcessStartFailureTests(unittest.TestCase):
    def test_missing_ps_fails_before_registering_and_scope_close_stays_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scope = ProcessScope(root)
            state = root / "state"
            environment = {**os.environ, "PATH": str(root / "empty"),
                           "SKILLS_AGENT_PROCESS_SCOPE": str(scope.path)}
            result = subprocess.run([sys.executable, str(HELPER), "start", "--state-dir", str(state),
                                     "--name", "missing-ps", "--", sys.executable, "-c", "pass"],
                                    capture_output=True, text=True, env=environment, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires ps", result.stderr)
            self.assertFalse((state / "missing-ps.json").exists())
            self.assertFalse(list((scope.path / "runs").glob("*.json")))
            scope.close()

    def test_supervisor_identity_failure_is_saved_before_any_child_is_spawned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "worker.json"
            token = "a" * 32
            artifacts = root / ("worker." + token)
            artifacts.mkdir()
            scope = ProcessScope(root)
            record = {"version": 1, "name": "worker", "token": token, "state": "starting",
                      "record": str(path), "artifacts": str(artifacts), "supervisor_pid": None,
                      "child_pid": None, "returncode": None, "process_scope": str(scope.path), "persistent": False}
            helper.save_record(path, record)
            with patch.object(helper, "pid_identity", side_effect=FileNotFoundError("ps")), \
                    patch.object(helper.subprocess, "Popen") as spawn:
                self.assertEqual(helper.supervise(argparse.Namespace(record=str(path), token=token, scope_id=None)), 127)
            spawn.assert_not_called()
            saved = json.loads(path.read_text())
            self.assertEqual(saved["state"], "failed")
            self.assertEqual(saved["returncode"], 127)
            self.assertIsNone(saved["child_pid"])
            scope.close()

    def test_confirmed_pre_identity_supervisor_exit_does_not_leave_starting_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args = argparse.Namespace(command=[sys.executable, "-c", "pass"], state_dir=str(root),
                                      name="worker", cwd=None, persistent=False, startup_timeout=5)
            process = Mock(returncode=7)
            process.poll.return_value = 7
            with patch.object(helper.subprocess, "Popen", return_value=process), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(helper.start(args), 7)
            saved = json.loads((root / "worker.json").read_text())
            self.assertEqual(saved["state"], "failed")
            self.assertIsNone(saved["supervisor_pid"])
            self.assertIsNone(saved["child_pid"])

    def test_completion_published_during_supervisor_poll_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "worker.json"
            args = argparse.Namespace(command=[sys.executable, "-c", "pass"], state_dir=str(root),
                                      name="worker", cwd=None, persistent=False, startup_timeout=5)

            def publish_completion():
                record = helper.load_record(path)
                record.update(state="exited", returncode=0, supervisor_pid=999999, child_pid=999998)
                helper.save_record(path, record)
                return 0

            process = Mock(returncode=0)
            process.poll.side_effect = publish_completion
            output = io.StringIO()
            with patch.object(helper.subprocess, "Popen", return_value=process), redirect_stdout(output):
                self.assertEqual(helper.start(args), 0)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["state"], "exited")
            self.assertEqual(saved["returncode"], 0)
            self.assertEqual(saved["supervisor_pid"], 999999)
            self.assertEqual(saved["child_pid"], 999998)
            self.assertTrue(json.loads(output.getvalue())["ok"])
