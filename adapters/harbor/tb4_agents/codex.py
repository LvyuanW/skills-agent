"""Pinned Codex CLI with DeepSeek's official model catalog for TB4.

The upstream Harbor adapter remains responsible for prompts, task MCP servers,
skills, timeout cleanup, native rollout export and ATIF conversion. This thin
adapter only installs the pinned offline runtime and uploads the model catalog
alongside each task's effective configuration.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

from pydantic import Field

from harbor.agents.installed.codex import Codex, CodexOptions
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment


_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "codex"
_VERSION = "0.149.1"
_PACKAGE_SHA512 = (
    "39fe5f19882beed00cb328fabe15dbe3f44cfd4a00dd9abc04b7a05010c2d3d50"
    "dcb55d32c660fff65c3f945fccf7aa1d0d0f0c587428c52167433628ca66081"
)


class DeepSeekCodexOptions(CodexOptions):
    package_path: str = Field(
        default="/data/tb4/artifacts/runtime/codex-0.149.1-linux-x64.tgz",
        description="Pinned official npm Linux x86_64 runtime archive on the host.",
    )
    models_path: str = Field(
        default=str(_ASSETS / "models.json"),
        description="Frozen DeepSeek official Codex model catalog on the host.",
    )


class DeepSeekCodex(Codex):
    """TB4 Codex/Flash/max profile; it never reads personal Codex settings."""

    MODEL_CONNECTION = ModelConnectionSpec(default_provider="deepseek")
    options_model = DeepSeekCodexOptions
    options: DeepSeekCodexOptions

    @staticmethod
    def name() -> str:
        return "tb4-deepseek-codex"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("version", _VERSION)
        kwargs.setdefault("reasoning_effort", "max")
        kwargs.setdefault("web_search", "disabled")
        kwargs.setdefault("model_name", "deepseek-flash")
        super().__init__(*args, **kwargs)
        if self._version != _VERSION:
            raise ValueError(f"TB4 Codex version is pinned to {_VERSION}")
        if self.model_name not in {"deepseek-flash", "deepseek/deepseek-flash"}:
            raise ValueError("TB4 Codex only permits deepseek-flash")
        if self.options.reasoning_effort != "max":
            raise ValueError("TB4 Codex requires reasoning_effort=max")
        if self.options.web_search != "disabled":
            raise ValueError("TB4 Codex requires web_search=disabled")

    async def install(self, environment: BaseEnvironment) -> None:
        package = Path(self.options.package_path)
        digest = hashlib.file_digest(package.open("rb"), "sha512").hexdigest()
        if digest != _PACKAGE_SHA512:
            raise ValueError("Codex runtime archive does not match npm sha512 integrity")
        archive = "/installed-agent/tb4-codex.tgz"
        await environment.upload_file(package, archive)
        await self.exec_as_root(
            environment,
            command=(
                "set -eu; mkdir -p /installed-agent/tb4-codex; "
                "tar -xzf /installed-agent/tb4-codex.tgz "
                "-C /installed-agent/tb4-codex --strip-components=1; "
                "chmod -R a+rX /installed-agent/tb4-codex; "
                "ln -sf /installed-agent/tb4-codex/vendor/"
                "x86_64-unknown-linux-musl/bin/codex /usr/local/bin/codex; "
                "ln -sf /installed-agent/tb4-codex/vendor/"
                "x86_64-unknown-linux-musl/codex-path/rg /usr/local/bin/rg; "
                "rm /installed-agent/tb4-codex.tgz; codex --version"
            ),
        )
        result = await self.exec_as_agent(environment, command="codex --version")
        if self.parse_version(result.stdout or "") != _VERSION:
            raise RuntimeError("Installed Codex version check failed")

    def _build_effective_config(self, openai_base_url: str | None = None) -> dict[str, Any]:
        config = super()._build_effective_config(None)
        config.update(
            {
                "model": "deepseek-flash",
                "model_provider": "deepseek",
                "preferred_auth_method": "apikey",
                "forced_login_method": "api",
                "model_reasoning_effort": "max",
                "web_search": "disabled",
                "model_catalog_json": str(self._REMOTE_CODEX_HOME / "models.json"),
            }
        )
        config["model_providers"] = {
            "deepseek": {
                "name": "deepseek",
                "base_url": openai_base_url or "https://api.deepseek.com",
                "wire_api": "responses",
                "env_key": "OPENAI_API_KEY",
            }
        }
        return config

    async def _upload_effective_config(
        self, environment: BaseEnvironment, config: dict[str, Any], remote_path: str
    ) -> None:
        models = Path(self.options.models_path)
        catalog = json.loads(models.read_text())
        flash = [m for m in catalog["models"] if m["slug"] == "deepseek-flash"]
        if len(flash) != 1 or flash[0]["context_window"] != 1048576:
            raise ValueError("Frozen DeepSeek Flash catalog validation failed")
        remote_models = str(self._REMOTE_CODEX_HOME / "models.json")
        await self._upload_agent_owned_file(environment, models, remote_models)
        await self.exec_as_root(
            environment, command=f"chmod 644 {shlex.quote(remote_models)}"
        )
        await super()._upload_effective_config(environment, config, remote_path)
