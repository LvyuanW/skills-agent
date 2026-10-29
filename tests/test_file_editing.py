import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from skills_agent.file_editing import EditError, SafeFile
from skills_agent.file_history import WorkspaceHistory


class FileEditingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.history = WorkspaceHistory(self.workspace, self.root / "state/history.git", session_id="test")
        self.addCleanup(self.history.close)

    def edit(self, operation, path, **kwargs):
        return self.history.edit(operation, path, **kwargs)

    def test_incremental_create_replace_move_and_delete(self):
        self.edit("create", "scene.txt", content="<svg>\n<circle/>\n</svg>\n")
        self.edit("replace", "scene.txt", old_text="<circle/>", new_text="<g><circle/></g>")
        self.edit("replace", "scene.txt", old_text="<circle/>", new_text="<circle r='12'/>")
        self.edit("replace", "scene.txt", old_text="<circle r='12'/>", new_text="")
        self.assertEqual((self.workspace / "scene.txt").read_bytes(), b"<svg>\n<g></g>\n</svg>\n")
        result = self.edit("move", "scene.txt", destination="renamed.txt")
        self.assertTrue(result["applied"])
        self.assertFalse((self.workspace / "scene.txt").exists())
        self.edit("delete", "renamed.txt")
        self.assertFalse((self.workspace / "renamed.txt").exists())

    def test_ambiguous_missing_overlapping_and_empty_matches_do_not_mutate(self):
        target = self.workspace / "notes.txt"
        original = b"aaa\nrepeat\nrepeat\n"
        target.write_bytes(original)
        for old in ("absent", "repeat", "aa", ""):
            with self.subTest(old=old), self.assertRaises(EditError):
                self.edit("replace", "notes.txt", old_text=old, new_text="replacement")
            self.assertEqual(target.read_bytes(), original)
        self.assertFalse(self.history.repository.exists())

    def test_empty_file_can_be_filled_without_a_general_overwrite_operation(self):
        result = self.edit("create", "empty.txt", content="")
        first = result["after_revision"]
        unchanged = self.edit("replace", "empty.txt", old_text="", new_text="")
        self.assertFalse(unchanged["changed"])
        self.assertEqual(self.history.last_checkpoint, first)
        self.edit("replace", "empty.txt", old_text="", new_text="now filled\r\n")
        self.assertEqual((self.workspace / "empty.txt").read_bytes(), b"now filled\r\n")
        with self.assertRaises(EditError):
            self.edit("replace", "empty.txt", old_text="", new_text="overwrite")

    def test_unicode_tabs_crlf_and_executable_mode_are_preserved(self):
        target = self.workspace / "字面量.py"
        original = "\t前缀\r\nreplace-me\n后缀\r\nlast\t尾".encode()
        target.write_bytes(original)
        target.chmod(0o751)
        replacement = "\t新值\r\n第二行"
        self.edit("replace", str(target), old_text="replace-me", new_text=replacement)
        self.assertEqual(target.read_bytes(), original.replace(b"replace-me", replacement.encode()))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o751)
        with self.assertRaises(EditError):
            self.edit("replace", target, old_text="    前缀\n", new_text="wrong")

    def test_create_and_move_refuse_overwrite_and_require_existing_parents(self):
        target = self.workspace / "existing.txt"
        target.write_bytes(b"keep")
        for operation, path, kwargs in (
            ("create", "existing.txt", {"content": "overwrite"}),
            ("create", "missing/new.txt", {"content": "new"}),
            ("move", "existing.txt", {"destination": "missing/new.txt"}),
            ("move", "existing.txt", {"destination": "existing.txt"}),
        ):
            with self.subTest(operation=operation, path=path), self.assertRaises(EditError):
                self.edit(operation, path, **kwargs)
        (self.workspace / "occupied.txt").write_bytes(b"occupied")
        with self.assertRaises(EditError):
            self.edit("move", "existing.txt", destination="occupied.txt")
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertEqual((self.workspace / "occupied.txt").read_bytes(), b"occupied")
        self.assertFalse((self.workspace / "missing").exists())

    def test_noop_keeps_inode_and_does_not_initialize_history(self):
        target = self.workspace / "keep.txt"
        target.write_bytes(b"before\r\nkeep")
        before = target.stat()
        result = self.edit("replace", "keep.txt", old_text="keep", new_text="keep")
        self.assertFalse(result["changed"])
        self.assertFalse(result["applied"])
        self.assertEqual(target.stat().st_ino, before.st_ino)
        self.assertFalse(self.history.repository.exists())

    def test_binary_move_delete_and_text_validation(self):
        target = self.workspace / "binary.dat"
        for data in (b"\xffkeep", b"keep\x00data"):
            target.write_bytes(data)
            with self.assertRaises(EditError):
                self.edit("replace", "binary.dat", old_text="keep", new_text="changed")
            self.assertEqual(target.read_bytes(), data)
        target.chmod(0o640)
        self.edit("move", "binary.dat", destination="moved.bin")
        moved = self.workspace / "moved.bin"
        self.assertEqual(moved.read_bytes(), b"keep\x00data")
        self.assertEqual(stat.S_IMODE(moved.stat().st_mode), 0o640)
        self.edit("delete", "moved.bin")
        self.assertFalse(moved.exists())

    def test_invalid_payloads_and_directories_are_rejected(self):
        target = self.workspace / "existing.txt"
        target.write_text("old")
        (self.workspace / "directory").mkdir()
        for payload in (None, b"bytes", "new\x00value", "\udcff"):
            with self.subTest(payload=repr(payload)), self.assertRaises(EditError):
                self.edit("replace", "existing.txt", old_text="old", new_text=payload)
            with self.assertRaises(EditError):
                self.edit("create", "new.txt", content=payload)
        for path in ("missing", "directory"):
            with self.subTest(path=path), self.assertRaises(EditError):
                self.edit("delete", path)
        self.assertEqual(target.read_text(), "old")
        self.assertFalse((self.workspace / "new.txt").exists())

    def test_symlink_leaf_parent_and_dangling_links_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        actual = outside / "actual.txt"
        actual.write_text("keep")
        (self.workspace / "link").symlink_to(actual)
        (self.workspace / "directory-link").symlink_to(outside, target_is_directory=True)
        (self.workspace / "dangling").symlink_to(outside / "absent")
        for operation, path, kwargs in (
            ("delete", "link", {}),
            ("replace", "link", {"old_text": "keep", "new_text": "wrong"}),
            ("create", "dangling", {"content": "wrong"}),
            ("create", "directory-link/new", {"content": "wrong"}),
        ):
            with self.subTest(operation=operation, path=path), self.assertRaises(EditError):
                self.edit(operation, path, **kwargs)
        self.assertEqual(actual.read_text(), "keep")
        self.assertFalse((outside / "new").exists())

    def test_workspace_and_protected_paths_cannot_be_edited(self):
        outside = self.root / "outside.txt"
        outside.write_text("keep")
        for path in ("../outside.txt", str(outside), ".git/config", ".GIT/config", ".venv/a.py",
                     ".agent/log", str(self.history.repository / "config")):
            with self.subTest(path=path), self.assertRaises(EditError):
                self.edit("create", path, content="wrong")
        self.assertEqual(outside.read_text(), "keep")

    def test_case_aliases_of_runtime_storage_are_protected(self):
        storage = self.workspace / "Runtime-Data"
        storage.mkdir()
        (storage / "receipt.json").write_text("keep")
        history = WorkspaceHistory(self.workspace, self.repository_path(), session_id="case",
                                   excluded_roots=[storage])
        self.addCleanup(history.close)
        with self.assertRaisesRegex(EditError, "Protected"):
            history.edit("replace", "runtime-data/receipt.json", old_text="keep", new_text="wrong")
        self.assertEqual((storage / "receipt.json").read_text(), "keep")

    def repository_path(self):
        return self.root / "case-history.git"

    def test_external_preimage_change_is_not_overwritten(self):
        target = self.workspace / "target.txt"
        target.write_text("old")
        commit = self.history._commit_paths
        def changed(states, metadata, phase):
            result = commit(states, metadata, phase)
            if phase == "before":
                target.write_text("external writer")
            return result
        with patch.object(self.history, "_commit_paths", side_effect=changed):
            with self.assertRaises(EditError) as failure:
                self.edit("replace", "target.txt", old_text="old", new_text="new")
        self.assertFalse(failure.exception.details["applied"])
        self.assertIsNotNone(failure.exception.details["before_revision"])
        self.assertEqual(target.read_text(), "external writer")

    def test_parent_replaced_by_symlink_after_backup_is_refused(self):
        folder = self.workspace / "folder"
        folder.mkdir()
        (folder / "file.txt").write_text("inside")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("outside")
        commit = self.history._commit_paths
        def changed(states, metadata, phase):
            result = commit(states, metadata, phase)
            if phase == "before":
                folder.rename(self.workspace / "moved")
                folder.symlink_to(outside, target_is_directory=True)
            return result
        with patch.object(self.history, "_commit_paths", side_effect=changed):
            with self.assertRaises(EditError):
                self.edit("replace", "folder/file.txt", old_text="inside", new_text="new")
        self.assertEqual((outside / "file.txt").read_text(), "outside")
        self.assertEqual((self.workspace / "moved/file.txt").read_text(), "inside")

    def test_cleanup_failure_after_publication_reports_applied(self):
        original = os.unlink
        failed = False
        def cleanup(path, *args, **kwargs):
            nonlocal failed
            if str(path).startswith(".file-editing-") and not failed:
                failed = True
                raise PermissionError("cleanup failed after publication")
            return original(path, *args, **kwargs)
        with patch("skills_agent.file_editing.os.unlink", side_effect=cleanup):
            with self.assertRaises(EditError) as failure:
                self.edit("create", "created.txt", content="published")
        self.assertTrue(failure.exception.details["applied"])
        self.assertIsNotNone(failure.exception.details["after_revision"])
        self.assertEqual((self.workspace / "created.txt").read_text(), "published")

    def test_partial_move_reports_created_destination_and_keeps_recovery(self):
        (self.workspace / "source.bin").write_bytes(b"\xff\x00source")
        with patch.object(SafeFile, "delete", side_effect=PermissionError("source deletion failed")):
            with self.assertRaises(EditError) as failure:
                self.edit("move", "source.bin", destination="destination.bin")
        details = failure.exception.details
        self.assertTrue(details["applied"])
        self.assertEqual(details["applied_paths"], ["destination.bin"])
        self.assertIsNotNone(details["before_revision"])
        self.assertIsNotNone(details["after_revision"])
        self.assertEqual((self.workspace / "destination.bin").read_bytes(), b"\xff\x00source")
        self.assertEqual((self.workspace / "source.bin").read_bytes(), b"\xff\x00source")

    def test_feedback_is_bounded_and_shows_the_edit(self):
        target = self.workspace / "large.txt"
        target.write_text("x" * 100000 + "old")
        result = self.edit("replace", "large.txt", old_text="old", new_text="new")
        self.assertLess(len(json.dumps(result)), 20000)
        self.assertTrue(any("new" in line["text"] for line in result["excerpt"]["lines"]))
        self.assertTrue(target.read_text().endswith("new"))


if __name__ == "__main__":
    unittest.main()
