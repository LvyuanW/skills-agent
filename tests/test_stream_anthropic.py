import copy
import json
import unittest
from unittest.mock import patch

from skills_agent.providers import AnthropicProvider, ProviderError


def start():
    return {"type": "message_start", "message": {
        "id": "msg_stream", "type": "message", "role": "assistant", "model": "test",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 5},
    }}


def block_start(index, block):
    return {"type": "content_block_start", "index": index, "content_block": block}


def delta(index, kind, field, value):
    return {"type": "content_block_delta", "index": index,
            "delta": {"type": kind, field: value}}


def block_stop(index):
    return {"type": "content_block_stop", "index": index}


def finish(reason="end_turn"):
    return [{"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None},
             "usage": {"output_tokens": 8}}, {"type": "message_stop"}]


def text_events():
    return [start(), block_start(0, {"type": "text", "text": ""}),
            delta(0, "text_delta", "text", "First"),
            delta(0, "text_delta", "text", " answer"), block_stop(0)]


class AnthropicStreamTests(unittest.TestCase):
    def complete(self, events, on_event=None):
        with patch("skills_agent.providers.anthropic.post_stream",
                   return_value=(event for event in events)):
            return AnthropicProvider("test", "secret").complete("", [], [], on_event=on_event)

    def test_text_arrives_before_stream_finishes(self):
        shown = []
        closed = []

        def stream():
            try:
                yield from text_events()
                self.assertEqual(shown, [{"type": "text_delta", "text": "First"},
                                         {"type": "text_delta", "text": " answer"}])
                yield from finish()
                self.fail("Provider must stop reading after message_stop")
            finally:
                closed.append(True)

        with patch("skills_agent.providers.anthropic.post_stream", return_value=stream()) as send:
            result = AnthropicProvider("test", "secret").complete("sys", [], [], on_event=shown.append)
        self.assertTrue(send.call_args.args[1]["stream"])
        self.assertEqual(result["content"], [{"type": "text", "text": "First answer"}])
        self.assertEqual(result["_raw"]["usage"], {
            "input_tokens": 10, "output_tokens": 8, "cache_read_input_tokens": 5,
        })
        self.assertEqual(closed, [True])

    def test_readable_thinking_streams_and_signatures_stay_raw(self):
        events = [start(), {"type": "ping"}, {"type": "future_event", "value": "raw only"},
                  block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
                  delta(0, "thinking_delta", "thinking", "PRIVATE_REASONING"),
                  delta(0, "signature_delta", "signature", "PRIVATE_SIGNATURE"), block_stop(0),
                  block_start(1, {"type": "text", "text": "I will "}),
                  delta(1, "text_delta", "text", "inspect"), block_stop(1),
                  block_start(2, {"type": "tool_use", "id": "call_1", "name": "shell", "input": {}}),
                  delta(2, "input_json_delta", "partial_json", '{"command": "echo '),
                  delta(2, "input_json_delta", "partial_json", 'PRIVATE_ARGUMENT"}'), block_stop(2),
                  block_start(3, {"type": "text", "text": "next"}), block_stop(3),
                  *finish("tool_use")]
        original = copy.deepcopy(events)
        shown = []
        result = self.complete(events, shown.append)
        self.assertEqual("".join(event["text"] for event in shown if event["type"] == "text_delta"),
                         "I will inspect\nnext")
        self.assertEqual(shown, [
            {"type": "thinking_delta", "text": "PRIVATE_REASONING"},
            {"type": "text_delta", "text": "I will "},
            {"type": "text_delta", "text": "inspect"},
            {"type": "tool_call_delta", "index": 2, "id": "call_1", "name": "shell"},
            {"type": "tool_call_delta", "index": 2, "arguments": '{"command": "echo '},
            {"type": "tool_call_delta", "index": 2, "arguments": 'PRIVATE_ARGUMENT"}'},
            {"type": "text_delta", "text": "\n"},
            {"type": "text_delta", "text": "next"},
        ])
        self.assertEqual(result["content"], [
            {"type": "thinking", "thinking": "PRIVATE_REASONING", "signature": "PRIVATE_SIGNATURE"},
            {"type": "text", "text": "I will inspect"}, {"type": "text", "text": "next"},
        ])
        self.assertEqual(json.loads(result["tool_calls"][0]["arguments"]), {
            "command": "echo PRIVATE_ARGUMENT",
        })
        self.assertEqual(result["_raw"]["content"][0], {
            "type": "thinking", "thinking": "PRIVATE_REASONING", "signature": "PRIVATE_SIGNATURE",
        })
        self.assertEqual(result["_raw"]["stream_events"], original)
        self.assertEqual(events, original)

    def test_redacted_thinking_is_preserved_without_display(self):
        events = [start(), block_start(0, {"type": "redacted_thinking", "data": "hidden"}),
                  block_stop(0), block_start(1, {"type": "text", "text": "visible"}),
                  block_stop(1), *finish()]
        shown = []
        result = self.complete(events, shown.append)
        self.assertEqual(shown, [{"type": "text_delta", "text": "visible"}])
        self.assertEqual(result["content"], [{"type": "redacted_thinking", "data": "hidden"},
                                              {"type": "text", "text": "visible"}])
        self.assertEqual(result["_raw"]["content"][0]["data"], "hidden")

    def test_eof_and_failed_finish_keep_received_body(self):
        for events, message in [(text_events(), "before message_stop"),
                                (text_events() + finish("max_tokens"), "max_tokens"),
                                (text_events() + [{"type": "error", "error": {
                                    "type": "overloaded_error", "message": "busy"}}], "returned an error")]:
            with self.subTest(message=message), self.assertRaises(ProviderError) as raised:
                self.complete(events)
            self.assertIn(message, str(raised.exception))
            self.assertEqual(raised.exception.partial_content, [{"type": "text", "text": "First answer"}])
            self.assertEqual(raised.exception.raw["stream_events"], events)

    def test_incomplete_or_invalid_tools_never_return_calls(self):
        tool = block_start(1, {"type": "tool_use", "id": "call_1", "name": "shell", "input": {}})
        prefixes = text_events()
        cases = [
            [*prefixes, tool, delta(1, "input_json_delta", "partial_json", '{"command":'), block_stop(1)],
            [*prefixes, tool, delta(1, "input_json_delta", "partial_json", '[]'), block_stop(1)],
            [*prefixes, tool, delta(1, "input_json_delta", "partial_json", '{}'), *finish("tool_use")],
            [*prefixes, tool, block_stop(1)],
            [*prefixes, tool, block_stop(1), *finish("end_turn")],
        ]
        for events in cases:
            with self.subTest(events=events), self.assertRaises(ProviderError) as raised:
                self.complete(events)
            self.assertEqual(raised.exception.partial_content, [{"type": "text", "text": "First answer"}])
            self.assertNotIn("tool_calls", raised.exception.raw)

    def test_keyboard_interrupt_and_transport_failure_preserve_body_and_close(self):
        for failure in [KeyboardInterrupt(), ProviderError("API connection failed")]:
            closed = []

            def stream():
                try:
                    yield from text_events()
                    raise failure
                finally:
                    closed.append(True)

            with self.subTest(failure=type(failure).__name__), patch(
                "skills_agent.providers.anthropic.post_stream", return_value=stream(),
            ), self.assertRaises(type(failure)) as raised:
                AnthropicProvider("test", "secret").complete("", [], [])
            self.assertEqual(raised.exception.partial_content, [{"type": "text", "text": "First answer"}])
            self.assertEqual(raised.exception.raw["stream_events"], text_events())
            self.assertEqual(closed, [True])

    def test_invalid_stream_order_and_types_raise_provider_errors(self):
        for events in [
            [{"type": "message_stop"}],
            [start(), start()],
            [start(), block_start(1, {"type": "text", "text": "wrong index"})],
            [start(), delta(0, "text_delta", "text", "without a block")],
            [start(), block_start(0, {"type": "text", "text": 42})],
            [start(), block_stop(0)],
            [start(), None],
        ]:
            with self.subTest(events=events), self.assertRaises(ProviderError):
                self.complete(events)


if __name__ == "__main__":
    unittest.main()
