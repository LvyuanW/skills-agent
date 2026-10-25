import hashlib
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from skills_agent import verification
from skills_agent.execution import ToolCleanupError
from skills_agent.processes import HELPER, ProcessScope, completion_result, observe_background_jobs
from skills_agent.tools import ToolRegistry


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX native process ownership")
class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.logs = self.root / "runtime logs"
        self.tools = ToolRegistry(self.root, log_dir=self.logs)
        self.source = self.root / "input with spaces.txt"
        self.output = self.root / "result.txt"
        self.source.write_text("original")

    def command(self, code):
        return shlex.join([sys.executable, "-c", code])

    def run_shell(self, command, **kwargs):
        return json.loads(self.tools.execute("shell", json.dumps({
            "name": "check an existing program", "command": command, "run_in_background": False, **kwargs})))

    def read_record(self, result):
        return json.loads(Path(result["evidence"]["record"]).read_text())

    def background(self, command, **kwargs):
        return self.run_shell(command, run_in_background=True, **kwargs)

    def scope(self):
        scope = ProcessScope(self.root / "session")
        self.addCleanup(scope.close)
        self.tools.process_scope = scope
        self.tools.process_context = "test-session"
        self.tools.tool_call_id = "test-call"
        return scope

    def finished(self, receipt, scope):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = next((job for job in observe_background_jobs([scope.path])
                        if job["id"] == receipt["job_id"]), None)
            if job and job["state"] in {"exited", "failed", "stopped", "timed_out"}:
                return job, completion_result(job)
            time.sleep(.02)
        self.fail("Background command did not reach confirmed completion")

    def test_ordinary_calls_never_start_evidence_and_keep_old_result(self):
        with patch("skills_agent.verification.begin", side_effect=AssertionError("must not record")):
            result = self.run_shell("printf plain")
        self.assertEqual(result, {"exit_code": 0, "stdout": "plain", "stderr": ""})
        self.assertFalse((self.logs / "evidence").exists())

    def test_invalid_bindings_are_rejected_before_command(self):
        for declaration in (False, {"subjects": ["relative"]}, {"outputs": [None]},
                            {"record_dir": "relative"}, {"scope": ""}, {"typo": []}):
            with self.subTest(declaration=declaration):
                result = self.run_shell(self.command(f"open({str(self.output)!r},'w').write('bad')"),
                                        evidence=declaration)
                self.assertIn("error", result)
                self.assertFalse(result["executed"])
                self.assertFalse(self.output.exists())

    def test_existing_shell_binds_absolute_files_and_actual_logs(self):
        subdir = self.root / "nested"
        subdir.mkdir()
        command = f"cd {shlex.quote(str(subdir))} && " + self.command(
            f"from pathlib import Path; p=Path({str(self.source)!r}); "
            f"Path({str(self.output)!r}).write_text(p.read_text().upper()); print('checked')")
        result = self.run_shell(command, evidence={"dependencies": [str(self.source)],
                                                  "outputs": [str(self.output)], "scope": "Uppercase this input"})
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "checked\n")
        self.assertEqual(result["evidence"]["task_binding"], "declared")
        record = self.read_record(result)
        self.assertEqual(record["observations"]["verdict"], "not_reported")
        self.assertEqual(record["command"], {"argv": ["/bin/sh", "-c", command], "cwd": str(self.root)})
        self.assertEqual(Path(record["execution"]["stdout_path"]).read_text(), "checked\n")
        output = next(row for row in record["capture"]["after"] if row["role"] == "outputs")
        self.assertEqual(output["sha256"], hashlib.sha256(b"ORIGINAL").hexdigest())
        self.assertEqual(record["capture"]["descendant_quiescence"], "owned_process_group_confirmed")
        self.source.write_text("new input")
        status = verification._backend().status_record(result["evidence"]["record"])
        self.assertEqual(status["current_relation"], "different")
        self.assertEqual(self.output.read_text(), "ORIGINAL", "status must not rerun")

    def test_changed_subject_and_exit_failure_remain_separate(self):
        result = self.run_shell(self.command(
            f"from pathlib import Path; Path({str(self.source)!r}).write_text('changed'); raise SystemExit(7)"),
            evidence={"subjects": [str(self.source)]})
        self.assertEqual(result["exit_code"], 7)
        record = self.read_record(result)
        self.assertEqual(record["execution"]["exit_code"], 7)
        self.assertEqual(record["capture"]["relation"], "different")
        self.assertEqual(record["observations"]["verdict"], "not_reported")

    def test_foreground_timeout_finishes_record_after_cleanup(self):
        result = self.run_shell(self.command("import time; print('partial',flush=True); time.sleep(30)"),
                                timeout_seconds=.15, evidence={"subjects": [str(self.source)]})
        self.assertEqual(result["exit_code"], 124)
        record = self.read_record(result)
        self.assertEqual(record["record_state"], "finalized")
        self.assertEqual(record["execution"]["state"], "timed_out")
        self.assertTrue(record["execution"]["cleanup_confirmed"])

    def test_no_task_files_is_explicit_and_record_dir_is_optional(self):
        result = self.run_shell("true", evidence={})
        self.assertEqual(result["evidence"]["task_binding"], "unbound")
        self.assertEqual(result["evidence"]["task_file_count"], 0)
        self.assertTrue(Path(result["evidence"]["record"]).is_relative_to(self.logs / "evidence"))

    def test_background_captures_at_exit_before_delayed_delivery(self):
        scope = self.scope()
        gate = self.root / "gate"
        code = ("from pathlib import Path\nimport time\n"
                f"while not Path({str(gate)!r}).exists(): time.sleep(.02)\n"
                f"Path({str(self.output)!r}).write_text('first')\n")
        receipt = self.background(self.command(code), evidence={"dependencies": [str(self.source)],
                                                                "outputs": [str(self.output)]})
        self.assertFalse(receipt["completed"])
        self.assertEqual(receipt["evidence"]["record_state"], "pending")
        self.assertEqual(self.read_record(receipt)["record_state"], "started")
        gate.touch()
        job, initial = self.finished(receipt, scope)
        record = self.read_record(initial)
        original = next(row for row in record["capture"]["after"] if row["role"] == "outputs")
        self.assertEqual(original["sha256"], hashlib.sha256(b"first").hexdigest())
        self.assertEqual(record["links"]["SKILLS_AGENT_TOOL_CALL_ID"], "test-call")
        self.output.write_text("later edit")
        delivered = completion_result(job)
        self.assertEqual(self.read_record(delivered), record, "delivery must not recapture output")
        self.assertEqual(verification._backend().status_record(delivered["evidence"]["record"])["current_relation"],
                         "different")

    def test_background_failure_and_timeout_retain_process_outcome(self):
        scope = self.scope()
        for code, timeout, expected in (("raise SystemExit(6)", 2, 6),
                                        ("import time; time.sleep(30)", .15, 124)):
            with self.subTest(code=expected):
                receipt = self.background(self.command(code), timeout_seconds=timeout,
                                          evidence={"subjects": [str(self.source)]})
                _, result = self.finished(receipt, scope)
                self.assertEqual(result["exit_code"], expected)
                self.assertEqual(self.read_record(result)["execution"]["exit_code"], expected)
                self.assertEqual(result["evidence"]["record_state"], "finalized")

    def test_output_capture_waits_for_residual_process_cleanup(self):
        scope = self.scope()
        for background in (False, True):
            with self.subTest(background=background):
                ready = self.root / f"ready-{background}"
                self.output.unlink(missing_ok=True)
                worker = ("import signal,time,sys\nfrom pathlib import Path\n"
                          f"def stop(*args):\n Path({str(self.output)!r}).write_text('cleaned'); sys.exit(0)\n"
                          "signal.signal(signal.SIGTERM,stop)\n"
                          f"Path({str(ready)!r}).touch()\nwhile True: time.sleep(.02)\n")
                launcher = ("import subprocess,sys,time\nfrom pathlib import Path\n"
                            f"subprocess.Popen([sys.executable,'-c',{worker!r}])\n"
                            f"while not Path({str(ready)!r}).exists(): time.sleep(.02)\n")
                result = self.run_shell(self.command(launcher), run_in_background=background, timeout_seconds=3,
                                        evidence={"outputs": [str(self.output)]})
                if background:
                    _, result = self.finished(result, scope)
                record = self.read_record(result)
                self.assertEqual(result["exit_code"], 0)
                self.assertTrue(record["execution"]["cleanup_required"])
                self.assertTrue(record["execution"]["cleanup_confirmed"])
                after = next(row for row in record["capture"]["after"] if row["role"] == "outputs")
                self.assertEqual(after["sha256"], hashlib.sha256(b"cleaned").hexdigest())

    def test_background_start_failure_finalizes_without_claiming_a_check_ran(self):
        spec = importlib.util.spec_from_file_location("evidence_process_helper_tests", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        handle = verification.begin({"subjects": [str(self.source)]}, ["/bin/sh", "-c", "true"],
                                    self.root, log_dir=self.logs)
        args = helper.parser().parse_args(["start", "--state-dir", str(self.root / "jobs"),
                                          "--name", "start-failure", "--cwd", str(self.root),
                                          "--evidence-record", str(handle["path"]), "--", "/bin/sh", "-c", "true"])
        with patch.object(helper.subprocess, "Popen", side_effect=OSError("no process capacity")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(helper.start(args), 127)
        record = json.loads(Path(handle["path"]).read_text())
        self.assertEqual(record["record_state"], "finalized")
        self.assertEqual(record["execution"]["state"], "start_failed")
        self.assertEqual(record["execution"]["exit_code"], 127)
        self.assertEqual(record["observations"]["verdict"], "not_reported")

    def test_capture_failure_does_not_erase_exit_status(self):
        with patch("skills_agent.verification.finish", side_effect=OSError("record store unavailable")):
            result = self.run_shell("exit 9", evidence={"subjects": [str(self.source)]})
        self.assertEqual(result["exit_code"], 9)
        self.assertEqual(result["evidence"]["record_state"], "record_error")
        self.assertIn("unavailable", result["evidence"]["error"])

    def test_unconfirmed_cleanup_does_not_finalize(self):
        with patch("skills_agent.tools.run_command", side_effect=ToolCleanupError("uncertain")):
            with self.assertRaises(ToolCleanupError):
                self.run_shell("true", evidence={"subjects": [str(self.source)]})
        records = list((self.logs / "evidence").glob("*/record.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())["record_state"], "started")

    def test_runtime_exception_without_completion_does_not_finalize(self):
        with patch("skills_agent.tools.run_command", side_effect=OSError("runtime unavailable")):
            result = self.run_shell("true", evidence={"subjects": [str(self.source)]})
        self.assertEqual(result["evidence"]["record_state"], "started")
        self.assertEqual(self.read_record(result)["record_state"], "started")
        self.assertIn("runtime unavailable", result["error"])


if __name__ == "__main__":
    unittest.main()
