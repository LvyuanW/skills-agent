import unittest
from unittest.mock import patch

from skills_agent.providers.openai import OpenAIProvider
from tests.test_deepseek_effort import chat_stream


BASE_URL = "https://ws-test.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1"
MODEL = "deepseek-v4.1-flash-1017"
MESSAGES = [{"role": "user", "content": "Hello"}]


class BailianDeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch(
            "skills_agent.providers.base.request.urlopen",
            side_effect=AssertionError("Tests must not make network requests"),
        ))

    def model(self, **kwargs):
        options = {"model": MODEL, "api_key": "not-a-real-key", "base_url": BASE_URL,
                   "deepseek_effort": "high"}
        options.update(kwargs)
        return OpenAIProvider(**options)

    def complete_payload(self, model, *, disabled=False):
        with patch("skills_agent.providers.openai.post_stream", side_effect=chat_stream) as send:
            model.complete("system", MESSAGES, [], disable_thinking=disabled)
        self.assertEqual(send.call_args.args[0], model.base_url + "/chat/completions")
        self.network.assert_not_called()
        return send.call_args.args[1]

    def test_effort_is_sent_using_bailian_native_parameters(self):
        for effort in ("low", "high", "max"):
            with self.subTest(effort=effort):
                payload = self.complete_payload(self.model(deepseek_effort=effort))
                self.assertEqual(payload["model"], MODEL)
                self.assertEqual(payload["reasoning_effort"], effort)
                self.assertIs(payload["enable_thinking"], True)
                self.assertNotIn("thinking", payload)
                self.assertEqual(payload["max_tokens"], 32768)
                self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_summary_disables_thinking_without_changing_following_call(self):
        model = self.model()
        payload = self.complete_payload(model, disabled=True)
        self.assertIs(payload["enable_thinking"], False)
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("thinking", payload)
        following = self.complete_payload(model)
        self.assertIs(following["enable_thinking"], True)
        self.assertEqual(following["reasoning_effort"], "high")

    def test_explicit_none_uses_bailian_disable_parameter(self):
        payload = self.complete_payload(self.model(deepseek_effort="none"))
        self.assertIs(payload["enable_thinking"], False)
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("thinking", payload)

    def test_unspecified_effort_keeps_service_defaults(self):
        payload = self.complete_payload(self.model(deepseek_effort=None))
        for key in ("enable_thinking", "reasoning_effort", "thinking"):
            self.assertNotIn(key, payload)

    def test_invalid_effort_fails_before_network(self):
        with self.assertRaisesRegex(ValueError, "DeepSeek effort"):
            self.model(deepseek_effort="medium")
        self.network.assert_not_called()

    def test_other_models_and_unrecognized_hosts_keep_existing_behavior(self):
        cases = [{"model": "qwen-test"}, {"base_url": "https://gateway.invalid/v1"},
                 {"base_url": "https://maas.aliyuncs.com.invalid/compatible-mode/v1"}]
        for options in cases:
            with self.subTest(options=options):
                payload = self.complete_payload(self.model(**options))
                for key in ("enable_thinking", "reasoning_effort", "thinking"):
                    self.assertNotIn(key, payload)

    def test_official_deepseek_disable_parameter_unchanged(self):
        model = self.model(base_url="https://api.deepseek.com/v1")
        enabled = self.complete_payload(model)
        self.assertEqual(enabled["reasoning_effort"], "high")
        self.assertNotIn("enable_thinking", enabled)
        disabled = self.complete_payload(model, disabled=True)
        self.assertEqual(disabled["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", disabled)

    def test_responses_protocol_does_not_activate_bailian_chat_parameters(self):
        model = self.model(api_format="responses", token_counter="official")
        self.assertIsNone(model.deepseek_effort)
        self.assertFalse(model._bailian_deepseek_chat)


if __name__ == "__main__":
    unittest.main()
