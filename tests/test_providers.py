import base64
from copy import deepcopy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from skills_agent.providers import AnthropicProvider, OpenAIProvider as NativeOpenAIProvider, ProviderError, create_provider


class OpenAIProvider(NativeOpenAIProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, api_format="chat", **kwargs)


TOOLS = [{"name": "shell", "description": "Run a command", "parameters": {
    "type": "object", "properties": {"command": {"type": "string"}},
    "required": ["command"],
}}]


def openai_response(content="done", calls=None, reason="stop"):
    return {"id": "r1", "choices": [{"finish_reason": reason, "message": {
        "role": "assistant", "content": content, "tool_calls": calls or [],
    }}], "usage": {"total_tokens": 10}}


def anthropic_response(content=None, reason="end_turn"):
    return {"id": "r2", "role": "assistant", "content": content if content is not None else [
        {"type": "text", "text": "done"}], "stop_reason": reason}


def response_events(response):
    if "choices" in response:
        choice = response["choices"][0]
        delta = deepcopy(choice["message"])
        if "tool_calls" in delta:
            delta["tool_calls"] = [{**deepcopy(call), "index": index}
                                   for index, call in enumerate(delta["tool_calls"])]
        yield {**{key: value for key, value in response.items() if key != "choices"},
               "choices": [{"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}]}
        yield None
    else:
        initial = {key: value for key, value in response.items() if key not in {"content", "stop_reason"}}
        yield {"type": "message_start", "message": {**initial, "content": [], "stop_reason": None}}
        for index, block in enumerate(response.get("content", [])):
            yield {"type": "content_block_start", "index": index, "content_block": block}
            yield {"type": "content_block_stop", "index": index}
        yield {"type": "message_delta", "delta": {"stop_reason": response.get("stop_reason")}}
        yield {"type": "message_stop"}


def response_stream(response):
    return io.BytesIO(b"".join(
        b"data: " + (b"[DONE]" if event is None else json.dumps(event).encode()) + b"\n\n"
        for event in response_events(response)
    ))


class ProviderTests(unittest.TestCase):
    def assert_raw(self, actual, expected):
        self.assertIn("stream_events", actual)
        self.assertEqual(actual["stream_events"], [event for event in response_events(expected) if event is not None])
        if "usage" in expected:
            self.assertEqual(actual["usage"], expected["usage"])

    def test_output_limits_reach_each_api_with_per_call_overrides(self):
        responses_events = [{"type": "response.completed", "response": {
            "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "done", "annotations": []},
            ]}],
        }}]
        cases = [
            ("chat", NativeOpenAIProvider, {"api_format": "chat"}, "openai",
             list(response_events(openai_response())), "max_completion_tokens"),
            ("responses", NativeOpenAIProvider, {}, "responses", responses_events, "max_output_tokens"),
            ("deepseek", NativeOpenAIProvider, {"base_url": "https://api.deepseek.com/v1"}, "openai",
             list(response_events(openai_response())), "max_tokens"),
            ("anthropic", AnthropicProvider, {}, "anthropic",
             list(response_events(anthropic_response())), "max_tokens"),
        ]
        for name, cls, settings, module, events, output_key in cases:
            for configured_limit in (None, 8192):
                settings = dict(settings)
                if configured_limit is not None:
                    settings["max_output_tokens"] = configured_limit
                provider = cls("test-model", "secret", **settings)
                # A one-call override must not replace the provider's configured default.
                for override in (None, 16384, None):
                    with self.subTest(api=name, configured=configured_limit, override=override), patch(
                        f"skills_agent.providers.{module}.post_stream",
                        return_value=(deepcopy(event) for event in events),
                    ) as send:
                        provider.complete("system", [{"role": "user", "content": "hello"}], TOOLS,
                                          max_output_tokens=override)
                        payload = send.call_args.args[1]
                        self.assertEqual(payload[output_key], override or configured_limit or 32768)
                        for other_key in {"max_tokens", "max_completion_tokens", "max_output_tokens"} - {output_key}:
                            self.assertNotIn(other_key, payload)
                        send.assert_called_once()

    def test_openai_request_and_raw_response(self):
        raw = openai_response()
        with patch("skills_agent.providers.base.request.urlopen", return_value=response_stream(raw)) as send:
            result = OpenAIProvider("test-model", "secret", "http://localhost/v1/", 200).complete(
                "system", [{"role": "user", "content": [{
                    "type": "text", "text": "hello", "attachment": True,
                    "path": "/private/test.txt", "name": "test.txt",
                }], "_raw": {"private": "do not send"}}], TOOLS)
        req = send.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "http://localhost/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer secret")
        self.assertEqual(payload["max_completion_tokens"], 200)
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "system"},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ])
        self.assertEqual(payload["tools"][0], {"type": "function", "function": TOOLS[0]})
        self.assertEqual(result["content"], [{"type": "text", "text": "done"}])
        self.assert_raw(result["_raw"], raw)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_openai_tool_response_and_followup(self):
        call = {"id": "a", "type": "function", "function": {
            "name": "shell", "arguments": '{"command":"pwd"}'}}
        raw = openai_response(None, [call], "tool_calls")
        provider = OpenAIProvider("test-model", "secret")
        with patch("skills_agent.providers.openai.post_stream", return_value=response_events(raw)):
            assistant = provider.complete("", [{"role": "user", "content": "pwd"}], TOOLS)
        self.assertEqual(assistant["tool_calls"][0]["arguments"], '{"command":"pwd"}')
        with patch("skills_agent.providers.openai.post_stream", return_value=response_events(openai_response())) as send:
            provider.complete("", [assistant, {
                "role": "tool", "tool_call_id": "a", "name": "shell", "content": "/tmp",
            }], TOOLS)
        sent = send.call_args.args[1]["messages"]
        self.assertEqual(sent[0]["tool_calls"], [call])
        self.assertNotIn("_raw", sent[0])
        self.assertNotIn("reasoning_content", sent[0])
        self.assertEqual(sent[1], {"role": "tool", "tool_call_id": "a", "content": "/tmp"})

    def test_anthropic_ignores_openai_reasoning_metadata(self):
        with patch("skills_agent.providers.anthropic.post_stream",
                   return_value=response_events(anthropic_response())) as send:
            AnthropicProvider("test-model", "secret").complete("", [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer", "reasoning_content": "PRIVATE_REASONING"},
                {"role": "user", "content": "next"},
            ], TOOLS)
        self.assertEqual(send.call_args.args[1]["messages"][1], {
            "role": "assistant", "content": [{"type": "text", "text": "answer"}],
        })
        self.assertNotIn("PRIVATE_REASONING", json.dumps(send.call_args.args[1]))

    def test_anthropic_merges_parallel_results_before_user_text(self):
        calls = [{"id": name, "name": "shell", "arguments": '{"command":"pwd"}'}
                 for name in ["a", "b"]]
        messages = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "checking", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "a", "content": "one"},
            {"role": "tool", "tool_call_id": "b", "content": "two"},
            {"role": "user", "content": "continue"},
        ]
        with patch("skills_agent.providers.base.request.urlopen",
                   return_value=response_stream(anthropic_response())) as send:
            AnthropicProvider("test-model", "secret", max_output_tokens=201).complete("sys", messages, TOOLS)
        req = send.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_header("X-api-key"), "secret")
        self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(payload["system"], "sys")
        self.assertEqual(payload["max_tokens"], 201)
        self.assertEqual(payload["tools"][0]["input_schema"], TOOLS[0]["parameters"])
        self.assertEqual(payload["messages"][1]["content"][1], {
            "type": "tool_use", "id": "a", "name": "shell", "input": {"command": "pwd"},
        })
        self.assertEqual(payload["messages"][2], {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "one"},
            {"type": "tool_result", "tool_use_id": "b", "content": "two"},
            {"type": "text", "text": "continue"},
        ]})
        self.assertEqual(len(payload["messages"]), 3)
        self.assertNotIn("thinking", payload)

    def test_anthropic_tool_response(self):
        raw = anthropic_response([
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "a", "name": "shell", "input": {"command": "pwd"}},
        ], "tool_use")
        with patch("skills_agent.providers.anthropic.post_stream", return_value=response_events(raw)):
            result = AnthropicProvider("model", "key").complete("", [], TOOLS)
        self.assertEqual(result["content"], [{"type": "text", "text": "checking"}])
        self.assertEqual(json.loads(result["tool_calls"][0]["arguments"]), {"command": "pwd"})
        self.assert_raw(result["_raw"], raw)

    def test_anthropic_omits_empty_text_after_context_compaction(self):
        messages = [
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": [{"type": "text", "text": "checking " * 300}],
             "tool_calls": [{"id": "a", "name": "shell", "arguments": "{}"}]},
            {"role": "tool", "tool_call_id": "a", "name": "shell", "content": "x" * 4000},
        ]
        selected = deepcopy(messages)
        selected[1]["content"] = [{"type": "text", "text": ""}]
        self.assertEqual(selected[1]["content"], [{"type": "text", "text": ""}])
        with patch("skills_agent.providers.anthropic.post_stream", return_value=response_events(anthropic_response())) as send:
            AnthropicProvider("model", "key").complete("", selected, TOOLS)
        sent = send.call_args.args[1]["messages"]
        self.assertEqual(sent[1]["content"], [{
            "type": "tool_use", "id": "a", "name": "shell", "input": {},
        }])
        self.assertEqual(sent[2]["content"][0]["tool_use_id"], "a")
        self.assertTrue(messages[1]["content"][0]["text"].startswith("checking "))

    def test_anthropic_skips_empty_assistant_messages(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},
            {"role": "assistant", "content": []},
            {"role": "user", "content": "next"},
        ]
        with patch("skills_agent.providers.anthropic.post_stream", return_value=response_events(anthropic_response())) as send:
            AnthropicProvider("model", "key").complete("", messages, [])
        self.assertEqual(send.call_args.args[1]["messages"], [{"role": "user", "content": [
            {"type": "text", "text": "first"}, {"type": "text", "text": "next"},
        ]}])

    def test_multimodal_maps_file_bytes_and_ignores_attachment_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            image, pdf = Path(directory) / "image.png", Path(directory) / "doc.pdf"
            image.write_bytes(b"image bytes")
            pdf.write_bytes(b"%PDF bytes")
            content = [
                {"type": "text", "text": "input", "path": "/meta.txt", "attachment": True},
                {"type": "image", "path": str(image), "mime_type": "image/png"},
                {"type": "file", "path": str(pdf), "mime_type": "application/pdf", "name": "doc.pdf"},
                {"type": "file", "path": "/archive.zip", "mime_type": "application/zip"},
            ]
            for kind, cls, raw in [("openai", OpenAIProvider, openai_response()),
                                   ("anthropic", AnthropicProvider, anthropic_response())]:
                with self.subTest(provider=kind), patch(
                    f"skills_agent.providers.{kind}.post_stream", return_value=response_events(raw),
                ) as send:
                    cls("model", "key").complete("", [{"role": "user", "content": content}], [])
                    blocks = send.call_args.args[1]["messages"][0]["content"]
                    self.assertEqual(blocks[0], {"type": "text", "text": "input"})
                    self.assertEqual(blocks[-1], {"type": "text", "text": "File: /archive.zip"})
                    if kind == "openai":
                        self.assertEqual(blocks[1]["image_url"]["url"],
                                         "data:image/png;base64," + base64.b64encode(b"image bytes").decode())
                        self.assertEqual(blocks[2]["file"]["filename"], "doc.pdf")
                        self.assertTrue(blocks[2]["file"]["file_data"].startswith("data:application/pdf;base64,"))
                    else:
                        self.assertEqual(blocks[1]["type"], "image")
                        self.assertEqual(blocks[2]["type"], "document")
                        self.assertEqual(base64.b64decode(blocks[2]["source"]["data"]), b"%PDF bytes")

    def test_refusal_content_is_preserved(self):
        raw = openai_response(None)
        raw["choices"][0]["message"]["refusal"] = "declined"
        for kind, cls, response in [("openai", OpenAIProvider, raw), (
            "anthropic", AnthropicProvider,
            anthropic_response([{"type": "text", "text": "declined"}], "refusal"),
        )]:
            with self.subTest(provider=kind), patch(f"skills_agent.providers.{kind}.post_stream", return_value=response_events(response)):
                result = cls("model", "key").complete("", [], [])
                self.assertEqual(result["content"], [{"type": "text", "text": "declined"}])

    def test_unsuccessful_stop_never_returns_partial_calls(self):
        cases = [("openai", OpenAIProvider, openai_response(None, [{"id": "a", "type": "function", "function": {"name": "shell", "arguments": "{}"}}], reason), reason)
                 for reason in ["length", "content_filter", None]]
        cases += [("anthropic", AnthropicProvider, anthropic_response([
            {"type": "tool_use", "id": "a", "name": "shell", "input": {}},
        ], reason), reason) for reason in ["max_tokens", "pause_turn", "model_context_window_exceeded"]]
        for kind, cls, raw, reason in cases:
            with self.subTest(provider=kind, reason=reason), patch(
                f"skills_agent.providers.{kind}.post_stream", return_value=response_events(raw),
            ), self.assertRaises(ProviderError) as raised:
                cls("model", "key").complete("", [], [])
            self.assertIn(str(reason), str(raised.exception))
            self.assert_raw(raised.exception.raw, raw)

    def test_truncated_response_preserves_received_text_without_tool_inputs(self):
        text = "  original\npartial answer  "
        openai_raw = openai_response([
            {"type": "text", "text": text}, {"type": "refusal", "refusal": "refusal text"},
        ], [{"id": "a", "type": "function", "function": {
            "name": "subagent", "arguments": '{"task":"PRIVATE_TASK"',
        }}], "length")
        anthropic_raw = anthropic_response([
            {"type": "text", "text": text},
            {"type": "tool_use", "id": "a", "name": "subagent", "input": {"task": "PRIVATE_TASK"}},
            {"type": "text", "text": "refusal text"},
        ], "max_tokens")
        for kind, cls, raw in [("openai", OpenAIProvider, openai_raw),
                               ("anthropic", AnthropicProvider, anthropic_raw)]:
            with self.subTest(provider=kind), patch(
                f"skills_agent.providers.{kind}.post_stream", return_value=response_events(raw),
            ), self.assertRaises(ProviderError) as raised:
                cls("model", "key").complete("", [], [])
            self.assertEqual(raised.exception.partial_content, [
                {"type": "text", "text": text}, {"type": "text", "text": "refusal text"},
            ])
            self.assert_raw(raised.exception.raw, raw)
            self.assertNotIn("PRIVATE_TASK", json.dumps(raised.exception.partial_content))

    def test_response_parse_errors_keep_text_and_top_level_refusal(self):
        raw = openai_response("original", [{"id": "a", "type": "function", "function": {
            "name": "shell", "arguments": "{"}}], "tool_calls")
        raw["choices"][0]["message"]["refusal"] = "received refusal"
        with patch("skills_agent.providers.openai.post_stream", return_value=response_events(raw)), self.assertRaises(ProviderError) as raised:
            OpenAIProvider("model", "key").complete("", [], [])
        self.assertEqual(raised.exception.partial_content, [
            {"type": "text", "text": "original"}, {"type": "text", "text": "received refusal"},
        ])
        malformed = {"content": [{"type": "text", "text": "received before invalid envelope"}]}
        with patch("skills_agent.providers.anthropic.post_stream", return_value=response_events(malformed)), self.assertRaises(ProviderError) as raised:
            AnthropicProvider("model", "key").complete("", [], [])
        self.assertEqual(raised.exception.partial_content, malformed["content"])

    def test_agent_does_not_execute_calls_from_truncated_responses(self):
        from skills_agent.agent import Agent
        from skills_agent.session import Session
        from skills_agent.skills import SkillRegistry

        call = {"id": "a", "type": "function", "function": {
            "name": "shell", "arguments": '{"command":"echo should-not-run"}',
        }}
        responses = [
            ("openai", OpenAIProvider, openai_response("partial answer", [call], "length")),
            ("anthropic", AnthropicProvider, anthropic_response([
                {"type": "text", "text": "partial answer"},
                {"type": "tool_use", "id": "a", "name": "shell", "input": {"command": "echo should-not-run"}},
            ], "max_tokens")),
        ]
        for kind, cls, raw in responses:
            with self.subTest(provider=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                session = Session(root, "s")
                provider = cls("model", "key")
                provider.count_tokens = lambda *_args: 10
                provider.count_text = lambda _text: 1
                agent = Agent(provider, session, SkillRegistry([]), root)
                with patch(f"skills_agent.providers.{kind}.post_stream", return_value=response_events(raw)), patch.object(
                    agent.tools, "execute",
                ) as execute, self.assertRaises(ProviderError):
                    agent.run(session.make_user_message("do the task", []))
                execute.assert_not_called()
                self.assertEqual(session.messages(), [{"role": "user", "content": [{"type": "text", "text": "do the task"}]}])

    def test_broken_tool_pairs_rejected_before_http(self):
        call = {"role": "assistant", "content": [], "tool_calls": [
            {"id": "a", "name": "shell", "arguments": "{}"},
        ]}
        result = {"role": "tool", "tool_call_id": "a", "content": "ok"}
        invalid = [[result], [call], [call, {"role": "user", "content": "interrupt"}, result],
                   [call, result, result]]
        for cls in [OpenAIProvider, AnthropicProvider]:
            for messages in invalid:
                with self.subTest(provider=cls.__name__, messages=messages), patch(
                    "skills_agent.providers.base.request.urlopen",
                ) as send, self.assertRaises(ProviderError):
                    cls("model", "key").complete("", messages, [])
                send.assert_not_called()

    def test_invalid_tool_arguments_are_not_returned(self):
        for arguments in ['{"command":', "[]"]:
            raw = openai_response(None, [{"id": "a", "type": "function", "function": {
                "name": "shell", "arguments": arguments}}], "tool_calls")
            with patch("skills_agent.providers.openai.post_stream", return_value=response_events(raw)), self.assertRaises(ProviderError):
                OpenAIProvider("model", "key").complete("", [], [])

    def test_duplicate_response_calls_are_rejected_before_execution(self):
        call = {"id": "same", "type": "function", "function": {"name": "shell", "arguments": "{}"}}
        anth_call = {"id": "same", "type": "tool_use", "name": "shell", "input": {}}
        for kind, cls, raw in [
            ("openai", OpenAIProvider, openai_response(None, [call, call], "tool_calls")),
            ("anthropic", AnthropicProvider, anthropic_response([anth_call, anth_call], "tool_use")),
        ]:
            with self.subTest(provider=kind), patch(
                f"skills_agent.providers.{kind}.post_stream", return_value=response_events(raw),
            ), self.assertRaisesRegex(ProviderError, "Duplicate") as raised:
                cls("model", "key").complete("", [], [])
            self.assert_raw(raised.exception.raw, raw)

    def test_http_error_does_not_expose_key_or_body_and_does_not_retry(self):
        error = HTTPError("https://example.com?key=secret", 401, "secret", {}, io.BytesIO(b"secret"))
        with (patch("skills_agent.providers.base.request.urlopen", side_effect=error) as send,
              self.assertRaises(ProviderError) as raised):
            OpenAIProvider("model", "secret").complete("", [], [])
        self.assertEqual(str(raised.exception), "API HTTP error 401")
        self.assertEqual(send.call_count, 1)

    def test_connection_and_invalid_json_errors(self):
        with patch("skills_agent.providers.base.request.urlopen", side_effect=URLError("secret")), self.assertRaises(ProviderError) as raised:
            OpenAIProvider("model", "secret").complete("", [], [])
        self.assertEqual(str(raised.exception), "API connection failed")
        with patch("skills_agent.providers.base.request.urlopen", return_value=io.BytesIO(b"not JSON")), self.assertRaises(ProviderError):
            OpenAIProvider("model", "secret").complete("", [], [])

    def test_factory(self):
        self.assertIsInstance(create_provider("openai", "model", "key"), NativeOpenAIProvider)
        self.assertIsInstance(create_provider("anthropic", "model", "key"), AnthropicProvider)
        for name in ("openai", "anthropic"):
            with self.subTest(provider=name):
                self.assertEqual(create_provider(name, "model", "key").max_output_tokens, 32768)
                self.assertEqual(create_provider(name, "model", "key", max_output_tokens=8192).max_output_tokens, 8192)
        with self.assertRaises(ValueError):
            create_provider("unknown", "model", "key")


if __name__ == "__main__":
    unittest.main()
