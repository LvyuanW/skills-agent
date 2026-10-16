"""Deterministic lifecycle races for the transient background-job observer."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from skills_agent.agent import Agent
from skills_agent.processes import BackgroundJobMonitor
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry


class BackgroundJobRaceTests(unittest.TestCase):
    def test_new_turn_replaces_watcher_that_is_finishing_its_idle_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = BackgroundJobMonitor(directory, lambda event: None, interval=0.01)
            pending = [True]
            monitor._snapshot = lambda: ([], pending[0], "now")
            paused, resume = threading.Event(), threading.Event()

            class ExitGate:
                """Pause after the exit decision releases its lock, before finally."""

                def __init__(self):
                    self.lock = threading.RLock()
                    self.used = False

                def __enter__(self):
                    self.lock.acquire()
                    return self

                def __exit__(self, *args):
                    pause = (not self.used
                             and threading.current_thread().name == "skills-agent-background-jobs"
                             and not monitor._turn_active and not monitor._pending)
                    if pause:
                        self.used = True
                    self.lock.release()
                    if pause:
                        paused.set()
                        resume.wait(5)

            monitor._guard = ExitGate()
            old = new = None
            try:
                monitor.start_turn()
                old = monitor._thread
                monitor.end_turn()
                pending[0] = False
                self.assertTrue(paused.wait(3), "Watcher did not reach its idle exit")

                monitor.start_turn()
                new = monitor._thread
                self.assertIsNot(new, old)
                self.assertTrue(new.is_alive())
                resume.set()
                old.join(3)
                self.assertFalse(old.is_alive())
                self.assertIs(monitor._thread, new)
                self.assertTrue(new.is_alive())
            finally:
                resume.set()
                monitor.close()
                if old is not None:
                    old.join(3)
            self.assertIsNone(monitor._thread)
            self.assertFalse(new.is_alive())

    def test_interrupt_after_monitor_start_cleans_scope_and_joins_watcher(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            session = Session(workspace, "interrupt")
            model = Mock(max_output_tokens=1000, model_context_window=None)
            agent = Agent(model, session, SkillRegistry([]), workspace)
            original_start = agent._background_jobs.start_turn
            started_threads = []

            def interrupted_start():
                original_start()
                started_threads.append(agent._background_jobs._thread)
                raise KeyboardInterrupt

            try:
                with patch.object(agent._background_jobs, "start_turn", side_effect=interrupted_start):
                    with self.assertRaises(KeyboardInterrupt):
                        agent.run(session.make_user_message("request", []))
                self.assertEqual(len(started_threads), 1)
                self.assertFalse(started_threads[0].is_alive())
                self.assertIsNone(agent._background_jobs._thread)
                self.assertFalse(agent._background_jobs._turn_active)
                self.assertEqual(agent._process_scopes, [])
                self.assertEqual(len(list((session.path / "process-scopes").glob("*/closed"))), 1)
                self.assertIsNone(agent.process_scope)
                self.assertIsNone(agent.tools.process_scope)
                model.complete.assert_not_called()
            finally:
                agent.close()


if __name__ == "__main__":
    unittest.main()
