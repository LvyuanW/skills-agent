import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills_agent.context import ContextBudgetError, WorkingContext, record_groups, working_message
from skills_agent.session import Session


class Counter:
    """An explicit test tokenizer: one whitespace word is one synthetic token."""
    model_context_window = None
    max_output_tokens = 20

    def __init__(self):
        self.requests = []
        self.compacted = []

    def count_tokens(self, system, messages, tools):
        self.requests.append(copy.deepcopy((system, messages, tools)))
        return len(system.split()) + sum(self.count_text(json.dumps(m)) for m in messages) + self.count_text(json.dumps(tools))

    def count_text(self, text):
        return len(text.split())

    def compact_messages(self, messages):
        self.compacted.append(copy.deepcopy(messages))
        return copy.deepcopy(messages)


class Summarizer:
    def __init__(self, result="verified summary"):
        self.calls = []
        self.result = result

    def compress(self, previous, records, current_user):
        self.calls.append(copy.deepcopy((previous, records, current_user)))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def pair(index, result="selected result"):
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"call-{index}", "name": "shell", "arguments": '{"name":"inspect","command":"ls"}'}]},
        {"role": "tool", "name": "shell", "tool_call_id": f"call-{index}", "content": result},
    ]


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Session(Path(self.directory.name), "test")
        self.counter = Counter()
        self.summary = Summarizer()

    def context(self, **kwargs):
        return WorkingContext(self.session, self.counter, self.summary, **kwargs)

    def append(self, *messages):
        for message in messages:
            self.session.append(message)

    def test_sync_saves_each_new_message_once_and_skips_unchanged_context(self):
        context = self.context()
        with patch.object(context, "save", wraps=context.save) as save:
            context.sync()
            save.assert_not_called()
            self.assertFalse(context.path.exists())
            self.append({"role": "user", "content": "task"}, *pair(0))
            context.sync()
            self.assertEqual(save.call_count, 3)
            self.assertEqual(self.context().state, context.state)
            context.sync()
            self.assertEqual(save.call_count, 3)

    def test_sync_checkpoints_trailing_stream_events_without_invalidating_tokens(self):
        self.append({"role": "user", "content": "task"})
        context = self.context()
        context.sync()
        context.state["token_count"] = 12
        context.save()
        self.session.append_event({"type": "text_delta", "text": "partial reply"})
        with patch.object(context, "save", wraps=context.save) as save:
            context.sync()
            save.assert_called_once()
            context.sync()
            save.assert_called_once()
        self.assertEqual(len(context.records), 1)
        self.assertEqual(context.state["token_count"], 12)
        self.assertEqual(context.state["cursor"], (self.session.path / "events.jsonl").stat().st_size)
        self.assertEqual(self.context().state, context.state)

    def test_interrupted_selection_preserves_completed_report_for_resume(self):
        self.append({"role": "user", "content": "task"}, *pair(0), *pair(1))
        selected = []
        def select(record, call):
            selected.append(call["id"])
            if selected == ["call-0", "call-1"]:
                raise KeyboardInterrupt
            record["message"]["content"] = f"report for {call['id']}"
            return record
        context = self.context()
        with self.assertRaises(KeyboardInterrupt):
            context.sync(select)
        restored = self.context()
        self.assertEqual(restored.records[2]["message"]["content"], "report for call-0")
        restored.sync(select)
        self.assertEqual(selected, ["call-0", "call-1", "call-1"])
        self.assertEqual(restored.records[-1]["message"]["content"], "report for call-1")
        self.assertEqual(self.context().state, restored.state)

    def test_incremental_restore_reads_only_new_bytes_and_never_reselects(self):
        self.append({"role": "user", "content": "task"}, *pair(0, "RAW_UNSELECTED"))
        context = self.context()
        selected = []
        def select(record, call):
            selected.append(call)
            record["message"]["content"] = "selected report"
            return record
        context.sync(select)
        cursor = context.state["cursor"]
        raw = (self.session.path / "events.jsonl").read_bytes()
        restored = self.context()
        calls = []
        original = self.session.iter_events
        self.session.iter_events = lambda after=0: (calls.append(after) or original(after))
        self.append({"role": "assistant", "content": "done"})
        restored.sync(select)
        self.assertEqual(calls, [cursor])
        self.assertEqual(len(selected), 1)
        self.assertEqual(restored.records[-2]["message"]["content"], "selected report")
        self.assertTrue((self.session.path / "events.jsonl").read_bytes().startswith(raw))

    def test_compaction_input_is_work_context_and_keeps_ten_complete_calls_and_user(self):
        self.append({"role": "user", "content": "latest user original with attachments", "custom": ["image reference"]})
        for index in range(14):
            self.append(*pair(index, ("raw unique hidden output " * 80)))
        context = self.context(max_tokens=1800)
        def select(record, call):
            record["message"]["content"] = "selected " * 110
            return record
        context.sync(select)
        raw = (self.session.path / "events.jsonl").read_bytes()
        before = copy.deepcopy(context.records)
        system, messages = context.prepare("system", [])
        self.assertEqual(len(self.summary.calls), 1)
        previous, older, current = self.summary.calls[0]
        self.assertEqual(previous, "")
        self.assertEqual(len(older), 8)
        self.assertNotIn("raw unique hidden", str(older))
        self.assertIn("selected", str(older))
        self.assertEqual(current, before[0]["message"])
        self.assertEqual(context.records, [before[0], *before[-20:]])
        self.assertEqual(sum(m["role"] == "assistant" for m in messages), 10)
        self.assertEqual(sum(m["role"] == "tool" for m in messages), 10)
        self.assertIn("Historical summary", system)
        self.assertEqual((self.session.path / "events.jsonl").read_bytes(), raw)
        context.prepare("system", [])
        self.assertEqual(len(self.summary.calls), 1)
        self.assertEqual(self.context(max_tokens=1800).state, context.state)

    def test_exact_ninety_percent_threshold_uses_provider_count(self):
        self.append({"role": "user", "content": "u"})
        for index in range(11):
            self.append({"role": "assistant", "content": str(index)})
        activity = []
        context = self.context(max_tokens=1000, on_activity=activity.append)
        context.sync()
        counts = iter([899, 900, 100, 200])
        self.counter.count_tokens = lambda *args: next(counts)
        context.prepare("large system", [{"name": "tool"}])
        self.assertFalse(self.summary.calls)
        self.assertFalse(activity)
        context.prepare("large system", [{"name": "tool"}])
        self.assertEqual(len(self.summary.calls), 1)
        self.assertEqual(activity, [{"kind": "compression"}, None])
        self.assertEqual(context.state["token_count"], 200)
        self.assertEqual(context.state["last_usage"], {"tokens": 200, "limit": 1000})
        self.append({"role": "assistant", "content": "new reply"})
        context.sync()
        self.assertIsNone(context.state["token_count"])
        self.assertEqual(self.context().state["last_usage"], {"tokens": 200, "limit": 1000})

    def test_compression_activity_covers_summary_and_clears_on_failure_or_interrupt(self):
        self.append({"role": "user", "content": "task"})
        for index in range(11):
            self.append({"role": "assistant", "content": str(index)})
        for error in (RuntimeError("quality check failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                activity = []
                context = self.context(max_tokens=1000, on_activity=activity.append)
                context.sync()
                before = context.path.read_bytes()
                counts = iter([900, 100])
                self.counter.count_tokens = lambda *args: next(counts)
                def compress(*args):
                    self.assertEqual(activity, [{"kind": "compression"}])
                    raise error
                with patch.object(self.summary, "compress", side_effect=compress):
                    with self.assertRaises(type(error)):
                        context.prepare("sys", [])
                self.assertEqual(activity, [{"kind": "compression"}, None])
                self.assertEqual(context.path.read_bytes(), before)

    def test_second_compaction_uses_old_summary_and_only_newly_displaced_records(self):
        self.append({"role": "user", "content": "task"})
        for index in range(11):
            self.append({"role": "assistant", "content": "first " * 20})
        context = self.context(max_tokens=260)
        context.sync()
        counts = iter([250, 100, 150])
        self.counter.count_tokens = lambda *args: next(counts)
        context.prepare("sys", [])
        covered = set(context.state["covered_event_ids"])
        self.append({"role": "assistant", "content": "second " * 60})
        context.sync()
        self.counter.count_tokens = lambda system, messages, tools: 250 if len(messages) > 11 else 150
        context.prepare("sys", [])
        previous, displaced, _ = self.summary.calls[-1]
        self.assertEqual(previous, "verified summary")
        self.assertTrue(covered.isdisjoint(r["event_id"] for r in displaced))

    def test_failed_summary_does_not_replace_old_context_or_recall_raw(self):
        self.append({"role": "user", "content": "task"})
        for index in range(11):
            self.append({"role": "assistant", "content": "long " * 20})
        context = self.context(max_tokens=280)
        context.sync()
        before = context.path.read_bytes()
        self.summary.result = RuntimeError("quality check failed")
        with self.assertRaisesRegex(RuntimeError, "quality"):
            context.prepare("sys", [])
        self.assertEqual(context.path.read_bytes(), before)
        self.assertEqual(context.state["summary"], "")

    def test_protected_user_and_calls_are_not_trimmed_on_overflow(self):
        self.append({"role": "user", "content": "large " * 500})
        activity = []
        context = self.context(max_tokens=100, on_activity=activity.append)
        context.sync()
        before = copy.deepcopy(context.records)
        with self.assertRaises(ContextBudgetError):
            context.prepare("system", [])
        self.assertEqual(context.records, before)
        self.assertFalse(self.summary.calls)
        self.assertFalse(activity)

    def test_direct_overflow_returns_original_source_without_report(self):
        self.append({"role": "user", "content": "read"}, *pair(1, "exact " * 800))
        context = self.context(max_tokens=500)
        def select(record, call):
            return {**record, "pending_direct": True,
                    "source": {"event_id": record["event_id"], "start_line": 1, "end_line": 800}}
        context.sync(select)
        _, messages = context.prepare("sys", [])
        result = json.loads(messages[-1]["content"])
        self.assertEqual(result["source"]["end_line"], 800)
        self.assertNotIn("report", result)
        self.assertFalse(self.summary.calls)
        self.assertIn("exact " * 800, self.session.messages()[-1]["content"])

    def test_actual_model_window_also_reserves_output(self):
        self.counter.model_context_window = 80
        self.append({"role": "user", "content": "data " * 65})
        context = self.context(max_tokens=272000)
        context.sync()
        with self.assertRaisesRegex(ContextBudgetError, "60"):
            context.prepare("sys", [])

    def test_thinking_tool_round_defers_compression_but_not_hard_budget(self):
        self.append({"role": "user", "content": "task"})
        for index in range(11):
            self.append(*pair(index))
        activity = []
        context = self.context(max_tokens=1000, on_activity=activity.append)
        context.sync()
        self.counter.can_compact = lambda messages: False
        self.counter.count_tokens = lambda *args: 950
        context.prepare("sys", [])
        self.assertFalse(self.summary.calls)
        before = copy.deepcopy(context.records)
        self.counter.count_tokens = lambda *args: 1001
        with self.assertRaises(ContextBudgetError):
            context.prepare("sys", [])
        self.assertEqual(context.records, before)
        self.assertFalse(self.summary.calls)
        self.assertFalse(activity)

    def test_tool_groups_and_anthropic_compaction_copy(self):
        self.append({"role": "user", "content": "u"})
        for index in range(11):
            self.append(*pair(index))
        context = self.context(max_tokens=300)
        context.sync()
        self.counter.count_tokens = lambda system, messages, tools: 290 if len(messages) > 21 else 100
        def compact(messages):
            result = copy.deepcopy(messages)
            for message in result:
                message.pop("reasoning_content", None)
            return result
        self.counter.compact_messages = compact
        context.records[-2]["message"]["reasoning_content"] = "signed old state"
        context.prepare("sys", [])
        self.assertNotIn("reasoning_content", context.records[-2]["message"])
        groups = record_groups(context.records)
        self.assertEqual([len(g) for g in groups], [1] + [2] * 10)

    def test_missing_or_orphan_results_are_rejected_before_model_count_or_compression(self):
        self.append({"role": "user", "content": "task"}, pair(1)[0])
        context = self.context()
        context.sync()
        with self.assertRaisesRegex(ValueError, "unfinished"):
            context.prepare("sys", [])
        self.assertFalse(self.counter.requests)
        self.assertFalse(self.summary.calls)
        with self.assertRaisesRegex(ValueError, "Orphan"):
            record_groups([{"event_id": "x", "message": pair(1)[1]}])

    def test_legacy_reasoning_and_native_state_survive_without_mutating_log(self):
        message = {"role": "assistant", "content": "done", "_responses_output": [{"type": "reasoning"}],
                   "_raw": {"stream_events": [{"choices": [{"delta": {"reasoning_content": "first "}}]},
                                               {"choices": [{"delta": {"reasoning_content": None}}]},
                                               {"choices": [{"delta": {"reasoning_content": "second"}}]}]}}
        original = copy.deepcopy(message)
        selected = working_message(message)
        self.assertEqual(selected["reasoning_content"], "first second")
        self.assertEqual(selected["_responses_output"], message["_responses_output"])
        self.assertNotIn("_raw", selected)
        self.assertEqual(message, original)


if __name__ == "__main__":
    unittest.main()
