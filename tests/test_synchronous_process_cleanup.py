"""Real process coverage for the shell entry-process ownership contract."""

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


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
class SynchronousProcessCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.addCleanup(self.stop_fixture_processes)
        for name, value in (("TERM_GRACE_SECONDS", 0.1), ("CLEANUP_TIMEOUT_SECONDS", 3)):
            setting = patch.object(execution, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def stop_fixture_processes(self):
        for path in self.root.glob("*.pid"):
            try:
                os.kill(int(path.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

    def script(self, name, source):
        path = self.root / name
        path.write_text(source, encoding="utf-8")
        return path

    def run_script(self, path, **kwargs):
        return execution.run_command(
            [sys.executable, "-u", str(path)], cwd=self.root, log_dir=self.root / "logs", **kwargs,
        )

    def launcher(self, *, exit_code=0, ignore_term=False, wait=False):
        pid_path = self.root / "child.pid"
        worker = self.script("worker.py", f"""import os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN if {ignore_term!r} else signal.SIG_DFL)
Path({str(pid_path)!r}).write_text(str(os.getpid()))
print('worker started', flush=True)
time.sleep(20)
Path({str(self.root / 'finished')!r}).write_text('unexpected completion')
""")
        launcher = self.script("launcher.py", f"""import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-u', {str(worker)!r}])
deadline = time.monotonic() + 3
while not Path({str(pid_path)!r}).exists():
    if time.monotonic() >= deadline:
        raise RuntimeError('fixture worker did not start')
    time.sleep(.005)
print('entry ready', flush=True)
if {wait!r}:
    child.wait()
sys.exit({exit_code})
""")
        return launcher, pid_path

    def assert_stopped(self, pid_path):
        self.assertTrue(pid_path.exists(), "Fixture did not start")
        pid = int(pid_path.read_text())
        probe = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=2)
        state = probe.stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), f"Residual process {pid}: {state}")

    def test_no_residual_work_preserves_compact_normal_result(self):
        script = self.script("complete.py", "print('done')\n")
        self.assertEqual(self.run_script(script), {"exit_code": 0, "stdout": "done\n", "stderr": ""})

    def test_zero_exit_stops_residual_work_and_explicitly_reports_cleanup(self):
        launcher, pid_path = self.launcher()
        callback = Mock()
        started = time.monotonic()
        result = self.run_script(launcher, timeout_seconds=5, cleanup=callback)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result["exit_code"], 0)
        self.assertIs(result["cleanup_required"], True)
        self.assertIs(result["cleanup_confirmed"], True)
        self.assertEqual(result["cleanup_reason"], "main_process_exited")
        self.assertIs(result["cleanup_forced"], False)
        self.assertGreaterEqual(result["cleanup_initial_process_count"], 1)
        self.assertIn("did not finish naturally", result["cleanup_message"])
        self.assertNotIn("timed_out", result)
        self.assert_stopped(pid_path)
        self.assertFalse((self.root / "finished").exists())
        callback.assert_not_called()

    def test_nonzero_entry_exit_is_preserved_when_residual_work_is_cleaned(self):
        launcher, pid_path = self.launcher(exit_code=7)
        result = self.run_script(launcher, timeout_seconds=5)
        self.assertEqual(result["exit_code"], 7)
        self.assertIs(result["cleanup_confirmed"], True)
        self.assert_stopped(pid_path)

    def test_term_ignoring_residual_work_is_killed_and_reported(self):
        launcher, pid_path = self.launcher(ignore_term=True)
        result = self.run_script(launcher, timeout_seconds=5)
        self.assertEqual(result["exit_code"], 0)
        self.assertIs(result["cleanup_forced"], True)
        self.assertIs(result["cleanup_confirmed"], True)
        self.assert_stopped(pid_path)

    def test_script_waiting_all_parallel_children_finishes_without_cleanup(self):
        marker_paths = [self.root / name for name in ("first.done", "second.done")]
        script = self.script("parallel.py", f"""import subprocess, sys
children = []
for path, delay, code in [({str(marker_paths[0])!r}, .05, 7), ({str(marker_paths[1])!r}, .2, 0)]:
    source = 'import time; from pathlib import Path; time.sleep(%r); Path(%r).write_text("done"); raise SystemExit(%r)' % (delay, path, code)
    children.append(subprocess.Popen([sys.executable, '-c', source]))
result = 0
for child in children:
    code = child.wait()
    if code:
        result = code
sys.exit(result)
""")
        result = self.run_script(script, timeout_seconds=5)
        self.assertEqual(result, {"exit_code": 7, "stdout": "", "stderr": ""})
        self.assertTrue(all(path.exists() for path in marker_paths))

    def test_timeout_still_cleans_entry_and_descendants_and_invokes_backend_cleanup(self):
        launcher, pid_path = self.launcher(ignore_term=True, wait=True)
        callback = Mock()
        result = self.run_script(launcher, timeout_seconds=0.5, cleanup=callback)
        self.assertEqual(result["exit_code"], 124)
        self.assertIs(result["timed_out"], True)
        self.assertNotEqual(result["process_returncode"], 0)
        self.assert_stopped(pid_path)
        callback.assert_called_once_with()

    def test_cancellation_propagates_only_after_processes_are_cleaned(self):
        launcher, pid_path = self.launcher(ignore_term=True, wait=True)
        callback = Mock()

        def interrupt_when_started(process, deadline):
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(pid_path.exists(), "Fixture did not start before cancellation")
            raise KeyboardInterrupt

        with patch.object(execution, "_wait_for_main_exit", side_effect=interrupt_when_started):
            with self.assertRaises(KeyboardInterrupt):
                self.run_script(launcher, timeout_seconds=3, cleanup=callback)
        self.assert_stopped(pid_path)
        callback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
