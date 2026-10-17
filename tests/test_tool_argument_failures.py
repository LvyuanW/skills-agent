"""Malformed model inputs are rejected tool calls, not failed model requests."""

from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from skills_agent.agent import Agent
from skills_agent.cli import ConsoleRenderer
from skills_agent.context import interrupted_result
from skills_agent.context.history import HistoryIndex
from skills_agent.providers import AnthropicProvider, OpenAIProvider, ProviderConnectionError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry
from tests.test_provider_counting import responses_stream
from tests.test_providers import anthropic_response, openai_response, response_events


DEEP_ARGUMENTS = "[" * 10000 + "0" + "]" * 10000


def call(identifier, arguments):
    return {"id": identifier, "name": "shell", "arguments": arguments}


def events_for(api, calls=()):
    if api == "chat":
        native = [{"id": item["id"], "type": "function", "function": {
            "name": item["name"], "arguments": item["arguments"],
        }} for item in calls]
        return list(response_events(openai_response(
            content="" if calls else "Done", calls=native, reason="tool_calls" if calls else "stop")))
    if api == "responses":
        output = [{"type": "function_call", "call_id": item["id"], "name": item["name"],
                   "arguments": item["arguments"]} for item in calls] if calls else [{
                       "type": "message", "content": [{"type": "output_text", "text": "Done"}],
                   }]
        return list(responses_stream(output))
    if not calls:
        return list(response_events(anthropic_response()))
    events = [{"type": "message_start", "message": {
        "role": "assistant", "content": [], "stop_reason": None,
    }}]
    for index, item in enumerate(calls):
        events.extend([
            {"type": "content_block_start", "index": index, "content_block": {
                "type": "tool_use", "id": item["id"], "name": item["name"], "input": {},
            }},
            {"type": "content_block_delta", "index": index, "delta": {
                "type": "input_json_delta", "partial_json": item["arguments"],
            }},
            {"type": "content_block_stop", "index": index},
        ])
    events.extend([{"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
                   {"type": "message_stop"}])
    return events


def provider_for(api):
    return (AnthropicProvider("model", "key") if api == "anthropic" else
            OpenAIProvider("model", "key", api_format=api))


class ToolArgumentFailureTests(unittest.TestCase):
    def test_complete_providers_preserve_invalid_json_and_nonobject_arguments(self):
        arguments = ('{"command":"touch never"}<｜DSML｜tool_calls>', "{", "[]", '"text"', "null",
                     DEEP_ARGUMENTS)
        for api in ("chat", "responses", "anthropic"):
            module = "openai" if api == "chat" else api
            for value in arguments:
                with self.subTest(api=api, arguments=value), patch(
                    f"skills_agent.providers.{module}.post_stream",
                    return_value=(event for event in events_for(api, [call("bad", value)])),
                ) as send:
                    reply = provider_for(api).complete("", [], [])
                    self.assertEqual(reply["tool_calls"], [call("bad", value)])
                    send.assert_called_once()

    def test_invalid_arguments_never_dispatch_a_tool(self):
        with TemporaryDirectory() as directory, patch("skills_agent.tools.run_command") as execute:
            tools = ToolRegistry(Path(directory))
            for value, error in (("{", "valid JSON"), ("[]", "JSON object"),
                                 ('"text"', "JSON object"), ("null", "JSON object"),
                                 (DEEP_ARGUMENTS, "valid JSON")):
                with self.subTest(arguments=value):
                    result = json.loads(tools.execute("shell", value))
                    self.assertIs(result["executed"], False)
                    self.assertIn(error, result["error"])
                    self.assertIn("call the tool again", result["error"])
                    self.assertEqual(interrupted_result(call("bad", value))["tool_call_id"], "bad")
            execute.assert_not_called()

    def test_agent_returns_same_id_error_and_executes_valid_sibling_once(self):
        good = json.dumps({"name": "Inspect", "command": "printf evidence", "run_in_background": False})
        for api in ("chat", "responses", "anthropic"):
            for value in ('{"command":"touch never"}<｜DSML｜tool_calls>', DEEP_ARGUMENTS,
                          "[" * 600 + "0" + "]" * 600):
                module = "openai" if api == "chat" else api
                calls = [call("bad", value), call("good", good)]
                with self.subTest(api=api, argument_length=len(value)), TemporaryDirectory() as directory:
                    root = Path(directory)
                    session = Session(root, "invalid-arguments")
                    model = provider_for(api)
                    model.count_tokens = Mock(return_value=128)
                    model.count_text = Mock(return_value=64)
                    agent = Agent(model, session, SkillRegistry([]), root, allow_subagent=False,
                                  on_event=ConsoleRenderer(io.StringIO()))
                    streams = [(event for event in events_for(api, calls)),
                               (event for event in events_for(api))]
                    try:
                        with patch(f"skills_agent.providers.{module}.post_stream", side_effect=streams) as send, \
                                patch("skills_agent.tools.run_command", return_value={
                                    "exit_code": 0, "stdout": "evidence", "stderr": "",
                                }) as execute, patch("skills_agent.providers.retry.time.sleep") as sleep:
                            self.assertEqual(agent.run({"role": "user", "content": "Inspect"}).lower(), "done")
                        execute.assert_called_once()
                        self.assertEqual(execute.call_args.args[0], "printf evidence")
                        self.assertEqual(send.call_count, 2)
                        sleep.assert_not_called()
                        results = [message for message in session.messages() if message["role"] == "tool"]
                        self.assertEqual([message["tool_call_id"] for message in results], ["bad", "good"])
                        self.assertIs(json.loads(results[0]["content"])["executed"], False)
                        self.assertEqual(json.loads(results[1]["content"])["stdout"], "evidence")
                        self.assertFalse(any(event["type"] == "retry" for event in session.events()))
                        self.assertGreaterEqual(HistoryIndex(session.path).sync(), 5)
                        next_request = send.call_args.args[1]
                        self.assertIn("Tool arguments", json.dumps(next_request))
                        if api == "anthropic":
                            native_calls = [block for message in next_request["messages"]
                                            for block in message["content"] if block["type"] == "tool_use"]
                            native_results = [block for message in next_request["messages"]
                                              for block in message["content"] if block["type"] == "tool_result"]
                            self.assertEqual([block["id"] for block in native_calls], ["good"])
                            self.assertEqual([block["tool_use_id"] for block in native_results], ["good"])
                    finally:
                        agent.close()

    def test_anthropic_projection_preserves_history_reasoning_and_valid_native_calls(self):
        bad, good = call("bad", "[]"), call("good", '{"command":"pwd"}')
        thinking = {"type": "thinking", "thinking": "reasoning", "signature": "opaque"}
        native = [thinking, {"type": "tool_use", "id": "bad", "name": "shell", "input": []},
                  {"type": "tool_use", "id": "good", "name": "shell", "input": {"command": "pwd"}}]
        assistant = {"role": "assistant", "content": [thinking], "tool_calls": [bad, good],
                     "_anthropic_content": native}
        messages = [assistant, {"role": "tool", "tool_call_id": "bad", "content":
                               '{"executed":false,"error":"Tool arguments must be a JSON object"}'},
                    {"role": "tool", "tool_call_id": "good", "content": "/tmp"}]
        original = deepcopy(messages)
        provider = AnthropicProvider("model", "key")
        payload = provider._payload("", messages, [])
        self.assertEqual(messages, original)
        self.assertEqual(payload["messages"][0]["content"][0], thinking)
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "text")
        self.assertEqual(payload["messages"][0]["content"][2], native[2])
        self.assertEqual(payload["messages"][1]["content"][0]["type"], "tool_result")
        self.assertIn("was not executed", payload["messages"][1]["content"][1]["text"])
        with patch("skills_agent.providers.anthropic.post_json", return_value={"input_tokens": 42}) as count:
            self.assertEqual(provider.count_tokens("", messages, []), 42)
        self.assertEqual(count.call_args.args[1], payload)

    def test_anthropic_truncated_invalid_call_is_still_a_connection_failure(self):
        events = events_for("anthropic", [call("bad", "{")])[:-1]
        with patch("skills_agent.providers.anthropic.post_stream", return_value=(event for event in events)), \
                self.assertRaises(ProviderConnectionError):
            AnthropicProvider("model", "key").complete("", [], [])

    def test_deepseek_recipe_can_render_rejected_argument_history(self):
        try:
            import deepseek_recipe as recipe
        except ImportError:
            self.skipTest("DeepSeek tokenizer extra is not installed")
        for value in ("{", '{"command":"pwd"}<｜DSML｜tool_calls>', "[]"):
            messages = [{"role": "assistant", "content": None, "tool_calls": [{
                "id": "bad", "type": "function", "function": {"name": "shell", "arguments": value},
            }]}, {"role": "tool", "tool_call_id": "bad", "content":
                   '{"executed":false,"error":"Invalid arguments"}'}]
            converted = recipe.ChatCompletionRequest({"model": "deepseek-flash", "messages": messages}).convert(
                recipe.ConversionOptions())
            for encoding in (recipe.DeepseekV4Encoding(), recipe.DeepseekV41Encoding()):
                with self.subTest(arguments=value, encoding=type(encoding).__name__):
                    encoding.render_conversation(converted.conversation)


if __name__ == "__main__":
    unittest.main()
