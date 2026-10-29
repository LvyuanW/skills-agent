import json
from pathlib import Path
import subprocess
import tempfile
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

    def test_start_failure_is_reported_and_model_does_not_run(self):
        model = ScriptedModel([])
        agent = self.agent(model)
        with patch.object(agent.file_history, "start", side_effect=RuntimeError("history unavailable")):
            with self.assertRaisesRegex(RuntimeError, "history unavailable"):
                agent.run({"role": "user", "content": "task"})
        self.assertEqual(model.calls, [])
        self.assertIn("history unavailable", self.session.events()[-1]["message"])


if __name__ == "__main__":
    unittest.main()
