"""Run using the pinned Harbor venv; all tests are keyless and offline."""
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from .skills import (native_failure, SkillsAgent, SkillsAgentProviderError, SkillsAgentLifecycleError,
                     OutputTokenExceededError, ContextWindowExceededError, NonZeroAgentExitCodeError)
from harbor.models.agent.context import AgentContext


class NativeFailureTests(unittest.TestCase):
    def summary(self, events):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "skills/session/events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
            return native_failure(root)

    def test_provider_error_survives_secondary_cleanup_without_payload_leak(self):
        result = self.summary([
            {"type": "message", "message": {"role": "assistant", "content": "PRIVATE ANSWER"}},
            {"type": "error", "category": "bad_gateway", "http_status": 502,
             "provider_code": None, "provider_type": None, "retryable": True,
             "diagnostics": {"response": {"headers": {"authorization": "PRIVATE KEY"}, "body": "PRIVATE BODY"},
                             "retry": {"attempts": 5, "max_attempts": 5, "exhausted": True, "reason": "attempt_limit"}},
             "_raw": {"content": "PRIVATE RAW"}},
            {"type": "error", "message": "Background process cleanup was not confirmed: PRIVATE COMMAND"},
        ])
        self.assertEqual(result["kind"], "provider")
        self.assertEqual(result["http_status"], 502)
        self.assertEqual(result["cleanup_error_count"], 1)
        self.assertTrue(result["retry"]["exhausted"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_lifecycle_and_unknown_process_exit_remain_distinct(self):
        self.assertEqual(self.summary([{"type": "error", "message": "Background process cleanup was not confirmed: details"}])["kind"], "lifecycle")
        self.assertEqual(self.summary([{"type": "error", "message": "PRIVATE arbitrary exception"}])["kind"], "agent_exit")

    def test_no_events_never_fabricates_provider_success(self):
        self.assertEqual(self.summary([])["kind"], "agent_exit")
        self.assertNotEqual(SkillsAgentProviderError.__name__, SkillsAgentLifecycleError.__name__)

    def test_output_limit_is_an_agent_limit_using_only_exact_protocol_fields(self):
        self.assertTrue(issubclass(OutputTokenExceededError, NonZeroAgentExitCodeError))
        self.assertTrue(issubclass(ContextWindowExceededError, NonZeroAgentExitCodeError))
        for event in ({"type": "error", "category": "output_limit", "finish_reason": "length"},
                      {"type": "error", "finish_reason": "max_output_tokens"}):
            with self.subTest(event=event):
                result = self.summary([event])
                self.assertEqual(result["kind"], "agent_limit")
                self.assertEqual(result["category"], "output_limit")
                self.assertEqual(result["limit_type"], "output_tokens")
        result = self.summary([{"type": "error", "category": "output_limit", "provider_code": "context_length_exceeded"}])
        self.assertEqual(result["limit_type"], "context_window")
        self.assertEqual(self.summary([{"type": "error", "message": "max_tokens length output_limit"}])["kind"], "agent_exit")

    def test_cleanup_or_provider_fault_alongside_limit_is_still_reviewable(self):
        limit = {"type": "error", "category": "output_limit", "finish_reason": "length"}
        cleanup = {"type": "error", "message": "Background process cleanup was not confirmed: details"}
        self.assertEqual(self.summary([limit, cleanup])["kind"], "lifecycle")
        self.assertEqual(self.summary([limit, {"type": "error", "category": "bad_gateway", "http_status": 502}])["kind"], "provider")


class InstallerDependenciesTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def config_file(root, **changes):
        budget = {"context_tokens": 524000, "model_context_window": 524000,
                  "max_output_tokens": 131072, "summary_output_tokens": 8192, **changes}
        path = root / "skills-agent.toml"
        path.write_text("".join(f"{key} = {json.dumps(value)}\n" for key, value in budget.items()))
        return budget

    def test_budgets_are_read_from_toml_without_duplicate_kwargs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = self.config_file(root)
            agent = SkillsAgent(logs_dir=root, assets_path=root)
            self.assertEqual(agent._native_budget_config(), budget)
            self.config_file(root, max_output_tokens=65536)
            self.assertEqual(agent._native_budget_config()["max_output_tokens"], 65536)
            for name in budget:
                with self.subTest(parameter=name), self.assertRaises(ValueError):
                    SkillsAgent(logs_dir=root, assets_path=root, **{name: budget[name]})
            for name in budget:
                for value in (0, -1, True, "524000", 524000.0):
                    self.config_file(root, **{name: value})
                    with self.subTest(parameter=name, value=value), self.assertRaises(ValueError):
                        agent._native_budget_config()
            self.config_file(root, max_output_tokens=524000)
            with self.assertRaises(ValueError):
                agent._native_budget_config()

    def test_gateway_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = SkillsAgent(logs_dir=root, assets_path=root, api_base="https://yuzhiapi.xyz/v1/")
            self.assertEqual(agent.api_base, "https://yuzhiapi.xyz/v1")
            self.assertEqual(agent.model_name, "deepseek-v4.1-flash")
            with self.assertRaises(ValueError):
                SkillsAgent(logs_dir=root, assets_path=root, model_name="unsupported-model")
            for endpoint in ("http://yuzhiapi.xyz/v1", "https://yuzhiapi.xyz.attacker.test/v1", "https://yuzhiapi.xyz/v1?key=bad"):
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    SkillsAgent(logs_dir=root, assets_path=root, api_base=endpoint)

    async def test_native_config_is_only_budget_source_and_metadata_matches(self):
        for budget_changes, endpoint in (({"context_tokens": 1000000, "model_context_window": 1048576,
                                          "max_output_tokens": 32768}, "https://rehdasu.cn/v1"),
                                         ({}, "https://yuzhiapi.xyz/v1")):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                budget = self.config_file(root, **budget_changes)
                (root / "runtime-bundle.sha256").write_text("0" * 64 + "  runtime-bundle.tar.gz\n")
                agent = SkillsAgent(logs_dir=root, assets_path=root, api_base=endpoint,
                                    extra_env={"DEEPSEEK_API_KEY": "TEST_NOT_A_REAL_CREDENTIAL"})
                runners = []

                async def upload_file(local_path, remote_path):
                    if remote_path == "/opt/tb4-skills/run.sh":
                        runners.append(Path(local_path).read_text())

                async def execute(environment, command, **kwargs):
                    if command == "bash /opt/tb4-skills/run.sh":
                        self.assertEqual(kwargs["env"]["TB4_ALLOWED_API_BASE"], endpoint)
                        self.assertEqual(kwargs["env"]["PYTHONPATH"], "/opt/tb4-skills/audit")
                    return SimpleNamespace(stdout="/app" if command == "pwd" else "")

                environment = SimpleNamespace(upload_file=AsyncMock(side_effect=upload_file))
                agent.exec_as_agent = execute
                context = AgentContext()
                await agent.run("OFFLINE FIXTURE", environment, context)
                native_line = next(line for line in runners[0].splitlines()
                                   if " -m skills_agent.benchmark " in line)
                arguments = shlex.split(native_line)
                self.assertEqual(arguments[arguments.index("--config") + 1], "/opt/tb4-skills/skills-agent.toml")
                for name in budget:
                    self.assertNotIn("--" + name.replace("_", "-"), arguments)
                self.assertEqual(arguments[arguments.index("--base-url") + 1], endpoint)
                self.assertEqual(arguments[arguments.index("--model") + 1], "deepseek-v4.1-flash")
                config = json.loads((root / "skills-config.json").read_text())
                effective = min(budget["context_tokens"], budget["model_context_window"] - budget["max_output_tokens"])
                for metadata in (context.metadata, config):
                    self.assertEqual(metadata["model"], "deepseek-v4.1-flash")
                    for name, value in budget.items():
                        self.assertEqual(metadata[name], value)
                    self.assertEqual(metadata["effective_input_token_limit"], effective)
                    self.assertEqual(metadata["compression_trigger_tokens"], min(budget["context_tokens"] * .9, effective))
                    self.assertEqual(metadata["native_config_path"], "/opt/tb4-skills/skills-agent.toml")

    async def test_install_uploads_the_actual_toml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.config_file(root)
            bundle = root / "runtime-bundle.tar.gz"
            bundle.write_bytes(b"offline fixture")
            import hashlib
            (root / "runtime-bundle.sha256").write_text(hashlib.sha256(bundle.read_bytes()).hexdigest() + "  runtime-bundle.tar.gz\n")
            agent = SkillsAgent(logs_dir=root, assets_path=root)
            agent.ensure_system_dependencies = AsyncMock()
            agent.exec_as_root = AsyncMock()
            agent._upload_config_text = AsyncMock()
            environment = SimpleNamespace(upload_file=AsyncMock())
            await agent.install(environment)
            self.assertIn(((root / "skills-agent.toml", "/opt/tb4-skills/skills-agent.toml"), {}),
                          [(call.args, call.kwargs) for call in environment.upload_file.await_args_list])

    async def test_installer_ensures_process_and_shell_dependencies_before_unpack(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = object.__new__(SkillsAgent)
            agent.assets_path = Path(directory)
            agent.ensure_system_dependencies = AsyncMock()
            agent.exec_as_root = AsyncMock()
            environment = object()
            with self.assertRaises(FileNotFoundError):
                await agent.install(environment)
            agent.ensure_system_dependencies.assert_awaited_once_with(environment, ("bash", "procps", "tar"))
            self.assertIn("ps", agent.SYSTEM_PACKAGES["procps"].commands)
            agent.exec_as_root.assert_awaited_once_with(environment, "ps -ww -p $$ -o lstart= -o command= >/dev/null")

    async def test_nonzero_native_run_preserves_taxonomy_and_evaluation_retry_environment(self):
        cases = [
            ({"type": "error", "category": "bad_gateway", "http_status": 502, "retryable": True},
             SkillsAgentProviderError, "provider"),
            ({"type": "error", "message": "Background process cleanup was not confirmed: details"},
             SkillsAgentLifecycleError, "lifecycle"),
            ({"type": "error", "category": "output_limit", "finish_reason": "length"},
             OutputTokenExceededError, "agent_limit"),
            ({"type": "error", "category": "output_limit", "finish_reason": "model_context_window_exceeded"},
             ContextWindowExceededError, "agent_limit"),
        ]
        for event, exception_type, expected_kind in cases:
            with self.subTest(kind=expected_kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "runtime-bundle.sha256").write_text("0" * 64 + "  runtime-bundle.tar.gz\n")
                self.config_file(root)
                agent = SkillsAgent(logs_dir=root, assets_path=str(root),
                                    model_name="deepseek-v4.1-flash",
                                    extra_env={"DEEPSEEK_API_KEY": "TEST_NOT_A_REAL_CREDENTIAL"})
                path = root / "skills/session/events.jsonl"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(event) + "\n")
                environment = SimpleNamespace(upload_file=AsyncMock())
                sent = []

                async def execute(environment, command, **kwargs):
                    if command == "pwd":
                        return SimpleNamespace(stdout="/app")
                    if command.startswith("bash /opt/tb4-skills/run.sh"):
                        sent.append(kwargs["env"])
                        raise NonZeroAgentExitCodeError("controlled offline exit")
                    return SimpleNamespace(stdout="")

                agent.exec_as_agent = execute
                context = AgentContext()
                with self.assertRaises(exception_type):
                    await agent.run("OFFLINE FIXTURE", environment, context)
                self.assertEqual(context.metadata["native_failure"]["kind"], expected_kind)
                self.assertEqual(sent[0]["SKILLS_AGENT_PROVIDER_MAX_ATTEMPTS"], "5")
                self.assertEqual(sent[0]["SKILLS_AGENT_PROVIDER_RETRY_BUDGET_SECONDS"], "300")
