"""Pinned Claude Code deployment with a DeepSeek-only model route."""
from pathlib import Path
import json
from typing import Annotated, Literal

from pydantic import Field
from harbor.agents.installed.claude_code import ClaudeCode, ClaudeCodeOptions
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.agents.options import Cli


class DeepSeekClaudeOptions(ClaudeCodeOptions):
    version: str = "2.1.300"
    reasoning_effort: Annotated[Literal["max"], Cli("--effort")] = "max"
    disallowed_tools: Annotated[str, Cli("--disallowedTools")] = "WebSearch"
    binary_path: str = "/data/tb4/releases/claude-code/package/claude"
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
        env = {
            **(extra_env or {}),
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "CLAUDE_CODE_EFFORT_LEVEL": "max",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        super().__init__(*args, extra_env=env, **kwargs)
        if self.options.version != "2.1.300" or self.options.reasoning_effort != "max":
            raise ValueError("This experiment pins Claude Code 2.1.300 and effort=max")
        if self.options.audit_base_url:
            self._extra_env["ANTHROPIC_BASE_URL"] = self.options.audit_base_url

    async def install(self, environment):
        binary = Path(self.options.binary_path)
        if not binary.is_file():
            raise FileNotFoundError("The pinned Claude Code binary is not deployed")
        await self.ensure_system_dependencies(environment, ("bash", "procps"))
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
        })
        return env

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
