"""Compression previews reuse private streams without changing compression work."""

import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prompt_toolkit.formatted_text import fragment_list_to_text, fragment_list_width
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer
from skills_agent.context import WorkingContext
from skills_agent.context.auxiliary import Auxiliary
from skills_agent.providers.base import ProviderConnectionError, ProviderError
from skills_agent.session import Session
from skills_agent.terminal import TerminalView


def payload_of(messages):
    content = messages[0]["content"]
    return json.loads(content[0]["text"] if isinstance(content, list) else content)


class StreamingModel:
    model_context_window = None

    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.summary_attempts = 0

    def count_tokens(self, *args):
        return 100

    def count_text(self, text):
        return len(text) // 4 + 1

    def compact_messages(self, messages):
        return copy.deepcopy(messages)

    def complete(self, system, messages, tools, *, on_event, max_output_tokens=None):
        payload = payload_of(messages)
        self.calls.append((system, copy.deepcopy(messages), copy.deepcopy(tools), max_output_tokens))
        if system.startswith("Summarize only"):
            self.summary_attempts += 1
            on_event({"type": "thinking_delta", "text": "较早的思考 " * 100})
            on_event({"type": "thinking_delta", "text": "\n\t最新结论\x1b[31mTAIL\x1b[0m\x00"})
            on_event({"type": "tool_call_delta", "arguments": "PRIVATE_UNUSED_ARGUMENTS"})
            if self.failure:
                failure, self.failure = self.failure, None
                raise failure
            text = "保留约束 " + " ".join(f"[{record['event_id']}]" for record in payload["records"])
            on_event({"type": "text_delta", "text": text})
        elif system.startswith("Independently verify"):
            on_event({"type": "thinking_delta", "text": "PRIVATE_REVIEW_THOUGHT"})
            on_event({"type": "text_delta", "text": "PRIVATE_REVIEW_JSON"})
            text = json.dumps({"passed": True, "issues": [],
                               "checked_event_ids": [r["event_id"] for r in payload["records"]],
                               "checked_previous_summary": True})
        else:
            on_event({"type": "text_delta", "text": "PRIVATE_TOOL_REPORT"})
            text = "Evidence report"
        return {"role": "assistant", "content": [{"type": "text", "text": text}]}


class CompressionProgressTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.session = Session(Path(directory.name), "main")
        self.session.append({"role": "user", "content": "Keep the user constraints"})
        self.records = [{"event_id": "event-1", "message": {"role": "user", "content": "Keep constraints"}},
                        {"event_id": "event-2", "message": {"role": "assistant", "content": "Observed facts"}}]

    def test_preview_is_latest_bounded_plain_text_and_keeps_review_and_reports_private(self):
        activities = []
        model = StreamingModel()
        auxiliary = Auxiliary(model, self.session, on_activity=activities.append)
        original = (self.session.path / "events.jsonl").read_bytes()
        result = auxiliary.compress("", self.records, None)
        baseline = StreamingModel()
        self.assertEqual(Auxiliary(baseline, self.session).compress("", self.records, None), result)
        self.assertEqual(model.calls, baseline.calls)
        self.assertEqual(activities[0], {"kind": "compression", "phase": "waiting", "batch": 1})
        thinking = [a for a in activities if a["phase"] == "thinking"]
        self.assertTrue(thinking[-1]["preview"].endswith("最新结论TAIL"))
        self.assertTrue(all(len(a.get("preview", "")) <= 256 for a in activities))
        self.assertTrue(all("\n" not in a.get("preview", "") and "\x1b" not in a.get("preview", "")
                            and "\x00" not in a.get("preview", "") for a in activities))
        self.assertEqual([a["preview"] for a in activities if a["phase"] == "summary"], [result])
        self.assertEqual(activities[-1], {"kind": "compression", "phase": "checking"})
        self.assertNotIn("PRIVATE", json.dumps(activities))
        self.assertEqual((self.session.path / "events.jsonl").read_bytes(), original)
        logs = "\n".join(path.read_text() for path in self.session.path.glob("subagents/*/events.jsonl"))
        self.assertIn("PRIVATE_UNUSED_ARGUMENTS", logs)
        self.assertIn("\\u001b[31m", logs)  # Private logs retain the original stream bytes.
        before_report = copy.deepcopy(activities)
        auxiliary.tool_report("raw evidence", "inspect", {"event_id": "event-1"}, {})
        self.assertEqual(activities, before_report)

    def test_each_batch_and_retry_starts_a_fresh_preview(self):
        model = StreamingModel(ProviderConnectionError("retry once"))
        activity = []
        auxiliary = Auxiliary(model, self.session, on_activity=activity.append)
        original_batches = auxiliary._batches

        def batches(records, system, payload, tools=None):
            if system.startswith("Summarize only"):
                yield from ([record] for record in records)
            else:
                yield from original_batches(records, system, payload, tools)

        auxiliary._batches = batches
        auxiliary.compress("", self.records, None)
        waiting = [a for a in activity if a["phase"] == "waiting"]
        self.assertEqual([a["batch"] for a in waiting], [1, 1, 2])
        self.assertTrue(all("preview" not in a for a in waiting))
        summary = [a for a in activity if a["phase"] == "summary"]
        self.assertEqual([a["batch"] for a in summary], [1, 2])
        self.assertEqual(summary[-1]["preview"], "保留约束 [event-2]")
        self.assertNotIn("最新结论", summary[-1]["preview"])

    def test_cancellation_or_failure_clears_preview_without_committing_a_partial_summary(self):
        for failure in (KeyboardInterrupt(), ProviderError("failed generation")):
            with self.subTest(failure=type(failure).__name__):
                for index in range(11):
                    self.session.append({"role": "assistant", "content": f"old-{index}"})
                activities = []
                model = StreamingModel(failure)
                auxiliary = Auxiliary(model, self.session, on_activity=activities.append)
                context = WorkingContext(self.session, model, auxiliary, max_tokens=1000,
                                         on_activity=activities.append)
                context.sync()
                original = context.path.read_bytes()
                context._count = lambda system, tools, records=None, summary=None: 900 if records is None else 100
                with self.assertRaises(type(failure)):
                    context.prepare("system", [])
                self.assertTrue(any(a and a.get("phase") == "thinking" for a in activities))
                self.assertIsNone(activities[-1])
                self.assertEqual(context.path.read_bytes(), original)
                self.assertEqual(context.state["summary"], "")

    def test_display_uses_one_bounded_row_and_freezes_during_selection(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
            view = TerminalView(input=DummyInput(), output=DummyOutput())
        renderer = ConsoleRenderer(view)
        renderer({"type": "activity", "activity": {"kind": "compression", "phase": "thinking",
                  "batch": 2, "preview": "较早片段" * 100 + "最新"}})
        rows = view._lines(60)
        self.assertEqual(len(rows), 1)
        self.assertEqual(fragment_list_width(rows[0]), 60)
        displayed = fragment_list_to_text(rows[0])
        self.assertIn("第 2 批", displayed)
        self.assertIn("思考", displayed)
        self.assertIn("…", displayed)
        self.assertRegex(displayed, r"最新\.{1,3}\s*$")
        tail_styles = [style for style, text in rows[0] if "最新" in text]
        self.assertEqual(tail_styles, ["fg:ansibrightblack bg:default"])
        self.assertEqual(view.blocks, [])
        content = view.control.create_content(60, 20)
        frozen = [fragment_list_to_text(content.get_line(i)) for i in range(content.line_count)]
        view._selecting = True
        renderer({"type": "activity", "activity": {"kind": "compression", "phase": "summary",
                  "preview": "摘要片段"}})
        content = view.control.create_content(60, 20)
        self.assertEqual([fragment_list_to_text(content.get_line(i)) for i in range(content.line_count)], frozen)
        view._selecting = False
        self.assertIn("摘要片段", fragment_list_to_text(view._lines(60)[0]))
        renderer({"type": "activity", "activity": None})
        self.assertFalse(any(fragment_list_to_text(row) for row in view._lines(60)))

    def test_stream_updates_keep_the_animation_clock_running_across_phases(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}), \
                patch("skills_agent.terminal.monotonic", return_value=100.0) as clock:
            view = TerminalView(input=DummyInput(), output=DummyOutput())
            renderer = ConsoleRenderer(view)

            def update(phase, preview):
                renderer({"type": "activity", "activity": {"kind": "compression",
                          "phase": phase, "preview": preview}})
                return fragment_list_to_text(view._lines(60)[0]).rstrip()

            self.assertTrue(update("thinking", "first").endswith("first."))
            clock.return_value = 101.0
            self.assertTrue(update("thinking", "latest").endswith("latest..."))
            clock.return_value = 101.7
            self.assertTrue(update("summary", "draft").endswith("draft.."))


if __name__ == "__main__":
    unittest.main()
