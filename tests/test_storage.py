from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from skills_agent.benchmark import export_runtime_logs, main as benchmark_main
from skills_agent.cli import main
from skills_agent.session import Session
from skills_agent.storage import WorkspaceStorage


class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "task"
        self.workspace.mkdir()
        self.data = self.root / "data"
        self.enterContext(patch.dict(os.environ, {"SKILLS_AGENT_DATA_DIR": str(self.data)}))

    def test_user_data_default_and_explicit_override(self):
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=self.root):
            default = WorkspaceStorage(self.workspace)
        self.assertEqual(default.data_dir, self.root / ".skills-agent")
        self.assertEqual(WorkspaceStorage(self.workspace).data_dir, self.data)
        self.assertEqual(WorkspaceStorage(self.workspace, self.root / "custom").data_dir,
                         self.root / "custom")

    def test_same_named_workspaces_are_separate_and_canonical_aliases_reuse_storage(self):
        first = WorkspaceStorage(self.workspace).ensure()
        other = self.root / "other" / "task"
        other.mkdir(parents=True)
        second = WorkspaceStorage(other).ensure()
        self.assertNotEqual(first.root, second.root)
        self.assertEqual(first.root, WorkspaceStorage(self.workspace / ".").root)
        metadata = json.loads((first.root / "workspace.json").read_text())
        self.assertEqual(metadata["workspace"], str(self.workspace))
        self.assertEqual(first.sessions.parent, first.history.parent)
        self.assertEqual(list(self.workspace.iterdir()), [])
        original = (first.root / "workspace.json").read_bytes()
        first.ensure()
        self.assertEqual((first.root / "workspace.json").read_bytes(), original)

    def test_cli_default_creates_session_outside_workspace_and_history_is_read_only(self):
        agent = Mock()
        with patch("skills_agent.cli.create_agent", return_value=agent), \
                patch("skills_agent.cli.interact", return_value=0), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "--workspace", str(self.workspace), "--session", "demo"]), 0)
        storage = WorkspaceStorage(self.workspace)
        session = Session(storage.sessions, "demo")
        session.append({"role": "user", "content": "saved request"})
        original = (session.path / "events.jsonl").read_bytes()
        with patch("skills_agent.cli.create_agent", side_effect=AssertionError("history must not start runtime")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["history", "--workspace", str(self.workspace), "--session", "demo"]), 0)
        self.assertIn("saved request", output.getvalue())
        self.assertEqual((session.path / "events.jsonl").read_bytes(), original)
        self.assertFalse((self.workspace / ".agent").exists())
        self.assertFalse(storage.history.exists())

    def test_missing_history_does_not_create_runtime_storage(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["history", "--workspace", str(self.workspace)]), 1)
        self.assertFalse(self.data.exists())

    def test_old_session_requires_explicit_location_and_remains_readable(self):
        legacy = self.workspace / ".agent/sessions"
        session = Session(legacy, "default")
        session.append({"role": "user", "content": "legacy request"})
        before = (session.path / "events.jsonl").read_bytes()
        for command in ("run", "history"):
            with redirect_stderr(io.StringIO()) as error:
                self.assertEqual(main([command, "--workspace", str(self.workspace)]), 1)
            self.assertIn("--sessions-dir", error.getvalue())
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["history", "--workspace", str(self.workspace),
                                   "--sessions-dir", str(legacy)]), 0)
        self.assertIn("legacy request", output.getvalue())
        self.assertEqual((session.path / "events.jsonl").read_bytes(), before)
        self.assertFalse(self.data.exists())

    def test_benchmark_receipt_and_export_preserve_session_after_failure(self):
        prompt = self.root / "prompt.txt"
        prompt.write_text("task")
        logs = self.root / "logs"

        def make_agent(args, session, **kwargs):
            agent = Mock()
            def run(message):
                session.append(message)
                session.append_event({"type": "error", "message": "model failed"})
                raise RuntimeError("model failed")
            agent.run.side_effect = run
            return agent

        argv = ["--workspace", str(self.workspace), "--prompt-file", str(prompt), "--log-dir", str(logs)]
        with patch("skills_agent.benchmark.create_agent", side_effect=make_agent):
            self.assertEqual(benchmark_main(argv), 1)
        receipt = json.loads((logs / "runtime-storage.json").read_text())
        live = Path(receipt["session"])
        self.assertTrue(live.is_relative_to(self.data))
        self.assertEqual((live / "events.jsonl").read_bytes(),
                         (logs / "session/events.jsonl").read_bytes())
        self.assertFalse((logs / "session").is_symlink())
        with redirect_stderr(io.StringIO()), patch("skills_agent.benchmark.create_agent") as factory:
            self.assertEqual(benchmark_main(argv), 1)
        factory.assert_not_called()

    @unittest.skipUnless(shutil.which("git"), "Git required")
    def test_export_has_independent_git_objects_and_survives_runtime_data_removal(self):
        storage = WorkspaceStorage(self.workspace).ensure()
        session = Session(storage.sessions, "test")
        session.append({"role": "user", "content": "task"})
        (self.workspace / "result.txt").write_text("kept result")
        subprocess.run(["git", "init", "--bare", "--initial-branch=history", str(storage.history)],
                       check=True, capture_output=True)
        (storage.history / "workspace.json").write_text(json.dumps({"workspace": str(self.workspace)}))
        (storage.history / "runtime").mkdir()
        (storage.history / "runtime/observer.json").write_text(json.dumps(
            {"monitoring": True, "pid": os.getpid(), "last_error": None}))
        command = ["git", f"--git-dir={storage.history}", f"--work-tree={self.workspace}"]
        subprocess.run([*command, "add", "result.txt"], check=True, capture_output=True)
        subprocess.run([*command, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-m", "checkpoint"], check=True, capture_output=True)
        logs = self.root / "logs"
        logs.mkdir()
        export_runtime_logs(logs, session, storage)
        shutil.rmtree(self.data)
        result = subprocess.check_output(["git", f"--git-dir={logs / 'history.git'}",
                                          "show", "HEAD:result.txt"], text=True)
        self.assertEqual(result, "kept result")
        self.assertTrue((logs / "session/events.jsonl").is_file())
        archived = json.loads((logs / "history.git/runtime/observer.json").read_text())
        self.assertFalse(archived["monitoring"])
        self.assertTrue(archived["archived"])
        self.assertEqual(json.loads((logs / "history.git/workspace.json").read_text())["workspace"],
                         str(self.workspace))
        status = subprocess.check_output([sys.executable, "-m", "skills_agent.file_history",
                                          "--repository", str(logs / "history.git"), "status"], text=True)
        self.assertFalse(json.loads(status)["monitoring"])


if __name__ == "__main__":
    unittest.main()
