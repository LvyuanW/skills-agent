"""Pinned Claude Code deployment with a DeepSeek-only model route."""
from pathlib import Path
import json
from typing import Annotated, Literal

from pydantic import Field
from harbor.agents.installed.claude_code import ClaudeCode, ClaudeCodeOptions
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.agents.options import Cli
from .gateway import DEFAULT_GATEWAY, audit_base, gateway_base


_MODEL = "deepseek-v4.1-flash[1m]"


class DeepSeekClaudeOptions(ClaudeCodeOptions):
    api_base: str = Field(default=DEFAULT_GATEWAY, description="Authorized Anthropic gateway root.")
    version: str = "2.1.300"
    reasoning_effort: Annotated[Literal["max"], Cli("--effort")] = "max"
    disallowed_tools: Annotated[str, Cli("--disallowedTools")] = "WebSearch"
    binary_path: str = "/data/tb4/releases/claude-code/glibc/package/claude"
    musl_binary_path: str = "/data/tb4/releases/claude-code/package/claude"
    audit_base_url: str | None = Field(default=None, description="Synthetic preflight only.")


class DeepSeekClaude(ClaudeCode):
    """Reuse Harbor's native skills, MCP, execution, logs and ATIF conversion."""

    options_model = DeepSeekClaudeOptions
    MODEL_CONNECTION = ModelConnectionSpec(
        default_provider="anthropic",
        api_key_envs=("DEEPSEEK_API_KEY",),
        base_url_envs=("ANTHROPIC_BASE_URL",),
    )

    def __init__(self, *args, extra_env=None, **kwargs):
        extra_env = dict(extra_env or {})
        audit = kwargs.get("audit_base_url")
        configured = kwargs.get("api_base", DEFAULT_GATEWAY if audit else extra_env.get("ANTHROPIC_BASE_URL", DEFAULT_GATEWAY))
        kwargs["api_base"] = self.api_base = gateway_base(configured, protocol="anthropic")
        if audit is not None:
            kwargs["audit_base_url"] = audit_base(audit, protocol="anthropic")
        self.effective_api_base = kwargs.get("audit_base_url") or self.api_base
        if extra_env.get("ANTHROPIC_BASE_URL"):
            normalizer = audit_base if audit is not None else gateway_base
            if normalizer(extra_env["ANTHROPIC_BASE_URL"], protocol="anthropic") != self.effective_api_base:
                raise ValueError("ANTHROPIC_BASE_URL conflicts with the explicit adapter gateway")
        kwargs.setdefault("version", "2.1.300")
        kwargs.setdefault("reasoning_effort", "max")
        kwargs.setdefault("model_name", _MODEL)
        env = {
            **extra_env,
            "ANTHROPIC_BASE_URL": self.effective_api_base,
            "CLAUDE_CODE_EFFORT_LEVEL": "max",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        super().__init__(*args, extra_env=env, **kwargs)
        if self.options.version != "2.1.300" or self.options.reasoning_effort != "max":
            raise ValueError("This experiment pins Claude Code 2.1.300 and effort=max")
        if self._resolved_model_name() != _MODEL:
            raise ValueError(f"TB4 Claude Code only permits {_MODEL}")

    async def install(self, environment):
        libc = await environment.exec(command="if [ -e /lib/ld-musl-x86_64.so.1 ]; then printf musl; else printf glibc; fi")
        binary = Path(self.options.musl_binary_path if (libc.stdout or "").strip() == "musl" else self.options.binary_path)
        if not binary.is_file():
            raise FileNotFoundError("The pinned Claude Code binary is not deployed")
        await self.ensure_system_dependencies(environment, ("bash", "procps", "ca_certificates"))
        await environment.upload_file(binary, "/tmp/tb4-claude-code")
        await environment.exec(command="chmod 0755 /tmp/tb4-claude-code", user="root")
        result = await self.exec_as_agent(
            environment,
            command=(
                'mkdir -p "$HOME/.local/bin" && '
                'cp /tmp/tb4-claude-code "$HOME/.local/bin/claude" && '
                'chmod 0755 "$HOME/.local/bin/claude" && '
                '"$HOME/.local/bin/claude" --version'
            ),
        )
        if self.parse_version(result.stdout or "") != self.options.version:
            raise RuntimeError("Installed Claude Code version does not match the pin")

    def _resolve_auth_env(self):
        env = super()._resolve_auth_env()
        env.update({
            "CLAUDE_CODE_EFFORT_LEVEL": "max",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": _MODEL,
            "ANTHROPIC_SMALL_FAST_MODEL": _MODEL,
        })
        return env

    def convert_trajectory(self, logs_dir):
        trajectory = super().convert_trajectory(logs_dir)
        if trajectory is None:
            return None

        def mark_unpriced(item):
            if item.final_metrics:
                metrics = item.final_metrics
                metrics.extra = {**(metrics.extra or {}),
                                 "native_estimated_cost_usd": metrics.total_cost_usd,
                                 "billing_source": "gateway_rate_unverified"}
                metrics.total_cost_usd = None
            for step in item.steps:
                if step.metrics and step.metrics.cost_usd is not None:
                    step.metrics.extra = {**(step.metrics.extra or {}),
                                          "native_estimated_cost_usd": step.metrics.cost_usd}
                    step.metrics.cost_usd = None
            for child in item.subagent_trajectories or []:
                mark_unpriced(child)

        mark_unpriced(trajectory)
        return trajectory

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "tb4-connection.json").write_text(json.dumps({
            "agent": "claude-code",
            "version": self.options.version,
            "model": self._resolved_model_name(),
            "reasoning_effort": "max",
            "base_url": self._get_env("ANTHROPIC_BASE_URL"),
            "auto_compact_window": 786432,
            "disallowed_tools": self.options.disallowed_tools,
            "synthetic_audit_enabled": bool(self.options.audit_base_url),
        }, indent=2) + "\n")
        await super().run(instruction, environment, context)
