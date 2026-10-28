"""Exercise tool deadlines through real shell processes and the Agent loop."""

from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.execution import ToolCleanupError
from skills_agent.processes import HELPER
from skills_agent.providers.base import ProviderOutputLimitError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry


def tool_response(command, *, identifier="command", name="shell", timeout_seconds=0.5):
    arguments = {"name": "Run bounded work", "command": command,
                 "timeout_seconds": timeout_seconds}
    if name == "shell":
        arguments["run_in_background"] = False
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": identifier, "name": name,
        "arguments": json.dumps(arguments),
    }]}


class ScriptedModel:
    max_output_tokens = 128

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def count_tokens(self, *args, **kwargs):
        return 128

    def count_text(self, text):
        return 128

    def complete(self, system, messages, tools, **kwargs):
        self.requests.append(deepcopy(messages))
        try:
            response = next(self.responses)
        except StopIteration:
            raise AssertionError("Agent requested an unexpected extra model response") from None
        return response(messages) if callable(response) else response


class ToolExecutionTimeoutTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.tools = ToolRegistry(self.root, log_dir=self.root / "logs")
        self.processes = []
        self.addCleanup(self.stop_test_processes)

    def stop_test_processes(self):
        # Only PIDs written by this test's temporary fixtures are touched.
        for path in self.root.glob("*.pid"):
            try:
                os.kill(int(path.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        for process in self.processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=3)

    def script(self, name, source):
        path = self.root / name
        path.write_text(source, encoding="utf-8")
        return path

    def command(self, path, *arguments):
        return shlex.join([sys.executable, "-u", str(path), *map(str, arguments)])

    def shell(self, command, *, timeout_seconds=0.5):
        return json.loads(self.tools.execute("shell", json.dumps({
            "name": "Run bounded work", "command": command, "timeout_seconds": timeout_seconds,
            "run_in_background": False,
        })))

    def assert_stopped(self, *pid_files):
        deadline = time.monotonic() + 3
        for path in pid_files:
            self.assertTrue(path.exists(), f"Fixture did not start: {path.name}")
            pid = int(path.read_text())
            while True:
                result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                        capture_output=True, text=True, timeout=2)
                state = result.stdout.strip()
                if not state or state.startswith("Z"):
                    break
                if time.monotonic() >= deadline:
                    self.fail(f"Timed out tool left fixture process {pid} running: {state}")
                time.sleep(0.01)

    def stubborn_worker(self):
        return self.script("worker.py", """import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
print('worker-output', flush=True)
print('worker-stderr', file=sys.stderr, flush=True)
time.sleep(10)
""")

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_timeout_stops_pipeline_and_term_ignoring_grandchild(self):
        worker = self.stubborn_worker()
        parent_pid, child_pid, reader_pid = [self.root / name for name in ("parent.pid", "child.pid", "reader.pid")]
        launcher = self.script("launcher.py", f"""import os, signal, subprocess, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path({str(parent_pid)!r}).write_text(str(os.getpid()))
subprocess.Popen([sys.executable, '-u', {str(worker)!r}, {str(child_pid)!r}])
time.sleep(10)
""")
        reader = self.script("reader.py", f"""import os, sys
from pathlib import Path
Path({str(reader_pid)!r}).write_text(str(os.getpid()))
for line in sys.stdin:
    print(line, end='', flush=True)
""")
        activity = []
        self.tools.on_activity = activity.append
        started = time.monotonic()
        result = self.shell(self.command(launcher) + " | " + self.command(reader))
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual(result["exit_code"], 124)
        self.assertIs(result["timed_out"], True)
        self.assertEqual(result["timeout_seconds"], 0.5)
        self.assertIn("worker-output", result["stdout"])
        self.assertIn("worker-stderr", result["stderr"])
        self.assertIsNone(activity[-1])
        self.assert_stopped(parent_pid, child_pid, reader_pid)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_parent_exit_does_not_leave_inherited_output_pipes_hanging(self):
        worker = self.stubborn_worker()
        child_pid = self.root / "child.pid"
        launcher = self.script("early_exit.py", f"""import subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, '-u', {str(worker)!r}, {str(child_pid)!r}])
deadline = time.monotonic() + 2
while not Path({str(child_pid)!r}).exists() and time.monotonic() < deadline:
    time.sleep(.01)
print('parent-exited', flush=True)
""")
        started = time.monotonic()
        result = self.shell(self.command(launcher))
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual(result["exit_code"], 0)
        self.assertIs(result["cleanup_required"], True)
        self.assertIs(result["cleanup_confirmed"], True)
        self.assertIs(result["cleanup_forced"], True)
        self.assertEqual(result["cleanup_reason"], "main_process_exited")
        self.assertNotIn("timed_out", result)
        self.assertIn("parent-exited", result["stdout"])
        self.assert_stopped(child_pid)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_agent_continues_after_timeout_without_killing_an_existing_service(self):
        service = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        self.processes.append(service)
        slow = self.script("slow.py", "import time\nprint('partial evidence', flush=True)\ntime.sleep(10)\n")
        quick = self.script("quick.py", "print('replanned successfully')\n")

        def replan(messages):
            self.assertEqual(messages[-1]["role"], "tool")
            result = json.loads(messages[-1]["content"])
            self.assertEqual(result["exit_code"], 124)
            self.assertIs(result["timed_out"], True)
            self.assertIn("partial evidence", result["stdout"])
            self.assertIsNone(service.poll(), "A different process group was killed by the timeout")
            return tool_response(self.command(quick), identifier="replanned", timeout_seconds=2)

        model = ScriptedModel([tool_response(self.command(slow)), replan,
                               {"role": "assistant", "content": "Recovered after timeout"}])
        session = Session(self.root, "session")
        agent = Agent(model, session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        self.assertEqual(agent.run(session.make_user_message("Complete bounded work", [])), "Recovered after timeout")
        results = [json.loads(message["content"]) for message in session.messages() if message["role"] == "tool"]
        self.assertEqual([result["exit_code"] for result in results], [124, 0])
        self.assertIn("replanned successfully", results[-1]["stdout"])
        self.assertEqual(len(model.requests), 3)
        self.assertFalse(any(event["type"] == "error" for event in session.events()))
        self.assertIsNone(service.poll())

    def test_cleanup_failure_escapes_tool_results_and_stops_agent_planning(self):
        failure = ToolCleanupError("Could not confirm container cleanup")

        def fail(*args, **kwargs):
            raise failure

        sandbox = SimpleNamespace(run=fail)
        model = ScriptedModel([tool_response("ignored", name="sandbox"),
                               {"role": "assistant", "content": "Must not continue"}])
        session = Session(self.root, "session")
        agent = Agent(model, session, SkillRegistry([]), self.root, sandbox=sandbox)
        self.addCleanup(agent.close)
        with self.assertRaises(ToolCleanupError) as caught:
            agent.run(session.make_user_message("Run sandboxed work", []))
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(session.events()[-1]["type"], "error")
        self.assertIn("cleanup", session.events()[-1]["message"])

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
    def test_foreground_cleanup_failure_preserves_original_error_and_output_logs(self):
        completed = self.script("completed.py", "import sys\n"
                                "print('saved stdout evidence', flush=True)\n"
                                "print('saved stderr evidence', file=sys.stderr, flush=True)\n")
        model = ScriptedModel([tool_response(self.command(completed), timeout_seconds=3),
                               {"role": "assistant", "content": "Must not continue"}])
        session = Session(self.root, "cleanup-diagnostics")
        agent = Agent(model, session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)

        def fail_exit_check(process):
            # The real entry process has already exited. Retain it for test cleanup
            # even if an assertion fails before the executor can reap it.
            self.processes.append(process)
            raise RuntimeError("initial probe failure")

        with patch("skills_agent.execution._finish_main_exit", side_effect=fail_exit_check), \
                patch("skills_agent.execution._signal_group",
                      side_effect=PermissionError(errno.EPERM, "signal permission denied")), \
                patch("skills_agent.execution._wait_for_completion",
                      side_effect=RuntimeError("inspection unavailable")):
            with self.assertRaises(ToolCleanupError) as caught:
                agent.run(session.make_user_message("Read the existing evidence", []))

        self.assertEqual(len(model.requests), 1)
        events = session.events()
        error = next(event for event in reversed(events) if event["type"] == "error")
        diagnostics = error["diagnostics"]
        self.assertEqual(diagnostics, caught.exception.diagnostics)
        self.assertIs(diagnostics["cleanup_confirmed"], False)
        self.assertEqual(diagnostics["original_error"]["type"], "RuntimeError")
        self.assertEqual(diagnostics["original_error"]["message"], "initial probe failure")
        self.assertIn("initial probe failure", diagnostics["original_error"]["traceback"])
        failures = diagnostics["cleanup_errors"]
        self.assertTrue(any(failure["type"] == "PermissionError"
                            and failure.get("errno") == errno.EPERM for failure in failures))
        self.assertTrue(any("inspection unavailable" in failure["message"] for failure in failures))
        self.assertTrue(all(failure.get("phase") for failure in failures))
        stdout_path, stderr_path = [Path(diagnostics[key]) for key in ("stdout_path", "stderr_path")]
        self.assertTrue(stdout_path.is_relative_to(session.path / "tool-runs"))
        self.assertEqual(stdout_path.read_text(), "saved stdout evidence\n")
        self.assertEqual(stderr_path.read_text(), "saved stderr evidence\n")
        recorded = [json.loads(message["content"]) for message in session.messages()
                    if message["role"] == "tool"]
        self.assertEqual(len(recorded), 1)
        self.assertIn("error", recorded[0])
        self.assertNotIn("exit_code", recorded[0])
        self.assertNotIn("stdout", recorded[0])

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
    def test_repeated_cleanup_failure_retains_both_rounds_of_diagnostics(self):
        completed = self.script("completed.py", "print('output before cleanup failures', flush=True)\n")
        model = ScriptedModel([tool_response(self.command(completed), timeout_seconds=3),
                               {"role": "assistant", "content": "Must not continue"}])
        session = Session(self.root, "repeated-cleanup")
        agent = Agent(model, session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        first_errors = [
            {"phase": "SIGTERM", "type": "PermissionError", "errno": errno.EPERM,
             "message": "first signal rejected"},
            {"phase": "SIGTERM_confirmation", "type": "RuntimeError",
             "message": "first inspection failed"},
        ]
        later_errors = [
            {"phase": "SIGKILL", "type": "PermissionError", "errno": errno.EPERM,
             "message": "later signal rejected"},
        ]
        original = ToolCleanupError("initial cleanup failed", diagnostics={
            "cleanup_confirmed": False, "cleanup_errors": deepcopy(first_errors),
        })
        final = ToolCleanupError("outer cleanup failed", diagnostics={
            "cleanup_confirmed": False, "cleanup_errors": deepcopy(later_errors),
        })

        def fail_exit_cleanup(process):
            self.processes.append(process)
            raise original

        with patch("skills_agent.execution._finish_main_exit", side_effect=fail_exit_cleanup), \
                patch("skills_agent.execution._cleanup", side_effect=final) as cleanup:
            with self.assertRaises(ToolCleanupError) as caught:
                agent.run(session.make_user_message("Read the existing evidence", []))

        cleanup.assert_called_once_with(self.processes[-1], None)
        self.assertIs(caught.exception, final)
        self.assertEqual(len(model.requests), 1)
        error = next(event for event in reversed(session.events()) if event["type"] == "error")
        diagnostics = error["diagnostics"]
        self.assertEqual(diagnostics["cleanup_errors"], first_errors + later_errors)
        self.assertEqual(original.diagnostics["cleanup_errors"], first_errors)
        self.assertEqual(diagnostics["original_error"]["type"], "ToolCleanupError")
        self.assertEqual(diagnostics["original_error"]["message"], "initial cleanup failed")
        self.assertIn("initial cleanup failed", diagnostics["original_error"]["traceback"])
        self.assertIs(diagnostics["cleanup_confirmed"], False)
        self.assertEqual(Path(diagnostics["stdout_path"]).read_text(), "output before cleanup failures\n")
        self.assertEqual(Path(diagnostics["stderr_path"]).read_text(), "")

    def test_timeout_metadata_survives_auto_report_in_next_model_request(self):
        status = {
            "exit_code": 124, "process_returncode": -9, "timed_out": True,
            "timeout_seconds": 0.5, "duration_seconds": 1.6,
            "stdout_path": str(self.root / "stdout.log"),
            "stderr_path": str(self.root / "stderr.log"),
            "stdout_truncated": True, "stderr_truncated": False,
        }
        original = {**status, "stdout": "partial command evidence\n" * 20000, "stderr": "interrupted"}
        for report_fails in (False, True):
            with self.subTest(report_fails=report_fails):
                model = ScriptedModel([tool_response("must only execute once"),
                                       {"role": "assistant", "content": "Use saved logs and replan"}])
                session = Session(self.root, "report-failure" if report_fails else "report-success")
                agent = Agent(model, session, SkillRegistry([]), self.root)
                self.addCleanup(agent.close)
                failure = ProviderOutputLimitError("Tool report exceeded its output limit", finish_reason="length")
                with patch.object(agent.tools, "execute", return_value=json.dumps(original)) as execute, \
                     patch.object(model, "count_text", return_value=9000), \
                     patch.object(agent.auxiliary, "tool_report", return_value="Partial evidence summarized",
                                  side_effect=failure if report_fails else None) as report:
                    self.assertEqual(agent.run(session.make_user_message("Complete bounded work", [])),
                                     "Use saved logs and replan")
                execute.assert_called_once()
                report.assert_called_once()
                self.assertEqual(len(model.requests), 2)
                message = model.requests[-1][-1]
                self.assertEqual(message["role"], "tool")
                selected = json.loads(message["content"])
                for key, value in status.items():
                    self.assertEqual(selected[key], value, key)
                self.assertTrue(selected["source"]["path"].endswith("events.jsonl"))
                if report_fails:
                    self.assertEqual(selected["report_error"]["operation"], "tool_report")
                    self.assertEqual(selected["report_error"]["finish_reason"], "length")
                else:
                    self.assertEqual(selected["report"], "Partial evidence summarized")
                self.assertNotIn("stdout", selected)
                saved = [item for item in session.messages() if item["role"] == "tool"]
                self.assertEqual(len(saved), 1)
                self.assertEqual(json.loads(saved[0]["content"]), original)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
    def test_timeout_preserves_registered_service_from_a_previous_tool_call(self):
        state = self.root / "services"
        start = shlex.join([sys.executable, str(HELPER), "start", "--state-dir", str(state),
                            "--name", "existing", "--service", "--", sys.executable, "-c", "import time; time.sleep(10)"])
        slow = self.script("slow.py", "import time\ntime.sleep(10)\n")

        def run_slow(messages):
            self.assertEqual(json.loads(messages[-1]["content"])["exit_code"], 0)
            return tool_response(self.command(slow), identifier="slow")

        def finish(messages):
            result = json.loads(messages[-1]["content"])
            self.assertEqual(result["exit_code"], 124)
            self.assertIs(result["timed_out"], True)
            record = json.loads((state / "existing.json").read_text())
            self.assertEqual(record["state"], "running")
            os.kill(record["child_pid"], 0)
            return {"role": "assistant", "content": "Earlier service still running"}

        model = ScriptedModel([tool_response(start, identifier="start-service", timeout_seconds=3), run_slow, finish])
        session = Session(self.root, "session")
        agent = Agent(model, session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        self.assertEqual(agent.run(session.make_user_message("Start service then bounded work", [])),
                         "Earlier service still running")
        agent.close()
        self.assertIn(json.loads((state / "existing.json").read_text())["state"], {"stopped", "exited"})


if __name__ == "__main__":
    unittest.main()
