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

from skills_agent.agent import Agent
from skills_agent.processes import HELPER, ProcessScope
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry


def response(text="done", name=None, values=None):
    result = {"role": "assistant", "content": text}
    if name:
        if name == "shell":
            values = {"run_in_background": False, **values}
        result["tool_calls"] = [{"id": "task", "name": name, "arguments": json.dumps(values)}]
    return result


class ScriptedModel:
    max_output_tokens = 1000

    def __init__(self, responses):
        self.responses = iter(responses)

    def count_tokens(self, *args, **kwargs):
        return 100

    def count_text(self, text):
        return 20

    def complete(self, *args, **kwargs):
        result = next(self.responses)
        if isinstance(result, BaseException):
            raise result
        return result


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.state = self.root / "state"
        self.names = set()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.stop_remaining)

    def helper(self, operation, name, *extra):
        return [sys.executable, str(HELPER), operation, "--state-dir", str(self.state),
                "--name", name, *extra]

    def stop_remaining(self):
        for name in self.names:
            if (self.state / f"{name}.json").exists():
                result = subprocess.run(self.helper("stop", name, "--grace", "0.1", "--timeout", "3"),
                                        capture_output=True, timeout=5)
                if result.returncode:
                    record = self.record(name)
                    # Test-owned live records only; avoid leaving a service after a failed assertion.
                    for pid, group in ((record.get("child_pid"), True),
                                       (record.get("supervisor_pid"), False)):
                        if pid:
                            try:
                                (os.killpg if group else os.kill)(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass

    def command(self, name, *, service=False, persistent=False):
        self.names.add(name)
        code = ("import pathlib,time; p=pathlib.Path(" + repr(str(self.root / name)) + "); "
                "exec(\"while True:\\n with p.open('a') as out: out.write('x')\\n time.sleep(.02)\")")
        return shlex.join(self.helper("start", name, *(["--service"] if service else []),
                                      *(["--persistent"] if persistent else []),
                                      "--", sys.executable, "-c", code))

    def shell(self, name, *, service=False, persistent=False):
        return response("", "shell", {"name": name, "command": self.command(name, service=service,
                                                                            persistent=persistent)})

    def record(self, name):
        return json.loads((self.state / f"{name}.json").read_text())

    def assert_stopped(self, name):
        record = self.record(name)
        self.assertIn(record["state"], {"exited", "stopped"})
        with self.assertRaises(ProcessLookupError):
            os.kill(record["child_pid"], 0)
        path = self.root / name
        before = path.read_bytes() if path.exists() else b""
        time.sleep(.08)
        self.assertEqual(path.read_bytes() if path.exists() else b"", before)

    def test_turn_cancel_preserves_prior_work_and_session_exit_honors_explicit_persistence(self):
        # A helper started outside the agent is unrelated even in the same workspace.
        unrelated = subprocess.run(self.command("unrelated"), shell=True, cwd=self.root,
                                   capture_output=True, timeout=5)
        self.assertEqual(unrelated.returncode, 0, unrelated.stderr)
        model = ScriptedModel([self.shell("prior", service=True), response(),
                               self.shell("cancelled", service=True, persistent=True), KeyboardInterrupt(),
                               self.shell("ongoing", service=True, persistent=True), response()])
        session = Session(self.root, "session")
        agent = Agent(model, session, SkillRegistry([]), self.root, allow_persistent_processes=True)
        self.addCleanup(agent.close)
        agent.run(session.make_user_message("start", []))
        with self.assertRaises(KeyboardInterrupt):
            agent.run(session.make_user_message("cancel", []))
        self.assert_stopped("cancelled")
        self.assertEqual(self.record("prior")["state"], "running")
        agent.run(session.make_user_message("ongoing service", []))
        agent.close()
        self.assert_stopped("prior")
        self.assertEqual(self.record("ongoing")["state"], "running")
        self.assertEqual(self.record("unrelated")["state"], "running")

    def test_parent_failure_cleans_a_subagents_background_work(self):
        model = ScriptedModel([
            response("", "subagent", {"name": "child", "task": "start an ongoing service"}),
            self.shell("child-worker", service=True), response(), RuntimeError("parent failed"),
        ])
        session = Session(self.root, "session")
        agent = Agent(model, session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        with self.assertRaisesRegex(RuntimeError, "parent failed"):
            agent.run(session.make_user_message("task", []))
        self.assert_stopped("child-worker")

    def test_closed_scope_refuses_late_starts_and_benchmark_policy_refuses_persistence(self):
        scope = ProcessScope(self.root)
        self.addCleanup(scope.close)
        tools = ToolRegistry(self.root)
        tools.process_scope = scope
        rejected = json.loads(tools.execute("shell", json.dumps({
            "name": "persistent", "command": self.command("forbidden", service=True, persistent=True),
            "run_in_background": False,
        })))
        self.assertNotEqual(rejected["exit_code"], 0)
        self.assertIn("not allowed", rejected["stderr"])
        self.assertFalse((self.state / "forbidden.json").exists())
        scope.close()
        late = json.loads(tools.execute("shell", json.dumps({
            "name": "late", "command": self.command("late"),
            "run_in_background": False,
        })))
        self.assertNotEqual(late["exit_code"], 0)
        self.assertIn("closed", late["stderr"])
        self.assertFalse((self.state / "late.json").exists())

    def test_cleanup_tracks_tokens_across_name_reuse_and_ignores_changed_public_record(self):
        scope = ProcessScope(self.root)
        self.addCleanup(scope.close)
        tools = ToolRegistry(self.root)
        tools.process_scope = scope
        self.names.add("reuse")
        quick = shlex.join(self.helper("start", "reuse", "--", sys.executable, "-c", "pass"))
        waited = shlex.join(self.helper("wait", "reuse", "--timeout", "3"))
        for command in (quick, waited, self.command("reuse")):
            result = json.loads(tools.execute("shell", json.dumps({"name": "run", "command": command, "run_in_background": False})))
            self.assertEqual(result["exit_code"], 0, result)
        original = self.record("reuse")
        public = self.state / "reuse.json"
        public.write_text(json.dumps({**original, "child_pid": os.getpid(), "supervisor_pid": os.getpid()}))
        try:
            scope.close()
        finally:
            # Supervisor normally publishes its actual final record over the changed alias.
            if self.record("reuse")["state"] == "running":
                public.write_text(json.dumps(original))
        self.assert_stopped("reuse")

    def test_forged_registration_cannot_stop_another_scopes_service(self):
        foreign = ProcessScope(self.root)
        victim = ProcessScope(self.root)
        self.addCleanup(foreign.close)
        self.addCleanup(victim.close)
        tools = ToolRegistry(self.root)
        tools.process_scope = foreign
        result = json.loads(tools.execute("shell", json.dumps({
            "name": "foreign", "command": self.command("foreign"),
            "run_in_background": False,
        })))
        self.assertEqual(result["exit_code"], 0, result)
        record = self.record("foreign")
        forged = victim.path / "runs" / f"{record['token']}.json"
        forged.write_text(json.dumps({**record, "process_scope": str(victim.path)}))
        try:
            with self.assertRaisesRegex(RuntimeError, "identity"):
                victim.close()
            self.assertEqual(self.record("foreign")["state"], "running")
            os.kill(record["child_pid"], 0)
            self.assertFalse((Path(record["artifacts"]) / "stop.json").exists())
        finally:
            forged.unlink()
        foreign.close()
        self.assert_stopped("foreign")
