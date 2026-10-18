"""Only model-reported usage determines usage-based context compression."""

import base64
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from skills_agent.agent import Agent
from skills_agent.context import WorkingContext, working_message
from skills_agent.context.auxiliary import Auxiliary
from skills_agent.processes import CALL_ENV, CONTEXT_ENV, SCOPE_ENV
from skills_agent.providers import OpenAIProvider
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_stream_openai import chunk, tool


TOOLS = [{"name": "shell", "description": "Execute a script", "parameters": {
    "type": "object", "properties": {"command": {"type": "string"}},
    "required": ["command"],
}}]
_MISSING = object()


def text_output(text="done"):
    return [{"type": "message", "id": "msg", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": text, "annotations": []}]}]


def completed_events(output, usage=_MISSING):
    response = {"id": "resp", "status": "completed", "output": deepcopy(output)}
    if usage is not _MISSING:
        response["usage"] = deepcopy(usage)
    yield {"type": "response.completed", "response": response}


class UsageCountingTests(unittest.TestCase):
    def provider(self, **options):
        return OpenAIProvider("openai/gpt-6-luna", "test-key", "https://openrouter.ai/api/v1",
                              api_format="responses", max_output_tokens=options.pop("max_output_tokens", 1024),
                              **options)

    def complete(self, provider, system, messages, tools=(), *, output=None, usage=_MISSING):
        with patch("skills_agent.providers.responses.post_stream",
                   return_value=completed_events(text_output() if output is None else output, usage)):
            return provider.complete(system, deepcopy(messages), list(tools))

    def test_openrouter_responses_auto_selects_usage_counter(self):
        self.assertEqual(self.provider().token_counter, "usage")
        self.assertEqual(self.provider(token_counter="usage").token_counter, "usage")

    def test_unknown_counts_neither_estimate_content_nor_call_count_endpoint(self):
        with patch("skills_agent.providers.openai.post_json") as count, patch(
            "skills_agent.providers.base.request.urlopen"
        ) as network:
            provider = self.provider(token_counter="usage")
            messages = [{"role": "user", "content": "hello" * 100000},
                        {"role": "user", "content": [{"type": "image", "path": "/not-a-real-image.png",
                                                       "mime_type": "image/png"}]}]
            self.assertIsNone(provider.count_tokens("large system" * 100000, messages, TOOLS))
            for text in ("", "a", "abcd", "abcde", "你好", "😀", "x" * 100000):
                self.assertIsNone(provider.count_text(text))
        count.assert_not_called()
        network.assert_not_called()

    def test_successful_response_records_actual_input_and_output_usage_only(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "task"}]
        reply = self.complete(provider, "system", messages, TOOLS,
                              usage={"input_tokens": 1000, "output_tokens": 50,
                                     "total_tokens": 1050,
                                     "output_tokens_details": {"reasoning_tokens": 40}})
        self.assertEqual(reply["_token_usage"], {"version": 2, "model": provider.model, "tokens": 1050})
        self.assertEqual(provider.count_tokens("system", messages + [reply], TOOLS), 1050)
        extended = messages + [reply, {"role": "user", "content": "continue" * 100000}]
        self.assertEqual(provider.count_tokens("system", extended, TOOLS), 1050)

    def test_native_reasoning_output_does_not_add_to_the_reported_usage(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "task"}]
        output = [{"type": "reasoning", "id": "reason", "summary": [],
                   "encrypted_content": "opaque-not-ordinary-text" * 1000}, *text_output()]
        reply = self.complete(provider, "system", messages, output=output,
                              usage={"input_tokens": 1000, "output_tokens": 50})
        self.assertEqual(provider.count_tokens("system", messages + [reply], []), 1050)

    def test_function_call_result_leaves_reported_usage_unchanged(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "read status"}]
        output = [{"type": "function_call", "id": "fc", "call_id": "call-1",
                   "name": "shell", "arguments": '{"command":"status"}'}]
        reply = self.complete(provider, "system", messages, TOOLS, output=output,
                              usage={"input_tokens": 2000, "output_tokens": 40})
        result = {"role": "tool", "name": "shell", "tool_call_id": "call-1", "content": "ready" * 100000}
        self.assertEqual(provider.count_tokens("system", messages + [reply, result], TOOLS), 2040)

    def test_usage_survives_working_message_and_json_restart(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "task"}]
        reply = self.complete(provider, "system", messages, TOOLS,
                              usage={"input_tokens": 1000, "output_tokens": 50})
        persisted = json.loads(json.dumps(working_message(reply)))
        self.assertNotIn("_raw", persisted)
        self.assertIn("_token_usage", persisted)
        self.assertEqual(self.provider().count_tokens("system", messages + [persisted], TOOLS), 1050)

    def test_shared_provider_auxiliary_and_other_session_do_not_replace_main_snapshot(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "main task"}]
        main_reply = self.complete(provider, "main system", messages, TOOLS,
                                   usage={"input_tokens": 1000, "output_tokens": 50})
        other_messages = [{"role": "user", "content": "summarize unrelated evidence"}]
        other_reply = self.complete(provider, "private summary", other_messages,
                                    usage={"input_tokens": 10000, "output_tokens": 500})
        self.assertIsNone(provider.count_text("tool output"))
        self.assertEqual(provider.count_tokens("private summary", other_messages + [other_reply], []), 10500)
        self.assertEqual(provider.count_tokens("main system", messages + [main_reply], TOOLS), 1050)
        self.assertIsNone(provider.count_tokens("other session", other_messages, []))

    def test_header_history_and_thinking_changes_do_not_recalculate_reported_usage(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "task"}]
        reply = self.complete(provider, "system", messages, TOOLS,
                              usage={"input_tokens": 10000, "output_tokens": 50})
        conversation = messages + [reply]
        changed = [("new summary and system", conversation, TOOLS),
                   ("system", conversation, []),
                   ("system", [{"role": "user", "content": "edited task"}, reply], TOOLS),
                   ("system", [reply], TOOLS)]
        for system, current, tools in changed:
            with self.subTest(system=system, messages=current, tools=tools):
                self.assertEqual(provider.count_tokens(system, current, tools), 10050)
        self.assertEqual(provider.count_tokens("system", conversation, TOOLS, disable_thinking=True), 10050)
        other_model = OpenAIProvider("another-model", "test-key", "https://openrouter.ai/api/v1",
                                     api_format="responses", token_counter="usage")
        self.assertIsNone(other_model.count_tokens("system", conversation, TOOLS))

    def test_latest_matching_reported_usage_replaces_earlier_snapshot(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "task"}]
        first = self.complete(provider, "system", messages, usage={"input_tokens": 1000, "output_tokens": 50})
        second = self.complete(provider, "system", messages + [first],
                               usage={"input_tokens": 700, "output_tokens": 20})
        self.assertEqual(provider.count_tokens("system", messages + [first, second], []), 720)

    def test_missing_or_malformed_usage_remains_unknown_without_estimation(self):
        invalid_usage = [_MISSING, None, {}, {"input_tokens": 1},
                         {"input_tokens": True, "output_tokens": 1},
                         {"input_tokens": 1, "output_tokens": False},
                         {"input_tokens": -1, "output_tokens": 1},
                         {"input_tokens": 1, "output_tokens": -1},
                         {"input_tokens": "1000", "output_tokens": 1},
                         {"input_tokens": 1000, "output_tokens": 1.5}]
        messages = [{"role": "user", "content": "task"}]
        for usage in invalid_usage:
            with self.subTest(usage=usage):
                provider = self.provider()
                reply = self.complete(provider, "system", messages, usage=usage)
                self.assertNotIn("_token_usage", reply)
                self.assertIsNone(provider.count_tokens("system", messages + [reply], []))

    def test_zero_reported_usage_is_a_known_zero(self):
        provider = self.provider()
        reply = self.complete(provider, "system", [], usage={"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(provider.count_tokens("system", [reply], []), 0)

    def test_malformed_persisted_usage_never_becomes_a_snapshot(self):
        messages = [{"role": "user", "content": "task"}]
        for metadata in (None, True, [], "not metadata", {},
                         {"version": 1, "model": "openai/gpt-6-luna", "tokens": 1000},
                         {"version": 2, "tokens": 1000},
                         {"version": 2, "model": "openai/gpt-6-luna", "tokens": -1},
                         {"version": 2, "model": "openai/gpt-6-luna", "tokens": True},
                         {"version": 2, "model": "openai/gpt-6-luna", "tokens": 1.5},
                         {"version": 2, "model": "openai/gpt-6-luna", "tokens": "1000"}):
            with self.subTest(metadata=metadata):
                assistant = {"role": "assistant", "content": "done", "_token_usage": metadata}
                self.assertIsNone(self.provider().count_tokens("system", messages + [assistant], []))

    def test_expired_or_new_images_do_not_change_reported_usage(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            path.write_bytes(png)
            messages = [{"role": "user", "content": [{"type": "image", "path": str(path),
                                                        "mime_type": "image/png"}]}]
            provider = self.provider()
            self.assertIsNone(provider.count_tokens("system", messages, []))
            reply = self.complete(provider, "system", messages,
                                  usage={"input_tokens": 10000, "output_tokens": 50})
            path.unlink()
            self.assertEqual(provider.count_tokens("system", messages + [reply], []), 10050)
            self.assertEqual(provider.count_tokens("system", messages + [reply] + messages, []), 10050)

    def test_actual_agent_shell_report_loop_and_reopened_session_need_no_count_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session(root, "usage-loop")
            arguments = json.dumps({"name": "Write evidence", "command": "printf observed > proof.txt; printf observed",
                                    "run_in_background": False, "result_mode": "report"})
            tool_output = [{"type": "function_call", "id": "fc", "call_id": "call-loop",
                            "name": "shell", "arguments": arguments}]
            responses = [completed_events(tool_output, {"input_tokens": 6000, "output_tokens": 40}),
                         completed_events(text_output("Observed proof.txt was written."),
                                          {"input_tokens": 20000, "output_tokens": 100}),
                         completed_events(text_output("completed"), {"input_tokens": 6100, "output_tokens": 10}),
                         completed_events(text_output("continued"), {"input_tokens": 6200, "output_tokens": 10})]
            environment = {key: value for key, value in os.environ.items()
                           if key not in {SCOPE_ENV, CONTEXT_ENV, CALL_ENV}}
            with patch.dict(os.environ, environment, clear=True), patch(
                "skills_agent.providers.responses.post_stream", side_effect=responses
            ) as send, patch("skills_agent.providers.openai.post_json",
                             side_effect=AssertionError("Counting endpoint must not be called")) as count:
                first = Agent(self.provider(), session, SkillRegistry([]), root, allow_subagent=False)
                try:
                    self.assertIsNone(first.context._count("system", []))
                    self.assertEqual(first.run(session.make_user_message("write proof", [])), "completed")
                    self.assertEqual(first.context._count("system", []), 6110)
                finally:
                    first.close()
                self.assertEqual((root / "proof.txt").read_text(), "observed")
                tools = [message for message in session.messages() if message["role"] == "tool"]
                self.assertEqual(len(tools), 1)
                self.assertEqual(tools[0]["tool_call_id"], "call-loop")
                execution = json.loads(tools[0]["content"])
                self.assertEqual(execution["exit_code"], 0)
                self.assertEqual(execution["stdout"], "observed")
                persisted = json.loads((session.path / "context.json").read_text())
                assistants = [record["message"] for record in persisted["records"]
                              if record["message"]["role"] == "assistant"]
                self.assertTrue(all("_token_usage" in reply for reply in assistants))
                selected_tools = [record["message"] for record in persisted["records"]
                                  if record["message"]["role"] == "tool"]
                self.assertIn("Observed proof.txt was written.", selected_tools[0]["content"])
                reopened = Agent(self.provider(), Session(root, "usage-loop"), SkillRegistry([]), root,
                                 allow_subagent=False)
                try:
                    self.assertEqual(reopened.context._count("system", []), 6110)
                    self.assertEqual(reopened.run(session.make_user_message("continue", [])), "continued")
                    self.assertEqual(reopened.context._count("system", []), 6210)
                finally:
                    reopened.close()
                count.assert_not_called()
            self.assertEqual(send.call_count, 4)
            self.assertTrue(any(item.get("type") == "function_call_output"
                                and item.get("call_id") == "call-loop"
                                for item in send.call_args_list[2].args[1]["input"]))

    def test_7999_usage_ignores_new_user_and_large_result_then_9000_compacts_once(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Path(directory), "usage-compression")
            provider = self.provider(model_context_window=10000)
            messages = [{"role": "user", "content": "original task"},
                        *[{"role": "assistant", "content": f"old finding {index}"} for index in range(11)]]
            for message in messages:
                session.append(message)
            output = [{"type": "function_call", "id": "fc", "call_id": "call-budget",
                       "name": "shell", "arguments": '{"command":"status"}'}]
            reply = self.complete(provider, "system", messages, TOOLS, output=output,
                                  usage={"input_tokens": 7900, "output_tokens": 99})
            session.append(reply)
            session.append({"role": "tool", "name": "shell", "tool_call_id": "call-budget",
                            "content": "very large result\n" * 100000})
            # Roughly 5% of this window as ASCII; no local count may be added.
            session.append({"role": "user", "content": "x" * 2000})
            activity = []
            auxiliary = Auxiliary(provider, session, output_tokens=512, context_tokens=10000)
            context = WorkingContext(session, provider, auxiliary, max_tokens=10000,
                                     keep_calls=1, on_activity=activity.append)
            context.sync()
            with patch("skills_agent.providers.responses.post_stream") as send:
                context.prepare("system", TOOLS)
            send.assert_not_called()
            self.assertEqual(context.state["token_count"], 7999)
            self.assertEqual(context.state["last_usage"]["tokens"], 7999)
            self.assertEqual(activity, [])
            reply = self.complete(provider, "system", [record["message"] for record in context.records], TOOLS,
                                  usage={"input_tokens": 8900, "output_tokens": 100})
            session.append(reply)
            context.sync()
            self.assertEqual(context._count("system", TOOLS), 9000)
            summary = '{"summary":"Prior findings retained.","references":{}}'
            with patch("skills_agent.providers.responses.post_stream",
                       return_value=completed_events(text_output(summary),
                                                     {"input_tokens": 2000, "output_tokens": 30})) as send, patch(
                "skills_agent.providers.openai.post_json",
                side_effect=AssertionError("Counting endpoint must not be called"),
            ) as count:
                system, retained = context.prepare("system", TOOLS)
                context.prepare("system", TOOLS)
                count.assert_not_called()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(activity, [{"kind": "compression"}, None])
            self.assertEqual(context.state["summary"], summary)
            self.assertTrue(context.state["covered_event_ids"])
            self.assertIsNone(context.state["token_count"])
            self.assertIsNone(context.state["last_usage"])
            self.assertIsNone(provider.count_tokens(system, retained, TOOLS))
            self.assertFalse(any("_token_usage" in message for message in retained))
            # Restarting must not resurrect a pre-compression snapshot.
            reopened = WorkingContext(Session(Path(directory), "usage-compression"), self.provider(), auxiliary,
                                      max_tokens=10000, keep_calls=1)
            self.assertIsNone(reopened._count("system", TOOLS))

    def test_usage_threshold_is_exactly_eighty_percent_without_output_reserve(self):
        cases = [(10000, 10000, 7999, 1024, False),
                 (10000, 10000, 8000, 1024, True),
                 (10000, 10000, 6000, 5000, False),
                 (5000, 10000, 4000, 1024, True),
                 (40000000000000001, 40000000000000001, 32000000000000000, 1024, False)]
        for budget, window, tokens, output_limit, should_compact in cases:
            with self.subTest(budget=budget, window=window, tokens=tokens, output_limit=output_limit):
                with tempfile.TemporaryDirectory() as directory:
                    session = Session(Path(directory), "boundary")
                    provider = self.provider(model_context_window=window, max_output_tokens=output_limit)
                    messages = [{"role": "user", "content": "original task"},
                                {"role": "assistant", "content": "old finding"}]
                    for message in messages:
                        session.append(message)
                    reply = self.complete(provider, "system", messages,
                                          usage={"input_tokens": tokens, "output_tokens": 0})
                    session.append(reply)
                    auxiliary = Mock(output_tokens=512)
                    auxiliary.compress.return_value = "summary"
                    context = WorkingContext(session, provider, auxiliary, max_tokens=budget, keep_calls=1)
                    context.sync()
                    context.prepare("system", [])
                    self.assertEqual(auxiliary.compress.call_count, int(should_compact))


class ChatUsageCountingTests(unittest.TestCase):
    def provider(self, **options):
        return OpenAIProvider("deepseek-flash", "test-key", "https://api.deepseek.com",
                              max_output_tokens=options.pop("max_output_tokens", 1024), **options)

    def events(self, usage=_MISSING, *, arguments=None, text="done", thinking=None):
        if thinking is not None:
            yield chunk({"reasoning_content": thinking})
        if arguments is None:
            yield chunk({"content": text}, "stop")
        else:
            yield chunk(tool(id="call-1", type="function", function={
                "name": "shell", "arguments": arguments}), "tool_calls")
        if usage is not _MISSING:
            yield {"choices": [], "usage": deepcopy(usage)}
        yield None

    def complete(self, provider, messages=(), usage=_MISSING, **options):
        with patch("skills_agent.providers.openai.post_stream", return_value=self.events(usage, **options)):
            return provider.complete("system", list(messages), TOOLS)

    def test_chat_uses_total_tokens_instead_of_recomputing_or_adding_reasoning(self):
        provider = self.provider()
        reply = self.complete(provider, usage={"total_tokens": 1050, "prompt_tokens": 4000,
                                              "completion_tokens": 50,
                                              "completion_tokens_details": {"reasoning_tokens": 40}})
        self.assertEqual(reply["_token_usage"], {"version": 2, "model": provider.model, "tokens": 1050})
        self.assertEqual(provider.count_tokens("system", [reply], TOOLS), 1050)
        self.assertEqual(provider.count_tokens("changed header", [reply, {
            "role": "user", "content": "new message" * 100000}], []), 1050)

    def test_missing_and_invalid_total_tokens_do_not_fall_back_to_other_fields(self):
        invalid = [_MISSING, None, {}, {"prompt_tokens": 100, "completion_tokens": 20},
                   *[{"total_tokens": value, "prompt_tokens": 100, "completion_tokens": 20}
                     for value in (None, -1, True, False, "120", 120.0)]]
        for usage in invalid:
            with self.subTest(usage=usage):
                provider = self.provider()
                reply = self.complete(provider, usage=usage)
                self.assertNotIn("_token_usage", reply)
                self.assertIsNone(provider.count_tokens("system", [reply], TOOLS))
        reply = self.complete(self.provider(), usage={"total_tokens": 0})
        self.assertEqual(self.provider().count_tokens("system", [reply], TOOLS), 0)

    def test_later_missing_or_invalid_usage_retains_last_valid_gateway_snapshot(self):
        provider = self.provider()
        first = self.complete(provider, usage={"total_tokens": 1000})
        for usage in (_MISSING, None, {}, {"prompt_tokens": 100, "completion_tokens": 20},
                      {"total_tokens": None}, {"total_tokens": -1}, {"total_tokens": True},
                      {"total_tokens": "9000"}, {"total_tokens": 9000.0}):
            with self.subTest(usage=usage):
                latest = self.complete(provider, [first], usage=usage)
                self.assertNotIn("_token_usage", latest)
                self.assertIsNone(provider.count_tokens("system", [latest], TOOLS))
                self.assertEqual(provider.count_tokens("system", [first, latest], TOOLS), 1000)

    def test_unknown_usage_reads_neither_new_images_nor_any_count_endpoint(self):
        messages = [{"role": "user", "content": [
            {"type": "image", "path": "/not-a-real-image.png", "mime_type": "image/png"},
            {"type": "text", "text": "large content" * 100000}]}]
        with patch("skills_agent.providers.openai.post_json") as count, patch.object(Path, "read_bytes") as read:
            provider = self.provider()
            self.assertEqual(provider.token_counter, "usage")
            self.assertIsNone(provider.count_tokens("large system" * 100000, messages, TOOLS))
            self.assertIsNone(provider.count_text("large text" * 100000))
        count.assert_not_called()
        read.assert_not_called()

    def test_reasoning_and_tool_result_roundtrip_preserves_snapshot_after_restart(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "inspect"}]
        reply = self.complete(provider, messages, {"total_tokens": 2040},
                              arguments='{"command":"status"}', thinking="private reasoning")
        result = {"role": "tool", "tool_call_id": "call-1", "content": "large result" * 100000}
        restored = json.loads(json.dumps(working_message(reply)))
        self.assertNotIn("_raw", restored)
        self.assertEqual(self.provider().count_tokens("system", messages + [restored, result], TOOLS), 2040)
        next_payload = provider._chat_payload("system", messages + [restored, result], TOOLS)
        self.assertEqual(next_payload["messages"][2]["reasoning_content"], "private reasoning")
        self.assertNotIn("_token_usage", json.dumps(next_payload))
        self.assertNotIn("private reasoning", json.dumps(restored["_token_usage"]))

    def test_subagent_and_auxiliary_requests_do_not_replace_main_usage(self):
        provider = self.provider()
        main = self.complete(provider, usage={"total_tokens": 1000})
        child = self.complete(provider, usage={"total_tokens": 5000})
        summary = self.complete(provider, usage={"total_tokens": 200})
        self.assertEqual(provider.count_tokens("main", [main], TOOLS), 1000)
        self.assertEqual(provider.count_tokens("child", [child], TOOLS), 5000)
        self.assertEqual(provider.count_tokens("summary", [summary], []), 200)
        self.assertIsNone(provider.count_tokens("new conversation", [], TOOLS))
        latest = self.complete(provider, [main], {"total_tokens": 900})
        self.assertEqual(provider.count_tokens("main", [main, latest], TOOLS), 900)

    def test_7999_with_new_content_waits_for_9000_then_compacts_at_eighty_percent(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Path(directory), "chat-usage-compression")
            provider = self.provider(model_context_window=10000)
            messages = [{"role": "user", "content": "original task"},
                        *[{"role": "assistant", "content": f"finding {index}"} for index in range(11)]]
            for message in messages:
                session.append(message)
            reply = self.complete(provider, messages, {"total_tokens": 7999},
                                  arguments='{"command":"status"}')
            session.append(reply)
            session.append({"role": "tool", "tool_call_id": "call-1", "content": "result" * 100000})
            session.append({"role": "user", "content": "x" * 2000})
            auxiliary = Mock(output_tokens=512)
            auxiliary.compress.return_value = "Prior findings retained."
            context = WorkingContext(session, provider, auxiliary, max_tokens=10000, keep_calls=1)
            context.sync()
            with patch("skills_agent.providers.openai.post_json") as count:
                context.prepare("system", TOOLS)
                auxiliary.compress.assert_not_called()
                self.assertEqual(context.state["token_count"], 7999)
                session.append(self.complete(provider, usage={"total_tokens": 9000}))
                context.sync()
                context.prepare("system", TOOLS)
                context.prepare("system", TOOLS)
            count.assert_not_called()
            auxiliary.compress.assert_called_once()
            self.assertIsNone(context.state["token_count"])
            self.assertIsNone(context.state["last_usage"])
            reopened = WorkingContext(Session(Path(directory), "chat-usage-compression"), self.provider(), auxiliary,
                                      max_tokens=10000, keep_calls=1)
            self.assertIsNone(reopened._count("system", TOOLS))

    def test_chat_threshold_is_exactly_eighty_percent_without_output_reserve(self):
        for tokens, expected in ((7999, False), (8000, True), (9000, True)):
            with self.subTest(tokens=tokens), tempfile.TemporaryDirectory() as directory:
                session = Session(Path(directory), "chat-boundary")
                provider = self.provider(model_context_window=10000, max_output_tokens=5000)
                session.append({"role": "user", "content": "task"})
                session.append({"role": "assistant", "content": "finding"})
                session.append(self.complete(provider, usage={"total_tokens": tokens}))
                auxiliary = Mock(output_tokens=512)
                auxiliary.compress.return_value = "summary"
                context = WorkingContext(session, provider, auxiliary, max_tokens=10000, keep_calls=1)
                context.sync()
                context.prepare("system", [])
                self.assertEqual(auxiliary.compress.call_count, int(expected))

    def test_actual_agent_chat_tool_loop_and_restart_never_call_count_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session(root, "chat-usage-loop")
            arguments = json.dumps({"name": "Write evidence", "command": "printf observed > proof.txt; printf observed",
                                    "run_in_background": False, "result_mode": "report"})
            streams = [self.events({"total_tokens": 6040}, arguments=arguments),
                       self.events({"total_tokens": 20100}, text="Observed proof.txt was written."),
                       self.events({"total_tokens": 6110}, text="completed"),
                       self.events({"total_tokens": 6210}, text="continued")]
            environment = {key: value for key, value in os.environ.items()
                           if key not in {SCOPE_ENV, CONTEXT_ENV, CALL_ENV}}
            with patch.dict(os.environ, environment, clear=True), patch(
                "skills_agent.providers.openai.post_stream", side_effect=streams,
            ) as send, patch("skills_agent.providers.openai.post_json",
                             side_effect=AssertionError("Counting endpoint must not be called")) as count:
                agent = Agent(self.provider(), session, SkillRegistry([]), root, allow_subagent=False)
                try:
                    self.assertIsNone(agent.context._count("system", []))
                    self.assertEqual(agent.run(session.make_user_message("write proof", [])), "completed")
                    self.assertEqual(agent.context._count("system", []), 6110)
                finally:
                    agent.close()
                self.assertEqual((root / "proof.txt").read_text(), "observed")
                reopened = Agent(self.provider(), Session(root, "chat-usage-loop"), SkillRegistry([]), root,
                                 allow_subagent=False)
                try:
                    self.assertEqual(reopened.context._count("system", []), 6110)
                    self.assertEqual(reopened.run(session.make_user_message("continue", [])), "continued")
                    self.assertEqual(reopened.context._count("system", []), 6210)
                finally:
                    reopened.close()
                count.assert_not_called()
            self.assertEqual(send.call_count, 4)


if __name__ == "__main__":
    unittest.main()
