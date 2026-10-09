"""Run against upstream ALE with ALE_SOURCE_DIR=/path/to/agents-last-exam."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
ALE_SOURCE = os.environ.get("ALE_SOURCE_DIR")
if ALE_SOURCE:
    sys.path.insert(0, ALE_SOURCE)
    import ale_run.agents
    ale_run.agents.__path__ = [str(ROOT / "adapters" / "ale"), *ale_run.agents.__path__]
    from ale_run.agents.skills_agent.config import SkillsAgentConfig
    from ale_run.agents.skills_agent.deployer import SkillsAgentDeployer
    from ale_run.base_interface.agent_deployer import AgentRunResult
    from ale_run.base_interface.trajectory import TrajectoryBuilder


def executor(path, **config):
    return SimpleNamespace(
        work_dir=str(path / "logs"), env={},
        sandbox=SimpleNamespace(os="linux", task_data_root=str(path / "task-data")),
        config=SkillsAgentConfig(package="skills-cli-agent==0.1.0", model="test", **config),
    )


def builder():
    return TrajectoryBuilder(agent_name="skills_agent", task_path="test/task", variant_index=0)


def write_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def message(role, content, **extra):
    return {"type": "message", "message": {"role": role, "content": content, **extra}}


@unittest.skipUnless(ALE_SOURCE, "Set ALE_SOURCE_DIR to test the real ALE contracts")
class AdapterTests(unittest.TestCase):
    def test_config_resolves_through_upstream_loader_and_factory(self):
        from ale_run.orchestration.config_loader import load_experiment
        from ale_run.orchestration.factory import build_config, resolve_agent

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "configs" / "agents").mkdir(parents=True)
            (root / "configs" / "environments").mkdir()
            shutil.copyfile(ROOT / "adapters/ale/skills_agent.yaml", root / "configs/agents/skills_agent.yaml")
            shutil.copyfile(ROOT / "adapters/ale/smoke.yaml", root / "smoke.yaml")
            shutil.copyfile(Path(ALE_SOURCE) / "configs/environments/docker.yaml", root / "configs/environments/docker.yaml")
            values = {"SKILLS_AGENT_MODEL": "test", "SKILLS_AGENT_PACKAGE": "skills-cli-agent==0.1.0",
                      "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_API_KEY": "test-key"}
            with patch.dict(os.environ, values):
                experiment = load_experiment(root / "smoke.yaml")
            deployer_cls, config_cls = resolve_agent(experiment.agents[0])
            config = build_config(config_cls, experiment.agents[0].config)
            self.assertIs(deployer_cls, SkillsAgentDeployer)
            self.assertEqual(config.package, values["SKILLS_AGENT_PACKAGE"])
            self.assertEqual(config.api_key, "test-key")
            self.assertEqual(experiment.concurrency, 1)
            self.assertFalse(experiment.auto_resume)
            self.assertEqual(experiment.max_attempts, 1)
            self.assertEqual(len(experiment.tasks), 1)
            task = Path(ALE_SOURCE) / "tasks" / experiment.tasks[0].path / "task_card.json"
            self.assertEqual(json.loads(task.read_text())["vm"]["snapshot"], "cpu-free-ubuntu")
            experiment.agents[0].executor = "local"
            with self.assertRaisesRegex(ValueError, "supported set"):
                resolve_agent(experiment.agents[0])

    def test_install_uses_container_interpreter_and_reports_failure(self):
        async def check(path):
            deployed = SkillsAgentDeployer(executor(path))
            process = SimpleNamespace(wait=AsyncMock(return_value=0))
            with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as start:
                await deployed.install()
            self.assertEqual(start.call_args.args, (
                sys.executable, "-m", "pip", "install", "skills-cli-agent==0.1.0",
            ))
            process.wait.return_value = 2
            with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                with self.assertRaisesRegex(RuntimeError, "installation failed"):
                    await deployed.install()
            deployed.executor.sandbox.os = "windows"
            with self.assertRaisesRegex(ValueError, "Linux sandbox"):
                await deployed.install()

        with TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    def test_launch_passes_keys_in_environment_and_reports_nonzero_exit(self):
        async def check(path):
            deployed = SkillsAgentDeployer(executor(path, provider="anthropic", api_key="secret-key",
                                                   skill_dirs=["/opt/task skills"], workspace=str(path)))
            deployed.executor.env = {"ANTHROPIC_API_KEY": "older-key"}
            process = SimpleNamespace(wait=AsyncMock(return_value=7), pid=42)
            with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as start:
                result = await deployed.launch("Exact task\nwith newlines\n")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.exit_code, 7)
            self.assertEqual(start.call_args.kwargs["env"]["ANTHROPIC_API_KEY"], "secret-key")
            self.assertNotIn("secret-key", str(start.call_args.args))
            self.assertIn("/opt/task skills", start.call_args.args)
            self.assertEqual(start.call_args.kwargs["cwd"], path.resolve())
            self.assertEqual((path / "logs/prompt.txt").read_text(), "Exact task\nwith newlines\n")

        with TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_cancellation_reaps_process_and_kills_descendants(self):
        async def check(path):
            deployed = SkillsAgentDeployer(executor(path))
            pids_file = path / "pids.json"
            code = (
                "import os, subprocess, sys, time, json; from pathlib import Path; "
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                f"Path({str(pids_file)!r}).write_text(json.dumps([os.getpid(), child.pid])); "
                "time.sleep(60)"
            )
            with patch.object(deployed, "_argv", return_value=[sys.executable, "-c", code]):
                task = asyncio.create_task(deployed.launch("wait"))
                try:
                    async def ready():
                        while not pids_file.exists():
                            await asyncio.sleep(0.01)
                    await asyncio.wait_for(ready(), timeout=5)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
            parent, child = json.loads(pids_file.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(parent, 0)
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(child)],
                                   capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)

        with TemporaryDirectory() as directory:
            asyncio.run(check(Path(directory)))

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_sigterm_to_deployer_stops_benchmark_shell_and_descendant(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            shell_pids = path / "shell-pids.json"
            benchmark_pid = path / "benchmark.pid"
            source = (
                "import os, subprocess, sys, time, json; from pathlib import Path; "
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                f"Path({str(shell_pids)!r}).write_text(json.dumps([os.getpgrp(), child.pid])); "
                "time.sleep(60)"
            )
            command = shlex.join([sys.executable, "-c", source])

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    self.rfile.read(int(self.headers["Content-Length"]))
                    body = {"choices": [{"finish_reason": "tool_calls", "message": {
                        "content": "", "tool_calls": [{"id": "wait", "type": "function", "function": {
                            "name": "shell", "arguments": json.dumps({"command": command}),
                        }}],
                    }}]}
                    encoded = json.dumps(body).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            # Mirror _sandbox_entry: instantiate and await the real deployer
            # in a wrapper process, then send SIGTERM to that wrapper alone.
            wrapper = f"""
import asyncio, signal, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {ALE_SOURCE!r})
import ale_run.agents
ale_run.agents.__path__ = [{str(ROOT / "adapters/ale")!r}, *ale_run.agents.__path__]
from ale_run.agents.skills_agent.config import SkillsAgentConfig
from ale_run.agents.skills_agent.deployer import SkillsAgentDeployer
original_start = asyncio.create_subprocess_exec
async def record_pid(*args, **kwargs):
    child = await original_start(*args, **kwargs)
    Path({str(benchmark_pid)!r}).write_text(str(child.pid))
    return child
asyncio.create_subprocess_exec = record_pid
config = SkillsAgentConfig(package="unused", model="test", api_key="test-key",
    base_url="http://127.0.0.1:{server.server_port}/v1")
executor = SimpleNamespace(config=config, work_dir={str(path / 'logs')!r}, env={{}},
    sandbox=SimpleNamespace(os="linux", task_data_root={str(path / 'workspace')!r}))
try:
    asyncio.run(SkillsAgentDeployer(executor).launch("Run the shell command"))
except asyncio.CancelledError:
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
"""
            process = subprocess.Popen(
                [sys.executable, "-c", wrapper], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
            child_groups = []
            try:
                deadline = time.monotonic() + 5
                while not shell_pids.exists() and time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), "Deployer wrapper exited before launching shell")
                    time.sleep(0.01)
                self.assertTrue(shell_pids.exists(), "Benchmark did not launch its shell")
                shell, descendant = json.loads(shell_pids.read_text())
                benchmark = int(benchmark_pid.read_text())
                child_groups = [benchmark, shell]
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                for pid in [benchmark, shell, descendant]:
                    state = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                    ).stdout.strip()
                    self.assertTrue(not state or state.startswith("Z"), f"{pid} survived: {state}")
                events = (path / "logs/session/events.jsonl").read_text()
                self.assertIn('"message":"Interrupted"', events)
            finally:
                if benchmark_pid.exists():
                    child_groups.append(int(benchmark_pid.read_text()))
                if shell_pids.exists():
                    child_groups.append(json.loads(shell_pids.read_text())[0])
                for group in [process.pid, *child_groups]:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.communicate()
                server.shutdown()
                server.server_close()
                thread.join()

    def test_parser_retains_calls_results_metrics_and_nested_children(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            config = executor(path).config
            command = "# " + "reference " * 1500
            events = [
                message("user", "original task"),
                message("assistant", [{"type": "text", "text": "running"}], tool_calls=[{
                    "id": "shell1", "name": "shell", "arguments": json.dumps({"command": command}),
                }], _raw={"usage": {"prompt_tokens": 30, "completion_tokens": 5,
                                   "prompt_tokens_details": {"cached_tokens": 20}}}),
                message("tool", '{"exit_code":1,"stdout":"","stderr":"failed"}', tool_call_id="shell1"),
                message("assistant", "finished", _raw={"usage": {"input_tokens": 10, "output_tokens": 2,
                        "cache_read_input_tokens": 4, "cache_creation_input_tokens": 3}}),
            ]
            write_events(path / "session/events.jsonl", events)
            write_events(path / "session/subagents/child/events.jsonl", [
                message("user", "PRIVATE_TASK"), message("assistant", "PRIVATE_CHILD_RESULT"),
            ])
            trace = builder()
            trace.add_step(source="user", message="original task")
            SkillsAgentDeployer.parse_artifacts(work_dir=path, config=config,
                                               run_result=AgentRunResult("completed"), builder=trace)
            result = trace.finalize(reward=None)
            self.assertEqual([step.source for step in result.steps], ["user", "agent", "environment", "agent"])
            self.assertEqual(result.steps[1].tool_calls[0].arguments["command"], command)
            observation = result.steps[2].observation.results[0]
            self.assertEqual(observation.tool_call_id, "shell1")
            self.assertTrue(observation.is_error)
            self.assertEqual(observation.content[0].text, events[2]["message"]["content"])
            self.assertEqual(result.final_metrics.total_input_tokens, 40)
            self.assertEqual(result.final_metrics.total_cache_read_tokens, 24)
            self.assertEqual(result.final_metrics.total_cache_creation_tokens, 3)
            self.assertEqual(result.subagent_trajectories[0].steps[-1].message, "PRIVATE_CHILD_RESULT")
            self.assertNotIn("PRIVATE_", str([step.model_dump() for step in result.steps]))

    def test_parser_reports_missing_truncated_and_interrupted_logs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            config = executor(path).config
            def parse():
                trace = builder()
                SkillsAgentDeployer.parse_artifacts(work_dir=path, config=config,
                                                   run_result=AgentRunResult("failed"), builder=trace)
                return trace.trajectory
            self.assertIn("Missing", parse().steps[0].message)
            write_events(path / "session/events.jsonl", [message("user", "task")])
            with (path / "session/events.jsonl").open("a") as output:
                output.write('{"type":')
            self.assertIn("1 malformed", parse().steps[-1].message)
            write_events(path / "session/events.jsonl", [message("user", "task"), {
                "type": "error", "message": "Interrupted", "partial_content": [{"type": "text", "text": "partial"}],
                "_raw": {"usage": {"prompt_tokens": 3, "completion_tokens": 1}},
            }])
            trace = parse()
            self.assertEqual(trace.steps[0].message, "partial")
            self.assertEqual(trace.steps[0].metrics.input_tokens, 3)
            self.assertEqual(trace.steps[-1].message, "Interrupted")

    def test_launch_executes_real_benchmark_and_parses_both_protocols(self):
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider), TemporaryDirectory() as directory:
                path = Path(directory)
                requests = []
                source = "from pathlib import Path; Path('output').mkdir(); Path('output/answer.txt').write_text('42')"
                command = shlex.join([sys.executable, "-c", source])

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_POST(self):
                        requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                        if provider == "openai":
                            body = {"choices": [{"finish_reason": "tool_calls" if len(requests) == 1 else "stop", "message": {
                                "content": "" if len(requests) == 1 else "done",
                            }}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
                            if len(requests) == 1:
                                body["choices"][0]["message"]["tool_calls"] = [{
                                    "id": "shell1", "type": "function", "function": {"name": "shell",
                                        "arguments": json.dumps({"command": command})},
                                }]
                        else:
                            body = {"stop_reason": "tool_use" if len(requests) == 1 else "end_turn",
                                    "content": [{"type": "tool_use", "id": "shell1", "name": "shell", "input": {"command": command}}]
                                    if len(requests) == 1 else [{"type": "text", "text": "done"}],
                                    "usage": {"input_tokens": 5, "output_tokens": 2}}
                        encoded = json.dumps(body).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(encoded)))
                        self.end_headers()
                        self.wfile.write(encoded)

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    deployed = SkillsAgentDeployer(executor(path, provider=provider, api_key="test-secret",
                                                           base_url=f"http://127.0.0.1:{server.server_port}/v1"))
                    result = asyncio.run(deployed.launch("Write output/answer.txt"))
                    errors = (path / "logs/stderr.log").read_text()
                    self.assertEqual(result.status, "completed", errors)
                    self.assertEqual((path / "task-data/output/answer.txt").read_text(), "42")
                    trace = builder()
                    SkillsAgentDeployer.parse_artifacts(work_dir=path / "logs", config=deployed.config,
                                                       run_result=result, builder=trace)
                    parsed = trace.finalize(reward=None)
                    self.assertEqual([step.source for step in parsed.steps], ["agent", "environment", "agent"])
                    self.assertEqual(parsed.final_metrics.total_input_tokens, 10)
                    self.assertEqual(len(requests), 2)
                    self.assertNotIn("test-secret", (path / "logs/session/events.jsonl").read_text())
                    self.assertNotIn("test-secret", (path / "logs/transcript.log").read_text() + errors)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
