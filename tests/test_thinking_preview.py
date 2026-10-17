"""Bounded live thinking and incremental body layout avoid repeated Markdown work."""

import unittest
from unittest.mock import patch

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import fragment_list_to_text, fragment_list_width
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer
from skills_agent.terminal import TerminalView


class ThinkingPreviewTests(unittest.TestCase):
    def setUp(self):
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())

    def lines(self, width=60):
        return [fragment_list_to_text(row).rstrip() for row in self.view._lines(width)]

    def click(self, row=0):
        self.view.control.mouse_handler(MouseEvent(
            position=Point(x=2, y=row), event_type=MouseEventType.MOUSE_UP,
            button=MouseButton.LEFT, modifiers=frozenset(),
        ))

    def test_preview_is_exactly_eight_plain_text_rows_and_rolls_at_tail(self):
        self.view.write_delta("**first**", channel="thinking")
        with patch("skills_agent.terminal._Body") as markdown:
            self.assertEqual(self.lines()[1:], ["**first**", "", "", "", "", "", "", ""])
            for index in range(12):
                self.view.write_delta(f"\n**line-{index:02}**", channel="thinking")
                self.assertEqual(len(self.lines()), 9)
            markdown.assert_not_called()
        self.assertEqual(self.lines()[1:], [f"**line-{index:02}**" for index in range(4, 12)])

    def test_wrapped_tail_respects_terminal_cells_and_rebuilds_on_resize(self):
        self.view.write_delta("中文" * 40, channel="thinking")
        rows = self.view._lines(10)
        self.assertEqual(len(rows), 9)
        self.assertTrue(all(fragment_list_width(row) == 10 for row in rows))
        self.assertEqual(self.lines(10)[-1], "文中文中文")
        resized = self.lines(20)
        self.assertEqual(len(resized), 9)
        self.assertTrue(all(line == "中文" * 5 for line in resized[1:]))

    def test_body_transition_collapses_and_stops_thinking_without_response_end(self):
        renderer = ConsoleRenderer(self.view)
        renderer({"type": "thinking_delta", "text": "**完整思考**"})
        self.assertTrue(self.view._thinking_active())
        renderer({"type": "text_delta", "text": "**正文**"})
        self.assertEqual(self.lines(), ["▶ 思考", "assistant:", "**正文**"])
        self.assertFalse(self.view._thinking_active())
        self.assertTrue(self.view.blocks[0]["complete"])
        self.assertFalse(self.view.blocks[0]["expanded"])
        self.click()
        self.assertEqual(self.lines()[:2], ["▼ 思考", "完整思考"])
        # Finishing a later body must not re-collapse a manually opened history.
        renderer.close()
        self.assertEqual(self.lines(), ["▼ 思考", "完整思考", "assistant:", "正文"])

    def test_first_partial_tool_delta_collapses_even_before_tool_name_arrives(self):
        renderer = ConsoleRenderer(self.view)
        renderer({"type": "thinking_delta", "text": "准备工具参数"})
        renderer({"type": "tool_call_delta", "index": 0, "name": "sh"})
        self.assertEqual(self.lines(), ["▶ 思考"])
        self.assertFalse(self.view._thinking_active())

    def test_completed_thinking_markdown_is_lazy_and_cached_by_text_and_width(self):
        self.view.write_delta("**完整思考**", channel="thinking")
        self.view.finish_stream()
        from skills_agent.terminal import _Body
        with patch("skills_agent.terminal._Body", wraps=_Body) as markdown:
            self.lines()
            markdown.assert_not_called()
            self.click()
            self.assertIn("完整思考", self.lines())
            self.lines()
            self.click()
            self.lines()
            self.click()
            self.lines()
            self.assertEqual(markdown.call_count, 1)
            self.lines(40)
            self.assertEqual(markdown.call_count, 2)

    def test_body_live_layout_only_processes_new_chunks_and_final_markdown_is_cached(self):
        self.view.write_delta("**开头**\n")
        self.lines()
        cache = self.view.blocks[0]["plain_rows"]
        with patch.object(cache, "append", wraps=cache.append) as append, \
                patch("skills_agent.terminal._Body") as markdown:
            for _ in range(10):
                self.view.write_delta("新内容\n")
                self.lines()
                self.lines()
            self.assertEqual(append.call_count, 10)
            markdown.assert_not_called()
        self.view.finish_stream()
        self.assertEqual(self.view.blocks[0]["text"], "**开头**\n" + "新内容\n" * 10)
        from skills_agent.terminal import _Body
        with patch("skills_agent.terminal._Body", wraps=_Body) as markdown:
            self.assertIn("开头", "\n".join(self.lines()))
            self.lines()
            self.assertEqual(markdown.call_count, 1)

    def test_long_active_thinking_keeps_only_eight_wrapped_rows_in_layout_cache(self):
        self.view.write_delta("line\n" * 10000, channel="thinking")
        self.lines()
        cache = self.view.blocks[0]["plain_rows"]
        self.assertEqual(len(cache.rows), 8)
        self.view.finish_stream()
        self.assertEqual(self.view.blocks[0]["text"], "line\n" * 10000)

    def test_rendering_does_not_hold_control_lock(self):
        self.view.append_body("**正文**")
        from skills_agent.terminal import _Body
        def render(*args, **kwargs):
            self.assertFalse(self.view._lock._is_owned())
            return _Body(*args, **kwargs)
        with patch("skills_agent.terminal._Body", side_effect=render):
            self.view.control.create_content(60, 20)

    def test_failed_queued_callback_exits_ui_and_unblocks_prompt(self):
        with create_pipe_input() as input:
            view = TerminalView(input=input, output=DummyOutput()).start()
            try:
                def broken(event):
                    raise RuntimeError("display callback failed")
                view.post_event(broken, {"type": "message"})
                with self.assertRaisesRegex(RuntimeError, "display callback failed"):
                    view.prompt()
                self.assertIsInstance(view._failure, RuntimeError)
            finally:
                view.close()
            self.assertFalse(view.app.is_running)

    def test_final_drain_failure_still_wakes_pending_input_and_close_returns(self):
        with create_pipe_input() as input:
            view = TerminalView(input=input, output=DummyOutput()).start()
            def broken():
                raise RuntimeError("final display callback failed")
            view._display.post(broken)
            view.close()
            with self.assertRaisesRegex(RuntimeError, "final display callback failed"):
                view.prompt()
            self.assertFalse(view.app.is_running)

    def test_closed_view_ignores_late_events_and_display_mutations(self):
        self.view.close()
        renderer = ConsoleRenderer(self.view)
        self.view.post_event(renderer, {"type": "text_delta", "text": "late text"})
        self.view.write_delta("late thought", channel="thinking")
        self.view.append_line("late line", "tool")
        self.view.set_activity("late activity")
        self.assertEqual(self.view.blocks, [])
        self.assertIsNone(self.view._activity)

    def test_frame_redraw_interval_matches_ten_hertz_delivery(self):
        self.assertEqual(self.view.app.min_redraw_interval, 0.1)


if __name__ == "__main__":
    unittest.main()
