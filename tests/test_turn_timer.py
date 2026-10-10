import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import _interact
from skills_agent.input import ClipboardDraft
from skills_agent.session import Session
from skills_agent.terminal import TerminalView


class TurnTimerTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"TERM": "xterm-256color"})
        environment.start()
        self.addCleanup(environment.stop)
        timer = patch("skills_agent.terminal.monotonic", return_value=100.0)
        self.clock = timer.start()
        self.addCleanup(timer.stop)
        self.view = TerminalView(input=DummyInput(), output=DummyOutput())

    def submit(self, text):
        # Exercise the real submit/queue path without starting an input thread.
        self.view._busy = False
        self.view.buffer.text = text
        self.view._submit(self.view.buffer)
        return self.view.prompt()

    def test_whole_turn_includes_submission_delay_model_responses_and_tool_work(self):
        self.assertEqual(self.view._turn_status(), "")
        self.submit("inspect the project")
        self.clock.return_value = 102.0
        self.view.start_turn()
        self.view.write_delta("checking", channel="thinking")
        self.clock.return_value = 105.0
        self.view.finish_response()
        self.view.set_activity("shell: checking")
        self.clock.return_value = 109.0
        self.view.set_activity(None)
        self.view.write_delta("checked")
        self.view.finish_response()
        self.clock.return_value = 112.3
        self.assertEqual(self.view._turn_status(), "耗时 12.3 秒")

    def test_completion_freezes_elapsed_and_next_submission_starts_a_new_turn(self):
        self.submit("first task")
        self.view.start_turn()
        self.clock.return_value = 107.5
        self.view.finish_turn()
        self.clock.return_value = 300.0
        self.assertEqual(self.view._turn_status(), "耗时 7.5 秒")
        self.view.finish_turn()
        self.assertEqual(self.view._turn_status(), "耗时 7.5 秒")

        self.submit("second task")
        self.clock.return_value = 302.0
        self.view.start_turn()
        self.clock.return_value = 303.0
        self.assertEqual(self.view._turn_status(), "耗时 3.0 秒")
        self.view.finish_turn()
        self.clock.return_value = 900.0
        self.assertEqual(self.view._turn_status(), "耗时 3.0 秒")

    def test_selection_freezes_display_but_counts_work_until_completion(self):
        self.submit("task")
        self.view.start_turn()
        self.clock.return_value = 103.0
        before_selection = self.view._turn_status()
        self.assertEqual(before_selection, "耗时 3.0 秒")
        self.view._selecting = True
        self.clock.return_value = 111.0
        self.assertEqual(self.view._turn_status(), before_selection)
        self.view.finish_turn()
        self.clock.return_value = 200.0
        self.assertEqual(self.view._turn_status(), before_selection)
        self.view._selecting = False
        self.assertEqual(self.view._turn_status(), "耗时 11.0 秒")

    def test_cli_finishes_timer_after_success_error_and_keyboard_interrupt(self):
        for failure in (None, ValueError("failed"), KeyboardInterrupt()):
            with self.subTest(outcome=type(failure).__name__), TemporaryDirectory() as directory:
                self.view = TerminalView(input=DummyInput(), output=DummyOutput())
                self.clock.return_value = 100.0
                workspace = Path(directory)
                draft = ClipboardDraft(workspace / "clipboard", on_error=self.fail)
                prompts = iter(("perform task", "/exit"))
                finished = []

                def read_input():
                    prompt = next(prompts)
                    if prompt == "/exit":
                        self.clock.return_value = 500.0
                        finished.append(self.view._turn_status())
                    return self.submit(prompt)

                def run(message):
                    self.assertEqual(self.view._turn_status(), "耗时 0.0 秒")
                    self.clock.return_value = 105.0
                    self.view.finish_response()
                    if failure is not None:
                        raise failure

                agent = SimpleNamespace(workspace=workspace, session=Session(workspace, "main"), run=run)
                self.assertEqual(_interact(agent, [], draft, read_input=read_input, output=self.view), 0)
                self.assertEqual(finished, ["耗时 5.0 秒"])
                self.assertEqual(self.view._turn_status(), "耗时 5.0 秒")

    def test_empty_input_and_file_commands_do_not_start_a_request_timer(self):
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "note.txt").write_text("attachment")
            draft = ClipboardDraft(workspace / "clipboard", on_error=self.fail)
            agent = SimpleNamespace(workspace=workspace, session=Session(workspace, "main"), run=Mock())
            prompts = iter(("", "   ", "/file", "/file missing.txt", "/file note.txt", "/exit"))
            statuses = []

            def read_input():
                self.clock.return_value += 10.0
                statuses.append(self.view._turn_status())
                return self.submit(next(prompts))

            self.assertEqual(_interact(agent, [], draft, read_input=read_input, output=self.view), 0)
            agent.run.assert_not_called()
            self.assertEqual(statuses, [""] * 6)
            self.assertEqual(self.view._turn_status(), "")

    def test_attachment_preparation_displays_elapsed_and_failure_finishes_the_turn(self):
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            attachment = workspace / "note.txt"
            attachment.write_text("attachment")
            draft = ClipboardDraft(workspace / "clipboard", on_error=self.fail)
            agent = SimpleNamespace(workspace=workspace, session=Session(workspace, "main"), run=Mock())
            prompts = iter(("inspect the attachment", "/exit"))
            preparing = []
            finished = []

            def read_input():
                prompt = next(prompts)
                if prompt == "/exit":
                    self.clock.return_value = 500.0
                    finished.append(self.view._turn_status())
                return self.submit(prompt)

            def fail_copy(source, destination):
                # Called inside the real Session.make_user_message, before Agent.run.
                preparing.append(self.view._turn_status())
                self.clock.return_value = 104.2
                preparing.append(self.view._turn_status())
                raise OSError("attachment copy failed")

            with patch("skills_agent.session.shutil.copyfile", side_effect=fail_copy):
                self.assertEqual(_interact(agent, [attachment], draft,
                                           read_input=read_input, output=self.view), 0)
            self.assertEqual(preparing, ["耗时 0.0 秒", "耗时 4.2 秒"])
            self.assertEqual(finished, ["耗时 4.2 秒"])
            self.assertEqual(self.view._turn_status(), "耗时 4.2 秒")
            agent.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
