import os
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest
from unittest.mock import patch

from skills_agent.benchmark import parser as benchmark_parser
from skills_agent.cli import parser as cli_parser
from skills_agent.config import load_agent_config
from skills_agent.runtime import create_agent
from skills_agent.session import Session


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def args(self, *options, benchmark=False):
        if benchmark:
            parser = benchmark_parser()
            initial = ["--prompt-file", "task.txt", "--log-dir", str(self.root / "logs")]
        else:
            parser = cli_parser()
            initial = ["run", "--session", "demo"]
        return parser.parse_args([*initial, "--workspace", str(self.root),
                                 "--data-dir", str(self.root / "runtime-data"), *options])

    def write_config(self, content):
        path = self.root / "skills-agent.toml"
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
        self.assertEqual(override.skill_dirs, ["cli-skills"])

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
