import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from skills_agent.benchmark import parser as benchmark_parser
from skills_agent.cli import parser as cli_parser
from skills_agent.runtime import create_agent
from skills_agent.session import Session
from tests.test_providers import anthropic_response, response_events
from tests.test_stream_openai import chunk


MESSAGES = [{"role": "user", "content": "Hello"}]
TOOLS = [{"name": "shell", "description": "Run a command", "parameters": {
    "type": "object", "properties": {"command": {"type": "string"}},
}}]


def chat_stream(*args, **kwargs):
    yield chunk({"content": "done"}, "stop")
    yield None


def responses_stream(*args, **kwargs):
    yield {"type": "response.completed", "response": {
        "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "done", "annotations": []},
        ]}],
    }}


class DeepSeekEffortTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {
            "OPENAI_MODEL": "deepseek-v4.1-flash",
            "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
        }, clear=True))
        self.network = self.enterContext(patch(
            "skills_agent.providers.base.request.urlopen",
            side_effect=AssertionError("Tests must not make network requests"),
        ))

    def model(self, *options, benchmark=False):
        if benchmark:
            parser = benchmark_parser()
            initial = ["--prompt-file", "task.txt", "--log-dir", str(self.root / "logs")]
        else:
            parser = cli_parser()
            initial = ["run", "--session", "demo"]
        args = parser.parse_args([*initial, "--workspace", str(self.root), *options])
        return create_agent(args, Session(self.root, "test")).model

    def assert_controls(self, payload, expected):
        controls = {key: payload[key] for key in ("reasoning_effort", "thinking", "reasoning")
                    if key in payload}
        self.assertEqual(controls, expected)

    def count_and_complete(self, model, *, disable_thinking=False):
        counter = Mock()
        counter.count_request.return_value = 42
        with patch.object(model, "_counter", return_value=counter), patch(
            "skills_agent.providers.openai.post_stream", side_effect=chat_stream,
        ) as send:
            self.assertEqual(model.count_tokens("system", MESSAGES, TOOLS,
                                                disable_thinking=disable_thinking), 42)
            model.complete("system", MESSAGES, TOOLS, disable_thinking=disable_thinking)
        payload = send.call_args.args[1]
        self.assertEqual(counter.count_request.call_args.args[0], {
            key: value for key, value in payload.items()
            if key not in {"stream", "stream_options", "max_tokens"}
        })
        self.assertEqual(send.call_args.args[0], "https://api.deepseek.com/v1/chat/completions")
        return payload

    def test_environment_reaches_generation_and_counting_for_run_and_benchmark(self):
        cases = [
            ("low", {"reasoning_effort": "low"}),
            ("high", {"reasoning_effort": "high"}),
            ("max", {"reasoning_effort": "max"}),
            ("none", {"thinking": {"type": "disabled"}}),
            (None, {}),
            ("", {}),
        ]
        for benchmark in (False, True):
            for effort, expected in cases:
                with self.subTest(benchmark=benchmark, effort=effort):
                    if effort is None:
                        os.environ.pop("effort", None)
                    else:
                        os.environ["effort"] = effort
                    payload = self.count_and_complete(self.model(benchmark=benchmark))
                    self.assert_controls(payload, expected)
        self.network.assert_not_called()

    def test_invalid_effort_fails_at_startup(self):
        for effort in ("medium", "LOW", " low", "0"):
            with self.subTest(effort=effort):
                os.environ["effort"] = effort
                with self.assertRaisesRegex(ValueError, "DeepSeek effort.*none, low, high, max"):
                    self.model()
        self.network.assert_not_called()

    def test_other_providers_hosts_and_protocols_ignore_environment_effort(self):
        cases = [
            ("anthropic", ["--provider", "anthropic", "--model", "claude-test"],
             "anthropic", lambda *args, **kwargs: response_events(anthropic_response())),
            ("openai-responses", ["--base-url", "https://api.openai.com/v1"],
             "responses", responses_stream),
            ("other-chat", ["--base-url", "https://gateway.invalid/v1", "--token-counter", "deepseek"],
             "openai", chat_stream),
            ("deepseek-responses", ["--api-format", "responses", "--token-counter", "official"],
             "responses", responses_stream),
        ]
        for name, options, module, stream in cases:
            for effort in ("low", "none", "unsupported"):
                with self.subTest(route=name, effort=effort):
                    os.environ["effort"] = effort
                    model = self.model(*options)
                    with patch(f"skills_agent.providers.{module}.post_stream", side_effect=stream) as send:
                        model.complete("system", MESSAGES, TOOLS)
                    self.assert_controls(send.call_args.args[1], {})
        self.network.assert_not_called()

    def test_per_call_disable_thinking_overrides_effort_without_changing_next_call(self):
        os.environ["effort"] = "high"
        model = self.model()
        for disabled, expected in [
            (False, {"reasoning_effort": "high"}),
            (True, {"thinking": {"type": "disabled"}}),
            (False, {"reasoning_effort": "high"}),
        ]:
            with self.subTest(disable_thinking=disabled):
                self.assert_controls(self.count_and_complete(model, disable_thinking=disabled), expected)
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
