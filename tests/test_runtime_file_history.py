import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from skills_agent.cli import parser
from skills_agent.runtime import create_agent
from skills_agent.session import Session
from skills_agent.storage import WorkspaceStorage
from tests.test_agent_context import ScriptedModel, call, final


class RuntimeFileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.enterContext(patch.dict(os.environ, {"SKILLS_AGENT_DATA_DIR": str(self.root / "state")}))
        self.storage = WorkspaceStorage(self.workspace).ensure()
        self.session = Session(self.storage.sessions, "main")

    def agent(self, model):
        args = parser().parse_args([
            "run", "--workspace", str(self.workspace),
            "--model", "test-model",
        ])
        with patch("skills_agent.runtime.create_provider", return_value=model):
            agent = create_agent(args, self.session)
        self.addCleanup(agent.close)
        return agent

    def test_parent_and_child_share_recorded_edits_without_recording_other_inputs(self):
        (self.workspace / "input.dat").write_bytes(b"untouched input")
        model = ScriptedModel([
            call("edit_file", {"name": "Write parent", "operation": "create",
                               "path": "parent.txt", "content": "parent"}),
            call("subagent", {"name": "Write child", "task": "Create child.txt"}, number=2),
            call("edit_file", {"name": "Write child", "operation": "create",
                               "path": "child.txt", "content": "child"}, number=3),
            final("child done"), final("done"),
        ])
        agent = self.agent(model)
        with patch.object(agent.file_history, "start", side_effect=AssertionError("No history startup scan")):
            self.assertEqual(agent.run({"role": "user", "content": "Create both files"}), "done")
        agent.close()
        self.assertFalse((self.workspace / ".git").exists())
        self.assertFalse((self.workspace / ".agent").exists())
        for filename, expected in (("parent.txt", b"parent"), ("child.txt", b"child")):
            saved = subprocess.check_output([
                sys.executable, "-m", "skills_agent.file_history", "--repository", str(self.storage.history),
                "show", "HEAD", filename,
            ])
            self.assertEqual(saved, expected)
        saved_paths = subprocess.check_output([
            "git", "--git-dir", str(self.storage.history), "ls-tree", "-r", "--name-only", "HEAD",
        ], text=True).splitlines()
        self.assertEqual(len(saved_paths), 2)
        operations = [json.loads(path.read_text()) for path in (self.storage.history / "operations").glob("*.json")]
        recorded_sessions = {item["session_id"] for item in operations}
        self.assertIn(str(self.session.path), recorded_sessions)
        self.assertTrue(any(Path(path).parent == self.session.path / "subagents" for path in recorded_sessions))
        receipt = json.loads((self.session.path / "file-history.json").read_text())
        self.assertEqual(receipt["mode"], "editing-only")
        self.assertFalse(receipt["monitoring"])
        for system, _, tools in model.calls:
            self.assertIn(str(self.storage.history), system)
            self.assertIn("# File editing history", system)
            self.assertIn("history.py", system)
            self.assertIn("edit_file", {tool["name"] for tool in tools})

    def test_reading_and_program_outputs_do_not_initialize_or_populate_history(self):
        (self.workspace / "input.txt").write_text("source")
        model = ScriptedModel([
            call("shell", {"name": "Read input", "command": "cat input.txt"}),
            final("read"),
        ])
        agent = self.agent(model)
        self.assertFalse(self.storage.history.exists())
        agent.run({"role": "user", "content": "Read input"})
        (self.workspace / "generated.txt").write_text("program output")
        agent.close()
        self.assertFalse(self.storage.history.exists())
        self.assertFalse((self.workspace / ".git").exists())

    def test_history_retrieval_child_cannot_call_edit_file(self):
        model = ScriptedModel([
            call("subagent", {"name": "Recall", "task": "Find the user's request", "history": True}),
            call("edit_file", {"name": "Forbidden edit", "operation": "create",
                               "path": "unexpected.txt", "content": "no"}, number=2),
            final("retrieved"), final("done"),
        ])
        agent = self.agent(model)
        self.assertEqual(agent.run({"role": "user", "content": "Recall my request"}), "done")
        self.assertIn("edit_file", {tool["name"] for tool in model.calls[0][2]})
        self.assertNotIn("edit_file", {tool["name"] for tool in model.calls[1][2]})
        self.assertFalse((self.workspace / "unexpected.txt").exists())
        self.assertFalse(self.storage.history.exists())

    def test_user_is_saved_and_displayed_before_saving_runtime_receipt(self):
        model = ScriptedModel([final("done"), final("again")])
        agent = self.agent(model)
        visible = []
        agent.on_event = visible.append
        entered = threading.Event()
        release = threading.Event()
        results, failures = [], []
        save_receipt = agent._save_file_history_receipt
        def blocked_receipt():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release receipt save")
            return save_receipt()
        def run():
            try:
                results.append(agent.run({"role": "user", "content": "first task"}))
            except BaseException as exc:
                failures.append(exc)
        def users(events):
            return [event["message"]["content"] for event in events
                    if event["type"] == "message" and event["message"]["role"] == "user"]
        with patch.object(agent, "_save_file_history_receipt", side_effect=blocked_receipt), \
                patch.object(agent.context, "sync", wraps=agent.context.sync) as sync:
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(users(visible), ["first task"])
                reopened = Session(self.storage.sessions, "main")
                self.assertEqual(users(reopened.events()), ["first task"])
                self.assertEqual(model.calls, [])
                sync.assert_not_called()
            finally:
                release.set()
                worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(results, ["done"])
            self.assertFalse(agent.file_history.status()["monitoring"])
            self.assertEqual(agent.run({"role": "user", "content": "second task"}), "again")
        self.assertEqual(users(visible), ["first task", "second task"])
        self.assertEqual(users(self.session.events()), ["first task", "second task"])
        self.assertEqual(len(model.calls), 2)

    def test_receipt_failure_preserves_user_and_diagnostic_and_next_turn_recovers(self):
        model = ScriptedModel([final("recovered")])
        agent = self.agent(model)
        visible = []
        agent.on_event = visible.append
        with patch.object(agent, "_save_file_history_receipt", side_effect=RuntimeError("receipt unavailable")):
            with self.assertRaisesRegex(RuntimeError, "receipt unavailable"):
                agent.run({"role": "user", "content": "task"})
        self.assertEqual(model.calls, [])
        saved = Session(self.storage.sessions, "main").events()
        self.assertEqual(saved[0]["message"], {"role": "user", "content": "task"})
        self.assertEqual(visible[0]["message"], saved[0]["message"])
        self.assertEqual(saved[-1]["type"], "error")
        self.assertEqual(saved[-1]["message"], "File history receipt could not be saved: receipt unavailable")
        self.assertEqual(agent.run({"role": "user", "content": "retry"}), "recovered")
        users = [event["message"]["content"] for event in self.session.events()
                 if event["type"] == "message" and event["message"]["role"] == "user"]
        self.assertEqual(users, ["task", "retry"])

    def test_context_sync_failure_after_acknowledgement_uses_normal_error_path(self):
        model = ScriptedModel([])
        agent = self.agent(model)
        with patch.object(agent.context, "sync", side_effect=RuntimeError("context unavailable")):
            with self.assertRaisesRegex(RuntimeError, "context unavailable"):
                agent.run({"role": "user", "content": "task"})
        self.assertEqual(model.calls, [])
        self.assertFalse(agent.file_history.status()["monitoring"])
        saved = self.session.events()
        self.assertEqual(saved[0]["message"]["content"], "task")
        self.assertEqual(saved[-1]["type"], "error")
        self.assertEqual(saved[-1]["message"], "context unavailable")


if __name__ == "__main__":
    unittest.main()
