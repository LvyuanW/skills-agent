"""Tool image links through a real terminal, including wide-character hit areas."""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from skills_agent.session import Session
from tests import test_cli_terminal as terminal_helpers


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class ToolImageLinkTerminalTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp

    def test_replayed_tool_image_opens_saved_snapshot_after_original_is_removed(self):
        source = self.workspace / "原始 截图.png"
        snapshot = self.workspace / "snapshot.png"
        opened = self.workspace / "opened.jsonl"
        session = Session(self.storage.sessions, "terminal")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(b"saved image")
        source.write_bytes(b"original image")
        source.unlink()
        session.append({"role": "user", "content": "Inspect the saved page."})
        session.append({"role": "assistant", "content": [], "tool_calls": [{
            "id": "capture-1", "name": "shell",
            "arguments": json.dumps({"name": "截取页面", "command": "capture"}),
        }]})
        session.append({"role": "tool", "name": "shell", "task_name": "截取页面",
                        "tool_call_id": "capture-1", "content": [
            {"type": "text", "text": json.dumps({"exit_code": 0, "stdout": "", "stderr": ""})},
            {"type": "image", "source_path": str(source), "path": str(snapshot),
             "mime_type": "image/png"},
        ]})
        session.append({"role": "assistant", "content": [{"type": "text", "text": "SAVED_RESULT"}]})
        before = (session.path / "events.jsonl").read_bytes()

        # Keep the real click handler and background opener; stub only the OS
        # command in the child, so this test never opens a GUI or needs an image.
        startup = f"""
import json, runpy, subprocess
from pathlib import Path
import skills_agent.terminal as terminal
original_run = subprocess.run
def record_open(arguments, *args, **kwargs):
    if arguments[0] in {{'open', 'xdg-open'}}:
        with Path({str(opened)!r}).open('a') as stream:
            stream.write(json.dumps(arguments) + '\\n')
        return subprocess.CompletedProcess(arguments, 0)
    return original_run(arguments, *args, **kwargs)
terminal.subprocess.run = record_open
runpy.run_module('skills_agent', run_name='__main__')
"""
        original_popen = subprocess.Popen

        def launch(arguments, **kwargs):
            return original_popen([arguments[0], "-c", startup, *arguments[3:]], **kwargs)

        with patch.object(terminal_helpers.subprocess, "Popen", side_effect=launch):
            terminal = terminal_helpers.Terminal(self.workspace, "http://127.0.0.1:1/v1")
        self.addCleanup(terminal.close)
        terminal.prompt("SAVED_RESULT")
        self.assertIn("\x1b[?1000h", terminal.text)

        def label_cells():
            for y in range(terminal.screen.lines):
                row = terminal.screen.buffer[y]
                for x in range(terminal.screen.columns):
                    if "".join(row[c].data for c in range(x, terminal.screen.columns)).startswith("[图片]"):
                        end = next(c for c in range(x + 1, terminal.screen.columns) if row[c].data == "]")
                        return x, end, y
            self.fail(f"Image link is not visible:\n{terminal.visible}")

        def click(x, y):
            terminal.send(f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m")

        start, end, row = label_cells()
        self.assertIn("截取页面", terminal.screen.display[row - 1])
        self.assertEqual(terminal.screen.buffer[row][start].fg, "blue")
        self.assertTrue(terminal.screen.buffer[row][start].underscore)
        click(start - 1, row)
        click(end + 1, row)
        # The final bracket follows two double-width Chinese characters.
        # This checks the real terminal-cell to transcript-character mapping.
        click(end, row)
        terminal.wait_for(lambda output: opened.exists() and opened.read_text().endswith("\n"))
        terminal.send('/file "missing image.png"\n')
        terminal.prompt("error:")
        terminal.send("/exit\n")
        terminal.wait_exit()

        calls = [json.loads(line) for line in opened.read_text().splitlines()]
        self.assertEqual([call[1:] for call in calls], [[str(snapshot)]])
        self.assertEqual(terminal.process.returncode, 0)
        self.assertEqual((session.path / "events.jsonl").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
