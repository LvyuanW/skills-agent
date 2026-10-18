import base64
from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from skills_agent.providers import AnthropicProvider, OpenAIProvider, ProviderError
from skills_agent.providers.base import tool_feedback_messages
from tests.test_providers import anthropic_response, openai_response, response_events


def png_bytes(color):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0" + bytes(color))) + chunk(b"IEND", b""))


class FeedbackProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.images = []
        self.encoded = []
        for name, color in (("first.png", (255, 0, 0)), ("second.png", (0, 0, 255))):
            path = root / name
            data = png_bytes(color)
            path.write_bytes(data)
            self.images.append({"type": "image", "path": str(path), "name": name, "mime_type": "image/png"})
            self.encoded.append(base64.b64encode(data).decode("ascii"))
        self.original_result = '{"exit_code":0,"stdout":"rendered\\n","stderr":""}'
        self.messages = [
            {"role": "user", "content": "Inspect the saved artifact."},
            {"role": "assistant", "content": [{"type": "text", "text": "Inspect two frames."}],
             "reasoning_content": "preserved provider state", "tool_calls": [
                 {"id": name, "name": "shell", "arguments": "{}"} for name in ("frame-a", "frame-b")
             ]},
            {"role": "tool", "tool_call_id": "frame-a", "name": "shell", "content": [
                {"type": "text", "text": self.original_result}, self.images[0],
            ]},
            {"role": "tool", "tool_call_id": "frame-b", "name": "shell", "content": [
                {"type": "text", "text": "second result"}, self.images[1],
            ]},
        ]

    def test_projection_waits_for_the_whole_group_and_preserves_original_records(self):
        messages = [*self.messages, {"role": "user", "content": "A later real user request."}]
        original = deepcopy(messages)
        projected = tool_feedback_messages(messages)
        self.assertEqual([message["role"] for message in projected],
                         ["user", "assistant", "tool", "tool", "user", "user"])
        self.assertEqual(projected[2]["content"], self.original_result)
        self.assertEqual(projected[-1], messages[-1])
        observations = projected[4]["content"]
        self.assertIn("not new user instructions", observations[0]["text"])
        self.assertIn("frame-a: first.png", observations[1]["text"])
        self.assertIn("frame-b: second.png", observations[3]["text"])
        self.assertEqual([observations[2], observations[4]], self.images)
        observations[2]["path"] = "changed only in the request projection"
        self.assertEqual(messages, original)

    def test_string_results_are_unchanged_and_unsupported_blocks_fail_explicitly(self):
        messages = deepcopy(self.messages)
        for message in messages[2:]:
            message["content"] = " \tJSON-like output\r\nwithout a final newline"
        self.assertEqual(tool_feedback_messages(messages), messages)
        for invalid in ({"type": "audio"}, {"type": "text", "text": 3}, "not a block"):
            with self.subTest(block=invalid):
                broken = deepcopy(self.messages)
                broken[2]["content"] = [invalid]
                with self.assertRaises(ProviderError):
                    tool_feedback_messages(broken)

    def test_incomplete_or_interrupted_groups_are_rejected_before_reading_images(self):
        invalid_sequences = (self.messages[:-1], [*self.messages[:3], {"role": "user", "content": "interrupt"}, self.messages[3]])
        builders = [OpenAIProvider("model", "key", api_format="chat")._chat_payload,
                    OpenAIProvider("model", "key")._responses_payload,
                    AnthropicProvider("model", "key")._payload]
        for messages in invalid_sequences:
            for builder in builders:
                with self.subTest(builder=builder.__name__), patch.object(Path, "read_bytes") as read:
                    with self.assertRaises(ProviderError):
                        builder("", messages, [])
                    read.assert_not_called()

    def test_all_builders_send_real_pixels_after_all_tool_results(self):
        original = deepcopy(self.messages)
        chat = OpenAIProvider("model", "key", api_format="chat")._chat_payload("", self.messages, [])
        self.assertEqual([message["role"] for message in chat["messages"]],
                         ["user", "assistant", "tool", "tool", "user"])
        self.assertTrue(chat["messages"][2]["content"].startswith(self.original_result))
        self.assertIn(Path(self.images[0]["path"]).as_uri(), chat["messages"][2]["content"])
        self.assertEqual(chat["messages"][1]["reasoning_content"], "preserved provider state")
        self.assertEqual([block["image_url"]["url"] for block in chat["messages"][-1]["content"]
                          if block["type"] == "image_url"],
                         ["data:image/png;base64," + value for value in self.encoded])

        responses = OpenAIProvider("model", "key")._responses_payload("", self.messages, [])
        items = responses["input"]
        self.assertEqual([item["call_id"] for item in items if item.get("type") == "function_call_output"],
                         ["frame-a", "frame-b"])
        self.assertEqual(items[-2]["type"], "function_call_output")
        self.assertEqual(items[-1]["role"], "user")
        self.assertEqual([block["image_url"] for block in items[-1]["content"] if block["type"] == "input_image"],
                         ["data:image/png;base64," + value for value in self.encoded])

        anthropic = AnthropicProvider("model", "key")._payload("", self.messages, [])
        content = anthropic["messages"][-1]["content"]
        self.assertEqual([block["tool_use_id"] for block in content[:2]], ["frame-a", "frame-b"])
        self.assertTrue(content[0]["content"].startswith(self.original_result))
        self.assertIn(Path(self.images[0]["path"]).as_uri(), content[0]["content"])
        self.assertEqual([block["source"]["data"] for block in content if block["type"] == "image"], self.encoded)
        self.assertEqual(self.messages, original)

    def test_projection_keeps_native_assistant_state_and_multiple_groups(self):
        messages = deepcopy(self.messages)
        responses_state = [
            {"type": "reasoning", "id": "reason", "encrypted_content": "opaque state", "summary": []},
            *[{"type": "function_call", "call_id": call["id"], "name": call["name"],
               "arguments": call["arguments"]} for call in messages[1]["tool_calls"]],
        ]
        messages[1]["_responses_output"] = responses_state
        payload = OpenAIProvider("model", "key")._responses_payload("", messages, [])
        self.assertEqual(payload["input"][1:4], responses_state)
        self.assertEqual(messages[1]["_responses_output"], responses_state)

        anthropic_state = [{"type": "thinking", "thinking": "signed reasoning", "signature": "signature"},
                           *[{"type": "tool_use", "id": call["id"], "name": call["name"], "input": {}}
                             for call in messages[1]["tool_calls"]]]
        messages[1]["_anthropic_content"] = anthropic_state
        payload = AnthropicProvider("model", "key")._payload("", messages, [])
        self.assertEqual(payload["messages"][1]["content"], anthropic_state)
        self.assertEqual(messages[1]["_anthropic_content"], anthropic_state)

        next_group = deepcopy(self.messages[1:])
        next_group[0]["tool_calls"] = [{"id": "next-a", "name": "shell", "arguments": "{}"},
                                       {"id": "next-b", "name": "shell", "arguments": "{}"}]
        next_group[1]["tool_call_id"], next_group[2]["tool_call_id"] = "next-a", "next-b"
        projected = tool_feedback_messages([*self.messages, *next_group])
        self.assertEqual([message["role"] for message in projected],
                         ["user", "assistant", "tool", "tool", "user", "assistant", "tool", "tool", "user"])
        self.assertIn("next-a", projected[-1]["content"][1]["text"])

    def test_pdf_feedback_uses_existing_native_file_conversion(self):
        path = Path(self.directory.name) / "report.pdf"
        path.write_bytes(b"%PDF-1.4\nfixture")
        messages = deepcopy(self.messages)
        messages[2]["content"][1] = {"type": "file", "path": str(path), "name": "report.pdf", "mime_type": "application/pdf"}
        cases = [
            (OpenAIProvider("model", "key", api_format="chat")._chat_payload, "file_data"),
            (OpenAIProvider("model", "key")._responses_payload, "file_data"),
            (AnthropicProvider("model", "key")._payload, "application/pdf"),
        ]
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        for builder, marker in cases:
            with self.subTest(builder=builder.__name__):
                payload = json.dumps(builder("", messages, []))
                self.assertIn(marker, payload)
                self.assertIn(encoded, payload)

    def test_counting_and_generation_share_the_feedback_projection(self):
        responses_output = [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "done", "annotations": []},
        ]}]
        cases = [
            (OpenAIProvider("model", "key"), "openai", "responses",
             [{"type": "response.completed", "response": {"status": "completed", "output": responses_output}}],
             {"stream", "store", "max_output_tokens", "include"}),
            (AnthropicProvider("model", "key"), "anthropic", "anthropic",
             list(response_events(anthropic_response())), {"stream", "max_tokens"}),
        ]
        for provider, count_module, stream_module, events, output_fields in cases:
            with self.subTest(provider=type(provider).__name__), patch(
                f"skills_agent.providers.{count_module}.post_json", return_value={"input_tokens": 42},
            ) as count, patch(
                f"skills_agent.providers.{stream_module}.post_stream", return_value=(event for event in deepcopy(events)),
            ) as send:
                self.assertEqual(provider.count_tokens("system", self.messages, []), 42)
                provider.complete("system", self.messages, [])
            self.assertEqual(count.call_args.args[1], {
                key: value for key, value in send.call_args.args[1].items() if key not in output_fields
            })
            for encoded in self.encoded:
                self.assertIn(encoded, json.dumps(count.call_args.args[1]))

        provider = OpenAIProvider("deepseek-flash", "key", "https://api.deepseek.com")
        events = list(response_events(openai_response()))
        events.insert(-1, {"choices": [], "usage": {"total_tokens": 210}})
        with patch("skills_agent.providers.openai.post_json") as count, patch(
            "skills_agent.providers.openai.post_stream", return_value=(event for event in events),
        ) as send:
            self.assertIsNone(provider.count_tokens("system", self.messages, []))
            reply = provider.complete("system", self.messages, [])
            self.assertEqual(provider.count_tokens("system", self.messages + [reply], []), 210)
        count.assert_not_called()
        for encoded in self.encoded:
            self.assertIn(encoded, json.dumps(send.call_args.args[1]))


if __name__ == "__main__":
    unittest.main()
