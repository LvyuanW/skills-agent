import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from skills_agent import file_history
from skills_agent.file_history import TransientScanError, WorkspaceHistory


class FileHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.repository = self.root / "data" / "history.git"

    def history(self, **kwargs):
        instance = WorkspaceHistory(self.workspace, self.repository,
                                    session_id=kwargs.pop("session_id", "test-session"), **kwargs)
        self.addCleanup(instance.close)
        return instance

    def git(self, *args, check=True):
        result = subprocess.run(["git", "--git-dir", str(self.repository), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout

    def content(self, revision, path):
        return self.git("cat-file", "blob", revision + ":" + path)

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("File history watcher did not record the expected state")

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "skills_agent.file_history",
                               "--repository", str(self.repository), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    def test_actual_watcher_records_created_modified_deleted_and_renamed_files(self):
        file = self.workspace / "original.txt"
        file.write_bytes(b"original\r\n")
        history = self.history().start()
        initial = history.last_checkpoint
        self.assertEqual(self.content(initial, "original.txt"), b"original\r\n")
        file.write_text("changed")
        self.wait_for(lambda: history.last_checkpoint != initial)
        changed = history.last_checkpoint
        self.assertEqual(self.content(changed, "original.txt"), b"changed")
        file.rename(self.workspace / "renamed.txt")
        self.wait_for(lambda: history.last_checkpoint != changed)
        renamed = history.last_checkpoint
        self.assertEqual(self.content(renamed, "renamed.txt"), b"changed")
        (self.workspace / "renamed.txt").unlink()
        self.wait_for(lambda: history.last_checkpoint != renamed)
        self.assertEqual(self.git("ls-tree", "--name-only", history.last_checkpoint), b"")
        self.assertEqual(self.content(renamed, "renamed.txt"), b"changed")
        self.assertFalse((self.workspace / ".git").exists())

    def test_dependency_directory_names_do_not_exclude_ordinary_files(self):
        (self.workspace / "venv").write_bytes(b"task input")
        (self.workspace / "node_modules").mkdir()
        (self.workspace / "node_modules" / "cache").write_bytes(b"dependency")
        checkpoint = self.history().checkpoint()
        self.assertEqual(self.content(checkpoint, "venv"), b"task input")
        self.assertEqual(self.git("ls-tree", "--name-only", checkpoint), b"venv\n")

    def test_status_reports_latest_error_by_time_not_observer_id(self):
        self.history().checkpoint()
        for name, date, error in (("aaaa", "2026-10-29T12:00:00+00:00", "new error"),
                                  ("zzzz", "2026-10-28T12:00:00+00:00", "old error")):
            record = {"recorded_at": date, "last_error": error, "monitoring": False}
            (self.repository / "runtime" / (name + ".json")).write_text(json.dumps(record))
        result = self.cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["last_error"], "new error")

    def test_no_change_does_not_commit_and_close_captures_pending_write(self):
        history = self.history().start()
        initial = history.last_checkpoint
        self.assertIs(history.start(), history)
        self.assertEqual(history.checkpoint("no change"), initial)
        (self.workspace / "last.txt").write_text("last write")
        history.close()
        self.assertFalse(history.status()["monitoring"])
        self.assertEqual(self.content(history.last_checkpoint, "last.txt"), b"last write")
        self.assertEqual(int(self.git("rev-list", "--count", "HEAD")), 2)
        self.assertFalse(history._thread.is_alive())

    def test_user_git_index_head_and_uncommitted_contents_remain_unchanged(self):
        def project_git(*args):
            result = subprocess.run(["git", "-C", str(self.workspace), *args],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return result.stdout
        project_git("init")
        project_git("config", "user.name", "User")
        project_git("config", "user.email", "user@example.invalid")
        file = self.workspace / "work.txt"
        file.write_text("committed")
        project_git("add", "work.txt")
        project_git("commit", "-m", "user commit")
        file.write_text("staged")
        project_git("add", "work.txt")
        file.write_text("unstaged")
        before_head = project_git("rev-parse", "HEAD")
        before_index = (self.workspace / ".git/index").read_bytes()
        before_status = project_git("status", "--porcelain")
        history = self.history()
        commit = history.checkpoint("capture user work")
        self.assertEqual(self.content(commit, "work.txt"), b"unstaged")
        self.assertEqual(before_head, project_git("rev-parse", "HEAD"))
        self.assertEqual(before_index, (self.workspace / ".git/index").read_bytes())
        self.assertEqual(before_status, project_git("status", "--porcelain"))
        self.assertNotIn(b".git/", self.git("ls-tree", "-r", "--name-only", commit))

    def test_ignored_outputs_exact_bytes_symlinks_and_unusual_names_are_preserved(self):
        (self.workspace / ".gitignore").write_text("*.csv\n")
        (self.workspace / ".gitattributes").write_text("* text eol=lf\n")
        name = "数据 [1]\t\n.csv"
        content = b"a\r\nb\0binary"
        (self.workspace / name).write_bytes(content)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside content")
        (self.workspace / "external").symlink_to(outside, target_is_directory=True)
        history = self.history()
        commit = history.checkpoint()
        self.assertEqual(self.content(commit, name), content)
        self.assertEqual(self.content(commit, "external"), os.fsencode(outside))
        paths = self.git("ls-tree", "-r", "-z", "--name-only", commit).split(b"\0")
        self.assertNotIn(b"external/secret.txt", paths)
        log = self.cli("log", "--path", name)
        self.assertEqual(log.returncode, 0, log.stderr)
        self.assertEqual(len(json.loads(log.stdout)), 1)

    def test_runtime_storage_and_cache_directories_are_excluded_without_gitignore(self):
        storage = self.workspace / "runtime-data"
        for relative in [".agent/session.log", ".venv/a.py", "node_modules/b.js", "__pycache__/c.pyc",
                         "runtime-data/session/events.jsonl"]:
            path = self.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not task content")
        (self.workspace / "result.csv").write_text("keep")
        history = self.history(excluded_roots=[storage])
        history.checkpoint()
        self.assertEqual(self.git("ls-tree", "-r", "--name-only", "HEAD"), b"result.csv\n")

    def test_two_observers_serialize_updates_and_share_one_history(self):
        one = self.history(session_id="one").start()
        two = self.history(session_id="two").start()
        (self.workspace / "result.txt").write_text("shared result")
        errors = []
        def save(instance):
            try:
                instance.checkpoint("concurrent capture")
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=save, args=(instance,)) for instance in (one, two)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        one.close()
        two.close()
        self.assertEqual(int(self.git("rev-list", "--count", "HEAD")), 2)
        self.assertEqual(self.content("HEAD", "result.txt"), b"shared result")
        self.assertIn(b"any writer", self.git("log", "-1", "--format=%B"))

    def test_separate_processes_share_repository_without_index_conflicts(self):
        history = self.history()
        history.checkpoint("initial")
        (self.workspace / "file.txt").write_text("new value")
        script = ("import sys; from skills_agent.file_history import WorkspaceHistory; "
                  "h=WorkspaceHistory(sys.argv[1],sys.argv[2],session_id=sys.argv[3]); "
                  "h.checkpoint('another process')")
        children = [subprocess.Popen([sys.executable, "-c", script, str(self.workspace),
                                      str(self.repository), str(index)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for index in range(2)]
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stdout + stderr)
        self.assertEqual(int(self.git("rev-list", "--count", "HEAD")), 2)
        self.assertEqual(self.content("HEAD", "file.txt"), b"new value")

    def test_busy_file_is_retried_without_stopping_watcher(self):
        history = self.history().start()
        initial = history.last_checkpoint
        original = history._entries
        attempted = threading.Event()
        def scan():
            if not attempted.is_set():
                attempted.set()
                raise TransientScanError("writer is still updating file")
            return original()
        with patch.object(history, "_entries", side_effect=scan):
            (self.workspace / "busy.txt").write_text("settled content")
            self.assertTrue(attempted.wait(5))
            self.wait_for(lambda: history.last_checkpoint != initial)
        self.assertTrue(history.status()["monitoring"])
        self.assertIsNone(history.last_error)
        self.assertIsNone(history.pending_error)
        self.assertEqual(self.content(history.last_checkpoint, "busy.txt"), b"settled content")

    @unittest.skipUnless(os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"), "POSIX directory fds")
    def test_parent_directory_replaced_by_symlink_cannot_capture_outside_file(self):
        folder = self.workspace / "subdir"
        folder.mkdir()
        (folder / "file.txt").write_text("inside")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("outside")
        history = self.history()
        original = history._regular_blob
        def race(path, info):
            folder.rename(self.workspace / "moved")
            folder.symlink_to(outside, target_is_directory=True)
            return original(path, info)
        with patch.object(history, "_regular_blob", side_effect=race):
            with self.assertRaises(TransientScanError):
                history.checkpoint()
        self.assertIsNone(history.last_error)
        commit = history.checkpoint()
        self.assertEqual(self.content(commit, "moved/file.txt"), b"inside")
        self.assertEqual(self.content(commit, "subdir"), os.fsencode(outside))
        self.assertNotIn(b"subdir/file.txt", self.git("ls-tree", "-r", "--name-only", commit))

    def test_read_commands_show_raw_content_and_compare_checkpoints(self):
        path = self.workspace / "file.txt"
        path.write_bytes(b"one\r\n")
        history = self.history()
        before = history.checkpoint()
        path.write_bytes(b"two\r\n")
        after = history.checkpoint()
        result = self.cli("show", before, "file.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"one\r\n")
        result = self.cli("diff", before, after, "--path", "file.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"+two", result.stdout)
        result = self.cli("log", "--limit", "1")
        self.assertEqual(json.loads(result.stdout)[0]["checkpoint"], after)
        self.assertEqual(json.loads(self.cli("status").stdout)["workspace"], str(self.workspace))
        self.assertNotEqual(self.cli("show", after, "../outside").returncode, 0)

    def test_failure_is_visible_and_cannot_be_reported_as_success_on_close(self):
        history = self.history().start()
        with patch.object(history, "_entries", side_effect=OSError("cannot read workspace")):
            with self.assertRaisesRegex(OSError, "cannot read workspace"):
                history.checkpoint()
        self.assertIn("cannot read workspace", history.last_error)
        with self.assertRaisesRegex(RuntimeError, "cannot read workspace"):
            history.close()
        status = json.loads(self.cli("status").stdout)
        self.assertFalse(status["monitoring"])
        self.assertIn("cannot read workspace", status["last_error"])

    def test_existing_repository_without_runtime_marker_is_not_modified(self):
        subprocess.run(["git", "init", "--bare", str(self.repository)],
                       check=True, capture_output=True)
        def contents():
            return {path.relative_to(self.repository): path.read_bytes()
                    for path in self.repository.rglob("*") if path.is_file()}
        before = contents()
        history = WorkspaceHistory(self.workspace, self.repository, session_id="misconfigured")
        with self.assertRaisesRegex(ValueError, "Refuse to take over"):
            history.checkpoint()
        with self.assertRaisesRegex(RuntimeError, "Refuse to take over"):
            history.close()
        self.assertEqual(contents(), before)
        self.assertFalse((self.repository / "workspace.json").exists())
        self.assertFalse((self.repository / "runtime").exists())

    def test_owner_marker_precedes_git_init_and_allows_retry_after_interruption(self):
        history = WorkspaceHistory(self.workspace, self.repository, session_id="interrupted")
        original = file_history._git
        def interrupted(repository, *args, **kwargs):
            if args[0] == "init":
                self.assertEqual(json.loads((repository / "workspace.json").read_text())["workspace"],
                                 str(self.workspace))
                raise OSError("initialization interrupted")
            return original(repository, *args, **kwargs)
        with patch.object(file_history, "_git", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "initialization interrupted"):
                history.checkpoint()
        with self.assertRaisesRegex(RuntimeError, "initialization interrupted"):
            history.close()
        resumed = self.history()
        commit = resumed.checkpoint("resumed initialization")
        self.assertEqual(self.git("rev-parse", "HEAD").strip().decode(), commit)


if __name__ == "__main__":
    unittest.main()
