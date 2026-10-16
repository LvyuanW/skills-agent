"""Harbor adapter for the published skills-agent wheel, without core patches."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import tomllib
from dataclasses import replace

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import (
    AgentAuthenticationError, BaseInstalledAgent, NonZeroAgentExitCodeError,
    OutputTokenExceededError, ContextWindowExceededError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage


class SkillsAgentProviderError(RuntimeError):
    """Native provider failure; keep distinct from a scored capability miss."""


class SkillsAgentLifecycleError(RuntimeError):
    """Native process lifecycle could not complete safely."""


def native_failure(logs_dir: Path):
    """Read only terminal error metadata from the one native benchmark turn.

    Never copy raw responses, prompts, tool results, diagnostics bodies/headers,
    or arbitrary exception prose into the Harbor failure summary.
    """
    path = logs_dir / "skills" / "session" / "events.jsonl"
    errors = []
    if path.exists():
        with path.open() as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "error":
                    errors.append(event)
    cleanup = sum(str(e.get("message", "")).startswith("Background process cleanup was not confirmed:")
                  for e in errors)
    limit_reasons = {"length", "max_tokens", "max_output_tokens", "model_context_window_exceeded"}
    limits = [e for e in errors if e.get("category") == "output_limit" or (
        e.get("category") is None and e.get("finish_reason") in limit_reasons)]
    providers = [e for e in errors if e.get("category") and e not in limits]
    failure = {"kind": "provider" if providers else "lifecycle" if cleanup else "agent_limit" if limits else "agent_exit",
               "source": "native_session_events", "error_events": len(errors),
               "cleanup_error_count": cleanup}
    if providers or limits:
        event = (providers or limits)[-1]
        for key in ("category", "http_status", "provider_code", "provider_type", "finish_reason", "retryable"):
            value = event.get(key)
            if value is None or type(value) in (str, int, bool):
                failure[key] = value[:2048] if isinstance(value, str) else value
        diagnostics = event.get("diagnostics")
        retry = diagnostics.get("retry") if isinstance(diagnostics, dict) else None
        if isinstance(retry, dict):
            failure["retry"] = {key: retry[key] for key in (
                "attempts", "max_attempts", "elapsed_seconds", "max_elapsed_seconds", "exhausted", "reason",
            ) if key in retry and (retry[key] is None or type(retry[key]) in (str, int, float, bool))}
        if limits and not providers:
            failure["category"] = "output_limit"
            failure["limit_type"] = ("context_window" if event.get("finish_reason") == "model_context_window_exceeded"
                                     or event.get("provider_code") == "context_length_exceeded" else "output_tokens")
    return failure


class SkillsAgent(BaseInstalledAgent):
    capabilities = AgentCapabilities(skills=True, mcp_servers=True)
    DEFAULT_SETUP_TIMEOUT_SEC = 600.0
    ROOT = "/opt/tb4-skills"
    SYSTEM_PACKAGES = {
        **BaseInstalledAgent.SYSTEM_PACKAGES,
        # Harbor's shared procps probe checks pgrep alone. Native ownership
        # verification additionally needs ps and its full process arguments.
        "procps": replace(BaseInstalledAgent.SYSTEM_PACKAGES["procps"], commands=("ps", "pgrep")),
    }

    def __init__(self, *args, assets_path="/data/tb4/experiments/gateway-yuzhi-20261016/skills-assets-7441b0691f21", api_base="https://yuzhiapi.xyz/v1", **kwargs):
        if {"context_tokens", "model_context_window", "max_output_tokens", "summary_output_tokens"} & kwargs.keys():
            raise ValueError("Native token budgets must be configured in skills-agent.toml")
        self.assets_path = Path(assets_path)
        self.api_base = api_base.rstrip("/")
        if self.api_base not in {"https://api.deepseek.com", "https://rehdasu.cn/v1", "https://yuzhiapi.xyz/v1"}:
            raise ValueError("Unapproved DeepSeek endpoint")
        super().__init__(*args, **kwargs)
        if self.model_name not in {None, "deepseek-v4.1-flash"}:
            raise ValueError("This evaluation adapter requires deepseek-v4.1-flash")
        self.model_name = "deepseek-v4.1-flash"
        release_manifest = self.assets_path / "runtime-manifest.json"
        self.release_manifest = json.loads(release_manifest.read_text()) if release_manifest.exists() else {}

    def _native_budget_config(self):
        path = self.assets_path / "skills-agent.toml"
        config = tomllib.loads(path.read_text())
        budget = {name: config[name] for name in (
            "context_tokens", "model_context_window", "max_output_tokens", "summary_output_tokens")}
        if any(type(value) is not int or value <= 0 for value in budget.values()):
            raise ValueError("skills-agent.toml token budgets must be positive integers")
        if budget["max_output_tokens"] >= budget["model_context_window"]:
            raise ValueError("skills-agent.toml output budget must be less than the total model window")
        return budget

    @staticmethod
    def name():
        return "tb4-skills-agent"

    def get_version_command(self):
        return f"{self.ROOT}/venv/bin/python -c 'import importlib.metadata; print(importlib.metadata.version(\"skills-cli-agent\"))'"

    async def install(self, environment: BaseEnvironment):
        # Native process-management uses ps to verify ownership, including close.
        await self.ensure_system_dependencies(environment, ("bash", "procps", "tar"))
        await self.exec_as_root(environment, "ps -ww -p $$ -o lstart= -o command= >/dev/null")
        bundle = self.assets_path / "runtime-bundle.tar.gz"
        expected = (self.assets_path / "runtime-bundle.sha256").read_text().split()[0]
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != expected:
            raise ValueError("skills-agent runtime bundle checksum mismatch")
        await self.exec_as_root(environment, f"mkdir -p {self.ROOT}")
        await environment.upload_file(bundle, f"{self.ROOT}/runtime-bundle.tar.gz")
        await self.exec_as_root(environment,
            f"tar -xzf {self.ROOT}/runtime-bundle.tar.gz -C {self.ROOT} && "
            f"rm {self.ROOT}/runtime-bundle.tar.gz && "
            f"{self.ROOT}/runtime/bin/python3.12 -m venv {self.ROOT}/venv && "
            f"{self.ROOT}/venv/bin/python -m pip install --no-index --find-links={self.ROOT}/wheels "
            f"'{self.ROOT}/skills_cli_agent-0.1.0-py3-none-any.whl[deepseek]' mcp==1.30.0",
            timeout_sec=480)
        await environment.upload_file(self.assets_path / "skills-agent.toml", f"{self.ROOT}/skills-agent.toml")
        source = Path(__file__).parent / "assets" / "skills"
        for filename in ("mcp_bridge.py", "sitecustomize.py"):
            remote = f"{self.ROOT}/audit/sitecustomize.py" if filename == "sitecustomize.py" else f"{self.ROOT}/{filename}"
            await self.exec_as_root(environment, f"mkdir -p {shlex.quote(str(Path(remote).parent))}")
            await environment.upload_file(source / filename, remote)
        await self.exec_as_root(environment,
            f"mkdir -p {self.ROOT}/skills/task-image-observation {self.ROOT}/skills/task-mcp")
        await environment.upload_file(source / "IMAGE-SKILL.md", f"{self.ROOT}/skills/task-image-observation/SKILL.md")
        if self.mcp_servers:
            await environment.upload_file(source / "MCP-SKILL.md", f"{self.ROOT}/skills/task-mcp/SKILL.md")
        await self._upload_config_text(environment,
            content=json.dumps([server.model_dump(mode="json") for server in self.mcp_servers]),
            remote_path=f"{self.ROOT}/mcp.json", filename="mcp.json")
        # Framework agent users may be unprivileged. Runtime is immutable to the
        # agent, while the local MCP socket needs an agent-writable directory.
        await self.exec_as_root(environment, f"chmod -R a+rX {self.ROOT} && chmod 1777 {self.ROOT}")

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext):
        budget = self._native_budget_config()
        key = self._get_env("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        logs = str(self.environment_logs_dir)
        await self.exec_as_agent(environment, f"mkdir -p {shlex.quote(logs)}/skills")
        with tempfile.TemporaryDirectory(prefix="tb4-skills-prompt-") as temp:
            prompt = Path(temp) / "instruction.txt"
            prompt.write_text(instruction)
            await environment.upload_file(prompt, f"{self.ROOT}/instruction.txt")
        workspace_result = await self.exec_as_agent(environment, "pwd")
        workspace = (workspace_result.stdout or "").strip()
        if not workspace.startswith("/") or "\n" in workspace:
            raise ValueError("Could not resolve the task workspace")
        parts = [
            f"{self.ROOT}/venv/bin/python", "-m", "skills_agent.benchmark",
            "--config", f"{self.ROOT}/skills-agent.toml",
            "--prompt-file", f"{self.ROOT}/instruction.txt", "--workspace", workspace,
            "--log-dir", f"{logs}/skills", "--provider", "openai", "--model", "deepseek-v4.1-flash",
            "--base-url", self.api_base, "--api-format", "chat",
            "--token-counter", "deepseek", "--tokenizer-path", f"{self.ROOT}/tokenizer-v41.json",
            "--tokenizer-encoding", "v41", "--skill-dir", f"{self.ROOT}/skills",
        ]
        if self.skills_dir:
            parts.extend(["--skill-dir", str(self.skills_dir)])
        command = " ".join(shlex.quote(part) for part in parts)
        # Both processes live in this task environment. Preserve the original
        # benchmark exit status, stop MCP sessions, and let benchmark SIGTERM
        # handling clean its native process scope on cancellation.
        runner = f'''#!/bin/bash
set -uo pipefail
bridge_pid=""
agent_pid=""
cleanup() {{
  if [ -n "$agent_pid" ] && kill -0 "$agent_pid" 2>/dev/null; then kill -TERM "$agent_pid" 2>/dev/null || true; wait "$agent_pid" 2>/dev/null || true; fi
  if [ -n "$bridge_pid" ]; then kill -TERM "$bridge_pid" 2>/dev/null || true; wait "$bridge_pid" 2>/dev/null || true; fi
  rm -f {self.ROOT}/mcp.sock
}}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
if [ "{int(bool(self.mcp_servers))}" = "1" ]; then
  {self.ROOT}/venv/bin/python {self.ROOT}/mcp_bridge.py serve --config {self.ROOT}/mcp.json >{shlex.quote(logs)}/mcp-bridge.log 2>&1 &
  bridge_pid=$!
  for ((i=0;i<300;i++)); do
    [ -S {self.ROOT}/mcp.sock ] && break
    kill -0 "$bridge_pid" 2>/dev/null || exit 70
    sleep 0.1
  done
  [ -S {self.ROOT}/mcp.sock ] || exit 70
fi
{command} >{shlex.quote(logs)}/skills-console.log 2>&1 &
agent_pid=$!
wait "$agent_pid"
status=$?
agent_pid=""
exit "$status"
'''
        with tempfile.TemporaryDirectory(prefix="tb4-skills-runner-") as temp:
            path = Path(temp) / "run.sh"
            path.write_text(runner)
            await environment.upload_file(path, f"{self.ROOT}/run.sh")
        effective_input_limit = min(budget["context_tokens"], budget["model_context_window"] - budget["max_output_tokens"])
        metadata = {
            "model": "deepseek-v4.1-flash", "effort": "max", **budget,
            "api_base": self.api_base,
            "gateway_effort_compatibility": self.api_base in {"https://rehdasu.cn/v1", "https://yuzhiapi.xyz/v1"},
            "effective_input_token_limit": effective_input_limit,
            "compression_trigger_tokens": min(budget["context_tokens"] * 0.9, effective_input_limit),
            "native_config_path": f"{self.ROOT}/skills-agent.toml",
            "native_config_sha256": hashlib.sha256((self.assets_path / "skills-agent.toml").read_bytes()).hexdigest(),
            "summary_thinking": "disabled",
            "workspace": workspace, "task_skills_dir": self.skills_dir,
            "task_mcp_servers": [server.name for server in self.mcp_servers],
            "runtime_bundle_sha256": (self.assets_path / "runtime-bundle.sha256").read_text().split()[0],
            "agent_wheel_sha256": self.release_manifest.get("files", {}).get("skills_cli_agent-0.1.0-py3-none-any.whl", {}).get("sha256"),
            "agent_source_tree_sha256": self.release_manifest.get("source_tree_sha256"),
            "adapter_core_patch": False,
            "provider_retry_policy": {"max_attempts": 5, "retry_budget_seconds": 300,
                                      "budget_scope": "start_next_request", "transport_timeout_seconds": 120},
        }
        (self.logs_dir / "skills-config.json").write_text(json.dumps(metadata, indent=2) + "\n")
        context.metadata = metadata
        try:
            await self.exec_as_agent(environment, f"bash {self.ROOT}/run.sh", env={
                "OPENAI_API_KEY": key, "effort": "max",
                "PYTHONPATH": f"{self.ROOT}/audit",
                "TB4_API_AUDIT_LOG": f"{logs}/api-audit.jsonl",
                "TB4_ALLOWED_API_BASE": self.api_base,
                "SKILLS_AGENT_PROVIDER_MAX_ATTEMPTS": "5",
                "SKILLS_AGENT_PROVIDER_RETRY_BUDGET_SECONDS": "300",
            })
        except NonZeroAgentExitCodeError:
            failure = native_failure(self.logs_dir)
            context.metadata = {**(context.metadata or {}), "native_failure": failure}
            # Also preserve a small standalone report if later lifecycle cleanup
            # prevents Harbor from retaining the exception's full context.
            (self.logs_dir / "native-failure.json").write_text(json.dumps(failure, indent=2) + "\n")
            if failure.get("category") == "authentication":
                raise AgentAuthenticationError("DeepSeek rejected the skills-agent credential") from None
            if failure["kind"] == "provider":
                raise SkillsAgentProviderError(json.dumps(failure, sort_keys=True)) from None
            if failure["kind"] == "lifecycle":
                raise SkillsAgentLifecycleError(json.dumps(failure, sort_keys=True)) from None
            if failure["kind"] == "agent_limit":
                kind = ContextWindowExceededError if failure["limit_type"] == "context_window" else OutputTokenExceededError
                raise kind(json.dumps(failure, sort_keys=True)) from None
            raise
        finally:
            self.populate_context_post_run(context)

    def populate_context_post_run(self, context: AgentContext):
        path = self.logs_dir / "api-audit.jsonl"
        if not path.exists():
            return
        usages = {}
        requests = 0
        try:
            lines = path.read_text().splitlines()
        except OSError as exc:
            context.metadata = {**(context.metadata or {}), "usage_read_error": type(exc).__name__}
            return
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "request":
                requests += 1
            if event.get("event") == "usage":
                usages[event.get("audit_id")] = event.get("usage", {})
        context.metadata = {**(context.metadata or {}), "api_requests": requests,
                            "api_requests_with_usage": len(usages),
                            "usage_complete": bool(requests) and requests == len(usages)}
        if not usages:
            return
        incoming = sum(item.get("prompt_tokens", 0) for item in usages.values())
        cached = sum(item.get("prompt_cache_hit_tokens", item.get("prompt_tokens_details", {}).get("cached_tokens", 0)) for item in usages.values())
        outgoing = sum(item.get("completion_tokens", 0) for item in usages.values())
        context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens = incoming, cached, outgoing
        context.model_usage = {"deepseek-v4.1-flash": ModelUsage(n_input_tokens=incoming, n_cache_tokens=cached, n_output_tokens=outgoing)}
