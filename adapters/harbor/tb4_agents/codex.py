"""Pinned Codex CLI with an experiment-derived Flash/max catalog for TB4.

The upstream Harbor adapter remains responsible for prompts, task MCP servers,
skills, timeout cleanup, native rollout export and ATIF conversion. This thin
adapter only installs the pinned offline runtime and uploads the model catalog
alongside each task's effective configuration.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field

from harbor.agents.installed.codex import Codex, CodexOptions
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import FinalMetrics, Trajectory
from harbor.utils.trajectory_utils import compute_model_usage
from .gateway import DEFAULT_GATEWAY, audit_base, gateway_base


_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "codex"
_VERSION = "0.149.1"
_CATALOG_SHA256 = "9d11eb2daa7d6732162a6b6fc304a47f55681a25e4fe64ede864d482149a944e"
_CA_BUNDLE_SHA256 = "4f3975ff30abbf35443b486064ae8bcb41dff707ffdf576c76d292fb458aa0a9"
_REMOTE_CA_BUNDLE = "/installed-agent/tb4-codex-ca-bundle.pem"
_PACKAGE_SHA512 = (
    "39fe5f19882beed00cb328fabe15dbe3f44cfd4a00dd9abc04b7a05010c2d3d50"
    "dcb55d32c660fff65c3f945fccf7aa1d0d0f0c587428c52167433628ca66081"
)


class DeepSeekCodexOptions(CodexOptions):
    api_base: str = Field(default=DEFAULT_GATEWAY + "/v1", description="Authorized Responses gateway base.")
    audit_base_url: str | None = Field(default=None, description="Explicit local/private mock only.")
    package_path: str = Field(
        default="/data/tb4/artifacts/runtime/codex-0.149.1-linux-x64.tgz",
        description="Pinned official npm Linux x86_64 runtime archive on the host.",
    )
    models_path: str = Field(
        default=str(_ASSETS / "models.json"),
        description="Frozen experiment-derived Flash/max-only model catalog on the host.",
    )
    ca_bundle_path: str = Field(
        default=str(_ASSETS / "certs" / "ca-bundle.pem"),
        description="Pinned public certifi 2026.1.4 CA bundle, uploaded offline.",
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
        extra_env = dict(kwargs.get("extra_env") or {})
        audit = kwargs.get("audit_base_url")
        configured = kwargs.get("api_base", DEFAULT_GATEWAY if audit else extra_env.get("DEEPSEEK_BASE_URL", DEFAULT_GATEWAY))
        kwargs["api_base"] = self.api_base = gateway_base(configured, protocol="responses")
        if audit is not None:
            kwargs["audit_base_url"] = audit_base(audit, protocol="responses")
        self.effective_api_base = kwargs.get("audit_base_url") or self.api_base
        if extra_env.get("DEEPSEEK_BASE_URL"):
            normalizer = audit_base if audit is not None else gateway_base
            if normalizer(extra_env["DEEPSEEK_BASE_URL"], protocol="responses") != self.effective_api_base:
                raise ValueError("DEEPSEEK_BASE_URL conflicts with the explicit adapter gateway")
        kwargs["extra_env"] = {
            **extra_env,
            "DEEPSEEK_BASE_URL": self.effective_api_base,
            "CODEX_CA_CERTIFICATE": _REMOTE_CA_BUNDLE,
        }
        kwargs.setdefault("version", _VERSION)
        kwargs.setdefault("reasoning_effort", "max")
        kwargs.setdefault("web_search", "disabled")
        kwargs.setdefault("model_name", "deepseek-v4.1-flash")
        super().__init__(*args, **kwargs)
        if self._version != _VERSION:
            raise ValueError(f"TB4 Codex version is pinned to {_VERSION}")
        if self.model_name not in {"deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"}:
            raise ValueError("TB4 Codex only permits deepseek-v4.1-flash")
        if self.options.reasoning_effort != "max":
            raise ValueError("TB4 Codex requires reasoning_effort=max")
        if self.options.web_search != "disabled":
            raise ValueError("TB4 Codex requires web_search=disabled")

    async def install(self, environment: BaseEnvironment) -> None:
        package = Path(self.options.package_path)
        with package.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha512").hexdigest()
        if digest != _PACKAGE_SHA512:
            raise ValueError("Codex runtime archive does not match npm sha512 integrity")
        ca_bundle = Path(self.options.ca_bundle_path)
        if hashlib.sha256(ca_bundle.read_bytes()).hexdigest() != _CA_BUNDLE_SHA256:
            raise ValueError("Codex public CA bundle differs from pinned certifi snapshot")
        await environment.upload_file(ca_bundle, _REMOTE_CA_BUNDLE)
        archive = "/installed-agent/tb4-codex.tgz"
        await environment.upload_file(package, archive)
        await self.exec_as_root(
            environment,
            command=(
                "set -eu; mkdir -p /installed-agent/tb4-codex; "
                f"chmod 644 {_REMOTE_CA_BUNDLE}; "
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
        if openai_base_url:
            normalizer = audit_base if self.options.audit_base_url else gateway_base
            if normalizer(openai_base_url, protocol="responses") != self.effective_api_base:
                raise ValueError("Resolved Responses base differs from the explicit adapter gateway")
        config = super()._build_effective_config(None)
        config.update(
            {
                "model": "deepseek-v4.1-flash",
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
                "base_url": self.effective_api_base,
                "wire_api": "responses",
                "env_key": "OPENAI_API_KEY",
            }
        }
        return config

    async def _upload_effective_config(
        self, environment: BaseEnvironment, config: dict[str, Any], remote_path: str
    ) -> None:
        models = Path(self.options.models_path)
        blob = models.read_bytes()
        if hashlib.sha256(blob).hexdigest() != _CATALOG_SHA256:
            raise ValueError("DeepSeek model catalog hash differs from frozen snapshot")
        catalog = json.loads(blob)
        flash = [m for m in catalog["models"] if m["slug"] == "deepseek-v4.1-flash"]
        if len(flash) != 1 or flash[0]["context_window"] != 1048576:
            raise ValueError("Frozen DeepSeek Flash catalog validation failed")
        if len(catalog["models"]) != 1 or flash[0]["default_reasoning_level"] != "max":
            raise ValueError("Experiment catalog must expose Flash/max only")
        if [x["effort"] for x in flash[0]["supported_reasoning_levels"]] != ["max"]:
            raise ValueError("Experiment catalog must expose max effort only")
        remote_models = str(self._REMOTE_CODEX_HOME / "models.json")
        await self._upload_agent_owned_file(environment, models, remote_models)
        await self.exec_as_root(
            environment, command=f"chmod 644 {shlex.quote(remote_models)}"
        )
        await super()._upload_effective_config(environment, config, remote_path)

    def convert_trajectory(self, logs_dir: Path) -> Trajectory | None:
        """Retain the parent and every child instead of selecting the last file.

        Harbor's pinned Codex converter normally chooses the lexically latest
        session file, which can be a child. Parse each file separately with the
        upstream converter, embed children, and aggregate per-call metrics.
        """
        sessions: dict[str, tuple[Path, dict[str, Any]]] = {}
        for path in sorted((logs_dir / "sessions").rglob("rollout-*.jsonl")):
            with path.open() as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "session_meta":
                        meta = event.get("payload") or {}
                        sessions[meta.get("id") or path.name] = (path, meta)
                        break
        if not sessions:
            return super().convert_trajectory(logs_dir)
        converted = []
        for session_id, (path, meta) in sessions.items():
            with tempfile.TemporaryDirectory(prefix="tb4-codex-one-session-") as temporary:
                single = Path(temporary)
                (single / path.name).symlink_to(path.resolve())
                trajectory = self._convert_events_to_trajectory(single)
            if trajectory is None:
                continue
            trajectory.trajectory_id = session_id
            trajectory.extra = {**(trajectory.extra or {}), "tb4_native_source": meta.get("source")}
            for step in trajectory.steps:
                if step.metrics is not None:
                    if step.metrics.cost_usd is not None:
                        step.metrics.extra = {**(step.metrics.extra or {}),
                                              "native_estimated_cost_usd": step.metrics.cost_usd}
                    step.metrics.cost_usd = None
            if trajectory.final_metrics is not None:
                trajectory.final_metrics.total_cost_usd = None
            converted.append((trajectory, meta))
        if not converted:
            return None
        roots = [(trajectory, meta) for trajectory, meta in converted
                 if not (isinstance(meta.get("source"), dict) and "subagent" in meta["source"])]
        if len(roots) != 1:
            raise RuntimeError(f"Expected one fresh Codex root session; found {len(roots)}")
        root = roots[0][0]
        children = [trajectory for trajectory, _ in converted if trajectory is not root]
        root.subagent_trajectories = children or None
        usage = compute_model_usage(root)
        root.extra = {**(root.extra or {}), "tb4_native_session_count": len(converted),
                      "tb4_root_native_metrics": root.final_metrics.model_dump() if root.final_metrics else None,
                      "tb4_usage_scope": "parent_and_all_embedded_child_sessions",
                      "tb4_cost_source": "gateway billing unknown; native price estimates excluded"}
        if usage:
            root.final_metrics = FinalMetrics(
                total_prompt_tokens=sum(x.n_input_tokens for x in usage.values()),
                total_cached_tokens=sum(x.n_cache_tokens for x in usage.values()),
                total_completion_tokens=sum(x.n_output_tokens for x in usage.values()),
                total_cost_usd=None, total_steps=len(root.steps),
                extra={"tb4_includes_subagents": True},
            )
            root.notes = "Token totals include every embedded child; total_steps counts the root timeline. Gateway billing is unknown."
        return Trajectory.model_validate(root.model_dump())

    def populate_context_post_run(self, context: AgentContext) -> None:
        super().populate_context_post_run(context)
        path = self.logs_dir / "trajectory.json"
        if not path.exists():
            return
        trajectory = Trajectory.model_validate_json(path.read_text())
        usage = compute_model_usage(trajectory)
        if usage:
            context.model_usage = usage
            context.n_input_tokens = sum(x.n_input_tokens for x in usage.values())
            context.n_cache_tokens = sum(x.n_cache_tokens for x in usage.values())
            context.n_output_tokens = sum(x.n_output_tokens for x in usage.values())
            context.cost_usd = None
        context.metadata = {**(context.metadata or {}),
                            "tb4_native_session_count": (trajectory.extra or {}).get("tb4_native_session_count"),
                            "tb4_usage_scope": "parent_and_all_embedded_child_sessions",
                            "tb4_cost_source": "gateway billing unknown; native price estimates excluded"}
