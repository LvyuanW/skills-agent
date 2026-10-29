import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from skills_agent.execution import ToolCleanupError
from skills_agent.sandbox import DockerSandbox


def result(code=0, stdout="", **extra):
    return {"exit_code": code, "stdout": stdout, "stderr": "", **extra}


class SandboxTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        self.sandbox = DockerSandbox("test-image", self.workspace)

    def test_success_uses_bounded_executor_and_preserves_container_options(self):
        with patch("skills_agent.sandbox.run_command", return_value=result(stdout="done")) as run:
            self.assertEqual(self.sandbox.run("echo done", timeout_seconds=12, log_dir=self.workspace / "logs"),
                             result(stdout="done"))
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["docker", "run", "--rm", "--network", "none"])
        self.assertIn(f"{self.workspace.resolve()}:{self.workspace.resolve()}:rw", command)
        self.assertEqual(command[-4:], ["test-image", "sh", "-lc", "echo done"])
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 12)
        self.assertEqual(run.call_args.kwargs["log_dir"], self.workspace / "logs")
        self.assertTrue(callable(run.call_args.kwargs["cleanup"]))

    def test_timeout_removes_container_and_keeps_partial_result(self):
        timed_out = result(124, "before timeout", timed_out=True, process_returncode=-15)
        with patch("skills_agent.sandbox.run_command", side_effect=[timed_out, result()]) as run:
            self.assertEqual(self.sandbox.run("sleep 60", timeout_seconds=.2), timed_out)
        name = run.call_args_list[0].args[0][6]
        self.assertEqual(run.call_args_list[1].args[0], ["docker", "rm", "-f", name])
        self.assertGreater(run.call_args_list[1].kwargs["timeout_seconds"], 0)
        self.assertLessEqual(run.call_args_list[1].kwargs["timeout_seconds"], 5)

    def test_images_have_a_writable_mount_while_session_records_remain_read_only(self):
        workspace = self.workspace / "task"
        workspace.mkdir()
        sessions = self.workspace / "runtime" / "sessions"
        sessions.mkdir(parents=True)
        images = self.workspace / "runtime" / "images" / "main"
        sandbox = DockerSandbox("test-image", workspace, read_roots=[sessions], image_directory=images)
        self.assertFalse(images.exists())
        for selected in (images, images / "subagents" / "child"):
            with self.subTest(images=selected), patch("skills_agent.sandbox.run_command", return_value=result()) as run:
                sandbox.run("render-image", image_directory=selected)
            arguments = run.call_args.args[0]
            self.assertIn(f"{sessions.resolve()}:{sessions.resolve()}:ro", arguments)
            self.assertIn(f"{selected.resolve()}:{selected.resolve()}:rw", arguments)
            self.assertIn(f"SKILLS_AGENT_IMAGE_DIR={selected.resolve()}", arguments)
            self.assertTrue(selected.is_dir())

    def test_already_removed_container_is_confirmed_with_successful_inventory(self):
        with patch("skills_agent.sandbox.run_command", side_effect=[result(7), result(1), result()]) as run:
            self.assertEqual(self.sandbox.run("exit 7")["exit_code"], 7)
        self.assertEqual(run.call_args_list[-1].args[0],
                         ["docker", "container", "ls", "--all", "--format", "{{.Names}}"])

    def test_nonzero_exit_cleanup_masks_cancel_signals_and_restores_handlers(self):
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def run(command, **kwargs):
            if command[1] == "run":
                return result(7)
            for signum in handlers:
                self.assertEqual(signal.getsignal(signum), signal.SIG_IGN)
            return result()

        with patch("skills_agent.sandbox.run_command", side_effect=run):
            self.assertEqual(self.sandbox.run("exit 7")["exit_code"], 7)
        self.assertEqual({sig: signal.getsignal(sig) for sig in handlers}, handlers)

    def test_failed_or_timed_out_cleanup_is_not_an_ordinary_tool_failure(self):
        for removal, inventory in ((result(1), result(1)), (result(124, timed_out=True), None)):
            with self.subTest(removal=removal), patch("skills_agent.sandbox.run_command") as run:
                run.side_effect = [result(124, timed_out=True), removal, inventory]
                with self.assertRaises(ToolCleanupError):
                    self.sandbox.run("sleep 60", timeout_seconds=.2)

    def test_a_container_still_in_inventory_is_a_cleanup_failure(self):
        def run(command, **kwargs):
            if command[1] == "run":
                self.name = command[6]
                return result(124, timed_out=True)
            if command[1] == "rm":
                return result(1)
            return result(stdout=self.name + "\n")

        with patch("skills_agent.sandbox.run_command", side_effect=run), \
                self.assertRaises(ToolCleanupError):
            self.sandbox.run("sleep 60", timeout_seconds=.2)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX process groups required")
    def test_real_client_timeout_also_stops_a_detached_simulated_container(self):
        # The fake daemon's child escapes the CLI group, like a Docker container.
        # Only the subsequent docker rm can stop its writes.
        driver = self.workspace / "docker.py"
        driver.write_text('''import os, signal, subprocess, sys, time
from pathlib import Path
root = Path(os.environ["SKILLS_AGENT_TEST_DOCKER_ROOT"])
if sys.argv[1] == "run":
    name = sys.argv[sys.argv.index("--name") + 1]
    source = "import sys,time; from pathlib import Path; p=Path(sys.argv[1]);\\nwhile True:\\n with p.open('a') as f: f.write('.')\\n time.sleep(.02)"
    child = subprocess.Popen([sys.executable, "-c", source, str(root / "heartbeat")],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    (root / "daemon.pid").write_text(str(child.pid))
    (root / name).write_text(str(child.pid))
    print("container started", flush=True)
    time.sleep(60)
elif sys.argv[1] == "rm":
    path = root / sys.argv[-1]
    if path.exists():
        try: os.kill(int(path.read_text()), signal.SIGKILL)
        except ProcessLookupError: pass
        path.unlink()
elif sys.argv[1:3] == ["container", "ls"]:
    print("\\n".join(path.name for path in root.glob("skills-agent-*")))
else:
    sys.exit(2)
''')
        binary = self.workspace / "docker"
        binary.write_text('#!/bin/sh\nexec "$SKILLS_AGENT_TEST_PYTHON" "$SKILLS_AGENT_TEST_DOCKER" "$@"\n')
        binary.chmod(0o755)
        environment = {
            "PATH": str(self.workspace) + os.pathsep + os.environ.get("PATH", ""),
            "SKILLS_AGENT_TEST_PYTHON": sys.executable,
            "SKILLS_AGENT_TEST_DOCKER": str(driver),
            "SKILLS_AGENT_TEST_DOCKER_ROOT": str(self.workspace),
        }
        began = time.monotonic()
        try:
            with patch.dict(os.environ, environment):
                observed = self.sandbox.run("sleep 60", timeout_seconds=1, log_dir=self.workspace / "logs")
            self.assertTrue(observed["timed_out"])
            self.assertEqual(observed["exit_code"], 124)
            self.assertIn("container started", observed["stdout"], observed)
            self.assertLess(time.monotonic() - began, 10)
            self.assertTrue(Path(observed["stdout_path"]).is_file())
            self.assertEqual(list(self.workspace.glob("skills-agent-*")), [])
            heartbeat = self.workspace / "heartbeat"
            self.assertTrue(heartbeat.exists())
            size = heartbeat.stat().st_size
            time.sleep(.1)
            self.assertEqual(heartbeat.stat().st_size, size)
        finally:
            pid_file = self.workspace / "daemon.pid"
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
