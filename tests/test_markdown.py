from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pyte

from skills_agent.cli import ConsoleRenderer, main
from skills_agent.session import Session


def terminal_screen(text, *, width=80, height=24):
    screen = pyte.HistoryScreen(width, height, history=10000)
    # PTYs translate LF to CRLF; StringIO-backed terminal tests need the same behavior.
    pyte.Stream(screen).feed(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    return screen


def terminal_lines(text, *, width=80, height=24):
    screen = terminal_screen(text, width=width, height=height)
    rows = [*screen.history.top, *(screen.buffer[y] for y in range(screen.lines))]
    return ["".join(row[x].data for x in range(screen.columns)).rstrip() for row in rows]


def terminal_text(text, *, width=80, height=24):
    return "\n".join(terminal_lines(text, width=width, height=height)).rstrip()


class Output(io.StringIO):
    def isatty(self):
        return True


def completed(text):
    return {"type": "message", "streamed": True,
            "message": {"role": "assistant", "content": text}}


class MarkdownTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "TERM": "xterm-256color", "COLUMNS": "80", "LINES": "24",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_markdown_split_across_chunks_renders_and_keeps_role_colors(self):
        chunks = [
            "**bold", " words** and `inline", "_code`\n\n",
            "- first item\n- second item\n\n",
            "```py", "thon\nprint('script value')\n", "```\n\n",
            "| Name | Value |\n| --- | --- |\n| row | cell |\n\n",
            "[Documentation](https://example.com", "/docs)\n",
        ]
        output = Output()
        renderer = ConsoleRenderer(output)
        for chunk in chunks:
            renderer({"type": "text_delta", "text": chunk})
        renderer(completed("".join(chunks)))
        rendered = terminal_text(output.getvalue())
        for expected in ("bold words and inline_code", "first item", "second item",
                         "print('script value')", "Name", "Value", "row", "cell", "Documentation"):
            self.assertIn(expected, rendered)
        for literal in ("**", "`", "[Documentation]", "--- |"):
            self.assertNotIn(literal, rendered)
        self.assertEqual(rendered.count("bold words"), 1)
        screen = terminal_screen(output.getvalue())
        rows = [*screen.history.top, *(screen.buffer[y] for y in range(screen.lines))]
        body = next(row for row in rows if "bold words" in "".join(row[x].data for x in range(80)))
        self.assertTrue(body[0].bold)
        self.assertEqual((body[0].fg, body[0].bg), ("black", "brightwhite"))
        self.assertIn("https://example.com/docs", output.getvalue())

    def test_first_chunk_is_visible_before_completion(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "first **visible** chunk"})
        self.assertIn("first visible chunk", terminal_text(output.getvalue()))
        renderer({"type": "text_delta", "text": " and final"})
        self.assertIn("first visible chunk and final", terminal_text(output.getvalue()))
        renderer.close()
        self.assertEqual(terminal_text(output.getvalue()).count("first visible chunk"), 1)

    def test_body_tool_body_keeps_arrival_order_in_live_and_replay(self):
        events = [
            {"type": "text_delta", "text": "**before tool**"},
            {"type": "tool_call_delta", "index": 0, "name": "shell",
             "arguments": json.dumps({"command": "PRIVATE_COMMAND", "summary": "run script"})},
            {"type": "text_delta", "text": "**after tool**"},
            completed("**before tool****after tool**"),
        ]
        original = copy.deepcopy(events)
        for live in (True, False):
            with self.subTest(live=live):
                output = Output()
                renderer = ConsoleRenderer(output, live=live)
                for event in events:
                    renderer(event)
                rendered = terminal_text(output.getvalue())
                ordered = ["before tool", "shell：run script", "after tool"]
                self.assertEqual([rendered.index(item) for item in ordered],
                                 sorted(rendered.index(item) for item in ordered))
                for item in ordered:
                    self.assertEqual(rendered.count(item), 1)
                self.assertNotIn("PRIVATE_COMMAND", rendered)
        self.assertEqual(events, original)

    def test_long_stream_leaves_every_line_once_in_small_terminal_scrollback(self):
        with patch.dict(os.environ, {"COLUMNS": "40", "LINES": "7"}):
            output = Output()
            renderer = ConsoleRenderer(output)
            for index in range(32):
                renderer({"type": "text_delta", "text": f"ROW-{index:03} **value**\n\n"})
            renderer.close()
        lines = terminal_lines(output.getvalue(), width=40, height=7)
        self.assertEqual([line for line in lines if line.startswith("ROW-")],
                         [f"ROW-{index:03} value" for index in range(32)])
        self.assertEqual(lines.count("assistant:"), 1)

    def test_interrupted_markdown_is_finalized_once_and_next_answer_can_render(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        partial = "**received** `partial`"
        renderer({"type": "text_delta", "text": partial})
        renderer({"type": "error", "message": "Interrupted", "partial_content": [
            {"type": "text", "text": partial},
        ]})
        renderer({"type": "text_delta", "text": "**next answer**"})
        renderer(completed("**next answer**"))
        rendered = terminal_text(output.getvalue())
        self.assertEqual(rendered.count("received partial"), 1)
        self.assertEqual(rendered.count("next answer"), 1)
        self.assertLess(rendered.index("received partial"), rendered.index("error: Interrupted"))
        self.assertLess(rendered.index("error: Interrupted"), rendered.index("next answer"))

    def test_history_renders_markdown_without_changing_log_and_pipe_keeps_source(self):
        body = "**original** `source`\n\n- item"
        with TemporaryDirectory() as directory:
            session = Session(Path(directory) / ".agent/sessions", "default")
            session.append_event({"type": "text_delta", "text": body})
            session.append_event(completed(body))
            log = session.path / "events.jsonl"
            before = log.read_bytes()
            for full in (False, True):
                command = ["history", "--workspace", directory] + (["--full"] if full else [])
                output = Output()
                with redirect_stdout(output):
                    self.assertEqual(main(command), 0)
                rendered = terminal_text(output.getvalue())
                self.assertIn("original source", rendered)
                self.assertNotIn("**original**", rendered)
                self.assertEqual(rendered.count("original source"), 1)
                plain = io.StringIO()
                with redirect_stdout(plain):
                    self.assertEqual(main(command), 0)
                self.assertEqual(plain.getvalue(), "assistant:\n" + body + "\n")
                self.assertEqual(log.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
