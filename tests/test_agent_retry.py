"""Transient API failures preserve the current turn and its completed tools."""

from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

from skills_agent.agent import Agent
from skills_agent.cli import ConsoleRenderer
from skills_agent.providers.base import ProviderConnectionError, ProviderError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry


def answer(text):
    return {"role": "assistant", "content": text}


def unavailable():
    return ProviderError("API HTTP error 503", diagnostics={
        "kind": "http", "response": {"status": 503},
    })


def shell_call():
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": "completed-shell", "name": "shell",
        "arguments": json.dumps({"name": "Inspect evidence", "command": "ignored"}),
    }]}


class ScriptedModel:
    model_context_window = None
    max_output_tokens = 32

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.count_tokens = Mock(return_value=128)
        self.count_text = Mock(return_value=64)

    def complete(self, system, messages, tools, *, on_event=None, **options):
        self.calls.append(deepcopy((system, messages, tools, options)))
        try:
            response = next(self.replies)
        except StopIteration:
            raise AssertionError("Unexpected extra model attempt") from None
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(messages, on_event)
        return response


class AgentRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = Session(self.root, "main")
        sleeping = patch("skills_agent.providers.retry.time.sleep")
        self.sleep = sleeping.start()
        self.addCleanup(sleeping.stop)

    def agent(self, model, **options):
        agent = Agent(model, self.session, SkillRegistry([]), self.root, **options)
        self.addCleanup(agent.close)
        return agent

    def test_main_retries_beyond_three_attempts_without_adopting_stream_fragments(self):
        def incomplete(messages, on_event):
            on_event({"type": "text_delta", "text": "FAILED_STREAM_PREFIX"})
            on_event({"type": "tool_call_delta", "index": 0, "name": "shell",
                      "arguments": '{"name":"UNFINISHED_TOOL",'})
            # A provider attempt cannot mutate the input retained for retry.
            messages.append(answer("MUTATED_ATTEMPT_INPUT"))
            raise ProviderConnectionError("Stream ended", raw={"partial": "RAW_ATTEMPT"},
                                          partial_content=[{"type": "text", "text": "FAILED_STREAM_PREFIX"}])

        model = ScriptedModel([incomplete, unavailable(), ProviderConnectionError("reset"),
                               unavailable(), answer("COMPLETE_ANSWER")])
        output = io.StringIO()
        agent = self.agent(model, on_event=ConsoleRenderer(output))
        with patch.object(agent.tools, "execute") as execute:
            self.assertEqual(agent.run({"role": "user", "content": "Continue"}), "COMPLETE_ANSWER")
        execute.assert_not_called()
        self.assertEqual(len(model.calls), 5)
        self.assertTrue(all(request == model.calls[0] for request in model.calls))
        self.assertEqual(self.sleep.call_args_list, [call(5)] * 4)
        self.assertEqual(self.session.messages(), [
            {"role": "user", "content": "Continue"}, answer("COMPLETE_ANSWER"),
        ])
        self.assertNotIn("FAILED_STREAM_PREFIX", json.dumps(agent.context.records))
        self.assertNotIn("MUTATED_ATTEMPT_INPUT", json.dumps(agent.context.records))
        self.assertEqual(output.getvalue().count("COMPLETE_ANSWER"), 1)
        retries = [event for event in self.session.events() if event["type"] == "retry"]
        self.assertEqual(len(retries), 4)
        self.assertEqual(retries[0]["partial_content"], [
            {"type": "text", "text": "FAILED_STREAM_PREFIX"},
        ])

    def test_retry_after_completed_shell_does_not_reexecute_the_tool(self):
        gateway_timeout = ProviderError("[Deadline Exceeded]", diagnostics={
            "kind": "stream", "response": {"status": 200, "body": {"error": {
                "code": "GatewayTimeout", "type": "GatewayTimeout",
            }}},
        })
        model = ScriptedModel([shell_call(), gateway_timeout, *[unavailable() for _ in range(3)], answer("Done")])
        agent = self.agent(model)
        result = json.dumps({"exit_code": 0, "stdout": "SAVED_TOOL_RESULT", "stderr": ""})
        with patch.object(agent.tools, "execute", return_value=result) as execute:
            self.assertEqual(agent.run({"role": "user", "content": "Inspect"}), "Done")
        execute.assert_called_once_with("shell", shell_call()["tool_calls"][0]["arguments"])
        self.assertEqual(len(model.calls), 6)
        self.assertTrue(all(request == model.calls[1] for request in model.calls[1:]))
        self.assertEqual([message["role"] for message in model.calls[1][1]], ["user", "assistant", "tool"])
        self.assertIn("SAVED_TOOL_RESULT", model.calls[1][1][-1]["content"])
        self.assertEqual(len([m for m in self.session.messages() if m["role"] == "tool"]), 1)
        self.assertEqual(self.sleep.call_args_list, [call(5)] * 4)

    def test_working_context_retries_remote_token_count_before_generation(self):
        model = ScriptedModel([answer("Counted")])
        model.count_tokens.side_effect = [*[unavailable() for _ in range(4)], 128]
        agent = self.agent(model)
        self.assertEqual(agent.run({"role": "user", "content": "Count first"}), "Counted")
        self.assertEqual(model.count_tokens.call_count, 5)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.sleep.call_args_list, [call(5)] * 4)

    def test_tool_result_selection_retries_remote_text_count_without_reexecuting_shell(self):
        model = ScriptedModel([shell_call(), answer("Selected")])
        model.count_text.side_effect = [*[unavailable() for _ in range(4)], 64]
        agent = self.agent(model)
        result = json.dumps({"exit_code": 0, "stdout": "EVIDENCE", "stderr": ""})
        with patch.object(agent.tools, "execute", return_value=result) as execute:
            self.assertEqual(agent.run({"role": "user", "content": "Inspect"}), "Selected")
        execute.assert_called_once()
        self.assertEqual(model.count_text.call_count, 5)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len([m for m in self.session.messages() if m["role"] == "tool"]), 1)
        self.assertEqual(self.sleep.call_args_list, [call(5)] * 4)

    def test_cancellation_during_retry_wait_propagates_without_another_attempt(self):
        for interruption in (KeyboardInterrupt(), SystemExit(130)):
            with self.subTest(interruption=type(interruption).__name__):
                model = ScriptedModel([unavailable(), answer("Must not run")])
                self.sleep.side_effect = interruption
                agent = self.agent(model)
                with self.assertRaises(type(interruption)) as caught:
                    agent.run({"role": "user", "content": "Stop while waiting"})
                self.assertIs(caught.exception, interruption)
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(self.session.events()[-1]["message"], "Interrupted")
                self.sleep.side_effect = None

    def test_retry_event_resets_renderer_attempt_state(self):
        output = io.StringIO()
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "FAILED_PREFIX"})
        renderer({"type": "tool_call_delta", "index": 0, "name": "shell",
                  "arguments": '{"name":"Old attempt",'})
        renderer({"type": "retry", "operation": "complete", "attempt": 1,
                  "retry_in_seconds": 5, "category": "connection", "message": "API connection failed"})
        self.assertFalse(renderer._had_deltas)
        self.assertFalse(renderer._streaming)
        self.assertEqual(renderer._calls, {})
        renderer({"type": "tool_call_delta", "index": 0, "name": "shell",
                  "arguments": '{"name":"Fresh attempt","command":"true"}'})
        self.assertEqual(renderer._calls[0]["name"], "shell")
        self.assertEqual(json.loads(renderer._calls[0]["arguments"])["name"], "Fresh attempt")
        self.assertIn("Fresh attempt", output.getvalue())


if __name__ == "__main__":
    unittest.main()
