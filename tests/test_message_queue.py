import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import fragment_list_to_text, fragment_list_width
from prompt_toolkit.input import DummyInput
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import _interact
from skills_agent.input import ClipboardDraft
from skills_agent.session import Session
from skills_agent.terminal import TerminalView


class MessageQueueTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"TERM": "xterm-256color"})
        environment.start()
        self.addCleanup(environment.stop)
        timer = patch("skills_agent.terminal.monotonic", return_value=100.0)
        self.clock = timer.start()
        self.addCleanup(timer.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())

    def submit(self, text):
        self.view.buffer.text = text
        self.view.buffer.validate_and_handle()

    def rows(self, width=30):
        content = self.view.queue_control.create_content(width, 10)
        return [content.get_line(index) for index in range(content.line_count)]

    def mouse(self, kind, x, y=0):
        # The application clears previous hover before dispatching each key/mouse event.
        self.view._clear_hover(None)
        return self.view.queue_control.mouse_handler(MouseEvent(
            position=Point(x=x, y=y), event_type=kind, button=MouseButton.LEFT,
            modifiers=frozenset(),
        ))

    def test_busy_input_remains_editable_and_messages_dispatch_in_order(self):
        self.submit("active")
        self.assertEqual(self.view.prompt(), "active")
        self.assertTrue(self.view._busy)
        self.assertFalse(self.view.buffer.read_only())
        self.view.write_delta("still answering")
        self.submit("first queued")
        self.submit("second queued")
        self.view.buffer.text = "unfinished draft"

        self.assertEqual([item.text for item in self.view.queue_control.items()],
                         ["first queued", "second queued"])
        self.assertIn("still answering", [fragment_list_to_text(row).rstrip()
                                         for row in self.view._lines(60)])
        self.assertEqual(self.view.prompt(), "first queued")
        self.assertEqual(self.view.prompt(), "second queued")
        self.assertEqual(self.view.queue_control.items(), [])
        self.assertEqual(self.view.buffer.text, "unfinished draft")
        self.view.finish_response()
        self.assertEqual(self.view.blocks[0]["text"], "still answering")

    def test_cancel_identical_text_by_identity_and_ignore_already_dispatched_item(self):
        self.view._busy = True
        self.submit("same text")
        self.submit("same text")
        first, second = self.view.queue_control.items()
        self.view.buffer.text = "leave this draft alone"

        self.view.cancel_message(first)
        self.assertEqual(self.view.queue_control.items(), [second])
        self.assertEqual(self.view.prompt(), "same text")
        self.submit("later message")
        self.view.cancel_message(first)
        self.view.cancel_message(second)
        self.assertEqual(self.view.prompt(), "later message")
        self.assertEqual(self.view.queue_control.items(), [])

    def test_submission_wakes_waiting_prompt_without_losing_the_message(self):
        entered = Event()
        received = []

        def read():
            entered.set()
            received.append(self.view.prompt())

        reader = Thread(target=read, daemon=True)
        reader.start()
        self.assertTrue(entered.wait(1))
        self.submit("wake up")
        reader.join(1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(received, ["wake up"])
        self.assertEqual(self.view.queue_control.items(), [])

    def test_queued_submission_keeps_active_timer_and_counts_its_own_wait(self):
        self.submit("first")
        self.view.prompt()
        self.view.start_turn()
        self.clock.return_value = 110.0
        self.submit("second")
        self.clock.return_value = 120.0
        self.assertEqual(self.view._turn_status(), "耗时 20.0 秒")
        self.view.finish_turn()
        self.clock.return_value = 130.0
        self.assertEqual(self.view.prompt(), "second")
        self.view.start_turn()
        self.assertEqual(self.view._turn_status(), "耗时 20.0 秒")
        self.clock.return_value = 135.0
        self.view.finish_turn()
        self.assertEqual(self.view._turn_status(), "耗时 25.0 秒")

    def test_queue_rows_clip_chinese_and_newlines_without_changing_submitted_text(self):
        original = "读取非常长的中文文件名称\n然后分析全部相关日志" * 3
        self.submit(original)
        self.submit("short")
        rows = self.rows(width=30)
        self.assertEqual(len(rows), 2)
        for row in rows:
            text = fragment_list_to_text(row)
            self.assertEqual(fragment_list_width(row), 30)
            self.assertNotIn("\n", text)
            self.assertTrue(text.endswith("排队中"))
            self.assertEqual(fragment_list_width(row[:-1]), 24)
            self.assertIn("ansiblue", row[-1][0])
        self.assertTrue(fragment_list_to_text(rows[0][:-1]).rstrip().endswith("…"))
        self.assertEqual(self.view.prompt(), original)

    def test_hover_changes_status_and_click_cancels_without_changing_draft(self):
        self.submit("检查当前工作目录")
        self.submit("keep this")
        self.view.buffer.text = "unfinished draft"
        row = self.rows()[0]
        # UIControl mouse coordinates count characters, after Window maps terminal cells.
        status_x = len(fragment_list_to_text(row[:-1]))
        self.mouse(MouseEventType.MOUSE_MOVE, status_x)
        hovered = self.rows()[0]
        self.assertEqual(hovered[-1][1].strip(), "撤回")
        self.assertIn("ansired", hovered[-1][0])
        self.assertEqual(fragment_list_width(hovered), 30)

        self.mouse(MouseEventType.MOUSE_MOVE, 0)
        self.assertIn("ansiblue", self.rows()[0][-1][0])
        self.mouse(MouseEventType.MOUSE_MOVE, status_x)
        self.mouse(MouseEventType.MOUSE_UP, status_x)
        self.assertEqual([item.text for item in self.view.queue_control.items()], ["keep this"])
        self.assertEqual(self.view.buffer.text, "unfinished draft")
        self.view._clear_hover(None)
        self.assertEqual(self.rows()[0][-1][1], "排队中")

    def test_selection_freezes_pending_rows_while_worker_consumes_the_next_message(self):
        self.submit("first")
        self.submit("second")
        before = self.rows()
        self.view._selecting = True
        self.assertTrue(self.view.buffer.read_only())
        self.assertEqual(self.view.prompt(), "first")
        self.assertEqual(self.rows(), before)
        self.mouse(MouseEventType.MOUSE_UP, 29)
        self.view._selecting = False
        self.assertEqual([item.text for item in self.view.queue_control.items()], ["second"])
        self.assertEqual(len(self.rows()), 1)

    def test_cli_records_queued_messages_only_after_current_turn_and_never_records_withdrawn(self):
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            session = Session(workspace, "queue-test")
            draft = ClipboardDraft(workspace / "clipboard", on_error=self.fail)
            received = []

            def run(message):
                text = message["content"][0]["text"]
                received.append(text)
                session.append(message)
                if text == "first":
                    self.submit("second")
                    self.submit("withdraw this")
                    self.view.cancel_message(self.view.queue_control.items()[-1])
                    self.assertEqual([item["content"][0]["text"] for item in session.messages()],
                                     ["first"])
                else:
                    self.submit("/exit")
                session.append({"role": "assistant", "content": [{"type": "text", "text": "done"}]})

            self.submit("first")
            agent = SimpleNamespace(workspace=workspace, session=session, run=run)
            result = _interact(agent, [], draft, read_input=self.view.prompt, output=self.view)
            self.assertEqual(result, 0)
            self.assertEqual(received, ["first", "second"])
            messages = session.messages()
            self.assertEqual([message["role"] for message in messages],
                             ["user", "assistant", "user", "assistant"])
            self.assertEqual([message["content"][0]["text"] for message in messages],
                             ["first", "done", "second", "done"])


if __name__ == "__main__":
    unittest.main()
