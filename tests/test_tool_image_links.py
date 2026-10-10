import copy
import io
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import DummyInput
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer, render_event
from skills_agent.session import Session
from skills_agent.terminal import TerminalView


def tool_event(*images):
    return {"type": "message", "message": {
        "role": "tool", "name": "shell", "task_name": "检查页面", "tool_call_id": "capture-1",
        "content": [{"type": "text", "text": json.dumps({
            "exit_code": 0, "stdout": "rendered", "stderr": "",
        })}, *images],
    }}


class ToolImageDisplayTests(unittest.TestCase):
    def setUp(self):
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())

    def test_compact_and_full_views_use_original_image_paths(self):
        event = tool_event(
            {"type": "image", "source_path": "/original/first.png", "path": "/snapshot/first.png"},
            {"type": "image", "path": "/legacy/second.png"},
        )
        original = copy.deepcopy(event)
        for full in (False, True):
            with self.subTest(full=full), patch.object(self.view, "append_image_link") as append:
                render_event(event, self.view, full=full)
                self.assertEqual(append.call_args_list, [
                    call("[图片1]", "/original/first.png"),
                    call("[图片2]", "/legacy/second.png"),
                ])
                self.assertEqual(event, original)

    def test_plain_history_lists_images_below_tool_result_without_reading_files(self):
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.png"
            snapshot.write_bytes(b"saved image")
            missing_source = str(Path(directory) / "removed source.png")
            event = tool_event({"type": "image", "source_path": missing_source,
                                "path": str(snapshot)})
            for full in (False, True):
                with self.subTest(full=full):
                    output = io.StringIO()
                    render_event(event, output, full=full)
                    lines = output.getvalue().splitlines()
                    self.assertEqual(lines[-1], f"  [图片] {missing_source}")
                    self.assertNotIn(str(snapshot), output.getvalue())
                    self.assertTrue(lines[0].startswith("tool shell:" if full else "· shell"))

    def test_multiline_plain_history_numbers_only_image_attachments(self):
        event = tool_event(
            {"type": "image", "source_path": "/original/a.png", "path": "/snapshot/a.png"},
            {"type": "file", "path": "/report.txt"},
            {"type": "image", "path": "/legacy/b.png"},
        )
        output = io.StringIO()
        render_event(event, output)
        self.assertEqual(output.getvalue().splitlines()[-2:], [
            "  [图片1] /original/a.png", "  [图片2] /legacy/b.png",
        ])

    def test_clipboard_user_and_assistant_images_never_become_tool_links(self):
        image = {"type": "image", "marker": "[图片1]", "path": "/clipboard.png"}
        for role in ("user", "assistant"):
            event = {"type": "message", "message": {"role": role, "content": [
                {"type": "text", "text": "看看 [图片1]"}, image,
            ]}}
            for full in (False, True):
                with self.subTest(role=role, full=full), patch.object(self.view, "append_image_link") as append:
                    render_event(event, self.view, full=full)
                    append.assert_not_called()

    def test_session_replay_preserves_saved_event_and_displays_one_link(self):
        with TemporaryDirectory() as directory:
            session = Session(Path(directory), "pictures")
            session.append_event(tool_event({"type": "image", "source_path": "/source.png",
                                             "path": "/immutable.png"}))
            log = session.path / "events.jsonl"
            saved = log.read_bytes()
            events = session.events()
            original = copy.deepcopy(events)
            with patch.object(self.view, "append_image_link") as append:
                renderer = ConsoleRenderer(self.view, live=False)
                for event in events:
                    renderer(event)
                renderer.close()
                append.assert_called_once_with("[图片]", "/source.png")
            self.assertEqual(events, original)
            self.assertEqual(log.read_bytes(), saved)


class ToolImageInteractionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"TERM": "xterm-256color"})
        environment.start()
        self.addCleanup(environment.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())

    def rows(self, width=60, height=20):
        content = self.view.control.create_content(width, height)
        return [content.get_line(index) for index in range(content.line_count)]

    def mouse(self, row, column=2, *, event_type=MouseEventType.MOUSE_UP, button=MouseButton.LEFT):
        return self.view.control.mouse_handler(MouseEvent(
            position=Point(x=column, y=row), event_type=event_type, button=button, modifiers=frozenset(),
        ))

    def label_location(self, label="[图片]", width=60, height=20):
        for row, fragments in enumerate(self.rows(width, height)):
            text = fragment_list_to_text(fragments)
            if label in text:
                return row, text.index(label)
        self.fail(f"missing visible label: {label}")

    def test_only_left_release_on_image_label_opens_original_path(self):
        self.view.append_line("· shell：完成", "tool")
        self.view.append_image_link("[图片]", "/source with spaces.png")
        row, column = self.label_location()
        rows = self.rows()
        styles = [style for style, text, *rest in rows[row] if any(char in text for char in "图片")]
        self.assertTrue(styles)
        self.assertTrue(all("underline" in style and "blue" in style for style in styles))
        with patch.object(self.view, "open_image") as open_image:
            self.mouse(row, column, event_type=MouseEventType.MOUSE_DOWN)
            self.mouse(row, column, button=MouseButton.RIGHT)
            self.mouse(row, column + len("[图片]"))
            self.mouse(row, 50)
            if column:
                self.mouse(row, column - 1)
            self.mouse(0, 2)
            open_image.assert_not_called()
            self.mouse(row, column + 1)
            open_image.assert_called_once_with("/source with spaces.png")

    def test_plain_text_that_looks_like_an_image_label_is_not_clickable(self):
        self.view.append_line("user: [图片]", "user")
        row, column = self.label_location()
        with patch.object(self.view, "open_image") as open_image:
            self.mouse(row, column)
            open_image.assert_not_called()

    def test_click_targets_follow_resize_and_thinking_expansion(self):
        self.view.append_body("长思考 " * 30, channel="thinking")
        self.view.append_line("· shell：" + "说明 " * 20, "tool")
        self.view.append_image_link("[图片]", "/result.png")
        initial_row, initial_column = self.label_location(width=80)
        resized_row, resized_column = self.label_location(width=14)
        self.assertGreater(resized_row, initial_row)
        with patch.object(self.view, "open_image") as open_image:
            self.mouse(initial_row, initial_column)
            open_image.assert_not_called()
            self.mouse(resized_row, resized_column)
            open_image.assert_called_once_with("/result.png")
            open_image.reset_mock()
            self.mouse(0)
            expanded_row, expanded_column = self.label_location(width=14)
            self.assertGreater(expanded_row, resized_row)
            self.mouse(resized_row, resized_column)
            open_image.assert_not_called()
            self.mouse(expanded_row, expanded_column)
            open_image.assert_called_once_with("/result.png")

    def test_wrapped_label_fragments_are_clickable_without_their_padding(self):
        self.view.append_image_link("[图片123456789]", "/wrapped.png")
        rows = self.rows(width=7)
        targets = []
        padding = []
        for row, fragments in enumerate(rows):
            column = 0
            for style, text, *rest in fragments:
                if "underline" in style and text.strip():
                    targets.append((row, column + next(i for i, value in enumerate(text) if not value.isspace())))
                elif text.strip() == "" and text:
                    padding.append((row, column))
                column += len(text)
        self.assertGreater(len({row for row, _ in targets}), 1)
        with patch.object(self.view, "open_image") as open_image:
            for row, column in padding:
                self.mouse(row, column)
            open_image.assert_not_called()
            for row, column in targets:
                self.mouse(row, column)
            self.assertEqual(open_image.call_args_list, [call("/wrapped.png")] * len(targets))

    def test_scroll_uses_transcript_rows_and_does_not_open_images(self):
        for number in range(20):
            self.view.append_line(f"记录 {number}", "tool")
        self.view.append_image_link("[图片]", "/scrolled.png")
        row, column = self.label_location(height=5)
        self.assertGreater(self.view._scroll, 0)
        with patch.object(self.view, "open_image") as open_image:
            self.mouse(row, column, event_type=MouseEventType.SCROLL_UP)
            self.rows(height=5)
            self.mouse(row, column, event_type=MouseEventType.SCROLL_DOWN)
            self.rows(height=5)
            open_image.assert_not_called()
            self.mouse(row, column)
            open_image.assert_called_once_with("/scrolled.png")

    def test_selection_mode_freezes_display_and_never_activates_image(self):
        self.view.append_image_link("[图片]", "/first.png")
        row, column = self.label_location()
        original = [fragment_list_to_text(row) for row in self.rows()]
        self.view._selecting = True
        self.view.append_image_link("[图片2]", "/second.png")
        with patch.object(self.view, "open_image") as open_image:
            self.assertEqual([fragment_list_to_text(row) for row in self.rows()], original)
            self.mouse(row, column)
            open_image.assert_not_called()
            self.view._selecting = False
            row, column = self.label_location("[图片2]")
            self.mouse(row, column)
            open_image.assert_called_once_with("/second.png")

    def test_system_opener_receives_exact_path_as_a_single_argument(self):
        path = "/images/a space;$(literal).png"
        for platform, executable in (("darwin", "open"), ("linux", "xdg-open")):
            with self.subTest(platform=platform), patch("skills_agent.terminal.sys.platform", platform), \
                    patch("skills_agent.terminal.subprocess.run") as run, \
                    patch("skills_agent.terminal.Thread", side_effect=self.immediate_thread):
                self.view.open_image(path)
                self.assertEqual(run.call_args.args[0], [executable, path])
                self.assertFalse(run.call_args.kwargs.get("shell", False))
        with patch("skills_agent.terminal.sys.platform", "win32"), \
                patch("skills_agent.terminal.os.startfile", create=True) as startfile, \
                patch("skills_agent.terminal.Thread", side_effect=self.immediate_thread):
            self.view.open_image(path)
            startfile.assert_called_once_with(path)

    def test_failed_opener_leaves_image_link_available_and_reports_failure(self):
        self.view.append_image_link("[图片]", "/missing.png")
        for failure in (FileNotFoundError("open unavailable"),
                        subprocess.CalledProcessError(1, ["open", "/missing.png"])):
            with self.subTest(failure=type(failure).__name__), patch("skills_agent.terminal.sys.platform", "darwin"), \
                    patch("skills_agent.terminal.subprocess.run", side_effect=failure), \
                    patch("skills_agent.terminal.Thread", side_effect=self.immediate_thread):
                row, column = self.label_location()
                self.mouse(row, column)
                text = "\n".join(fragment_list_to_text(row) for row in self.rows())
                self.assertIn("[图片]", text)
                self.assertIn("失败", text)

    @staticmethod
    def immediate_thread(*args, **kwargs):
        return SimpleNamespace(start=lambda: kwargs["target"](
            *kwargs.get("args", ()), **kwargs.get("kwargs", {})))


if __name__ == "__main__":
    unittest.main()
