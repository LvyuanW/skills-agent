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

from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry


BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
SKILL_ROOT = BUILTIN_ROOT / "process-management"
SCRIPT = SKILL_ROOT / "scripts" / "process.py"


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "Managed processes require POSIX waitid/WNOWAIT")
class ProcessManagementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name).resolve()
        self.state_dir = self.workspace / "process state"
        self.names = set()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.stop_remaining)

    def stop_remaining(self):
        for name in self.names:
            path = self.state_dir / f"{name}.json"
            if not path.exists():
                continue
            # Normal cleanup exercises the public helper; direct termination is only
            # a test fallback for a failed assertion after record-tampering tests.
            result = self.process("stop", name=name, extra=("--grace", "0.2", "--timeout", "3"))
            if result.returncode:
                record = json.loads(path.read_text())
                child = record.get("child_pid")
                if child:
                    try:
                        os.killpg(child, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                supervisor = record.get("supervisor_pid")
                if supervisor:
                    try:
                        os.kill(supervisor, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def process(self, operation, *, name="worker", extra=()):
        self.names.add(name)
        return subprocess.run(
            [sys.executable, str(SCRIPT), operation, "--state-dir", str(self.state_dir), "--name", name, *map(str, extra)],
            cwd=self.workspace, capture_output=True, timeout=15,
        )

    def observation(self, result, *, success=True):
        self.assertEqual(result.returncode == 0, success, result.stderr.decode(errors="replace") or result.stdout.decode(errors="replace"))
        value = json.loads(result.stdout)
        self.assertIsInstance(value, dict)
        return value

    def start(self, code, *, name="worker", args=()):
        result = self.process("start", name=name, extra=("--", sys.executable, "-u", "-c", code, *args))
        return self.observation(result)["process"]

    def stop(self, *, name="worker", grace="0.2"):
        return self.observation(self.process("stop", name=name, extra=("--grace", grace, "--timeout", "5")))["process"]

    def read_record(self, name="worker"):
        path = self.state_dir / f"{name}.json"
        return path, json.loads(path.read_text())

    def wait_ready(self, *, name="worker", marker="READY", timeout="3"):
        return self.process("wait", name=name, extra=("--match", marker, "--stream", "stdout", "--timeout", timeout))

    def test_start_returns_with_detached_stdio_and_existing_shell_tool_is_unchanged(self):
        tools = ToolRegistry(self.workspace)
        argv = [sys.executable, str(SCRIPT), "start", "--state-dir", str(self.state_dir), "--name", "worker", "--", sys.executable, "-u", "-c", "import time; print('READY'); time.sleep(30)"]
        self.names.add("worker")
        began = time.monotonic()
        shell = json.loads(tools.execute("shell", json.dumps({"name": "start an owned worker", "command": shlex.join(argv)})))
        elapsed = time.monotonic() - began
        self.assertEqual(shell["exit_code"], 0, shell)
        self.assertLess(elapsed, 5, "The outer shell must return without waiting for a persistent worker")
        started = json.loads(shell["stdout"])["process"]
        self.assertEqual(started["state"], "running")
        self.assertTrue(Path(started["record"]).is_file())
        ready = self.observation(self.wait_ready())
        self.assertTrue(ready["observations"]["ready"])
        self.assertEqual(self.stop()["state"], "stopped")

    def test_exit_failure_and_full_separate_binary_logs_survive_other_shell_calls(self):
        self.start("import os,time; time.sleep(.15); os.write(1,b'x'*25000+bytes.fromhex('e4b8ad0a')); os.write(2,b'failure'+bytes.fromhex('ff0a')); raise SystemExit(7)")
        waited = self.process("wait", extra=("--timeout", "4"))
        self.assertEqual(waited.returncode, 7, waited.stdout)
        ended = json.loads(waited.stdout)["process"]
        self.assertEqual(ended["state"], "exited")
        self.assertEqual(ended["returncode"], 7)
        self.assertEqual(Path(ended["stdout"]).read_bytes(), b"x" * 25000 + "中\n".encode())
        self.assertEqual(Path(ended["stderr"]).read_bytes(), b"failure\xff\n")
        observed = self.observation(self.process("logs", extra=("--max-chars", "100")))["observations"]["logs"]
        self.assertEqual(len(observed["stdout"]["tail"]), 100)
        self.assertTrue(observed["stdout"]["truncated"])
        self.assertIn("failure", observed["stderr"]["tail"])
        self.assertEqual(self.observation(self.process("status"))["process"]["returncode"], 7)

    def test_missing_executable_is_a_confirmed_start_failure(self):
        result = self.process("start", extra=("--", str(self.workspace / "missing-command")))
        value = self.observation(result, success=False)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(value["process"]["state"], "failed")
        self.assertEqual(value["process"]["returncode"], 127)
        self.assertIn("No such file", value["process"]["error"])

    def test_wait_timeout_leaves_worker_running_and_can_be_followed_by_stop(self):
        self.start("import time; print('READY'); time.sleep(30)")
        result = self.wait_ready(marker="never-produced", timeout="0.2")
        value = self.observation(result, success=False)
        self.assertEqual(result.returncode, 124)
        self.assertTrue(value["observations"]["timed_out"])
        self.assertEqual(self.observation(self.process("status"))["process"]["state"], "running")
        self.assertEqual(self.stop()["state"], "stopped")

    def test_ready_wait_does_not_treat_successful_exit_as_a_running_service(self):
        self.start("import time; time.sleep(.1); print('READY')")
        self.observation(self.process("wait", extra=("--timeout", "4")))
        value = self.observation(self.wait_ready(), success=False)
        self.assertFalse(value["observations"]["ready"])
        self.assertIn("ended before readiness", value["observations"]["error"])

    def test_tcp_readiness_observes_a_real_local_service(self):
        self.start("import socket,time; server=socket.socket(); server.bind(('127.0.0.1',0)); server.listen(); print(server.getsockname()[1]); time.sleep(30)")
        _, record = self.read_record()
        deadline = time.monotonic() + 3
        port = ""
        while time.monotonic() < deadline:
            port = Path(record["stdout"]).read_text().strip()
            if port:
                break
            time.sleep(.05)
        self.assertTrue(port.isdecimal())
        value = self.observation(self.process("wait", extra=("--tcp", "127.0.0.1", port, "--timeout", "3")))
        self.assertTrue(value["observations"]["ready"])
        self.stop()

    def test_stop_cleans_inherited_children_and_escalates_ignored_term(self):
        marker = self.workspace / "child-survived.txt"
        child_code = "import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1.5); Path(" + repr(str(marker)) + ").write_text('survived'); time.sleep(30)"
        launcher = "import signal,subprocess,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); child=subprocess.Popen([sys.executable,'-u','-c'," + repr(child_code) + "]); print('READY',child.pid); time.sleep(30)"
        started = self.start(launcher)
        self.observation(self.wait_ready())
        time.sleep(.15)  # Both processes have installed their TERM handlers.
        ended = self.stop(grace="0.1")
        self.assertEqual(ended["state"], "stopped")
        self.assertTrue(ended["forced"])
        self.assertEqual(ended["returncode"], -signal.SIGKILL)
        time.sleep(1.6)
        self.assertFalse(marker.exists(), "A child must not keep working after confirmed group cleanup")
        with self.assertRaises(ProcessLookupError):
            os.kill(started["child_pid"], 0)

    def test_natural_parent_exit_also_cleans_remaining_children(self):
        marker = self.workspace / "orphan-survived.txt"
        child_code = "import time; from pathlib import Path; time.sleep(1); Path(" + repr(str(marker)) + ").write_text('survived'); time.sleep(30)"
        self.start("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); time.sleep(.2)")
        ended = self.observation(self.process("wait", extra=("--timeout", "4")))["process"]
        self.assertEqual(ended["state"], "exited")
        self.assertEqual(ended["returncode"], 0)
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_tampered_supervisor_identity_never_signals_an_unrelated_process(self):
        self.start("import time; print('READY'); time.sleep(30)")
        self.observation(self.wait_ready())
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(lambda: unrelated.wait(timeout=4))
        self.addCleanup(lambda: unrelated.terminate() if unrelated.poll() is None else None)
        path, original = self.read_record()
        tampered = dict(original, supervisor_pid=unrelated.pid)
        path.write_text(json.dumps(tampered))
        try:
            value = self.observation(self.process("stop", extra=("--grace", "0")), success=False)
            self.assertIn("no process was signalled", value["observations"]["error"])
            self.assertIsNone(unrelated.poll())
            status = self.observation(self.process("status"))["process"]
            self.assertEqual(status["state"], "orphaned")
            self.assertFalse((Path(original["artifacts"]) / "stop.json").exists())
        finally:
            path.write_text(json.dumps(original))
        self.stop()
        self.assertIsNone(unrelated.poll())

    def test_child_pid_in_record_is_not_used_to_choose_what_to_kill(self):
        self.start("import time; print('READY'); time.sleep(30)")
        self.observation(self.wait_ready())
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(lambda: unrelated.wait(timeout=4))
        self.addCleanup(lambda: unrelated.terminate() if unrelated.poll() is None else None)
        path, record = self.read_record()
        record["child_pid"] = unrelated.pid
        path.write_text(json.dumps(record))
        ended = self.stop()
        self.assertEqual(ended["state"], "stopped")
        self.assertNotEqual(ended["child_pid"], unrelated.pid)
        self.assertIsNone(unrelated.poll())

    def test_active_name_is_refused_and_ended_name_gets_fresh_logs(self):
        old = self.start("import time; print('OLD_READY'); time.sleep(30)")
        self.observation(self.wait_ready(marker="OLD_READY"))
        duplicate = self.process("start", extra=("--", sys.executable, "-c", "print('replacement')"))
        self.observation(duplicate, success=False)
        self.assertEqual(self.read_record()[1]["artifacts"], old["artifacts"])
        self.stop()
        new = self.start("import time; print('NEW_READY'); time.sleep(30)")
        self.assertNotEqual(new["stdout"], old["stdout"])
        self.assertTrue(Path(old["stdout"]).exists())
        self.observation(self.wait_ready(marker="NEW_READY"))
        stale = self.wait_ready(marker="OLD_READY", timeout="0.2")
        self.assertEqual(stale.returncode, 124)
        self.stop()

    def test_arguments_execute_directly_and_paths_with_spaces_work(self):
        unexpected = self.workspace / "accidental-shell-execution.txt"
        literal = "$(touch " + str(unexpected) + ")"
        self.start("import sys,time; print(sys.argv[1]); time.sleep(.2)", args=(literal,))
        value = self.observation(self.process("wait", extra=("--timeout", "4")))["process"]
        self.assertEqual(Path(value["stdout"]).read_text().strip(), literal)
        self.assertFalse(unexpected.exists())

    def test_discovery_and_help_expose_a_usable_helper_without_new_tools(self):
        catalog = SkillRegistry([BUILTIN_ROOT]).catalog()
        self.assertIn("process-management", catalog)
        self.assertIn(str(SKILL_ROOT / "SKILL.md"), catalog)
        self.assertTrue((SKILL_ROOT / "references" / "across-turns.md").is_file())
        result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        for operation in (b"start", b"status", b"wait", b"logs", b"stop"):
            self.assertIn(operation, result.stdout)


if __name__ == "__main__":
    unittest.main()
