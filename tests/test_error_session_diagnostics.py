"""Persist provider diagnostics without expanding public error messages."""

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from skills_agent.agent import Agent
from skills_agent.cli import render_event
from skills_agent.context.auxiliary import Auxiliary
from skills_agent.providers.base import ProviderConnectionError, ProviderError
from skills_agent.providers.openai import OpenAIProvider
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry


class Model:
    model_context_window = None
    max_output_tokens = 32

    def __init__(self):
        self.count_tokens = Mock(return_value=100)
        self.count_text = Mock(return_value=10)
        self.complete = Mock(return_value={"role": "assistant", "content": "Saved summary"})


def connection_error(number=1):
    return ProviderConnectionError("API connection failed", diagnostics={
        "exception_type": "TimeoutError", "message": "The read operation timed out",
        "endpoint": "https://api.example.test/v1/messages", "request_id": f"request-{number}",
    })


def http_error(status=503):
    return ProviderError(f"API HTTP error {status}", diagnostics={
        "response": {"status": status, "body": '{"error":{"message":"Upstream unavailable"}}'},
        "request_id": "request-http",
    })


class ErrorSessionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        sleeper = patch("skills_agent.providers.retry.time.sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = Session(self.root, "main")
        self.model = Model()
        self.agent = Agent(self.model, self.session, SkillRegistry([]), self.root)

    def read_events(self, session=None):
        path = (session or self.session).path / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def private_events(self):
        return [json.loads(line) for path in self.session.path.glob("subagents/*/events.jsonl")
                for line in path.read_text().splitlines()]

    def run_failure(self, failure):
        if failure.retryable:
            # Stop in the retry wait, then inspect the preserved failed attempt.
            self.sleep.side_effect = KeyboardInterrupt()
            try:
                with self.assertRaises(KeyboardInterrupt) as raised:
                    self.agent.run({"role": "user", "content": "Continue the task"})
            finally:
                self.sleep.side_effect = None
            return raised.exception, [e for e in self.read_events() if e["type"] == "retry"][-1]
        with self.assertRaises(type(failure)) as raised:
            self.agent.run({"role": "user", "content": "Continue the task"})
        return raised.exception, self.read_events()[-1]

    def assert_public_message_only(self, event):
        for full in (False, True):
            output = io.StringIO()
            render_event(event, output, full=full)
            self.assertIn(event["message"], output.getvalue())
            self.assertNotIn("request-http", output.getvalue())
            self.assertNotIn("request-1", output.getvalue())
            self.assertNotIn("Upstream unavailable", output.getvalue())
            self.assertNotIn("read operation timed out", output.getvalue())

    def test_main_generation_preserves_http_and_connection_diagnostics(self):
        for failure in (http_error(), connection_error()):
            with self.subTest(message=str(failure)):
                self.model.complete.side_effect = failure
                raised, event = self.run_failure(failure)
                self.assertIsInstance(raised, KeyboardInterrupt)
                self.assertEqual(event["type"], "retry")
                self.assertEqual(event["message"], str(failure))
                self.assertEqual(event["diagnostics"], failure.diagnostics)
                self.assert_public_message_only(event)

    def test_transport_http_error_is_redacted_through_provider_and_session(self):
        api_key = "local-http-credential-keep-private"
        body_secret = "nested-response-secret-keep-private"
        provider = OpenAIProvider("test", api_key, "https://api.example.test/v1", api_format="chat")
        agent = Agent(provider, self.session, SkillRegistry([]), self.root)
        body = json.dumps({"error": {"code": "upstream_busy", "message": "Capacity temporarily unavailable",
                                     "api_key": api_key, "details": {"secret": body_secret}}}).encode()
        error = HTTPError("https://api.example.test/v1/chat/completions", 503,
                          f"Upstream unavailable for {api_key}", {"x-request-id": "request-http"},
                          io.BytesIO(body))
        self.sleep.side_effect = KeyboardInterrupt()
        with (patch.object(provider, "count_tokens", return_value=100),
              patch("skills_agent.providers.base.request.urlopen", side_effect=error) as send,
              self.assertRaises(KeyboardInterrupt)):
            agent.run({"role": "user", "content": "Continue the task"})
        send.assert_called_once()
        persisted = (self.session.path / "events.jsonl").read_text()
        self.assertNotIn(api_key, persisted)
        self.assertNotIn(body_secret, persisted)
        event = [e for e in self.read_events() if e["type"] == "retry"][-1]
        self.assertEqual(event["message"], "API HTTP error 503")
        diagnostic = event["diagnostics"]
        self.assertEqual(diagnostic["kind"], "http")
        self.assertEqual(diagnostic["exception"]["type"], "HTTPError")
        self.assertEqual(diagnostic["response"]["status"], 503)
        saved_error = json.loads(diagnostic["response"]["body"])["error"]
        self.assertEqual(saved_error["code"], "upstream_busy")
        self.assertEqual(saved_error["message"], "Capacity temporarily unavailable")
        self.assertEqual(saved_error["api_key"], "[REDACTED]")
        self.assertEqual(saved_error["details"]["secret"], "[REDACTED]")
        self.assert_public_message_only(event)

    def test_transport_reset_preserves_the_redacted_exception_chain_in_session(self):
        api_key = "local-reset-credential-keep-private"
        password = "reset-password-keep-private"
        provider = OpenAIProvider("test", api_key, "https://api.example.test/v1", api_format="chat")
        agent = Agent(provider, self.session, SkillRegistry([]), self.root)
        error = URLError(ConnectionResetError(
            54, f"Connection reset by peer; Authorization: Bearer {api_key}; password={password}"))
        self.sleep.side_effect = KeyboardInterrupt()
        with (patch.object(provider, "count_tokens", return_value=100),
              patch("skills_agent.providers.base.request.urlopen", side_effect=error) as send,
              self.assertRaises(KeyboardInterrupt)):
            agent.run({"role": "user", "content": "Continue the task"})
        send.assert_called_once()
        persisted = (self.session.path / "events.jsonl").read_text()
        self.assertNotIn(api_key, persisted)
        self.assertNotIn(password, persisted)
        event = [e for e in self.read_events() if e["type"] == "retry"][-1]
        self.assertEqual(event["message"], "API connection failed")
        diagnostic = event["diagnostics"]
        self.assertEqual(diagnostic["kind"], "connection")
        self.assertEqual(diagnostic["phase"], "open_request")
        self.assertEqual(diagnostic["exception"]["type"], "URLError")
        reason = diagnostic["exception"]["reason"]
        self.assertEqual(reason["type"], "ConnectionResetError")
        self.assertEqual(reason["errno"], 54)
        self.assertIn("Connection reset by peer", reason["message"])
        self.assertIn("[REDACTED]", reason["message"])

    def test_main_count_failure_preserves_diagnostics_before_generation(self):
        failure = connection_error()
        self.model.count_tokens.side_effect = failure
        _, event = self.run_failure(failure)
        self.model.complete.assert_not_called()
        self.assertNotIn("_raw", event)
        self.assertEqual(event["diagnostics"], failure.diagnostics)

    def test_errors_without_diagnostics_keep_the_existing_event_shape(self):
        failure = ProviderError("Model unavailable")
        self.model.complete.side_effect = failure
        _, event = self.run_failure(failure)
        self.assertNotIn("diagnostics", event)

    def test_partial_stream_and_diagnostics_survive_together(self):
        failure = connection_error()
        failure.raw = {"stream_events": [{"type": "content_block_delta"}]}
        failure.partial_content = [{"type": "text", "text": "Partial answer"}]
        self.model.complete.side_effect = failure
        _, event = self.run_failure(failure)
        self.assertEqual(event["_raw"], failure.raw)
        self.assertEqual(event["partial_content"], failure.partial_content)
        self.assertEqual(event["diagnostics"], failure.diagnostics)

    def test_auxiliary_generation_retries_preserve_each_attempt(self):
        failures = [connection_error(1), connection_error(2)]
        self.model.complete.side_effect = [*failures, {"role": "assistant", "content": "Saved summary"}]
        result = self.agent.auxiliary.compress("", [], None)
        self.assertEqual(result, "Saved summary")
        errors = [event for event in self.private_events() if event["type"] == "error"]
        self.assertEqual([event["attempt"] for event in errors], [1, 2])
        self.assertEqual([event["operation"] for event in errors], ["complete", "complete"])
        self.assertEqual([event["diagnostics"] for event in errors], [exc.diagnostics for exc in failures])
        self.assertEqual(self.model.complete.call_count, 3)

    def test_auxiliary_http_failure_is_saved_once_without_retry(self):
        failure = http_error(401)
        self.model.complete.side_effect = failure
        with self.assertRaises(ProviderError):
            self.agent.auxiliary.compress("", [], None)
        errors = [event for event in self.private_events() if event["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["diagnostics"], failure.diagnostics)
        self.model.complete.assert_called_once()

    def test_auxiliary_generation_retries_until_interrupted_with_private_diagnostics(self):
        failures = [connection_error(number) for number in range(1, 6)]
        self.model.complete.side_effect = [*failures, KeyboardInterrupt()]
        with patch.object(self.agent.context, "prepare", side_effect=lambda *_:
                          self.agent.auxiliary.compress("", [], None)):
            with self.assertRaises(KeyboardInterrupt):
                self.agent.run({"role": "user", "content": "Continue"})
        self.assertEqual(self.read_events()[-1]["message"], "Interrupted")
        self.assertNotIn("diagnostics", self.read_events()[-1])
        errors = [event for event in self.private_events() if event["type"] == "error" and "diagnostics" in event]
        self.assertEqual([event["diagnostics"] for event in errors], [exc.diagnostics for exc in failures])
        self.assertNotIn("attempts", failures[-1].diagnostics)

    def test_auxiliary_fits_retries_are_saved_in_the_compression_session(self):
        failures = [connection_error(1), connection_error(2)]
        self.model.count_tokens.side_effect = [*failures, 100, 100]
        self.assertEqual(self.agent.auxiliary.compress("", [], None), "Saved summary")
        logs = list(self.session.path.glob("subagents/*/events.jsonl"))
        self.assertEqual(len(logs), 1)
        errors = [event for event in self.private_events() if event["type"] == "error"]
        self.assertEqual([event["attempt"] for event in errors], [1, 2])
        self.assertEqual([event["operation"] for event in errors], ["count_tokens", "count_tokens"])
        self.assertEqual([event["diagnostics"] for event in errors], [exc.diagnostics for exc in failures])

    def test_auxiliary_fits_retries_until_interrupted_preserving_each_diagnostic(self):
        failures = [connection_error(number) for number in range(1, 6)]
        self.model.count_tokens.side_effect = [*failures, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.agent.auxiliary.compress("", [], None)
        errors = [event for event in self.private_events() if event["type"] == "error" and "diagnostics" in event]
        self.assertEqual([event["attempt"] for event in errors], [1, 2, 3, 4, 5])
        self.assertEqual([event["diagnostics"] for event in errors], [exc.diagnostics for exc in failures])
        self.model.complete.assert_not_called()

    def test_auxiliary_call_count_failure_is_distinguished_from_generation(self):
        failure = connection_error()
        self.model.count_tokens.side_effect = [100, failure, 100]
        self.assertEqual(self.agent.auxiliary.compress("", [], None), "Saved summary")
        errors = [event for event in self.private_events() if event["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["operation"], "count_tokens")
        self.assertEqual(errors[0]["diagnostics"], failure.diagnostics)
        self.model.complete.assert_called_once()

    def test_auxiliary_fits_http_error_is_persisted_without_retry(self):
        failure = http_error(401)
        self.model.count_tokens.side_effect = failure
        with self.assertRaises(ProviderError):
            self.agent.auxiliary.compress("", [], None)
        errors = [event for event in self.private_events() if event["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["operation"], "count_tokens")
        self.assertEqual(errors[0]["diagnostics"], failure.diagnostics)
        self.model.count_tokens.assert_called_once()

    def test_tool_report_text_count_failure_is_persisted(self):
        failure = connection_error()
        self.model.count_text.side_effect = [failure, 10]
        self.agent.auxiliary.tool_report("evidence", "Inspect", {}, {})
        errors = [event for event in self.private_events() if event["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["operation"], "count_text")
        self.assertEqual(errors[0]["diagnostics"], failure.diagnostics)

    def test_fits_without_explicit_log_creates_one_private_retry_log(self):
        failure = connection_error()
        self.model.count_tokens.side_effect = [failure, 100]
        auxiliary = Auxiliary(self.model, self.session)
        self.assertTrue(auxiliary._fits("system", {}))
        events = self.private_events()
        self.assertEqual(events[0]["purpose"], "token_counting")
        errors = [event for event in events if event["type"] == "error"]
        self.assertEqual(errors[0]["diagnostics"], failure.diagnostics)


if __name__ == "__main__":
    unittest.main()
