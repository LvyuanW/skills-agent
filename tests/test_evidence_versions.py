import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills_agent/builtin_skills/feedback/scripts"
spec = importlib.util.spec_from_file_location("version_evidence", SCRIPTS / "_evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class EvidenceVersionsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.subject = self.root / "产物 with spaces.json"
        self.subject.write_bytes(b'{"x":1}\r\n')
        self.records = self.root / "records"

    def cli(self, script, *args):
        result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                                cwd=self.root, capture_output=True, text=True, timeout=10)
        return result, json.loads(result.stdout)["observations"]

    def check(self, *args):
        return self.cli("check.py", "json", self.subject, "--pointer", "/x", "--equals-json", "1",
                        "--record-dir", self.records, *args)

    def manifest(self, **fields):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(fields))
        return path

    def run_command(self, code, **fields):
        return self.cli("feedback.py", "run", "--log-dir", self.root / "logs",
                        "--record-dir", self.records, "--manifest", self.manifest(**fields),
                        "--", sys.executable, "-c", code)

    def test_check_binds_exact_raw_bytes_and_status_does_not_rerun(self):
        result, output = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(Path(output["record"]).read_text())
        consumed = record["capture"]["consumed"][0]
        self.assertEqual(consumed["sha256"], hashlib.sha256(self.subject.read_bytes()).hexdigest())
        self.assertEqual(consumed["size"], len(self.subject.read_bytes()))
        self.assertEqual(record["observations"]["verdict"], "pass")
        _, current = self.cli("feedback.py", "status", "--record", output["record"])
        self.assertEqual(current["current_relation"], "same")
        original = self.subject.stat()
        self.subject.write_bytes(b'{"x":2}\r\n')
        os.utime(self.subject, ns=(original.st_atime_ns, original.st_mtime_ns))
        _, current = self.cli("feedback.py", "status", "--record", output["record"])
        self.assertEqual(current["current_relation"], "different")
        self.assertEqual(current["historical_observations"]["verdict"], "pass")
        self.assertEqual(len(list(self.records.glob("*/record.json"))), 1)

    def test_content_check_keeps_failure_and_acquisition_error(self):
        for content, code, verdict in [(b'{"x":2}', 1, "mismatch"), (b'{oops', 2, "acquisition_error")]:
            self.subject.write_bytes(content)
            result, output = self.check()
            self.assertEqual(result.returncode, code)
            record = json.loads(Path(output["record"]).read_text())
            self.assertEqual(record["observations"]["verdict"], verdict)
            self.assertEqual(record["capture"]["consumed"][0]["sha256"], hashlib.sha256(content).hexdigest())

    def test_changed_subject_is_distinct_from_generated_output(self):
        generated = self.root / "new.json"
        code = f"from pathlib import Path; Path({str(self.subject)!r}).write_text('changed'); Path({str(generated)!r}).write_text('new')"
        result, output = self.run_command(code, subjects=[str(self.subject)], outputs=[str(generated)])
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(Path(output["record"]).read_text())
        self.assertEqual(record["capture"]["relation"], "different")
        self.assertEqual(record["observations"]["verdict"], "not_reported")
        self.assertEqual(record["capture"]["descendant_quiescence"], "unobserved")
        result, output = self.run_command("print('only observe')", subjects=[str(self.subject)], outputs=[str(generated)])
        self.assertEqual(json.loads(Path(output["record"]).read_text())["capture"]["relation"], "same")

    def test_generation_and_content_check_have_separate_dependencies(self):
        generator = self.root / "generate.py"
        generator.write_text("print('v1')")
        _, generation = self.run_command("pass", dependencies=[str(generator)], outputs=[str(self.subject)])
        _, check = self.check()
        generator.write_text("print('v2')")
        self.assertEqual(evidence.status_record(generation["record"])["current_relation"], "different")
        self.assertEqual(evidence.status_record(check["record"])["current_relation"], "same")

    def test_consumed_dependency_identity_precedes_later_boundary_capture(self):
        consumed = evidence.bytes_fingerprint(self.subject, self.subject.read_bytes())
        self.subject.write_text('{"x":2}')
        handle = evidence.start_record(self.records, "experiment-run", {"dependencies": [str(self.subject)]})
        path = evidence.finish_record(handle, {"exit_code": 0}, consumed=[consumed])
        status = evidence.status_record(path)
        self.assertEqual(status["boundary_relation"], "same")
        row = next(row for row in status["files"] if row["role"] == "dependencies")
        self.assertEqual(row["reference"]["sha256"], consumed["sha256"])
        self.assertEqual(row["relation"], "different")

    def test_symlink_retarget_is_observed_for_check_and_run(self):
        link = self.root / "link.json"
        target2 = self.root / "second.json"
        target2.write_text('{"x":2}')
        link.symlink_to(self.subject)
        _, checked = self.cli("check.py", "json", link, "--equals-json", '{"x":1}', "--record-dir", self.records)
        self.assertEqual(evidence.status_record(checked["record"])["current_relation"], "same")
        _, ran = self.run_command("pass", subjects=[str(link)])
        link.unlink()
        link.symlink_to(target2)
        for record in (checked, ran):
            self.assertEqual(evidence.status_record(record["record"])["current_relation"], "different")

    def test_budget_missing_and_directory_are_not_identity_matches(self):
        handle = evidence.start_record(self.records, "run", {
            "subjects": [str(self.subject), str(self.root / "missing"), str(self.root)],
            "limits": {"max_bytes": 1}}, command=["already known"])
        path = evidence.finish_record(handle, {"exit_code": 0})
        self.assertEqual(handle["record"]["capture"]["relation"], "unknown")
        self.assertNotEqual(evidence.status_record(path)["current_relation"], "same")

    def test_unchanged_outputs_do_not_make_an_unfinished_run_complete(self):
        handle = evidence.start_record(self.records, "run", {"subjects": [str(self.subject)]})
        status = evidence.status_record(handle["path"])
        self.assertEqual(status["record_state"], "started")
        self.assertEqual(status["current_relation"], "unknown")
        self.assertEqual(status["historical_execution"]["state"], "unfinished")

    def test_run_preserves_nonzero_startup_and_timeout(self):
        for argv, timeout, expected, state in [
            ([sys.executable, "-c", "raise SystemExit(7)"], "2", 7, "exited"),
            ([str(self.root / "absent executable")], "2", 1, "start_failed"),
            ([sys.executable, "-c", "import time; time.sleep(10)"], "0.1", 124, "timed_out")]:
            result, output = self.cli("feedback.py", "run", "--log-dir", self.root / "logs",
                                      "--record-dir", self.records, "--timeout", timeout, "--", *argv)
            self.assertEqual(result.returncode, expected, result.stderr)
            record = json.loads(Path(output["record"]).read_text())
            self.assertEqual(record["execution"]["state"], state)
            self.assertEqual(record["observations"]["verdict"], "not_reported")

    def test_interruption_leaves_started_record(self):
        process = subprocess.Popen([sys.executable, str(SCRIPTS / "feedback.py"), "run",
                                    "--log-dir", str(self.root / "logs"), "--record-dir", str(self.records),
                                    "--", sys.executable, "-c", "import time; time.sleep(20)"],
                                   start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not list(self.records.glob("*/record.json")) and time.monotonic() < deadline:
                time.sleep(0.01)
            import signal
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=5)
            record = next(self.records.glob("*/record.json"))
            self.assertEqual(evidence.status_record(record)["current_relation"], "unknown")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()

    def test_environment_selection_and_parallel_record_identity(self):
        handles = [evidence.start_record(self.records, "run", {"environment": ["PATH"]}) for _ in range(2)]
        self.assertNotEqual(handles[0]["path"], handles[1]["path"])
        self.assertEqual(set(handles[0]["record"]["environment"]), {"PATH"})
        for handle in handles:
            evidence.finish_record(handle, {"exit_code": 0})

    def test_unbound_command_does_not_claim_task_versions_unchanged(self):
        result, output = self.cli("feedback.py", "run", "--log-dir", self.root / "logs",
                                  "--record-dir", self.records, "--", sys.executable, "-c",
                                  f"from pathlib import Path; print(Path({str(self.subject)!r}).read_text())")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.subject.write_text('{"x":99}')
        record = Path(output["record"])
        status = evidence.status_record(record)
        self.assertEqual(status["task_binding"], "unbound")
        self.assertEqual(status["task_file_count"], 0)
        self.assertEqual(status["task_relation"], "unbound")
        self.assertEqual(status["helpers_relation"], "same")
        self.assertEqual(status["current_relation"], "unknown")
        # Existing schema-v1 records, before the coverage fields were added, remain readable.
        old = json.loads(record.read_text())
        old.pop("task_binding")
        old.pop("task_file_count")
        record.write_text(json.dumps(old))
        self.assertEqual(evidence.status_record(record)["task_binding"], "unbound")

    def test_task_versions_are_separate_from_helper_or_environment_changes(self):
        with patch.dict(os.environ, {"VERSION_TEST_SETTING": "before"}):
            _, output = self.run_command("pass", subjects=[str(self.subject)],
                                          environment=["VERSION_TEST_SETTING"])
        with patch.dict(os.environ, {"VERSION_TEST_SETTING": "after"}):
            status = evidence.status_record(output["record"])
        self.assertEqual(status["task_binding"], "declared")
        self.assertEqual(status["task_file_count"], 1)
        self.assertEqual(status["task_relation"], "same")
        self.assertEqual(status["helpers_relation"], "same")
        self.assertEqual(status["current_relation"], "different")

    def test_started_record_retains_declared_scope_before_capture(self):
        handle = evidence.start_record(self.records, "run", {"subjects": [str(self.subject)]})
        handle["record"]["capture"]["before"] = []
        evidence.atomic_json(handle["path"], handle["record"])
        status = evidence.status_record(handle["path"])
        self.assertEqual(status["task_binding"], "declared")
        self.assertEqual(status["task_relation"], "unknown")

    def test_cwd_and_standalone_installed_style_scripts(self):
        standalone = self.root / "copied scripts"
        standalone.mkdir()
        for name in ("feedback.py", "check.py", "_evidence.py"):
            shutil.copyfile(SCRIPTS / name, standalone / name)
        result = subprocess.run([sys.executable, "-I", str(standalone / "check.py"), "json", str(self.subject),
                                 "--equals-json", '{"x":1}', "--record-dir", str(self.records)],
                                cwd="/", capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        result, output = self.cli("feedback.py", "run", "--cwd", standalone, "--log-dir", self.root / "logs",
                                  "--", sys.executable, "-c", "import os; print(os.getcwd())")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(output["stdout_tail"].strip()), standalone.resolve())

    def test_recording_failure_does_not_report_success(self):
        blocked = self.root / "not a directory"
        blocked.write_text("keep")
        result, output = self.cli("check.py", "json", self.subject, "--equals-json", '{"x":1}',
                                  "--record-dir", blocked)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", output)
        self.assertEqual(blocked.read_text(), "keep")

    def test_invalid_manifests_and_records_are_structured_errors(self):
        manifest = self.root / "manifest.json"
        for invalid in ([], 0, None, {"subjects": "not-a-list"}):
            manifest.write_text(json.dumps(invalid))
            result, output = self.cli("feedback.py", "run", "--log-dir", self.root / "logs",
                                      "--record-dir", self.records, "--manifest", manifest,
                                      "--", sys.executable, "-c", "raise SystemExit(99)")
            self.assertEqual(result.returncode, 1)
            self.assertIn("error", output)
        for capture in (None, [], {"before": [1]}):
            manifest.write_text(json.dumps({"schema_version": 1, "record_state": "finalized",
                                            "capture": capture, "manifest": {}}))
            result, output = self.cli("feedback.py", "status", "--record", manifest)
            self.assertEqual(result.returncode, 1)
            self.assertIn("error", output)

    def test_launcher_uses_command_cwd_for_relative_path_entries(self):
        child = self.root / "command cwd"
        for directory in (self.root, child):
            executable = directory / "bin" / "tool"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\npwd\n")
            executable.chmod(0o755)
        with patch.dict(os.environ, {"PATH": "bin"}):
            result, output = self.cli("feedback.py", "run", "--cwd", child,
                                      "--log-dir", self.root / "logs", "--record-dir", self.records,
                                      "--", "tool")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(Path(output["record"]).read_text())
        self.assertEqual(record["runtime"]["command_launcher"], str(child / "bin" / "tool"))


if __name__ == "__main__":
    unittest.main()
