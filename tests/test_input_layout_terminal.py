"""Exercise the input frame in a real PTY without external model requests."""

import os
import signal
import struct
import unittest

from tests import test_cli_terminal as fixtures

if os.name == "posix":
    import fcntl
    import termios


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class InputLayoutTerminalTests(unittest.TestCase):
    setUp = fixtures.TerminalTests.setUp
    server = fixtures.TerminalTests.server
    response = fixtures.TerminalTests.response
    terminal = fixtures.TerminalTests.terminal

    def frame(self, terminal):
        """Find the input by its cursor and surrounding rules, not fixed rows."""
        lines = terminal.screen.display
        cursor = terminal.screen.cursor
        rules = [index for index, line in enumerate(lines)
                 if len(line.strip()) == terminal.screen.columns - 2
                 and set(line.strip()) == {"─"}]
        above = [index for index in rules if index < cursor.y]
        below = [index for index in rules if index > cursor.y]
        self.assertTrue(above and below, terminal.visible)
        top, bottom = max(above), min(below)
        footers = [index for index, line in enumerate(lines) if "F2" in line]
        self.assertEqual(len(footers), 1, terminal.visible)
        self.assertGreater(footers[0], bottom, terminal.visible)
        self.assertLess(bottom, terminal.screen.lines - 1, terminal.visible)
        self.assertGreaterEqual(cursor.x, 1)
        self.assertLess(cursor.x, terminal.screen.columns - 1)
        return top, bottom

    def assert_padding(self, terminal):
        top, bottom = self.frame(terminal)
        lines = terminal.screen.display
        self.assertFalse(lines[top + 1].strip(), terminal.visible)
        self.assertFalse(lines[bottom - 1].strip(), terminal.visible)
        for row in range(top + 1, bottom):
            self.assertEqual(terminal.screen.buffer[row][0].data, " ")
            self.assertEqual(terminal.screen.buffer[row][terminal.screen.columns - 1].data, " ")
        return top, bottom

    def resize(self, terminal, rows, columns):
        before = len(terminal.output)
        terminal.screen.resize(lines=rows, columns=columns)
        fcntl.ioctl(terminal.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        terminal.process.send_signal(signal.SIGWINCH)
        terminal.wait_for(lambda output: len(terminal.output) > before
                          and any("F2" in line for line in terminal.screen.display)
                          and sum(len(line.strip()) == columns - 2 and set(line.strip()) == {"─"}
                                  for line in terminal.screen.display) >= 2)

    def test_input_has_padding_rules_and_default_terminal_colors(self):
        terminal, requests = self.terminal(lambda payload, count: self.response("UNUSED"))
        terminal.send("layout draft")
        terminal.wait_for(lambda output: "layout draft" in terminal.visible)
        self.assert_padding(terminal)
        row = terminal.screen.cursor.y
        self.assertTrue(terminal.screen.display[row].startswith(" > layout draft"), terminal.visible)
        # The editor inherits the user's terminal theme; no hardcoded black box.
        cell = terminal.screen.buffer[row][3]
        self.assertEqual(cell.data, "l")
        self.assertEqual((cell.fg, cell.bg), ("default", "default"))
        self.assertEqual(requests, [])

    def test_narrow_multiline_input_wraps_and_cursor_edits_submit_exact_text(self):
        terminal, requests = self.terminal(lambda payload, count: self.response("LAYOUT_EDIT_DONE"))
        self.resize(terminal, 24, 32)
        message = "0123456789" * 5 + "END\nsecond line\nlastline"
        terminal.send("\x1b[200~" + message + "\x1b[201~")
        terminal.wait_for(lambda output: "lastline" in terminal.visible)
        top, bottom = self.assert_padding(terminal)
        content_rows = [line for line in terminal.screen.display[top + 1:bottom] if line.strip()]
        self.assertGreaterEqual(len(content_rows), 4, terminal.visible)
        self.assertEqual(requests, [])
        terminal.send("\x01edited-\x05\x7f!")
        terminal.wait_for(lambda output: "edited-lastlin!" in terminal.visible)
        terminal.send("\n")
        terminal.prompt("LAYOUT_EDIT_DONE")
        expected = message.removesuffix("lastline") + "edited-lastlin!"
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": expected}])
        self.assert_padding(terminal)

    def test_short_terminal_keeps_draft_and_footer_visible_then_restores_padding(self):
        terminal, requests = self.terminal(lambda payload, count: self.response("RESIZE_DONE"))
        message = "0123456789" * 4 + "\nTAIL"
        terminal.send("\x1b[200~" + message + "\x1b[201~")
        terminal.wait_for(lambda output: "TAIL" in terminal.visible)
        self.resize(terminal, 12, 28)
        terminal.wait_for(lambda output: "TAIL" in terminal.visible)
        self.frame(terminal)
        self.assertEqual(requests, [])
        terminal.send("!")
        terminal.wait_for(lambda output: "TAIL!" in terminal.visible)
        self.resize(terminal, 24, 80)
        terminal.wait_for(lambda output: "TAIL!" in terminal.visible)
        self.assert_padding(terminal)
        terminal.send("\n")
        terminal.prompt("RESIZE_DONE")
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": message + "!"}])


if __name__ == "__main__":
    unittest.main()
