import base64
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from skills_agent.images import (
    MAX_IMAGE_BYTES, MAX_REQUEST_IMAGES, ImageRequestError,
    image_bytes, image_reference, project_images,
)
from skills_agent.providers import AnthropicProvider, OpenAIProvider
from skills_agent.providers.base import ProviderImageError, encoded_file


def call(identifier):
    return {"id": identifier, "name": "shell", "arguments": "{}"}


def sse(*events):
    return b"".join(b"data: " + (b"[DONE]" if event is None else json.dumps(event).encode()) + b"\n\n"
                    for event in events)


def native_images(value):
    if isinstance(value, list):
        return [part for item in value for part in native_images(item)]
    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image", "image"}:
            return [value]
        return [part for item in value.values() for part in native_images(item)]
    return []


class RequestImageTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def image(self, name="snapshot.png", data=b"fixture-image"):
        path = self.root / name
        if data is not None:
            path.write_bytes(data)
        return {"type": "image", "path": str(path), "name": name, "mime_type": "image/png"}

    def test_each_assistant_response_expires_previous_user_and_tool_images(self):
        old_user = self.image("old-user.png", None)
        old_tool = self.image("old-tool.png", None)
        current = self.image("current.png", None)
        messages = [
            {"role": "user", "content": [old_user]},
            {"role": "assistant", "content": "", "tool_calls": [call("first")]},
            {"role": "tool", "tool_call_id": "first", "content": [old_tool]},
            {"role": "assistant", "content": "", "tool_calls": [call("second")]},
            {"role": "tool", "tool_call_id": "second", "content": [current]},
        ]
        original = deepcopy(messages)
        with patch.object(Path, "open", side_effect=AssertionError("Projection must not read files")):
            projected = project_images(messages)
        self.assertEqual(messages, original)
        self.assertEqual(native_images(projected), [current])
        self.assertEqual([item.get("tool_call_id") for item in projected],
                         [item.get("tool_call_id") for item in original])
        text = json.dumps(projected)
        for block in (old_user, old_tool, current):
            self.assertIn(Path(block["path"]).as_uri(), text)
        self.assertEqual(project_images(projected), projected)

    def test_parallel_tool_images_keep_latest_five_with_explicit_references(self):
        blocks = [self.image(f"image-{index}.png", None) for index in range(7)]
        messages = [{"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
                    {"role": "tool", "tool_call_id": "a", "content": blocks[:4]},
                    {"role": "tool", "tool_call_id": "b", "content": blocks[4:]}]
        projected = project_images(messages)
        self.assertEqual(native_images(projected), blocks[-MAX_REQUEST_IMAGES:])
        references = [block for message in projected for block in message["content"]
                      if isinstance(block, dict) and "image_reference" in block]
        self.assertEqual(len(references), 7)
        self.assertEqual([block["image_reference"].get("omitted_reason") for block in references[:2]],
                         ["request_limit", "request_limit"])
        self.assertTrue(all("not attached" in block["text"] for block in references[:2]))
        self.assertEqual(project_images(projected), projected)

    def test_snapshot_url_is_stable_and_original_source_is_retained(self):
        block = self.image("space 中文.png", None)
        block.update(url="data:image/png;base64,ignored", source_url="https://example.test/original.png")
        reference = image_reference(block)
        self.assertEqual(reference["image_reference"]["url"], Path(block["path"]).as_uri())
        self.assertEqual(reference["image_reference"]["source_url"], block["source_url"])
        self.assertNotIn("data:image", reference["text"])
        reference["image_reference"]["event_id"] = "evidence-123"
        self.assertEqual(image_reference(reference), reference)

    def test_incremental_parallel_results_do_not_duplicate_existing_references(self):
        images = [self.image(f"image-{index}.png", None) for index in range(7)]
        projected = project_images([
            {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
            {"role": "tool", "tool_call_id": "a", "content": images[:5]},
        ])
        for block in projected[-1]["content"]:
            if "image_reference" in block:
                block["image_reference"]["event_id"] = "event-first-result"
        projected.append({"role": "tool", "tool_call_id": "b", "content": images[5:]})
        original = deepcopy(projected)
        result = project_images(projected)
        references = [block["image_reference"] for message in result
                      for block in message.get("content", [])
                      if isinstance(block, dict) and "image_reference" in block]
        self.assertEqual(len(references), 7)
        self.assertEqual([reference["event_id"] for reference in references[:5]], ["event-first-result"] * 5)
        self.assertEqual([reference.get("omitted_reason") for reference in references[:2]],
                         ["request_limit", "request_limit"])
        self.assertEqual(native_images(result), images[-5:])
        self.assertEqual(project_images(result), result)
        self.assertEqual(projected, original)

    def test_changed_history_invalidates_following_anthropic_thinking_only(self):
        signed = {"type": "thinking", "thinking": "private thought", "signature": "signed"}
        redacted = {"type": "redacted_thinking", "data": "opaque"}
        tool_use = {"type": "tool_use", "id": "a", "name": "shell", "input": {}}
        responses_state = [{"type": "reasoning", "encrypted_content": "preserved"}]
        messages = [
            {"role": "assistant", "content": [signed], "_anthropic_content": [signed]},
            {"role": "user", "content": [self.image("old.png", None)]},
            {"role": "assistant", "content": [signed, redacted],
             "_anthropic_content": [signed, redacted, tool_use], "tool_calls": [call("a")],
             "_responses_output": responses_state, "reasoning_content": "preserved"},
            {"role": "tool", "tool_call_id": "a", "content": [self.image("new.png", None)]},
        ]
        original = deepcopy(messages)
        projected = project_images(messages)
        self.assertEqual(projected[0], original[0])
        self.assertEqual(projected[2]["content"], [])
        self.assertEqual(projected[2]["_anthropic_content"], [tool_use])
        self.assertEqual(projected[2]["tool_calls"], [call("a")])
        self.assertEqual(projected[2]["_responses_output"], responses_state)
        self.assertEqual(projected[2]["reasoning_content"], "preserved")
        self.assertEqual(messages, original)
        self.assertEqual(project_images(projected), projected)

    def test_image_size_boundary_and_pdf_are_distinct(self):
        block = self.image(data=b"x" * MAX_IMAGE_BYTES)
        self.assertEqual(len(image_bytes(block)), MAX_IMAGE_BYTES)
        self.assertEqual(len(base64.b64decode(encoded_file(block))), MAX_IMAGE_BYTES)
        Path(block["path"]).write_bytes(b"x" * (MAX_IMAGE_BYTES + 1))
        with self.assertRaises(ImageRequestError):
            image_bytes(block)
        with self.assertRaises(ProviderImageError) as caught:
            encoded_file(block)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.diagnostics["phase"], "prepare_image")
        pdf = {**block, "type": "file", "mime_type": "application/pdf"}
        self.assertEqual(len(base64.b64decode(encoded_file(pdf))), MAX_IMAGE_BYTES + 1)

    def test_oversized_current_image_never_reaches_network(self):
        providers = [OpenAIProvider("test", "key", api_format="responses"),
                     OpenAIProvider("test", "key", "https://api.deepseek.com", api_format="chat"),
                     AnthropicProvider("test", "key")]
        messages = [{"role": "user", "content": [self.image(data=b"x" * (MAX_IMAGE_BYTES + 1))]}]
        for provider in providers:
            with self.subTest(provider=type(provider).__name__, api_format=getattr(provider, "api_format", None)), \
                    patch("skills_agent.providers.base.request.urlopen") as send:
                with self.assertRaises(ProviderImageError):
                    provider.complete("", messages, [])
                send.assert_not_called()

    def test_counting_and_generation_send_the_same_current_images_on_all_apis(self):
        old = self.image("missing-old.png", None)
        first, second = self.image("first.png", b"first"), self.image("second.png", b"second")
        messages = [
            {"role": "user", "content": [old]},
            {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
            {"role": "tool", "tool_call_id": "a", "content": [first]},
            {"role": "tool", "tool_call_id": "b", "content": [second]},
        ]
        original = deepcopy(messages)
        providers = [
            (OpenAIProvider("test", "key", "https://api.deepseek.com", api_format="chat"),
             sse({"choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]}, None)),
            (OpenAIProvider("test", "key", api_format="responses"), sse({
                "type": "response.completed", "response": {"status": "completed", "output": [{
                    "type": "message", "content": [{"type": "output_text", "text": "done"}]}]}})),
            (AnthropicProvider("test", "key"), sse(
                {"type": "message_start", "message": {"role": "assistant", "content": [], "stop_reason": None}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"})),
        ]
        for provider, stream in providers:
            with self.subTest(provider=type(provider).__name__, api_format=getattr(provider, "api_format", None)):
                counter = Mock()
                counter.count_request.return_value = 42
                provider._deepseek_counter = counter
                local = getattr(provider, "token_counter", None) == "deepseek"
                responses = [io.BytesIO(stream)] if local else [io.BytesIO(b'{"input_tokens":42}'), io.BytesIO(stream)]
                with patch("skills_agent.providers.base.request.urlopen", side_effect=responses) as send:
                    self.assertEqual(provider.count_tokens("system", messages, []), 42)
                    provider.complete("system", messages, [])
                counted = counter.count_request.call_args.args[0] if local else json.loads(send.call_args_list[0].args[0].data)
                sent = json.loads(send.call_args_list[-1].args[0].data)
                for key in ("stream", "stream_options", "max_tokens", "max_completion_tokens", "max_output_tokens", "store", "include"):
                    sent.pop(key, None)
                self.assertEqual(sent, counted)
                self.assertEqual(len(native_images(sent)), 2)
                wire = json.dumps(sent)
                for block in (old, first, second):
                    self.assertIn(Path(block["path"]).as_uri(), wire)
                self.assertEqual(messages, original)


if __name__ == "__main__":
    unittest.main()
