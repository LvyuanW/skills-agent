from contextlib import chdir
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest
from unittest.mock import patch

from skills_agent.benchmark import parser as benchmark_parser
from skills_agent.cli import parser as cli_parser
from skills_agent.config import load_agent_config, normalize_paths
from skills_agent.runtime import create_agent
from skills_agent.session import Session
from skills_agent.storage import WorkspaceStorage


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {"SKILLS_AGENT_DATA_DIR": str(self.root / "runtime-data")}, clear=True))

    def args(self, *options, benchmark=False):
        if benchmark:
            parser = benchmark_parser()
            initial = ["--prompt-file", "task.txt", "--log-dir", str(self.root / "logs")]
        else:
            parser = cli_parser()
            initial = ["run", "--session", "demo"]
        return parser.parse_args([*initial, "--workspace", str(self.root), *options])

    def write_config(self, content):
        path = self.root / "skills-agent.toml"
        path.write_text(content)
        return path

    def write_user_file(self, name, content):
        directory = self.root / "runtime-data"
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_text(content)
        return path

    def test_cli_and_benchmark_load_file_into_runtime(self):
        self.write_config('''
provider = "anthropic"
model = "configured-model"
base_url = "https://example.invalid/api"
context_tokens = 500000
model_context_window = 1000000
max_output_tokens = 64000
summary_output_tokens = 16000
''')
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        for benchmark in (False, True):
            with self.subTest(benchmark=benchmark):
                agent = create_agent(self.args(benchmark=benchmark), Session(self.root, str(benchmark)))
                self.assertEqual(agent.model.model, "configured-model")
                self.assertEqual(agent.model.base_url, "https://example.invalid/api")
                self.assertEqual(agent.model.api_key, "test-key")
                self.assertEqual(agent.model.max_output_tokens, 64000)
                self.assertEqual(agent.model.model_context_window, 1000000)
                self.assertEqual(agent.context.max_tokens, 500000)
                self.assertEqual(agent.auxiliary.window, 500000)
                self.assertEqual(agent.auxiliary.output_tokens, 16000)

    def test_cli_overrides_file_even_with_builtin_default_values(self):
        self.write_config('''
provider = "anthropic"
model = "file-model"
context_tokens = 2000000
max_output_tokens = 64000
api_format = "responses"
''')
        os.environ.update(OPENAI_MODEL="environment-model", SKILLS_AGENT_API_FORMAT="chat")
        args = load_agent_config(self.args("--provider", "openai", "--model", "cli-model",
                                           "--context-tokens", "1000000", "--max-output-tokens", "32768",
                                           "--api-format", "auto"))
        self.assertEqual((args.provider, args.model, args.api_format), ("openai", "cli-model", "auto"))
        self.assertEqual((args.context_tokens, args.max_output_tokens), (1000000, 32768))

    def test_file_overrides_environment_and_omitted_values_use_selected_provider(self):
        os.environ.update(OPENAI_MODEL="openai-env", ANTHROPIC_MODEL="anthropic-env",
                          ANTHROPIC_BASE_URL="https://example.invalid/env", SKILLS_AGENT_API_FORMAT="chat")
        self.write_config('provider = "anthropic"\napi_format = "auto"\n')
        args = load_agent_config(self.args())
        self.assertEqual(args.model, "anthropic-env")
        self.assertEqual(args.base_url, "https://example.invalid/env")
        self.assertEqual(args.api_format, "auto")
        self.write_config('model = "file-model"\nbase_url = "https://example.invalid/file"\n')
        args = load_agent_config(self.args())
        self.assertEqual((args.model, args.base_url), ("file-model", "https://example.invalid/file"))

    def test_missing_automatic_config_uses_existing_environment_and_defaults(self):
        os.environ.update(OPENAI_MODEL="existing-model", SKILLS_AGENT_TOKEN_COUNTER="usage")
        args = load_agent_config(self.args())
        self.assertEqual(args.model, "existing-model")
        self.assertEqual(args.context_tokens, 1000000)
        self.assertEqual(args.max_output_tokens, 32768)
        self.assertEqual(args.token_counter, "usage")
        self.assertFalse(hasattr(args, "tokenizer_path"))
        self.assertFalse(hasattr(args, "tokenizer_encoding"))
        self.assertIsNone(args.model_context_window)
        self.assertFalse((self.root / "runtime-data").exists())

    def test_user_config_supplies_defaults_outside_the_installation_directory(self):
        config = self.write_user_file("config.toml", '''
provider = "anthropic"
model = "user-model"
context_tokens = 12345
skill_dirs = ["user-skills"]
''')
        for benchmark in (False, True):
            with self.subTest(benchmark=benchmark):
                args = load_agent_config(self.args(benchmark=benchmark))
                self.assertEqual((args.provider, args.model, args.context_tokens),
                                 ("anthropic", "user-model", 12345))
                self.assertEqual(args.skill_dirs, [str(config.parent / "user-skills")])
                self.assertEqual(args.workspace, self.root)
                override = load_agent_config(self.args("--model", "cli-model", benchmark=benchmark))
                self.assertEqual(override.model, "cli-model")
        self.assertFalse((config.parent / "workspaces").exists())

    def test_workspace_and_explicit_configs_replace_the_user_file_without_merging(self):
        self.write_user_file("config.toml", 'model = "user-model"\ncontext_tokens = 12345\n')
        self.write_config('model = "workspace-model"\nskill_dirs = ["workspace-skills"]\n')
        args = load_agent_config(self.args())
        self.assertEqual(args.model, "workspace-model")
        self.assertEqual(args.context_tokens, 1000000)
        self.assertEqual(args.skill_dirs, [str(self.root / "workspace-skills")])

        directory = self.root / "settings"
        directory.mkdir()
        (directory / "agent.toml").write_text('model = "explicit-model"\nskill_dirs = ["explicit-skills"]\n')
        args = load_agent_config(self.args("--config", "settings/agent.toml"))
        self.assertEqual(args.model, "explicit-model")
        self.assertEqual(args.context_tokens, 1000000)
        self.assertEqual(args.skill_dirs, [str(directory / "explicit-skills")])
        with self.assertRaises(FileNotFoundError):
            load_agent_config(self.args("--config", "missing.toml"))

    def test_user_environment_fills_supported_settings_without_overwriting_existing_values(self):
        self.write_user_file(".env", '''
OPENAI_MODEL=user-model
OPENAI_API_KEY='literal-${VALUE}'
OPENAI_BASE_URL=https://example.invalid/openai
ANTHROPIC_MODEL=user-anthropic-model
ANTHROPIC_API_KEY=user-key
ANTHROPIC_BASE_URL=https://example.invalid/anthropic
SKILLS_AGENT_API_FORMAT=chat
SKILLS_AGENT_TOKEN_COUNTER=usage
effort=high
PATH=/unwanted/path
SKILLS_AGENT_DATA_DIR=/unwanted/storage
UNRELATED_SETTING=unwanted
''')
        os.environ.update(OPENAI_MODEL="existing-model", ANTHROPIC_API_KEY="", effort="max")
        args = load_agent_config(self.args())
        self.assertEqual(args.model, "existing-model")
        self.assertEqual(args.base_url, "https://example.invalid/openai")
        self.assertEqual((args.api_format, args.token_counter), ("chat", "usage"))
        self.assertEqual(os.environ["OPENAI_API_KEY"], "literal-${VALUE}")
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "")
        self.assertEqual(os.environ["effort"], "max")
        self.assertEqual(os.environ["SKILLS_AGENT_DATA_DIR"], str(self.root / "runtime-data"))
        self.assertNotIn("PATH", os.environ)
        self.assertNotIn("UNRELATED_SETTING", os.environ)
        args = load_agent_config(self.args("--provider", "anthropic", benchmark=True))
        self.assertEqual(args.model, "user-anthropic-model")
        self.assertEqual(args.base_url, "https://example.invalid/anthropic")

    def test_workspace_environment_file_is_not_loaded_automatically(self):
        (self.root / ".env").write_text('OPENAI_MODEL=workspace-model\nOPENAI_API_KEY=workspace-key\n')
        for benchmark in (False, True):
            with self.subTest(benchmark=benchmark):
                args = load_agent_config(self.args(benchmark=benchmark))
                self.assertIsNone(args.model)
                self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_usage_counter_is_accepted_from_cli_file_and_environment(self):
        for benchmark in (False, True):
            with self.subTest(source="cli", benchmark=benchmark):
                args = load_agent_config(self.args("--token-counter", "usage", benchmark=benchmark))
                self.assertEqual(args.token_counter, "usage")
        self.write_config('token_counter = "usage"\n')
        self.assertEqual(load_agent_config(self.args()).token_counter, "usage")
        (self.root / "skills-agent.toml").unlink()
        os.environ["SKILLS_AGENT_TOKEN_COUNTER"] = "usage"
        self.assertEqual(load_agent_config(self.args()).token_counter, "usage")

    def test_explicit_file_replaces_automatic_file_and_resolves_its_paths(self):
        self.write_config('context_tokens = 12345\n')
        directory = self.root / "other config"
        directory.mkdir()
        config = directory / "agent.toml"
        config.write_text('skill_dirs = ["skills"]\n')
        args = load_agent_config(self.args("--config", str(config)))
        self.assertEqual(args.context_tokens, 1000000)
        self.assertEqual(args.skill_dirs, [str(directory / "skills")])
        override = load_agent_config(self.args("--config", str(config), "--skill-dir", "cli-skills"))
        self.assertEqual(override.skill_dirs, [str(self.root / "cli-skills")])

    def test_selected_workspace_anchors_all_relative_task_paths(self):
        launcher, workspace = self.root / "launcher", self.root / "task"
        launcher.mkdir()
        (workspace / "settings").mkdir(parents=True)
        (workspace / "settings/agent.toml").write_text('model="workspace-model"\nskill_dirs=["config-skills"]\n')
        (launcher / "settings").mkdir()
        (launcher / "settings/agent.toml").write_text('model="wrong-launcher-model"\n')
        with chdir(launcher):
            supplied = cli_parser().parse_args([
                "run", "--workspace", "../task", "--config", "settings/agent.toml", "--file", "input.csv",
            ])
            args = load_agent_config(supplied)
            self.assertEqual(args.workspace, workspace)
            self.assertEqual(args.model, "workspace-model")
            self.assertEqual(args.config, workspace / "settings/agent.toml")
            self.assertEqual(args.file, [workspace / "input.csv"])
            self.assertEqual(args.skill_dirs, [str(workspace / "settings/config-skills")])
            supplied.skill_dirs = ["cli-skills"]
            self.assertEqual(load_agent_config(supplied).skill_dirs, [str(workspace / "cli-skills")])
            benchmark = normalize_paths(benchmark_parser().parse_args([
                "--workspace", "../task", "--prompt-file", "task.txt", "--log-dir", "logs",
            ]))
            self.assertEqual(benchmark.prompt_file, workspace / "task.txt")
            self.assertEqual(benchmark.log_dir, workspace / "logs")
        # A second normalization is independent of any later process cwd change.
        self.assertEqual(vars(normalize_paths(args)), vars(args))

    def test_unspecified_workspace_uses_current_directory_at_initialization(self):
        parser = cli_parser()
        benchmark = benchmark_parser()
        with chdir(self.root):
            self.assertEqual(normalize_paths(parser.parse_args(["run"])).workspace, self.root)
            self.assertEqual(normalize_paths(benchmark.parse_args([
                "--prompt-file", "task.txt", "--log-dir", "logs",
            ])).workspace, self.root)

    def test_runtime_prompt_and_storage_use_only_the_selected_working_directory(self):
        launcher, workspace = self.root / "launcher", self.root / "task"
        launcher.mkdir()
        workspace.mkdir()
        (workspace / "skills-agent.toml").write_text('model="task-model"\n')
        skill = workspace / "local-skills" / "example"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text('---\nname: example\ndescription: Test skill.\n---\n')
        with chdir(launcher), patch.dict(os.environ, {"SKILLS_AGENT_DATA_DIR": ".runtime"}):
            args = cli_parser().parse_args(["run", "--workspace", "../task", "--skill-dir", "local-skills"])
            storage = WorkspaceStorage(workspace).ensure()
            agent = create_agent(args, Session(storage.sessions, "selected-directory"))
            try:
                system = agent._system()
                self.assertEqual(agent.workspace, workspace)
                self.assertEqual(agent.file_history.repository, storage.history)
                self.assertIn(f"Primary working directory: {workspace}", system)
                self.assertIn(str(skill / "SKILL.md"), system)
                self.assertNotIn(str(launcher), system)
                self.assertTrue(agent.session.path.is_relative_to(workspace / ".runtime"))
            finally:
                agent.close()

    def test_removed_local_deepseek_configuration_is_rejected(self):
        for content in ('token_counter = "deepseek"', 'tokenizer_path = "tokenizer.json"',
                        'tokenizer_encoding = "v41"'):
            with self.subTest(content=content):
                self.write_config(content)
                with self.assertRaises(ValueError):
                    load_agent_config(self.args())

    def test_invalid_configuration_is_reported_instead_of_silently_ignored(self):
        for content in ('max_output_token = 32768', 'context_tokens = 0', 'max_output_tokens = true',
                        'summary_output_tokens = 1.5', 'model_context_window = -1',
                        'skill_dirs = "skills"', 'skill_dirs = [42]', 'provider = "invalid"',
                        'model = ""', 'api_format = "unsupported"', 'max_output_tokens = "32768"'):
            with self.subTest(content=content):
                self.write_config(content)
                with self.assertRaises(ValueError):
                    load_agent_config(self.args())
        self.write_config('model = [')
        with self.assertRaises(tomllib.TOMLDecodeError):
            load_agent_config(self.args())
        with self.assertRaises(FileNotFoundError):
            load_agent_config(self.args("--config", str(self.root / "missing.toml")))


if __name__ == "__main__":
    unittest.main()
