import copy
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from skills_agent.providers import OpenAIProvider, ProviderError
from skills_agent.providers.base import post_stream
from skills_agent.session import Session


def chunk(delta=None, finish=None, **metadata):
    return {"id": "stream-id", "choices": [{"index": 0, "delta": delta or {},
                                            "finish_reason": finish}], **metadata}


def tool(index=0, **fields):
    return {"tool_calls": [{"index": index, **fields}]}


class OpenAIStreamTests(unittest.TestCase):
    def complete(self, events, on_event=None):
        with patch("skills_agent.providers.openai.post_stream",
                   return_value=(event for event in events)):
            return OpenAIProvider("test", "secret", api_format="chat").complete("", [], [], on_event=on_event)

    def test_text_is_delivered_before_reading_later_frames_and_stream_closes(self):
        observed, closed = [], []

        def stream():
            try:
                yield chunk({"role": "assistant", "content": "你好"})
                self.assertEqual(observed, [{"type": "text_delta", "text": "你好"}])
                yield chunk({"content": "世界"}, "stop")
                yield {"id": "stream-id", "choices": [], "usage": {"total_tokens": 9}}
                yield None
                self.fail("Provider must stop reading after [DONE]")
            finally:
                closed.append(True)

        with patch("skills_agent.providers.openai.post_stream", return_value=stream()) as send:
            result = OpenAIProvider("test", "secret", api_format="chat").complete("", [], [], on_event=observed.append)
        self.assertEqual(result["content"], [{"type": "text", "text": "你好世界"}])
        self.assertEqual(result["_raw"]["usage"], {"total_tokens": 9})
        self.assertTrue(send.call_args.args[1]["stream"])
        self.assertEqual(send.call_args.args[1]["stream_options"], {"include_usage": True})
        self.assertEqual(closed, [True])

    def test_indexed_tools_text_and_thinking_preserve_wire_order(self):
        events = [
            chunk({"reasoning_content": "PRIVATE_REASONING", "content": "first"}),
            chunk(tool(1, id="call_", type="function", function={"name": "sub", "arguments": '{"task":'})),
            chunk({"content": "middle", **tool(0, id="a", type="function", function={"name": "sh", "arguments": '{"command":'})}),
            chunk({**tool(1, id="b", function={"name": "agent", "arguments": '"PRIVATE_TASK"}'}), "content": "after"}),
            chunk(tool(0, function={"name": "ell", "arguments": '"pwd"}'}), "tool_calls"),
            {"id": "stream-id", "choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 8}},
            None,
        ]
        original = copy.deepcopy(events)
        observed = []
        result = self.complete(events, observed.append)
        self.assertEqual([event["type"] for event in observed], [
            "thinking_delta", "text_delta", "text_delta", "tool_call_delta", "text_delta", "tool_call_delta",
            "tool_call_delta", "text_delta", "tool_call_delta",
        ])
        self.assertEqual(result["tool_calls"], [
            {"id": "a", "name": "shell", "arguments": '{"command":"pwd"}'},
            {"id": "call_b", "name": "subagent", "arguments": '{"task":"PRIVATE_TASK"}'},
        ])
        self.assertEqual(result["_raw"]["stream_events"], original[:-1])
        self.assertEqual(events, original)
        self.assertEqual(observed[0], {"type": "thinking_delta", "text": "PRIVATE_REASONING"})
        self.assertEqual(result["reasoning_content"], "PRIVATE_REASONING")
        self.assertEqual(result["content"], [{"type": "thinking", "thinking": "PRIVATE_REASONING"},
                                             {"type": "text", "text": "firstmiddleafter"}])

    def test_reasoning_roundtrips_through_tools_and_later_user_turns(self):
        reasoning = "  private\n思考 remains unchanged  "
        call = {"id": "a", "type": "function", "function": {
            "name": "shell", "arguments": '{"command":"pwd"}',
        }}
        sequences = [
            [chunk({"reasoning_content": "  private\n"}),
             chunk({"reasoning_content": "思考 remains unchanged  "}),
             chunk({"reasoning_content": None, **tool(**call)}, "tool_calls"), None],
            [chunk({"reasoning_content": "private final reasoning"}),
             chunk({"content": "done"}, "stop"), None],
            [chunk({"content": "next answer"}, "stop"), None],
        ]
        responses = [io.BytesIO(b"".join(
            b"data: " + (b"[DONE]" if event is None else json.dumps(event).encode()) + b"\n\n"
            for event in sequence
        )) for sequence in sequences]
        observed = []
        provider = OpenAIProvider("test", "secret", api_format="chat")
        tools = [{"name": "shell", "description": "Run a command", "parameters": {"type": "object"}}]
        with TemporaryDirectory() as directory, \
                patch("skills_agent.providers.base.request.urlopen", side_effect=responses) as send:
            session = Session(Path(directory), "reasoning")
            session.append({"role": "user", "content": "first task"})
            first = provider.complete("", session.messages(), tools, on_event=observed.append)
            self.assertEqual(first["reasoning_content"], reasoning)
            self.assertEqual(first["_raw"]["choices"][0]["message"]["reasoning_content"], reasoning)
            session.append(first)
            session.append({"role": "tool", "tool_call_id": "a", "content": "/tmp"})
            final = provider.complete("", session.messages(), tools, on_event=observed.append)
            session.append(final)
            session.append({"role": "user", "content": "second task"})
            restored = Session(Path(directory), "reasoning")
            provider.complete("", restored.messages(), tools, on_event=observed.append)
        second_request = json.loads(send.call_args_list[1].args[0].data)
        third_request = json.loads(send.call_args_list[2].args[0].data)
        self.assertEqual(second_request["messages"][1], {
            "role": "assistant", "content": None, "reasoning_content": reasoning,
            "tool_calls": [call],
        })
        self.assertEqual(third_request["messages"][1], second_request["messages"][1])
        self.assertEqual(third_request["messages"][3], {
            "role": "assistant", "content": [{"type": "text", "text": "done"}],
            "reasoning_content": "private final reasoning",
        })
        self.assertTrue(any(event["type"] == "thinking_delta" for event in observed))
        self.assertNotIn("_raw", json.dumps(third_request))
        self.assertTrue(all(response.closed for response in responses))

    def test_empty_reasoning_is_preserved_but_null_or_missing_is_not_invented(self):
        for delta in [{"reasoning_content": ""}, {"reasoning_content": None}, {}]:
            with self.subTest(delta=delta):
                result = self.complete([chunk({**delta, "content": "done"}, "stop"), None])
                with patch("skills_agent.providers.openai.post_stream", return_value=(event for event in [
                    chunk({"content": "next"}, "stop"), None,
                ])) as send:
                    OpenAIProvider("test", "secret", api_format="chat").complete("", [result], [])
                sent = send.call_args.args[1]["messages"][0]
                if delta.get("reasoning_content") == "":
                    self.assertEqual(result["reasoning_content"], "")
                    self.assertEqual(sent["reasoning_content"], "")
                else:
                    self.assertNotIn("reasoning_content", result)
                    self.assertNotIn("reasoning_content", sent)

    def test_invalid_reasoning_delta_is_rejected_and_retained_in_raw_log(self):
        for value in [7, False, [], {}]:
            first = chunk({"content": "received"})
            invalid = chunk({"reasoning_content": value})
            with self.subTest(value=value), self.assertRaisesRegex(ProviderError, "reasoning_content") as caught:
                self.complete([first, invalid, chunk(finish="stop"), None])
            self.assertEqual(caught.exception.raw["stream_events"], [first, invalid])
            self.assertEqual(caught.exception.partial_content, [{"type": "text", "text": "received"}])

    def test_invalid_saved_reasoning_is_rejected_before_request(self):
        for value in [None, 7, False, [], {}]:
            with self.subTest(value=value), patch("skills_agent.providers.openai.post_stream") as send:
                with self.assertRaisesRegex(ProviderError, "reasoning_content"):
                    OpenAIProvider("test", "secret", api_format="chat").complete("", [{
                        "role": "assistant", "content": "done", "reasoning_content": value,
                    }], [])
                send.assert_not_called()

    def test_refusal_streams_and_preserves_received_order(self):
        observed = []
        result = self.complete([chunk({"refusal": "Cannot ", "content": "explain"}),
                                chunk({"refusal": "do that"}, "stop"), None], observed.append)
        self.assertEqual([event["text"] for event in observed], ["Cannot ", "\n", "explain", "\n", "do that"])
        self.assertEqual(result["content"], [{"type": "text", "text": value}
                                             for value in ["Cannot ", "explain", "do that"]])

    def test_content_block_stream_matches_normalized_text_separators(self):
        observed = []
        result = self.complete([
            chunk({"content": [{"type": "text", "text": "first"},
                               {"type": "refusal", "refusal": "second"}]}),
            chunk({"content": [{"type": "text", "text": "third"}], "refusal": "last"}, "stop"),
            None,
        ], observed.append)
        streamed = "".join(event["text"] for event in observed)
        self.assertEqual(streamed, "first\nsecond\nthird\nlast")
        self.assertEqual(streamed, "\n".join(block["text"] for block in result["content"]))

    def test_incomplete_and_failed_streams_never_return_tools(self):
        first = chunk({"content": "received", **tool(0, id="a", type="function",
                                                     function={"name": "shell", "arguments": "{}"})})
        cases = [
            ([first, chunk(finish="tool_calls")], "before \\[DONE\\]"),
            ([first, None], "stopped: None"),
            ([first, chunk(finish="length"), None], "stopped: length"),
            ([first, {"error": {"message": "private provider details"}}], "API returned an error"),
            ([first, chunk(finish="stop"), None], "do not match"),
        ]
        for events, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ProviderError, message) as caught:
                self.complete(events)
            self.assertEqual(caught.exception.partial_content, [{"type": "text", "text": "received"}])
            self.assertEqual(caught.exception.raw["stream_events"], [event for event in events if event is not None])

    def test_invalid_tool_fragments_are_rejected(self):
        cases = [
            tool(-1, id="a", type="function", function={"name": "shell", "arguments": "{}"}),
            tool(2, id="a", type="function", function={"name": "shell", "arguments": "{}"}),
            tool(0, id="a", type="function", function={"name": "shell", "arguments": "{"}),
            tool(0, id="a", type="function", function={"name": "shell", "arguments": "[]"}),
            tool(0, id="a", type="function", function={"name": "", "arguments": "{}"}),
            tool(0, id="a", type="function", function={"name": "shell", "arguments": {}}),
        ]
        for delta in cases:
            with self.subTest(delta=delta), self.assertRaises(ProviderError):
                self.complete([chunk(delta, "tool_calls"), None])

    def test_cancel_retains_partial_raw_and_closes_http_response(self):
        observed = []
        first = chunk({"content": "received"})

        class InterruptedResponse(io.BytesIO):
            def __next__(self):
                try:
                    return super().__next__()
                except StopIteration:
                    raise KeyboardInterrupt

        response = InterruptedResponse(b"data: " + json.dumps(first).encode() + b"\n\n")
        with patch("skills_agent.providers.base.request.urlopen", return_value=response), self.assertRaises(KeyboardInterrupt) as caught:
            OpenAIProvider("test", "secret", api_format="chat").complete("", [], [], on_event=observed.append)
        self.assertTrue(response.closed)
        self.assertEqual(caught.exception.raw["stream_events"], [first])
        self.assertEqual(caught.exception.partial_content, [{"type": "text", "text": "received"}])
        self.assertEqual(observed, [{"type": "text_delta", "text": "received"}])


class SSETransportTests(unittest.TestCase):
    def test_comments_event_fields_multiline_json_and_crlf(self):
        wire = ': ping\r\nevent: chunk\r\nid: 4\r\ndata: {"text":\r\ndata: "中文"}\r\n\r\ndata: [DONE]\r\n\r\n'
        response = io.BytesIO(wire.encode())
        with patch("skills_agent.providers.base.request.urlopen", return_value=response) as send:
            self.assertEqual(list(post_stream("https://example.com", {}, {})), [{"text": "中文"}, None])
        self.assertTrue(response.closed)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0].get_header("Accept"), "text/event-stream")

    def test_malformed_json_and_nonobject_frames_close_response(self):
        for payload in [b"{", b"[]", b"\xff"]:
            response = io.BytesIO(b"data: " + payload + b"\n\n")
            with self.subTest(payload=payload), patch("skills_agent.providers.base.request.urlopen", return_value=response), self.assertRaises(ProviderError):
                list(post_stream("https://example.com", {}, {}))
            self.assertTrue(response.closed)

    def test_midstream_connection_failure_is_sanitized_and_keeps_received_text(self):
        class BrokenResponse(io.BytesIO):
            def __next__(self):
                try:
                    return super().__next__()
                except StopIteration:
                    raise OSError("secret transport details")

        first = chunk({"content": "partial"})
        response = BrokenResponse(b"data: " + json.dumps(first).encode() + b"\n\n")
        with patch("skills_agent.providers.base.request.urlopen", return_value=response), self.assertRaises(ProviderError) as caught:
            OpenAIProvider("test", "secret", api_format="chat").complete("", [], [])
        self.assertEqual(str(caught.exception), "API connection failed")
        self.assertEqual(caught.exception.raw["stream_events"], [first])
        self.assertEqual(caught.exception.partial_content, [{"type": "text", "text": "partial"}])
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
