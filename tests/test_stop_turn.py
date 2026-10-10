import os
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import DummyInput
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import _interact
from skills_agent.input import ClipboardDraft
from skills_agent.session import Session
from skills_agent.terminal import TerminalView


class StopTurnTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"TERM": "xterm-256color"})
        environment.start()
        self.addCleanup(environment.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())

    def mouse(self, handler, kind):
        self.view._clear_hover(None)
        return handler(MouseEvent(position=Point(0, 0), event_type=kind,
                                  button=MouseButton.LEFT, modifiers=frozenset()))

    def test_stop_hover_single_interrupt_and_stale_button_cannot_stop_next_turn(self):
        self.assertNotIn("停止", fragment_list_to_text(self.view._turn_controls()))
        self.view.start_turn()
        button = self.view._turn_controls()[-1]
        self.assertEqual(button[1], "停止")
        self.assertIn("ansiblue", button[0])
        self.mouse(button[2], MouseEventType.MOUSE_MOVE)
        self.assertIn("ansired", self.view._turn_controls()[-1][0])
        self.view._clear_hover(None)
        self.assertIn("ansiblue", self.view._turn_controls()[-1][0])

        with patch("skills_agent.terminal.os.kill") as kill:
            self.mouse(button[2], MouseEventType.MOUSE_UP)
            self.assertEqual(self.view._turn_controls()[-1][1], "停止中")
            self.mouse(button[2], MouseEventType.MOUSE_UP)
            self.view.stop_turn()
            kill.assert_called_once_with(os.getpid(), signal.SIGINT)
            self.view.finish_turn()
            self.view.stop_turn()
            self.assertNotIn("停止", fragment_list_to_text(self.view._turn_controls()))
            self.view.start_turn()
            self.mouse(button[2], MouseEventType.MOUSE_UP)
            self.assertEqual(kill.call_count, 1)
            self.view.stop_turn()
            self.assertEqual(kill.call_count, 2)

    def test_selection_freezes_stop_control_without_allowing_stale_clicks(self):
        self.view.start_turn()
        controls = self.view._turn_controls()
        button = controls[-1]
        self.view._selecting = True
        with patch("skills_agent.terminal.os.kill") as kill:
            self.mouse(button[2], MouseEventType.MOUSE_UP)
            kill.assert_not_called()
            self.view.finish_turn()
            self.assertEqual(self.view._turn_controls(), controls)
        self.view._selecting = False
        self.assertNotIn("停止", fragment_list_to_text(self.view._turn_controls()))

    def test_interruption_during_preparation_or_final_redraw_keeps_queued_turn(self):
        for stage in ("preparation", "finish"):
            with self.subTest(stage=stage), TemporaryDirectory() as directory:
                workspace = Path(directory)
                view = TerminalView(input=DummyInput(), output=DummyOutput())
                draft = ClipboardDraft(workspace / "clipboard", on_error=self.fail)
                session = Session(workspace, "main")
                agent = SimpleNamespace(workspace=workspace, session=session, run=Mock())
                for text in ("stop this", "queued message", "/exit"):
                    view.buffer.text = text
                    view.buffer.validate_and_handle()
                if stage == "preparation":
                    original = session.make_user_message
                    calls = 0

                    def prepare(*args):
                        nonlocal calls
                        calls += 1
                        if calls == 1:
                            raise KeyboardInterrupt
                        return original(*args)

                    target = patch.object(session, "make_user_message", side_effect=prepare)
                else:
                    original = view.finish_turn
                    calls = 0

                    def finish():
                        nonlocal calls
                        calls += 1
                        if calls == 1:
                            raise KeyboardInterrupt
                        return original()

                    target = patch.object(view, "finish_turn", side_effect=finish)
                with target:
                    self.assertEqual(_interact(agent, [], draft, read_input=view.prompt, output=view), 0)
                self.assertEqual(agent.run.call_args.args[0]["content"][0]["text"], "queued message")
                self.assertIsNone(view._turn_id)
                self.assertIsNone(view._turn_started)


if __name__ == "__main__":
    unittest.main()
