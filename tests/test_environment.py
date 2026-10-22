"""Environment snapshots describe the workspace and the actual shell executor."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.environment import environment_info
from skills_agent.execution import run_command


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()

    @unittest.skipUnless(shutil.which("git"), "Git integration")
    def test_git_detects_parent_repository_worktree_and_bare_repository(self):
        repository = self.workspace / "repository"
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        nested = repository / "nested" / "directory"
        nested.mkdir(parents=True)
        self.assertIn("- Is a git repository: true", environment_info(nested))
        self.assertIn(f"- Primary working directory: {nested}", environment_info(nested))

        subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                        "-c", "core.hooksPath=/dev/null",
                        "commit", "--allow-empty", "-qm", "initial"], check=True)
        worktree = self.workspace / "worktree"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "--detach",
                        "-q", str(worktree)], check=True)
        self.assertTrue((worktree / ".git").is_file())
        self.assertIn("- Is a git repository: true", environment_info(worktree))

        bare = self.workspace / "bare.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        self.assertIn("- Is a git repository: true", environment_info(bare))

    @unittest.skipUnless(shutil.which("git"), "Git integration")
    def test_non_repository_does_not_inherit_a_different_git_directory(self):
        repository = self.workspace / "repository"
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        unrelated = self.workspace / "unrelated"
        unrelated.mkdir()
        with patch.dict(os.environ, {"GIT_DIR": str(repository / ".git"),
                                    "GIT_WORK_TREE": str(repository),
                                    "ENVIRONMENT_TEST_SECRET": "do-not-output-this"}):
            result = environment_info(unrelated)
        self.assertIn("- Is a git repository: false", result)
        self.assertNotIn("do-not-output-this", result)
        self.assertNotIn("GIT_DIR", result)

    def test_git_probe_failure_and_unavailable_metadata_are_unknown(self):
        failures = (
            FileNotFoundError("git missing"),
            PermissionError("cannot execute git"),
            subprocess.TimeoutExpired("git", 1),
            subprocess.CompletedProcess([], 128, "", "fatal: dubious ownership"),
            subprocess.CompletedProcess([], 0, "", ""),
        )
        for failure in failures:
            with self.subTest(failure=failure), \
                    patch("skills_agent.environment.subprocess.run") as run, \
                    patch("skills_agent.environment.platform.system", side_effect=OSError("unavailable")), \
                    patch("skills_agent.environment.platform.release", return_value=""), \
                    patch("skills_agent.environment.sys.platform", ""):
                if isinstance(failure, Exception):
                    run.side_effect = failure
                else:
                    run.return_value = failure
                result = environment_info(self.workspace)
                self.assertIn("- Is a git repository: unknown", result)
                self.assertIn("- Platform: unknown", result)
                self.assertIn("- OS Version: unknown", result)
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["timeout"], 1)
                self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_unknown_workspace_skips_git_probe(self):
        with patch("skills_agent.environment.subprocess.run") as run:
            result = environment_info(None)
        self.assertIn("- Primary working directory: unknown", result)
        self.assertIn("- Is a git repository: unknown", result)
        run.assert_not_called()

    def test_snapshot_format_and_os_version(self):
        with patch("skills_agent.environment.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 128, "", "fatal: not a git repository")), \
                patch("skills_agent.environment.sys.platform", "linux"), \
                patch("skills_agent.environment.platform.system", return_value="Linux"), \
                patch("skills_agent.environment.platform.release", return_value="6.8.0"):
            result = environment_info(self.workspace)
        self.assertTrue(result.startswith("# Environment\n\nYou have been invoked in the following environment:"))
        self.assertIn("- Platform: linux", result)
        self.assertIn("- OS Version: Linux 6.8.0", result)
        self.assertEqual(len([line for line in result.splitlines() if line.startswith("- ")]), 5)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX shell executor")
    def test_shell_matches_tool_executor_and_ignores_login_shell(self):
        with patch.dict(os.environ, {"SHELL": "/nonexistent/login-shell"}):
            snapshot = environment_info(self.workspace)
            result = run_command('printf "%s" "$0"', cwd=self.workspace, shell=True,
                                 log_dir=self.workspace / "logs")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn(f"- Shell: {result['stdout']}", snapshot)
        self.assertNotIn("/nonexistent/login-shell", snapshot)

    def test_android_shell_matches_python_subprocess_selection(self):
        with patch("skills_agent.environment.sys.getandroidapilevel", create=True, return_value=35), \
                patch("skills_agent.environment.os.name", "posix"), \
                patch("skills_agent.environment.subprocess.run", side_effect=FileNotFoundError()):
            result = environment_info(self.workspace)
        self.assertIn("- Shell: /system/bin/sh", result)

    def test_cancellation_is_not_hidden_by_metadata_collection(self):
        with patch("skills_agent.environment.subprocess.run", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                environment_info(self.workspace)


if __name__ == "__main__":
    unittest.main()
