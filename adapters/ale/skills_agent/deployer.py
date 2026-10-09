"""ALE adapter; the agent runtime is installed inside the task sandbox."""

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

from ale_run.base_interface.agent_deployer import AgentRunResult, BaseAgentDeployer
from ale_run.base_interface.trajectory import (
    ContentPart, Observation, StepMetrics, ToolCall, ToolResult, TrajectoryBuilder,
)

from .config import SkillsAgentConfig


async def _wait(process):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    # ALE's sandbox entry runs on the main thread and may receive SIGTERM
    # directly, instead of cancelling launch() through asyncio.wait_for().
    previous = signal.signal(
        signal.SIGTERM, lambda signum, frame: loop.call_soon_threadsafe(task.cancel),
    )
    try:
        try:
            return await process.wait()
        except asyncio.CancelledError:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=8)
            except asyncio.TimeoutError:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
    finally:
        signal.signal(signal.SIGTERM, previous)


class SkillsAgentDeployer(BaseAgentDeployer):
    default_executor = "sandbox"
    supported_executors = frozenset({"sandbox"})

    async def install(self):
        if self.executor.sandbox.os != "linux":
            raise ValueError("skills_agent requires a Linux sandbox")
        work_dir = Path(self.executor.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        with (work_dir / "install.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", self.config.package,
                stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True,
            )
            code = await _wait(process)
        if code:
            raise RuntimeError(f"skills_agent installation failed ({code}); see install.log")

    def _argv(self, prompt_file, workspace, work_dir):
        cfg = self.config
        argv = [
            sys.executable, "-m", "skills_agent.benchmark",
            "--prompt-file", str(prompt_file), "--workspace", str(workspace),
            "--log-dir", str(work_dir), "--provider", cfg.provider,
            "--model", cfg.model, "--context-chars", str(cfg.context_chars),
            "--max-output-tokens", str(cfg.max_output_tokens),
        ]
        if cfg.base_url:
            argv.extend(["--base-url", cfg.base_url])
        for directory in cfg.skill_dirs:
            argv.extend(["--skill-dir", directory])
        return argv

    async def launch(self, prompt):
        cfg = self.config
        work_dir = Path(self.executor.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        workspace = Path(cfg.workspace or self.executor.sandbox.task_data_root).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        prompt_file = work_dir / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        env = {**os.environ, **(self.executor.env or {})}
        if cfg.api_key is not None:
            env[f"{cfg.provider.upper()}_API_KEY"] = cfg.api_key
        transcript = work_dir / "transcript.log"
        stderr = work_dir / "stderr.log"
        start = time.monotonic()
        with transcript.open("wb") as output, stderr.open("wb") as errors:
            process = await asyncio.create_subprocess_exec(
                *self._argv(prompt_file, workspace, work_dir),
                stdin=asyncio.subprocess.DEVNULL, stdout=output, stderr=errors,
                cwd=workspace, env=env, start_new_session=True,
            )
            code = await _wait(process)
        return AgentRunResult(
            status="completed" if code == 0 else "failed",
            transcript_path=str(transcript), stderr_path=str(stderr),
            pid=process.pid, exit_code=code, duration_s=time.monotonic() - start,
            error=f"skills_agent exited with {code}; see transcript.log and stderr.log" if code else None,
        )

    @classmethod
    def parse_artifacts(cls, *, work_dir, config, run_result, builder):
        session_dir = Path(work_dir) / "session"
        cls._parse_session(session_dir, builder, skip_first_user=True)
        for path in sorted((session_dir / "subagents").glob("*/events.jsonl")):
            child = TrajectoryBuilder(
                episode_id=path.parent.name, agent_name="skills_agent",
                model=config.model, task_path=builder.trajectory.task_path,
                variant_index=builder.trajectory.variant_index,
            )
            failed = cls._parse_session(path.parent, child)
            builder.trajectory.subagent_trajectories.append(child.finalize(
                reward=None, status="failed" if failed else "completed",
            ))

    @staticmethod
    def _parse_session(session_dir, builder, *, skip_first_user=False):
        path = session_dir / "events.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            builder.add_step(source="system", message=f"Missing or unreadable log: {path}")
            return True
        malformed = 0
        failed = False
        finished = False
        parsed = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                kind = event["type"]
                if kind == "message":
                    message = event["message"]
                    role = message["role"]
                    if role == "user":
                        finished = False
                        if skip_first_user:
                            skip_first_user = False
                        else:
                            builder.add_step(source="user", message=_text(message.get("content")))
                    elif role == "assistant":
                        calls = [ToolCall(
                            id=call["id"], name=call["name"],
                            arguments=json.loads(call["arguments"]),
                        ) for call in message.get("tool_calls", [])]
                        builder.add_step(
                            source="agent", message=_text(message.get("content")),
                            tool_calls=calls, metrics=_metrics(message.get("_raw", {})),
                        )
                        finished = not calls
                    elif role == "tool":
                        finished = False
                        content = message["content"]
                        try:
                            result = json.loads(content)
                        except (ValueError, TypeError):
                            result = {}
                        is_error = isinstance(result, dict) and bool(
                            result.get("error") or result.get("exit_code", 0)
                        )
                        builder.add_step(source="environment", observation=Observation(results=[
                            ToolResult(
                                tool_call_id=message["tool_call_id"], is_error=is_error,
                                content=[ContentPart(type="text", text=content)],
                            ),
                        ]))
                    else:
                        raise ValueError("Unknown message role")
                elif kind == "error":
                    failed = True
                    if event.get("partial_content") or event.get("_raw"):
                        builder.add_step(
                            source="agent", message=_text(event.get("partial_content")),
                            metrics=_metrics(event.get("_raw", {})), extra={"partial": True},
                        )
                    builder.add_step(source="system", message=event["message"])
                elif kind == "subagent_summary":
                    builder.add_step(
                        source="system", message=f"sub-agent: {event['goal']}",
                        extra={"status": event["status"]},
                    )
                parsed += 1
            except (ValueError, TypeError, KeyError, AttributeError):
                malformed += 1
        if malformed or not parsed or (not finished and not failed):
            builder.add_step(
                source="system",
                message=(f"Incomplete session log: {malformed} malformed records, "
                         f"{parsed} parsed records, final response recorded: {finished}."),
            )
        return failed or bool(malformed) or not finished


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in (content or []) if block.get("type") == "text")


def _metrics(raw):
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details") or {}
    return StepMetrics(
        input_tokens=usage.get("input_tokens", usage.get("prompt_tokens")),
        output_tokens=usage.get("output_tokens", usage.get("completion_tokens")),
        cache_read_tokens=usage.get("cache_read_input_tokens", details.get("cached_tokens")),
        cache_creation_tokens=usage.get("cache_creation_input_tokens"),
    )
