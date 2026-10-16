import os
import unittest
from unittest.mock import patch

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import fragment_list_to_text, fragment_list_width
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from skills_agent.cli import ConsoleRenderer
from skills_agent.terminal import TerminalView


class TerminalViewTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"TERM": "xterm-256color"})
        environment.start()
        self.addCleanup(environment.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())

    def lines(self, width=60, height=20):
        content = self.view.control.create_content(width, height)
        return [fragment_list_to_text(content.get_line(i)).rstrip()
                for i in range(content.line_count)]

    def mouse(self, event_type, row, button=MouseButton.LEFT):
        return self.view.control.mouse_handler(MouseEvent(
            position=Point(x=2, y=row), event_type=event_type, button=button, modifiers=frozenset(),
        ))

    def test_live_thinking_closes_after_response_and_click_restores_original(self):
        self.view.write_delta("**检查** ", channel="thinking")
        self.view.write_delta("当前任务", channel="thinking")
        self.assertIn("检查 当前任务", self.lines())
        self.view.finish_stream()
        self.view.append_line("· shell「读取」：调用", "tool")
        self.view.write_delta("完成")
        self.assertEqual([block["kind"] for block in self.view.blocks], ["thinking", "line", "text"])
        self.view.finish_response()
        self.assertEqual(self.lines(), ["▶ 思考", "· shell「读取」：调用", "assistant:", "完成"])
        self.mouse(MouseEventType.MOUSE_DOWN, 0)
        self.assertEqual(self.lines()[0], "▶ 思考")
        self.mouse(MouseEventType.MOUSE_UP, 0)
        self.assertEqual(self.lines()[:2], ["▼ 思考", "检查 当前任务"])
        self.mouse(MouseEventType.MOUSE_UP, 0)
        self.assertNotIn("检查 当前任务", self.lines())
        self.assertEqual(self.view.blocks[0]["text"], "**检查** 当前任务")

    def test_replayed_thinking_stays_collapsed_without_discarding_text(self):
        self.view.append_body("原始思考", channel="thinking")
        self.view.write_delta("流式记录", channel="thinking", live=False)
        self.view.finish_response()
        self.assertEqual(self.lines(), ["▶ 思考", "▶ 思考"])
        self.mouse(MouseEventType.MOUSE_UP, 1)
        self.assertEqual(self.lines(), ["▶ 思考", "▼ 思考", "流式记录"])

    def test_interrupted_renderer_folds_partial_thinking_without_repeating_content(self):
        renderer = ConsoleRenderer(self.view)
        renderer({"type": "thinking_delta", "text": "检查"})
        renderer({"type": "thinking_delta", "text": "工作目录"})
        renderer({"type": "tool_call_delta", "index": 0, "name": "shell",
                  "arguments": '{"name":"读取目录","command":"pwd"}'})
        renderer({"type": "text_delta", "text": "部分**正文**"})
        renderer({"type": "error", "message": "Interrupted", "partial_content": [
            {"type": "thinking", "thinking": "检查工作目录"},
            {"type": "text", "text": "部分**正文**"},
        ]})
        self.assertEqual(self.lines()[:4], [
            "▶ 思考", "· shell「读取目录」：已中断", "assistant:", "部分正文",
        ])
        self.assertIn("error: Interrupted", self.lines())
        self.mouse(MouseEventType.MOUSE_UP, 0)
        expanded = self.lines()
        self.assertEqual(expanded[:5], [
            "▼ 思考", "检查工作目录", "· shell「读取目录」：已中断", "assistant:", "部分正文",
        ])
        self.assertEqual(expanded.count("检查工作目录"), 1)
        self.assertEqual(expanded.count("部分正文"), 1)

    def test_reading_history_does_not_jump_to_new_output_and_resize_wraps_chinese(self):
        for number in range(30):
            self.view.append_line(f"消息 {number}", "user")
        self.lines(height=8)
        self.view.scroll(-10)
        position = self.view._scroll
        self.view.write_delta("新的正文" * 20)
        self.lines(width=20, height=8)
        self.assertEqual(self.view._scroll, position)
        self.assertTrue(all(get_cwidth(line) <= 20 for line in self.lines(width=20, height=8)))
        self.view.scroll(1000)
        self.lines(width=20, height=8)
        self.assertEqual(self.view._scroll, self.view._bottom)

    def test_markdown_and_role_colors_are_preserved(self):
        self.view.append_line("user:", "user")
        self.view.append_line("· shell：完成", "tool")
        self.view.append_body("**重点** 与 `code`\n\n- 条目")
        self.assertIn("重点 与 code", self.lines())
        self.assertNotIn("**", "\n".join(self.lines()))
        rows = self.view._lines(60)
        self.assertIn("ansiwhite bg:ansiblack", rows[0][0][0])
        self.assertIn("ansiwhite bg:ansiblack", rows[0][-1][0])
        self.assertIn("ansiblue bg:ansidefault", rows[1][0][0])
        self.assertIn("ansiblue bg:default", rows[1][-1][0])
        self.assertIn("bold", rows[3][0][0])
        self.assertIn("ansiblack bg:ansiwhite", rows[3][0][0])
        self.assertTrue(all(fragment_list_width(row) == 60 for row in rows))
        self.assertEqual(fragment_list_to_text(rows[4]), " " * 60)
        self.assertTrue(all("bg:ansiwhite" in style for style, text in rows[4] if text))

    def test_input_thread_edits_and_returns_submitted_text(self):
        with create_pipe_input() as input:
            view = TerminalView(input=input, output=DummyOutput()).start()
            try:
                input.send_text("你好\x1b[D！\r")
                self.assertEqual(view.prompt(), "你！好")
                self.assertFalse(view.buffer.read_only())
                self.assertIn("你！好", view.buffer.history.get_strings())
            finally:
                view.close()
            self.assertFalse(view.app.is_running)


if __name__ == "__main__":
    unittest.main()
