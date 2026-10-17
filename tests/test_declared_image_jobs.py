"""Image declarations belong to the exact managed run before its payload starts."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from skills_agent.processes import (BackgroundJobError, CALL_ENV, CONTEXT_ENV, HELPER, SCOPE_ENV,
                                    ProcessScope, completion_result, observe_background_jobs)
from skills_agent.tools import ToolRegistry


class DeclaredImageToolsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def test_images_are_a_shell_parameter_without_changing_sandbox(self):
        tools = ToolRegistry(self.root, sandbox=SimpleNamespace(run=Mock()))
        schemas = {schema["name"]: schema["parameters"] for schema in tools.schemas}
        images = schemas["shell"]["properties"]["images"]
        self.assertEqual(images["type"], "array")
        self.assertEqual(images["maxItems"], 10)
        self.assertEqual(images["items"]["required"], ["path"])
        self.assertNotIn("images", schemas["shell"]["required"])
        self.assertNotIn("images", schemas["sandbox"]["properties"])

    def test_invalid_declarations_are_rejected_before_either_execution_path(self):
        tools = ToolRegistry(self.root)
        tools.process_scope = SimpleNamespace(path=self.root, start_background=Mock())
        invalid = (None, "image.png", {}, False, ["image.png"], [{}],
                   [{"path": "relative.png"}], [{"path": str(self.root / "image.png"), "label": 1}],
                   [{"path": str(self.root / "image.png"), "extra": True}],
                   [{"path": str(self.root / f"{i}.png")} for i in range(11)])
        with patch("skills_agent.tools.run_command") as foreground:
            for background in (False, True):
                for images in invalid:
                    with self.subTest(background=background, images=images):
                        result = json.loads(tools.execute("shell", json.dumps({
                            "name": "invalid", "command": "must not execute", "run_in_background": background,
                            "images": images,
                        })))
                        self.assertIn("error", result)
            foreground.assert_not_called()
            tools.process_scope.start_background.assert_not_called()

    def test_foreground_keeps_explicit_images_and_empty_list_without_reading_files(self):
        tools = ToolRegistry(self.root)
        path = self.root / "not-created-yet.png"
        base = {"name": "produce image", "command": "script", "run_in_background": False}
        with patch("skills_agent.tools.run_command", return_value={"exit_code": 0, "stdout": "ordinary text", "stderr": ""}):
            result = json.loads(tools.execute("shell", json.dumps({
                **base, "images": [{"path": path.as_uri(), "label": "after execution"}],
            })))
            self.assertEqual(result["images"][0]["path"], str(path))
            self.assertEqual(result["images"][0]["label"], "after execution")
            self.assertFalse(path.exists(), "Declaration validation must not require an existing file")
        with patch("skills_agent.tools.run_command", side_effect=lambda *args, **kwargs: {
                "exit_code": 0, "stdout": "ordinary text", "stderr": ""}):
            empty = json.loads(tools.execute("shell", json.dumps({**base, "images": []})))
            omitted = json.loads(tools.execute("shell", json.dumps(base)))
        self.assertEqual(empty["images"], [])
        self.assertNotIn("images", omitted)

    def test_background_passes_normalized_declarations_without_attaching_to_receipt(self):
        tools = ToolRegistry(self.root)
        tools.process_scope = SimpleNamespace(path=self.root, start_background=Mock(return_value={
            "status": "started", "job_id": "job-1", "completed": False,
        }))
        path = self.root / "future.png"
        result = json.loads(tools.execute("shell", json.dumps({
            "name": "produce image", "command": "script", "run_in_background": True,
            "images": [{"path": path.as_uri(), "source_url": "https://example.test/source.png"}],
        })))
        self.assertEqual(result["job_id"], "job-1")
        self.assertNotIn("images", result)
        images = tools.process_scope.start_background.call_args.kwargs["images"]
        self.assertEqual(images[0]["path"], str(path))
        self.assertEqual(images[0]["source_url"], "https://example.test/source.png")
        self.assertFalse(path.exists())


@unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process ownership")
class DeclaredImageJobTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.scope = ProcessScope(self.root)
        self.addCleanup(self.scope.close)
        self.environment = {**os.environ, CONTEXT_ENV: "image-owner", CALL_ENV: "image-call"}

    def launch(self, code, *, images=None):
        return self.scope.start_background(shlex.join([sys.executable, "-c", code]), "produce image",
                                           self.root, self.environment, images=images)

    def finished(self, receipt):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = next(job for job in observe_background_jobs([self.scope.path]) if job["id"] == receipt["job_id"])
            if job["state"] in {"exited", "failed", "timed_out", "stopped"}:
                return job, completion_result(job)
            time.sleep(.02)
        self.fail("Declared-image job did not reach a confirmed terminal state")

    def test_fast_job_retains_declarations_in_token_record_and_after_scope_recovery(self):
        image = self.root / "future image.png"
        declared = [{"path": image.as_uri(), "label": "frame", "source_url": "https://example.test/frame.png"}]
        receipt = self.launch(f"from pathlib import Path; Path({str(image)!r}).write_bytes(b'not-read-at-start')",
                              images=declared)
        self.assertFalse(receipt["completed"])
        self.assertNotIn("images", receipt)
        self.scope = ProcessScope.attach(self.scope.path)
        job, result = self.finished(receipt)
        self.assertTrue(image.exists())
        expected = [{**declared[0], "path": str(image)}]
        self.assertEqual(job["images"], expected)
        self.assertEqual(result["images"], expected)
        self.assertEqual((result["owner_session_id"], result["origin_tool_call_id"]), ("image-owner", "image-call"))
        registered = json.loads(Path(job["_record_path"]).read_text())
        alias = json.loads(Path(receipt["process_record_path"]).read_text())
        self.assertEqual(registered["images"], expected)
        self.assertEqual(alias["images"], expected)
        self.assertEqual(registered["token"], alias["token"])

    def test_empty_and_omitted_declarations_remain_distinct_after_completion(self):
        omitted = self.launch("pass")
        explicit = self.launch("pass", images=[])
        _, omitted_result = self.finished(omitted)
        _, explicit_result = self.finished(explicit)
        self.assertNotIn("images", omitted_result)
        self.assertEqual(explicit_result["images"], [])

    def test_invalid_declarations_do_not_create_any_run(self):
        with self.assertRaises(ValueError):
            self.launch("pass", images=[{"path": "relative.png"}])
        self.assertEqual(list((self.scope.path / "runs").glob("*.json")), [])

    def test_corrupted_registered_images_are_not_silently_ignored_after_restart(self):
        receipt = self.launch("pass", images=[])
        job, _ = self.finished(receipt)
        path = Path(job["_record_path"])
        record = json.loads(path.read_text())
        record["images"] = [{"path": "relative.png"}]
        path.write_text(json.dumps(record))
        with self.assertRaises(BackgroundJobError):
            completion_result(job)

    def test_images_are_in_the_first_atomic_registration_before_supervisor_launch(self):
        spec = importlib.util.spec_from_file_location("declared_image_process_helper", HELPER)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        image = {"path": str(self.root / "future.png"), "label": "frame"}
        args = helper.parser().parse_args(["start", "--state-dir", str(self.scope.path / "jobs"),
                                          "--name", "atomic-image", "--images-json", json.dumps([image]),
                                          "--", sys.executable, "-c", "pass"])
        saved = []

        def start_supervisor(command, **kwargs):
            alias = Path(command[command.index("--record") + 1])
            record = json.loads(alias.read_text())
            registration = self.scope.path / "runs" / f"{record['token']}.json"
            initial = json.loads(registration.read_text())
            self.assertEqual(initial["state"], "starting")
            self.assertEqual(initial["images"], [image])
            self.assertEqual(initial["owner_session_id"], "image-owner")
            self.assertEqual(initial["origin_tool_call_id"], "image-call")
            saved.append(initial)
            record.update(state="exited", child_pid=2, returncode=0, exit_code=0, cleanup_confirmed=True)
            helper.save_record(alias, record)
            return SimpleNamespace(returncode=0)

        with patch.dict(os.environ, {**self.environment, SCOPE_ENV: str(self.scope.path)}), \
                patch.object(helper.subprocess, "Popen", side_effect=start_supervisor), patch.object(helper, "emit"):
            self.assertEqual(helper.start(args), 0)
        self.assertEqual(len(saved), 1)


if __name__ == "__main__":
    unittest.main()
