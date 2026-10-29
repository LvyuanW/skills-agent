import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from skills_agent import file_history
from skills_agent.file_editing import EditError
from skills_agent.file_history import WorkspaceHistory


class FileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.repository = self.root / "data/history.git"

    def history(self, **kwargs):
        instance = WorkspaceHistory(self.workspace, self.repository,
                                    session_id=kwargs.pop("session_id", "test-session"), **kwargs)
        self.addCleanup(instance.close)
        return instance

    def git(self, *args):
        return subprocess.check_output(["git", "--git-dir", str(self.repository), *args], stderr=subprocess.PIPE)

    def content(self, revision, path):
        return self.git("cat-file", "blob", revision + ":" + path)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "skills_agent.file_history", "--repository",
                               str(self.repository), *args], capture_output=True, timeout=10)

    def records(self):
        return [json.loads(path.read_text()) for path in (self.repository / "operations").glob("*.json")]

    def test_start_status_checkpoint_close_never_scan_watch_or_initialize(self):
        (self.workspace / "input.txt").write_text("input")
        history = self.history()
        with patch.object(file_history, "_git", side_effect=AssertionError("unexpected Git")), \
                patch.object(os, "walk", side_effect=AssertionError("unexpected scan")):
            self.assertIs(history.start(), history)
            self.assertIs(history.start(), history)
            (self.workspace / "generated.txt").write_text("program output")
            self.assertIsNone(history.checkpoint())
            self.assertEqual(history.status()["mode"], "editing-only")
            self.assertFalse(history.status()["monitoring"])
            history.close()
        self.assertFalse(self.repository.exists())
        self.assertFalse((self.workspace / ".git").exists())

    def test_first_edit_records_only_target_before_and_after_and_noop_adds_nothing(self):
        (self.workspace / "untouched.txt").write_text("private input")
        (self.workspace / "target.txt").write_bytes(b"old\r\n")
        history = self.history()
        result = history.edit("replace", "target.txt", old_text="old", new_text="new")
        self.assertEqual(self.content(result["before_revision"], "target.txt"), b"old\r\n")
        self.assertEqual(self.content(result["after_revision"], "target.txt"), b"new\r\n")
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"), b"target.txt\n")
        commits = self.git("rev-list", "--count", "HEAD")
        history.edit("replace", "target.txt", old_text="new", new_text="new")
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), commits)
        self.assertEqual(len(self.records()), 1)

    def test_sequential_edits_reuse_matching_preimage_and_preserve_operation_metadata(self):
        history = self.history()
        first = history.edit("create", "new.txt", content="one", tool_call_id="call-one")
        second = history.edit("replace", "new.txt", old_text="one", new_text="two",
                              tool_call_id="child-call", session_id="child-session")
        self.assertEqual(second["before_revision"], first["after_revision"])
        records = {item["tool_call_id"]: item for item in self.records()}
        self.assertEqual(records["child-call"]["session_id"], "child-session")
        self.assertEqual(records["child-call"]["status"], "succeeded")
        self.assertEqual(records["child-call"]["after_revision"], second["after_revision"])
        self.assertTrue(records["child-call"]["applied"])

    def test_delete_and_move_binary_contents_can_be_read_from_both_revisions(self):
        (self.workspace / "binary.bin").write_bytes(b"\xff\x00binary\r\n")
        history = self.history()
        moved = history.edit("move", "binary.bin", destination="renamed.bin")
        self.assertEqual(self.content(moved["before_revision"], "binary.bin"), b"\xff\x00binary\r\n")
        self.assertEqual(self.content(moved["after_revision"], "renamed.bin"), b"\xff\x00binary\r\n")
        removed = history.edit("delete", "renamed.bin")
        self.assertEqual(self.content(removed["before_revision"], "renamed.bin"), b"\xff\x00binary\r\n")
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", removed["after_revision"]), b"")

    def test_ignored_outputs_and_unusual_names_preserve_exact_bytes_without_filters(self):
        (self.workspace / ".gitignore").write_text("*.csv\n")
        (self.workspace / ".gitattributes").write_text("* text eol=lf\n")
        name = "数据 [1]\t\n.csv"
        before = b"a\r\nold\r\nlast"
        (self.workspace / name).write_bytes(before)
        result = self.history().edit("replace", name, old_text="old", new_text="new")
        self.assertEqual(self.content(result["before_revision"], name), before)
        self.assertEqual(self.content(result["after_revision"], name), before.replace(b"old", b"new"))
        self.assertEqual(self.git("ls-tree", "-r", "-z", "--name-only", "HEAD"), name.encode() + b"\0")

    def test_user_git_index_head_and_unrelated_work_remain_unchanged(self):
        def project_git(*args):
            return subprocess.check_output(["git", "-C", str(self.workspace), *args], stderr=subprocess.PIPE)
        project_git("init")
        project_git("config", "user.name", "User")
        project_git("config", "user.email", "user@example.invalid")
        target = self.workspace / "work.txt"
        target.write_text("committed")
        project_git("add", "work.txt")
        project_git("commit", "-m", "user commit")
        target.write_text("staged")
        project_git("add", "work.txt")
        target.write_text("unstaged keep")
        index = (self.workspace / ".git/index").read_bytes()
        head = project_git("rev-parse", "HEAD")
        self.history().edit("replace", "work.txt", old_text="unstaged", new_text="edited")
        self.assertEqual((self.workspace / ".git/index").read_bytes(), index)
        self.assertEqual(project_git("rev-parse", "HEAD"), head)
        self.assertEqual(target.read_text(), "edited keep")

    def test_two_instances_serialize_edits_to_one_file(self):
        (self.workspace / "shared.txt").write_text("left right")
        one, two = self.history(session_id="one"), self.history(session_id="two")
        errors = []
        def edit(history, old, new):
            try:
                history.edit("replace", "shared.txt", old_text=old, new_text=new)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=edit, args=(one, "left", "LEFT")),
                   threading.Thread(target=edit, args=(two, "right", "RIGHT"))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual((self.workspace / "shared.txt").read_text(), "LEFT RIGHT")
        self.assertEqual(self.content("HEAD", "shared.txt"), b"LEFT RIGHT")
        self.assertEqual({record["session_id"] for record in self.records()}, {"one", "two"})

    def test_separate_processes_serialize_edits_without_index_conflicts(self):
        (self.workspace / "shared.txt").write_text("left right")
        script = ("import sys;from skills_agent.file_history import WorkspaceHistory;"
                  "h=WorkspaceHistory(sys.argv[1],sys.argv[2],session_id=sys.argv[3]);"
                  "h.edit('replace','shared.txt',old_text=sys.argv[3],new_text=sys.argv[3].upper())")
        children = [subprocess.Popen([sys.executable, "-c", script, str(self.workspace),
                                      str(self.repository), word], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for word in ("left", "right")]
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stdout + stderr)
        self.assertEqual(self.content("HEAD", "shared.txt"), b"LEFT RIGHT")
        self.assertEqual(list(self.repository.glob("runtime-index-*")), [])

    def test_failure_before_mutation_keeps_preimage_and_failed_operation(self):
        (self.workspace / "target.txt").write_text("old")
        history = self.history()
        with patch("skills_agent.file_editing.PreparedEdit.apply", side_effect=PermissionError("cannot write")):
            with self.assertRaises(EditError) as failure:
                history.edit("replace", "target.txt", old_text="old", new_text="new")
        details = failure.exception.details
        self.assertFalse(details["applied"])
        self.assertEqual(self.content(details["before_revision"], "target.txt"), b"old")
        self.assertEqual((self.workspace / "target.txt").read_text(), "old")
        self.assertEqual(self.records()[0]["status"], "failed")

    def test_history_failure_after_write_reports_applied_and_preserves_recovery_revision(self):
        (self.workspace / "target.txt").write_text("old")
        history = self.history()
        commit = history._commit_paths
        def fail(states, metadata, phase):
            if phase != "before":
                raise OSError("history disk failure")
            return commit(states, metadata, phase)
        with patch.object(history, "_commit_paths", side_effect=fail):
            with self.assertRaises(EditError) as failure:
                history.edit("replace", "target.txt", old_text="old", new_text="new")
        details = failure.exception.details
        self.assertTrue(details["applied"])
        self.assertIsNone(details["after_revision"])
        self.assertEqual(self.content(details["before_revision"], "target.txt"), b"old")
        self.assertEqual((self.workspace / "target.txt").read_text(), "new")
        self.assertEqual(self.records()[0]["status"], "failed")
        self.assertTrue(self.records()[0]["applied"])

    def test_foreign_repository_without_owner_marker_is_not_modified(self):
        subprocess.run(["git", "init", "--bare", str(self.repository)], check=True, capture_output=True)
        before = {path.relative_to(self.repository): path.read_bytes()
                  for path in self.repository.rglob("*") if path.is_file()}
        with self.assertRaises(EditError):
            self.history().edit("create", "target.txt", content="new")
        self.assertFalse((self.workspace / "target.txt").exists())
        after = {path.relative_to(self.repository): path.read_bytes()
                 for path in self.repository.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_interruption_before_and_after_publication_is_recorded_then_reraised(self):
        target = self.workspace / "target.txt"
        target.write_text("old")
        history = self.history()
        with patch("skills_agent.file_editing.PreparedEdit.apply", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                history.edit("replace", "target.txt", old_text="old", new_text="new")
        interrupted = self.records()[0]
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertFalse(interrupted["applied"])
        self.assertEqual(target.read_text(), "old")
        commit = history._commit_paths
        def interrupt(states, metadata, phase):
            if phase == "after": raise SystemExit(2)
            return commit(states, metadata, phase)
        with patch.object(history, "_commit_paths", side_effect=interrupt):
            with self.assertRaises(SystemExit):
                history.edit("replace", "target.txt", old_text="old", new_text="new")
        applied = next(record for record in self.records() if record["applied"])
        self.assertEqual(applied["status"], "interrupted")
        self.assertIsNotNone(applied["after_revision"])
        self.assertEqual(target.read_text(), "new")
        self.assertEqual(self.content(applied["before_revision"], "target.txt"), b"old")

    def test_initialization_failure_has_metadata_but_no_commit_and_remains_inspectable(self):
        history = self.history()
        original = file_history._git
        def fail(repository, *args, **kwargs):
            if args[0] == "init": raise OSError("initialization interrupted")
            return original(repository, *args, **kwargs)
        with patch.object(file_history, "_git", side_effect=fail):
            with self.assertRaises(EditError) as failure:
                history.edit("create", "target.txt", content="new")
        self.assertFalse(failure.exception.details["applied"])
        self.assertFalse((self.workspace / "target.txt").exists())
        self.assertEqual(self.records()[0]["status"], "failed")
        status = self.cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertFalse(json.loads(status.stdout)["initialized"])
        self.assertIsNone(json.loads(status.stdout)["checkpoint"])
        self.assertEqual(json.loads(self.cli("log").stdout), [])
        self.history().edit("create", "target.txt", content="resumed")
        self.assertEqual(self.content("HEAD", "target.txt"), b"resumed")

    def test_interrupt_after_syscall_before_callback_records_uncertainty_and_actual_result(self):
        target = self.workspace / "target.txt"
        target.write_text("old")
        original = os.replace
        interrupted = False
        def interrupt_after_replace(source, destination, **kwargs):
            nonlocal interrupted
            result = original(source, destination, **kwargs)
            if kwargs.get("src_dir_fd") is not None and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return result
        with patch("skills_agent.file_editing.os.replace", side_effect=interrupt_after_replace):
            with self.assertRaises(KeyboardInterrupt):
                self.history().edit("replace", "target.txt", old_text="old", new_text="new")
        record = self.records()[0]
        self.assertEqual(record["status"], "interrupted")
        self.assertIsNone(record["applied"])
        self.assertTrue(record["changed"])
        self.assertEqual(record["application_state"], "uncertain")
        self.assertEqual(record["attempted_paths"], ["target.txt"])
        self.assertEqual(target.read_text(), "new")
        self.assertEqual(self.content(record["after_revision"], "target.txt"), b"new")

    def test_legacy_history_remains_readable_and_only_new_target_is_added(self):
        subprocess.run(["git", "init", "--bare", "--initial-branch=history", str(self.repository)],
                       check=True, capture_output=True)
        (self.repository / "workspace.json").write_text(json.dumps({"format": 1, "workspace": str(self.workspace)}))
        blob = file_history._git(self.repository, "hash-object", "-w", "--stdin", input=b"legacy bytes").strip()
        tree = file_history._git(self.repository, "mktree", input=b"100644 blob " + blob + b"\tlegacy.txt\n").strip()
        old = file_history._git(self.repository, "commit-tree", tree.decode(), input=b"old full snapshot\n").strip().decode()
        file_history._git(self.repository, "update-ref", "refs/heads/history", old)
        self.assertEqual(self.cli("show", old, "legacy.txt").stdout, b"legacy bytes")
        result = self.history().edit("create", "new.txt", content="new tool file")
        self.assertEqual(self.content(result["after_revision"], "legacy.txt"), b"legacy bytes")
        self.assertEqual(self.content(old, "legacy.txt"), b"legacy bytes")
        self.assertEqual(json.loads(self.cli("status").stdout)["mode"], "editing-only")

    def test_cli_status_log_diff_and_raw_show_are_read_only_and_work_before_first_edit(self):
        status = self.cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertFalse(json.loads(status.stdout)["initialized"])
        self.assertFalse(json.loads(status.stdout)["monitoring"])
        self.assertEqual(json.loads(self.cli("log").stdout), [])
        self.assertFalse(self.repository.exists())
        history = self.history()
        first = history.edit("create", "file.txt", content="one\r\n")
        second = history.edit("replace", "file.txt", old_text="one", new_text="two")
        self.assertEqual(self.cli("show", first["after_revision"], "file.txt").stdout, b"one\r\n")
        diff = self.cli("diff", second["before_revision"], second["after_revision"], "--path", "file.txt")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertIn(b"+two", diff.stdout)
        self.assertEqual(len(json.loads(self.cli("log", "--path", "file.txt", "--limit", "1").stdout)), 1)
        self.assertNotEqual(self.cli("show", "HEAD", "../outside").returncode, 0)


if __name__ == "__main__":
    unittest.main()
