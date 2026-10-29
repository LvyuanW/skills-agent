import json
from pathlib import Path
import subprocess
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
        self.storage = WorkspaceStorage(self.workspace, self.root / "state").ensure()
        self.session = Session(self.storage.sessions, "main")

    def agent(self, model):
        args = parser().parse_args([
            "run", "--workspace", str(self.workspace), "--data-dir", str(self.root / "state"),
            "--model", "test-model",
        ])
        with patch("skills_agent.runtime.create_provider", return_value=model):
            agent = create_agent(args, self.session)
        self.addCleanup(agent.close)
        return agent

    def test_parent_and_child_share_history_and_shutdown_saves_shell_outputs(self):
        model = ScriptedModel([
            call("shell", {"name": "Write parent", "command": "printf parent > parent.txt"}),
            call("subagent", {"name": "Write child", "task": "Create child.txt"}, number=2),
            call("shell", {"name": "Write child", "command": "printf child > child.txt"}),
            final("child done"), final("done"),
        ])
        agent = self.agent(model)
        with patch.object(agent.file_history, "start", wraps=agent.file_history.start) as start:
            self.assertEqual(agent.run({"role": "user", "content": "Create both files"}), "done")
            self.assertEqual(start.call_count, 1)
        agent.close()
        self.assertFalse((self.workspace / ".git").exists())
        self.assertFalse((self.workspace / ".agent").exists())
        for filename, expected in (("parent.txt", b"parent"), ("child.txt", b"child")):
            saved = subprocess.check_output([
                "git", "--git-dir", str(self.storage.history), "show", f"HEAD:{filename}",
            ])
            self.assertEqual(saved, expected)
        receipt = json.loads((self.session.path / "file-history.json").read_text())
        self.assertIsInstance(receipt, dict)
        for system, _, tools in model.calls:
            self.assertIn(str(self.storage.history), system)
            self.assertIn("# Workspace file history", system)
            self.assertIn("history.py", system)
            self.assertLessEqual({tool["name"] for tool in tools}, {"shell", "subagent"})

    def test_user_is_saved_and_displayed_before_blocked_history_but_model_waits(self):
        model = ScriptedModel([final("done"), final("again")])
        agent = self.agent(model)
        visible = []
        agent.on_event = visible.append
        entered = threading.Event()
        release = threading.Event()
        results, failures = [], []
        start = agent.file_history.start
        def blocked_start():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release history startup")
            return start()
        def run():
            try:
                results.append(agent.run({"role": "user", "content": "first task"}))
            except BaseException as exc:
                failures.append(exc)
        def users(events):
            return [event["message"]["content"] for event in events
                    if event["type"] == "message" and event["message"]["role"] == "user"]
        with patch.object(agent.file_history, "start", side_effect=blocked_start), \
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
            self.assertTrue(agent.file_history.status()["monitoring"])
            self.assertEqual(agent.run({"role": "user", "content": "second task"}), "again")
        self.assertEqual(users(visible), ["first task", "second task"])
        self.assertEqual(users(self.session.events()), ["first task", "second task"])
        self.assertEqual(len(model.calls), 2)

    def test_start_failure_preserves_user_and_diagnostic_and_next_turn_recovers(self):
        model = ScriptedModel([final("recovered")])
        agent = self.agent(model)
        visible = []
        agent.on_event = visible.append
        with patch.object(agent.file_history, "start", side_effect=RuntimeError("history unavailable")):
            with self.assertRaisesRegex(RuntimeError, "history unavailable"):
                agent.run({"role": "user", "content": "task"})
        self.assertEqual(model.calls, [])
        saved = Session(self.storage.sessions, "main").events()
        self.assertEqual(saved[0]["message"], {"role": "user", "content": "task"})
        self.assertEqual(visible[0]["message"], saved[0]["message"])
        self.assertEqual(saved[-1]["type"], "error")
        self.assertEqual(saved[-1]["message"], "File history could not start: history unavailable")
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
        self.assertTrue(agent.file_history.status()["monitoring"])
        saved = self.session.events()
        self.assertEqual(saved[0]["message"]["content"], "task")
        self.assertEqual(saved[-1]["type"], "error")
        self.assertEqual(saved[-1]["message"], "context unavailable")


if __name__ == "__main__":
    unittest.main()
