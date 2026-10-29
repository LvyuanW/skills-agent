import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from skills_agent.file_history import WorkspaceHistory
from skills_agent.tools import ToolRegistry


class EditFileToolTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.history = WorkspaceHistory(self.workspace, self.root / "history.git", session_id="test")
        self.tools = ToolRegistry(self.workspace, file_history=self.history)
        self.tools.tool_call_id = "edit-1"
        self.tools.edit_session_id = "actual-child-session"

    def edit(self, operation, path="notes.txt", **kwargs):
        return json.loads(self.tools.execute("edit_file", json.dumps({
            "name": "Update task file", "operation": operation, "path": path, **kwargs,
        })))

    def test_tool_operations_return_serializable_results_and_recoverable_contents(self):
        created = self.edit("create", content="first\r\nlast\n")
        self.assertNotIn("error", created)
        self.assertEqual((self.workspace / "notes.txt").read_bytes(), b"first\r\nlast\n")
        replaced = self.edit("replace", old_text="first", new_text="updated")
        self.assertNotIn("error", replaced)
        before = subprocess.check_output([
            "git", "--git-dir", str(self.history.repository), "show",
            replaced["before_revision"] + ":notes.txt",
        ])
        self.assertEqual(before, b"first\r\nlast\n")
        moved = self.edit("move", destination="renamed.txt")
        self.assertNotIn("error", moved)
        self.assertFalse((self.workspace / "notes.txt").exists())
        self.assertEqual((self.workspace / "renamed.txt").read_bytes(), b"updated\r\nlast\n")
        deleted = self.edit("delete", "renamed.txt")
        self.assertNotIn("error", deleted)
        self.assertFalse((self.workspace / "renamed.txt").exists())
        saved = subprocess.check_output([
            "git", "--git-dir", str(self.history.repository), "show",
            deleted["before_revision"] + ":renamed.txt",
        ])
        self.assertEqual(saved, b"updated\r\nlast\n")

    def test_invalid_arguments_do_not_invoke_edit_or_initialize_history(self):
        cases = [
            {"operation": "create"},
            {"operation": "create", "content": "x", "old_text": "y"},
            {"operation": "replace", "old_text": "x"},
            {"operation": "replace", "old_text": "x", "new_text": None},
            {"operation": "delete", "content": "x"},
            {"operation": "move", "destination": ""},
        ]
        with patch.object(self.history, "edit", side_effect=AssertionError("must validate first")) as execute:
            for values in cases:
                with self.subTest(values=values):
                    result = json.loads(self.tools.execute("edit_file", json.dumps({
                        "name": "Edit", "path": "notes.txt", **values,
                    })))
                    self.assertIn("error", result)
                    self.assertFalse(result["executed"])
            execute.assert_not_called()
        self.assertFalse(self.history.repository.exists())

    def test_empty_content_and_whitespace_match_are_valid(self):
        self.assertNotIn("error", self.edit("create", content=""))
        self.assertEqual((self.workspace / "notes.txt").read_bytes(), b"")
        self.assertNotIn("error", self.edit("replace", old_text="", new_text="filled"))
        self.assertEqual((self.workspace / "notes.txt").read_text(), "filled")
        self.assertNotIn("error", self.edit("create", "spaces.txt", content="a\t b"))
        self.assertNotIn("error", self.edit("replace", "spaces.txt", old_text="\t ", new_text=""))
        self.assertEqual((self.workspace / "spaces.txt").read_bytes(), b"ab")

    def test_call_identity_is_passed_to_history(self):
        with patch.object(self.history, "edit", return_value={"changed": False}) as edit:
            self.assertEqual(self.edit("replace", old_text="a", new_text="a"), {"changed": False})
        edit.assert_called_once_with("replace", "notes.txt", old_text="a", new_text="a", tool_call_id="edit-1",
                                     session_id="actual-child-session")

    def test_partial_failure_keeps_recovery_information_in_tool_result(self):
        error = RuntimeError("after-version could not be saved")
        error.details = {"applied": True, "changed": True, "before_revision": "saved-before",
                         "operation_id": "recover-this-edit"}
        with patch.object(self.history, "edit", side_effect=error):
            result = self.edit("replace", old_text="a", new_text="b")
        self.assertIn("error", result)
        self.assertTrue(result["applied"])
        self.assertEqual(result["before_revision"], "saved-before")
        self.assertEqual(result["operation_id"], "recover-this-edit")

    def test_display_failure_does_not_erase_successful_edit_receipt(self):
        def display(activity):
            if activity is None:
                raise RuntimeError("display update failed")
        self.tools.on_activity = display
        result = self.edit("create", content="saved")
        self.assertNotIn("error", result)
        self.assertTrue(result["applied"])
        self.assertTrue(result["after_revision"])
        self.assertEqual(result["display_error"], "display update failed")
        self.assertEqual((self.workspace / "notes.txt").read_text(), "saved")


if __name__ == "__main__":
    unittest.main()
