"""Exercise the public shell API through real supervised processes and Agent turns."""

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
from skills_agent.background import RUNTIME_EVENT
from skills_agent.processes import HELPER, observe_background_jobs
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry
from tests.test_agent_context import ScriptedModel, call, final


def notices(messages):
    return [message for message in messages if message.get("runtime_event") == RUNTIME_EVENT]


def observation(message):
    return json.loads(message["content"][1]["text"])


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
class ShellBackgroundTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.agents = []
        self.receipts = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        failures = []
        receipts = {(receipt["state_dir"], receipt["name"]): receipt for receipt in self.receipts}
        # An assertion can fail before the model callback captures a receipt.
        # Recover only registrations created inside this test's private root so
        # successful/persistent services are still stopped before deleting logs.
        for path in self.root.glob("**/process-scopes/*/runs/*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            receipt = {"state_dir": str(Path(record["record"]).parent), "name": record["name"]}
            receipts.setdefault((receipt["state_dir"], receipt["name"]), receipt)
        for agent in self.agents:
            try:
                agent.close()
            except Exception as exc:
                failures.append(str(exc))
        for receipt in receipts.values():
            result = self.stop(receipt)
            if result.returncode:
                failures.append(result.stdout + result.stderr)
        if failures:
            self.fail("Fixture cleanup was not confirmed: " + "; ".join(failures))

    def stop(self, receipt):
        return subprocess.run(
            [sys.executable, str(HELPER), "stop", "--state-dir", receipt["state_dir"],
             "--name", receipt["name"], "--grace", "0.1", "--timeout", "4"],
            capture_output=True, text=True, timeout=6,
        )

    def script(self, name, source):
        path = self.root / name
        path.write_text(source, encoding="utf-8")
        return shlex.join([sys.executable, "-u", str(path)])

    def gated_command(self, name, *, gate=None):
        gate = gate or self.root / f"{name}.release"
        pid_file = self.root / f"{name}.pid"
        finished = self.root / f"{name}.finished"
        command = self.script(f"{name}.py", f"""import os, time
from pathlib import Path
Path({str(pid_file)!r}).write_text(str(os.getpid()))
deadline = time.monotonic() + 10
while not Path({str(gate)!r}).exists():
    if time.monotonic() >= deadline:
        raise RuntimeError('fixture gate was not released')
    time.sleep(.01)
Path({str(finished)!r}).write_text('completed')
print({(name + ' completed')!r}, flush=True)
""")
        return command, gate, pid_file, finished

    def agent(self, replies, *, persistent=False, name="main"):
        session = Session(self.root, name)
        model = ScriptedModel(replies)
        agent = Agent(model, session, SkillRegistry([]), self.root,
                      allow_persistent_processes=persistent)
        self.agents.append(agent)
        return agent, model, session

    def user(self):
        return {"role": "user", "content": "Complete the requested work and verify its output."}

    def latest_receipt(self, messages, *, call_id="call-1"):
        message = next(message for message in reversed(messages)
                       if message.get("role") == "tool" and message.get("tool_call_id") == call_id)
        receipt = json.loads(message["content"])
        self.assertEqual(receipt.get("status"), "started", receipt)
        if not any(item["job_id"] == receipt["job_id"] for item in self.receipts):
            self.receipts.append(receipt)
        return receipt

    def wait_file(self, path):
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(path.exists(), f"Fixture did not create {path.name}")

    def process_state(self, path):
        self.assertTrue(path.exists(), f"Fixture did not start: {path.name}")
        result = subprocess.run(["ps", "-o", "stat=", "-p", path.read_text().strip()],
                                capture_output=True, text=True, timeout=2)
        return result.stdout.strip()

    def assert_running(self, path):
        state = self.process_state(path)
        self.assertTrue(state and not state.startswith("Z"), f"Expected running fixture, got {state!r}")

    def assert_stopped(self, path):
        state = self.process_state(path)
        self.assertTrue(not state or state.startswith("Z"), f"Fixture still running: {state}")

    def test_run_in_background_is_required_boolean_and_validation_never_executes(self):
        tools = ToolRegistry(self.root)
        marker = self.root / "must-not-execute"
        command = self.script("forbidden.py", f"from pathlib import Path; Path({str(marker)!r}).touch()")
        base = {"name": "invalid request", "command": command}
        for value in ("missing", None, 0, 1, "false", [], {}):
            with self.subTest(value=value):
                arguments = dict(base)
                if value != "missing":
                    arguments["run_in_background"] = value
                self.assertIn("error", json.loads(tools.execute("shell", json.dumps(arguments))))
                self.assertFalse(marker.exists())
        arguments = {**base, "run_in_background": False, "background_lifecycle": "service"}
        self.assertIn("error", json.loads(tools.execute("shell", json.dumps(arguments))))
        self.assertFalse(marker.exists())

    def test_task_receipt_precedes_completion_and_progress_reply_waits_for_one_result(self):
        command, gate, pid_file, finished = self.gated_command("compute")
        artifact = self.root / "verified.txt"
        launch = call("shell", {"name": "compute", "command": command, "run_in_background": True})

        def progress(system, messages, tools, on_event):
            receipt = self.latest_receipt(messages)
            self.assertFalse(receipt["completed"])
            self.assertTrue(receipt["wait_on_exit"])
            self.assertEqual(receipt["kind"], "job")
            self.assertEqual(receipt["job_timeout_seconds"], 3600)
            jobs = observe_background_jobs(agent._background_scope_paths())
            self.assertEqual([job["id"] for job in jobs], [receipt["job_id"]])
            self.assertFalse(finished.exists())
            self.assertEqual(notices(messages), [])
            return final("Extraction is running. I will inspect its result and write the artifact.")

        def consume(system, messages, tools, on_event):
            self.assertEqual(len(notices(messages)), 1)
            result = observation(notices(messages)[0])
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("compute completed", result["stdout"])
            self.assertTrue(finished.exists())
            return call("shell", {"name": "write verified artifact", "command":
                        self.script("write.py", f"from pathlib import Path; Path({str(artifact)!r}).write_text('verified')")}, 2)

        agent, model, session = self.agent([launch, progress, consume, final("Verified artifact written")])
        original_wait = agent._background_results.wait

        def release_only_after_runtime_wait(append):
            self.assertFalse(finished.exists())
            self.wait_file(pid_file)
            gate.touch()
            return original_wait(append)

        with patch.object(agent._background_results, "wait", side_effect=release_only_after_runtime_wait) as wait:
            self.assertEqual(agent.run(self.user()), "Verified artifact written")
        wait.assert_called_once()
        self.assertEqual(artifact.read_text(), "verified")
        self.assertEqual(len(model.calls), 4)
        self.assertEqual(len(notices(session.messages())), 1)
        self.assertEqual([message["tool_call_id"] for message in session.messages() if message["role"] == "tool"],
                         ["call-1", "call-2"])

    def test_task_timeout_is_delivered_as_124_and_model_recovers(self):
        command, _, pid_file, finished = self.gated_command("timeout")
        artifact = self.root / "recovered.txt"
        launch = call("shell", {"name": "bounded background", "command": command,
                                 "run_in_background": True, "timeout_seconds": .5})

        def handle(system, messages, tools, on_event):
            self.latest_receipt(messages)
            results = notices(messages)
            if not results:
                return final("The bounded task is running; I will handle its result.")
            self.assertEqual(len(results), 1)
            result = observation(results[0])
            self.assertEqual(result["exit_code"], 124)
            self.assertTrue(result["timed_out"])
            self.assertTrue(result["cleanup_confirmed"])
            if not artifact.exists():
                return call("shell", {"name": "recover", "command":
                            self.script("recover.py", f"from pathlib import Path; Path({str(artifact)!r}).write_text('recovered')")}, 2)
            return final("Recovered from background timeout")

        agent, _, session = self.agent([launch, *([handle] * 5)])
        self.assertEqual(agent.run(self.user()), "Recovered from background timeout")
        self.assertEqual(artifact.read_text(), "recovered")
        self.assertFalse(finished.exists())
        self.assert_stopped(pid_file)
        self.assertEqual(len(notices(session.messages())), 1)

    def test_zero_exit_with_residual_processes_delivers_cleanup_warning(self):
        child_pid = self.root / "residual.pid"
        worker = self.script("residual.py", f"""import os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path({str(child_pid)!r}).write_text(str(os.getpid()))
time.sleep(10)
""")
        command = self.script("early.py", f"""import shlex, subprocess, time
from pathlib import Path
subprocess.Popen(shlex.split({worker!r}))
deadline = time.monotonic() + 3
while not Path({str(child_pid)!r}).exists():
    if time.monotonic() >= deadline:
        raise RuntimeError('child fixture did not start')
    time.sleep(.01)
print('entry exited', flush=True)
""")
        launch = call("shell", {"name": "early exit", "command": command, "run_in_background": True})

        def handle(system, messages, tools, on_event):
            self.latest_receipt(messages)
            if not notices(messages):
                return final("Waiting for the runtime's confirmed result.")
            result = observation(notices(messages)[0])
            self.assertEqual(result["exit_code"], 0)
            self.assertTrue(result["cleanup_required"])
            self.assertTrue(result["cleanup_confirmed"])
            self.assertTrue(result["cleanup_forced"])
            self.assertEqual(result["cleanup_reason"], "main_process_exited")
            self.assertTrue(result["cleanup_message"])
            self.assert_stopped(child_pid)
            return final("The entry exited, but residual work was stopped")

        agent, _, session = self.agent([launch, *([handle] * 4)])
        self.assertEqual(agent.run(self.user()), "The entry exited, but residual work was stopped")
        self.assertEqual(len(notices(session.messages())), 1)

    def test_task_and_service_coexist_only_task_blocks_and_service_survives_close(self):
        service, _, service_pid, service_finished = self.gated_command("service")
        task, task_gate, task_pid, task_finished = self.gated_command("task")
        launch = call("shell", {"name": "local service", "command": service,
                                "run_in_background": True, "background_lifecycle": "service"})
        launch["tool_calls"] += call("shell", {"name": "finite task", "command": task,
                                               "run_in_background": True}, 2)["tool_calls"]

        def progress(system, messages, tools, on_event):
            service_receipt = self.latest_receipt(messages)
            task_receipt = self.latest_receipt(messages, call_id="call-2")
            self.assertEqual(service_receipt["kind"], "service")
            self.assertFalse(service_receipt["wait_on_exit"])
            self.assertIsNone(service_receipt["job_timeout_seconds"])
            self.assertTrue(task_receipt["wait_on_exit"])
            self.wait_file(service_pid)
            self.assertFalse(task_finished.exists())
            return final("The service is available and the finite task is still running.")

        def finish(system, messages, tools, on_event):
            results = [observation(message) for message in notices(messages)]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["kind"], "job")
            self.assertTrue(task_finished.exists())
            self.assert_running(service_pid)
            return final("Task complete; service remains available")

        agent, _, session = self.agent([launch, progress, finish], persistent=True)
        original_wait = agent._background_results.wait

        def release_task(append):
            self.wait_file(task_pid)
            task_gate.touch()
            return original_wait(append)

        with patch.object(agent._background_results, "wait", side_effect=release_task) as wait:
            self.assertEqual(agent.run(self.user()), "Task complete; service remains available")
        wait.assert_called_once()
        self.assert_running(service_pid)
        agent.close()
        self.assert_running(service_pid)
        self.assertFalse(service_finished.exists())
        service_receipt = next(receipt for receipt in self.receipts if receipt["kind"] == "service")
        stopped = self.stop(service_receipt)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assert_stopped(service_pid)
        self.assertEqual(len(notices(session.messages())), 1)

    def test_failed_or_cancelled_turn_cleans_new_service_even_when_persistence_allowed(self):
        for index, failure in enumerate((RuntimeError("model failed"), KeyboardInterrupt())):
            with self.subTest(failure=type(failure).__name__):
                command, _, pid_file, finished = self.gated_command(f"abort-{index}")
                launch = call("shell", {"name": "new service", "command": command,
                                        "run_in_background": True, "background_lifecycle": "service"})

                def fail(system, messages, tools, on_event):
                    self.latest_receipt(messages)
                    self.wait_file(pid_file)
                    raise failure

                agent, _, _ = self.agent([launch, fail], persistent=True, name=f"failed-{index}")
                with self.assertRaises(type(failure)):
                    agent.run(self.user())
                self.assert_stopped(pid_file)
                self.assertFalse(finished.exists())
                agent.close()

    def test_failed_subagent_service_is_cleaned_without_stopping_parent_service(self):
        parent_command, _, parent_pid, _ = self.gated_command("parent-service")
        child_command, _, child_pid, child_finished = self.gated_command("child-service")
        parent_start = call("shell", {"name": "parent service", "command": parent_command,
                                      "run_in_background": True, "background_lifecycle": "service"})
        child_start = call("shell", {"name": "child service", "command": child_command,
                                     "run_in_background": True, "background_lifecycle": "service"}, 3)

        def delegate(system, messages, tools, on_event):
            self.latest_receipt(messages)
            self.wait_file(parent_pid)
            return call("subagent", {"name": "failing delegated work", "task":
                        "Start the requested service, then report its result."}, 2)

        def fail_child(system, messages, tools, on_event):
            self.latest_receipt(messages, call_id="call-3")
            self.wait_file(child_pid)
            raise RuntimeError("delegated model failed")

        def parent_finishes(system, messages, tools, on_event):
            report = next(message for message in messages
                          if message.get("role") == "tool" and message.get("tool_call_id") == "call-2")
            self.assertIn("delegated model failed", json.loads(report["content"])["error"])
            self.assert_stopped(child_pid)
            self.assertFalse(child_finished.exists())
            self.assert_running(parent_pid)
            results = [observation(message) for message in notices(messages)]
            child_receipt = next(receipt for receipt in self.receipts if receipt["display_name"] == "child service")
            self.assertEqual([result["job_id"] for result in results], [child_receipt["job_id"]])
            self.assertTrue(results[0]["cleanup_confirmed"])
            return final("Delegated work failed; the parent service remains available")

        agent, _, session = self.agent(
            [parent_start, delegate, child_start, fail_child, parent_finishes], persistent=True,
        )
        self.assertEqual(agent.run(self.user()),
                         "Delegated work failed; the parent service remains available")
        agent.close()
        self.assert_stopped(child_pid)
        self.assert_running(parent_pid)
        self.assertEqual(len(notices(session.messages())), 1)
        parent_receipt = next(receipt for receipt in self.receipts if receipt["display_name"] == "parent service")
        stopped = self.stop(parent_receipt)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assert_stopped(parent_pid)

    def test_default_benchmark_policy_rejects_service_but_allows_finite_task(self):
        marker = self.root / "forbidden-service"
        rejected = self.script("forbidden-service.py", f"from pathlib import Path; Path({str(marker)!r}).touch()")
        task, gate, _, finished = self.gated_command("allowed-task")
        service_call = call("shell", {"name": "forbidden service", "command": rejected,
                                      "run_in_background": True, "background_lifecycle": "service"})

        def after_rejection(system, messages, tools, on_event):
            result = json.loads(messages[-1]["content"])
            self.assertIn("error", result)
            self.assertIn("service", result["error"].lower())
            self.assertFalse(marker.exists())
            return call("shell", {"name": "allowed finite task", "command": task,
                                  "run_in_background": True}, 2)

        def progress(system, messages, tools, on_event):
            self.latest_receipt(messages, call_id="call-2")
            return final("Finite task running")

        def finish(system, messages, tools, on_event):
            self.assertEqual(observation(notices(messages)[0])["exit_code"], 0)
            self.assertTrue(finished.exists())
            self.assertFalse(marker.exists())
            return final("Finite task completed under benchmark policy")

        agent, _, _ = self.agent([service_call, after_rejection, progress, finish])
        original_wait = agent._background_results.wait

        def release_task(append):
            gate.touch()
            return original_wait(append)

        with patch.object(agent._background_results, "wait", side_effect=release_task):
            self.assertEqual(agent.run(self.user()), "Finite task completed under benchmark policy")


if __name__ == "__main__":
    unittest.main()
