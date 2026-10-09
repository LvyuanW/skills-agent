import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skills_agent.context.auxiliary import Auxiliary
from skills_agent.providers.base import (
    ProviderConnectionError, ProviderError, ProviderOutputLimitError, validate_tool_pairs,
)
from skills_agent.session import Session


def reply(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def verdict(payload, issues=None, ids=None):
    issues = issues or []
    return reply(json.dumps({"passed": not issues, "issues": issues,
                             "checked_event_ids": ids if ids is not None else [
                                 record["event_id"] for record in payload["records"]],
                             "checked_previous_summary": True}))


class Model:
    model_context_window = None

    def __init__(self, responses=None, handler=None, window=None):
        self.responses = list(responses or [])
        self.handler = handler
        self.model_context_window = window
        self.calls = []
        self.counted = []

    def count_tokens(self, system, messages, tools):
        validate_tool_pairs(messages)
        self.counted.append(copy.deepcopy(messages))
        return (len(system) + len(json.dumps(messages, ensure_ascii=False))
                + len(json.dumps(tools, ensure_ascii=False))) // 4 + 1

    def count_text(self, text):
        return len(text) // 4 + bool(text)

    def complete(self, system, messages, tools, *, on_event=None, max_output_tokens=None):
        record = {"system": system, "messages": copy.deepcopy(messages),
                  "tools": tools, "max_output_tokens": max_output_tokens}
        self.calls.append(record)
        if self.handler:
            response = self.handler(system, messages, tools)
        else:
            response = self.responses.pop(0)
        if isinstance(response, BaseException):
            if on_event:
                on_event({"type": "text_delta", "text": "PRIVATE_INCOMPLETE_FRAGMENT"})
            raise response
        if callable(response):
            content = messages[0]["content"]
            response = response(json.loads(content[0]["text"] if isinstance(content, list) else content))
        if isinstance(response, str):
            response = reply(response)
        if on_event:
            on_event({"type": "thinking_delta", "text": "PRIVATE_REVIEW_THOUGHT"})
        return response


class AuxiliaryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.session = Session(Path(directory.name), "main")
        self.records = [
            {"event_id": "event-1", "message": {"role": "user", "content": "不要修改配置"}},
            {"event_id": "event-2", "message": {"role": "assistant", "content": "建议修改配置，尚未得到采纳。"}},
        ]
        (self.session.path / "events.jsonl").write_text("".join(
            json.dumps({"type": "message", **record}, ensure_ascii=False) + "\n" for record in self.records))
        self.summary = "用户要求 [event-1]「不要修改配置」。助手建议修改配置但未被采纳 [event-2]。"

    def private_events(self):
        return [json.loads(line) for path in (self.session.path / "subagents").glob("*/events.jsonl")
                for line in path.read_text().splitlines()]

    def test_compression_uses_selected_working_records_and_independent_complete_review(self):
        selected = copy.deepcopy(self.records)
        selected[1]["message"]["content"] = "Selected report, not complete raw tool output"
        original = copy.deepcopy(selected)
        model = Model([self.summary, verdict])
        raw_before = (self.session.path / "events.jsonl").read_bytes()
        result = Auxiliary(model, self.session).compress("", selected, {"role": "user", "content": "当前任务"})
        self.assertEqual(result, self.summary)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[0]["max_output_tokens"], 32768)
        self.assertEqual(json.loads(model.calls[0]["messages"][0]["content"])["records"], selected)
        self.assertNotIn("建议修改配置，尚未得到采纳。", str(model.calls))
        self.assertIn("Check BOTH directions", model.calls[1]["system"])
        self.assertEqual(len(model.calls[1]["messages"]), 1)
        self.assertEqual(selected, original)
        self.assertEqual((self.session.path / "events.jsonl").read_bytes(), raw_before)
        self.assertTrue(any(event.get("type") == "thinking_delta" for event in self.private_events()))

    def test_summary_quality_failure_gets_one_revision_and_new_independent_review(self):
        bad = "修改配置已被采纳 [event-2]。"
        issue = "event-1 禁止修改，event-2 的助手建议没有采纳依据"
        model = Model([bad, lambda p: verdict(p, [issue]), self.summary, verdict])
        result = Auxiliary(model, self.session).compress("", self.records, None)
        self.assertEqual(result, self.summary)
        self.assertEqual(len(model.calls), 4)
        repair = json.loads(model.calls[2]["messages"][0]["content"])
        self.assertEqual(repair["verification_issues"], [issue])
        self.assertEqual(repair["records"], self.records)
        self.assertEqual(json.loads(model.calls[3]["messages"][0]["content"])["draft"], self.summary)

    def test_failed_second_review_does_not_modify_parent_history(self):
        before = (self.session.path / "events.jsonl").read_bytes()
        fail = lambda p: verdict(p, ["event-1 用户约束仍被遗漏"])
        model = Model(["bad", fail, "still bad", fail])
        with self.assertRaisesRegex(ProviderError, "after one revision"):
            Auxiliary(model, self.session).compress("", self.records, None)
        self.assertEqual(len(model.calls), 4)
        self.assertEqual((self.session.path / "events.jsonl").read_bytes(), before)

    def test_unknown_reference_and_changed_literal_quote_override_model_approval(self):
        draft = "用户说 [event-1]「可以修改配置」 [event-999]"
        model = Model([draft, verdict, self.summary, verdict])
        self.assertEqual(Auxiliary(model, self.session).compress("", self.records, None), self.summary)
        issues = json.loads(model.calls[2]["messages"][0]["content"])["verification_issues"]
        self.assertTrue(any("Unknown source reference: event-999" in issue for issue in issues))
        self.assertTrue(any("Quote does not match event-1" in issue for issue in issues))

    def test_review_must_cover_every_supplied_record(self):
        model = Model([self.summary, lambda p: verdict(p, ids=["event-1"])])
        with self.assertRaisesRegex(ProviderError, "complete source material"):
            Auxiliary(model, self.session).compress("", self.records, None)
        self.assertEqual(len(model.calls), 2)

    def test_connection_retry_reuses_input_and_does_not_join_incomplete_fragments(self):
        model = Model([ProviderConnectionError("broken"), ProviderConnectionError("broken again"),
                       self.summary, verdict])
        result = Auxiliary(model, self.session).compress("", self.records, None)
        self.assertEqual(result, self.summary)
        self.assertEqual(model.calls[0]["messages"], model.calls[1]["messages"])
        self.assertEqual(model.calls[1]["messages"], model.calls[2]["messages"])
        self.assertNotIn("PRIVATE_INCOMPLETE_FRAGMENT", str(model.calls))
        self.assertEqual(sum(event.get("type") == "error" for event in self.private_events()), 2)

    def test_connection_fails_explicitly_after_three_attempts(self):
        model = Model([ProviderConnectionError("broken") for _ in range(3)])
        with self.assertRaisesRegex(ProviderConnectionError, "after 3 attempts"):
            Auxiliary(model, self.session).compress("", self.records, None)
        self.assertEqual(len(model.calls), 3)

    def test_output_truncation_cancellation_and_other_errors_are_not_retried(self):
        for error in (ProviderOutputLimitError("length"), KeyboardInterrupt(), ProviderError("bad response")):
            with self.subTest(error=type(error).__name__):
                model = Model([error])
                with self.assertRaises(type(error)):
                    Auxiliary(model, self.session).compress("", self.records, None)
                self.assertEqual(len(model.calls), 1)

    def test_review_can_read_multiple_cited_original_events_privately(self):
        def read_response(payload):
            return {"role": "assistant", "content": [], "tool_calls": [
                {"id": "read-1", "name": "read_history", "arguments": '{"event_id":"event-1"}'},
                {"id": "read-2", "name": "read_history", "arguments": '{"event_id":"event-2"}'},
            ]}
        model = Model([self.summary, read_response, verdict])
        before = self.session.events()
        self.assertEqual(Auxiliary(model, self.session).compress("", self.records, None), self.summary)
        continued = model.calls[2]["messages"]
        self.assertEqual([message["role"] for message in continued], ["user", "assistant", "tool", "tool"])
        self.assertIn("不要修改配置", continued[2]["content"])
        self.assertEqual(self.session.events(), before)
        self.assertEqual(sum(event.get("type") == "history_read" for event in self.private_events()), 2)

    def test_large_compression_material_keeps_call_result_groups_and_reviews_all_records(self):
        records = []
        for number in range(12):
            call = {"id": f"call-{number}", "name": "shell", "arguments": '{"command":"pwd"}'}
            records.extend([
                {"event_id": f"event-{number * 2 + 10}", "message": {
                    "role": "assistant", "content": "context " * 160, "tool_calls": [call]}},
                {"event_id": f"event-{number * 2 + 11}", "message": {
                    "role": "tool", "name": "shell", "tool_call_id": call["id"], "content": "result " * 180}},
            ])
        def handler(system, messages, tools):
            payload = json.loads(messages[0]["content"])
            return verdict(payload) if tools else reply("Preserved important findings [event-10].")
        model = Model(handler=handler, window=3000)
        Auxiliary(model, self.session, output_tokens=1000).compress("", records, None)
        generations = [json.loads(call["messages"][0]["content"])["records"]
                       for call in model.calls if not call["tools"]]
        reviews = [json.loads(call["messages"][0]["content"])["records"]
                   for call in model.calls if call["tools"]]
        self.assertGreater(len(generations), 1)
        for batches in (generations, reviews):
            self.assertEqual([item for batch in batches for item in batch], records)
            for batch in batches:
                validate_tool_pairs([item["message"] for item in batch])

    def test_indivisible_group_is_not_silently_truncated(self):
        record = {"event_id": "event-big", "message": {"role": "user", "content": "x" * 20000}}
        model = Model(window=1500)
        with self.assertRaisesRegex(ProviderError, "indivisible"):
            Auxiliary(model, self.session).compress("", [record], None)
        self.assertEqual(model.calls, [])

    def test_tool_report_rewrites_over_budget_without_truncating(self):
        model = Model(["x" * 10000, "退出码 0，已生成结果 /tmp/report.csv [event-2]。"])
        result = Auxiliary(model, self.session).tool_report(
            "exit_code: 0\nstdout: /tmp/report.csv", {"name": "shell"}, {"event_id": "event-2"}, {})
        self.assertEqual(result, "退出码 0，已生成结果 /tmp/report.csv [event-2]。")
        self.assertEqual(json.loads(model.calls[1]["messages"][0]["content"])["draft"], "x" * 10000)

    def test_tool_report_that_still_exceeds_budget_fails(self):
        model = Model(["x" * 10000, "y" * 10000])
        with self.assertRaisesRegex(ProviderError, "still exceeds 2048"):
            Auxiliary(model, self.session).tool_report("output", {}, {}, {})

    def test_long_tool_output_reads_every_line_including_middle_then_combines_reports(self):
        content = "".join(f"line {index}: " + "value " * 70 + "\n" for index in range(90))
        content = content.replace("line 45:", "line 45: IMPORTANT_MIDDLE_EVIDENCE")
        def handler(system, messages, tools):
            payload = json.loads(messages[0]["content"])
            output = payload["output"]
            return reply("IMPORTANT_MIDDLE_EVIDENCE [event-2]" if "IMPORTANT_MIDDLE_EVIDENCE" in output else "其他证据 [event-2]")
        model = Model(handler=handler, window=3000)
        result = Auxiliary(model, self.session, output_tokens=1000).tool_report(content, {}, {"event_id": "event-2"}, {})
        chunks = [json.loads(call["messages"][0]["content"]) for call in model.calls]
        original_chunks = [chunk for chunk in chunks if chunk.get("input_kind") != "partial_reports"]
        self.assertEqual("".join(chunk["output"] for chunk in original_chunks), content)
        self.assertIn("IMPORTANT_MIDDLE_EVIDENCE", result)
        self.assertEqual(original_chunks[0]["output_lines"]["start"], 1)
        self.assertEqual(original_chunks[-1]["output_lines"]["end"], 90)
        self.assertEqual(chunks[-1]["input_kind"], "partial_reports")

    def test_single_oversized_line_is_split_without_losing_text(self):
        content = "start " + "x" * 18000 + " END_EVIDENCE"
        def handler(system, messages, tools):
            payload = json.loads(messages[0]["content"])
            return reply("END_EVIDENCE" if "END_EVIDENCE" in payload["output"] else "chunk")
        model = Model(handler=handler, window=1500)
        result = Auxiliary(model, self.session, output_tokens=500).tool_report(content, {}, {}, {})
        material = [json.loads(call["messages"][0]["content"]) for call in model.calls]
        self.assertEqual("".join(item["output"] for item in material if "input_kind" not in item), content)
        self.assertEqual(result, "END_EVIDENCE")

    def test_model_window_reduces_generation_allowance_when_needed(self):
        model = Model(["short report"], window=1500)
        Auxiliary(model, self.session).tool_report("actual output", {}, {}, {})
        call = model.calls[0]
        self.assertEqual(call["max_output_tokens"], 1500 - model.count_tokens(call["system"], call["messages"], call["tools"]))
        self.assertGreater(call["max_output_tokens"], 0)

    def test_previous_verified_baseline_is_separate_from_unverified_draft(self):
        baseline = "历史约束仍然有效 [event-1]。"
        model = Model([self.summary, verdict])
        Auxiliary(model, self.session).compress(baseline, self.records, None)
        generated = json.loads(model.calls[0]["messages"][0]["content"])
        reviewed = json.loads(model.calls[1]["messages"][0]["content"])
        self.assertEqual(generated["previous_verified_summary"], baseline)
        self.assertEqual(generated["draft"], "")
        self.assertEqual(reviewed["previous_verified_summary"], baseline)
        self.assertEqual(reviewed["draft"], self.summary)

    def test_large_tool_report_background_keeps_latest_user_task(self):
        model = Model(["结果 [event-2]"], window=1500)
        background = {"system": "irrelevant " * 10000,
                      "messages": [{"role": "user", "content": "current intent"}]}
        Auxiliary(model, self.session).tool_report("actual tool evidence", {}, {"event_id": "event-2"}, background)
        supplied = json.loads(model.calls[0]["messages"][0]["content"])
        self.assertEqual(supplied["background"], {"current_user_task": {"role": "user", "content": "current intent"}})
        self.assertEqual(supplied["output"], "actual tool evidence")

    def test_citation_without_evidence_lookup_stays_within_selected_source_inventory(self):
        def read_response(payload):
            return {"role": "assistant", "content": [], "tool_calls": [{
                "id": "read-1", "name": "read_history", "arguments": '{"event_id":"event-999"}',
            }]}
        model = Model([self.summary, read_response, verdict])
        Auxiliary(model, self.session).compress("", self.records, None)
        self.assertIn("Only cited working-context events", model.calls[2]["messages"][-1]["content"])

    def test_compression_removes_protocol_duplicates_only_from_its_evidence_copy(self):
        records = copy.deepcopy(self.records)
        records[1]["message"].update({
            "_raw": {"provider": "PRIVATE_RAW"},
            "_anthropic_content": [{"type": "text", "text": "PRIVATE_NATIVE_DUPLICATE"}],
            "_responses_output": [{"type": "reasoning", "encrypted_content": "PRIVATE_ENCRYPTED"}],
            "reasoning_content": "readable thinking",
            "content": [
                {"type": "thinking", "thinking": "readable thinking", "signature": "PRIVATE_SIGNATURE"},
                {"type": "redacted_thinking", "data": "PRIVATE_REDACTED"},
                {"type": "text", "text": "assistant public body"},
            ],
        })
        original = copy.deepcopy(records)
        model = Model([self.summary, verdict])
        Auxiliary(model, self.session).compress("", records, None)
        for call in model.calls:
            supplied = call["messages"][0]["content"]
            self.assertNotIn("PRIVATE_", supplied)
            self.assertEqual(supplied.count("readable thinking"), 1)
            self.assertIn("assistant public body", supplied)
        self.assertEqual(records, original)

    def test_compression_passes_native_media_with_identical_counting_and_generation_requests(self):
        image = {"type": "image", "path": str(self.session.path / "image.png"),
                 "name": "image.png", "mime_type": "image/png"}
        pdf = {"type": "file", "path": str(self.session.path / "document.pdf"),
               "name": "document.pdf", "mime_type": "application/pdf"}
        records = [{"event_id": "event-1", "message": {"role": "user", "content": [
            {"type": "text", "text": "附件中的要求需要保留"}, image,
        ]}}]
        current = {"role": "user", "content": [{"type": "text", "text": "当前文档"}, pdf]}
        model = Model(["附件中的要求 [event-1]", verdict])
        Auxiliary(model, self.session).compress("", records, current)
        for call in model.calls:
            content = call["messages"][0]["content"]
            self.assertEqual([block for block in content if block["type"] in {"image", "file"}], [image, pdf])
            self.assertIn("event-1", content[1]["text"])
            self.assertIn("current_user_task", content[3]["text"])
            self.assertIn(call["messages"], model.counted)

    def test_required_user_background_that_is_itself_too_large_is_not_dropped(self):
        model = Model(window=1500)
        task = {"role": "user", "content": "不可丢弃的当前任务约束 " * 6000}
        with self.assertRaisesRegex(ProviderError, "task/background exceeds"):
            Auxiliary(model, self.session).tool_report("tool result", {}, {}, {"current_user_task": task})
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
