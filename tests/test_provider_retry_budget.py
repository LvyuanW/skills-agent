import datetime
import unittest
from unittest.mock import Mock, call, patch

from skills_agent.providers.base import ProviderError
from skills_agent.providers.retry import retry_call, retry_event


def failure(headers=None, status=502):
    return ProviderError("API failure", diagnostics={
        "response": {"status": status, "headers": headers or {}},
    })


class RetryBudgetTests(unittest.TestCase):
    def test_stream_gateway_timeout_obeys_attempt_limit(self):
        error = ProviderError("[Deadline Exceeded]", diagnostics={"response": {"status": 200}},
                              provider_error={"code": "GatewayTimeout", "type": "GatewayTimeout"})
        operation = Mock(side_effect=error)
        with patch("skills_agent.providers.retry.time.sleep") as sleep:
            with self.assertRaises(ProviderError) as caught:
                retry_call(operation, max_attempts=2)
        self.assertIs(caught.exception, error)
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(5)
        self.assertEqual(error.diagnostics["retry"]["reason"], "attempt_limit")

    def test_default_interactive_policy_still_recovers_after_many_failures(self):
        operation = Mock(side_effect=[*[failure() for _ in range(9)], "ok"])
        with patch.dict("os.environ", {}, clear=True), patch("skills_agent.providers.retry.time.sleep"):
            self.assertEqual(retry_call(operation), "ok")
        self.assertEqual(operation.call_count, 10)

    def test_evaluation_environment_caps_attempts_and_keeps_original_error(self):
        error = failure()
        operation = Mock(side_effect=error)
        with patch.dict("os.environ", {"SKILLS_AGENT_PROVIDER_MAX_ATTEMPTS": "5",
                                      "SKILLS_AGENT_PROVIDER_RETRY_BUDGET_SECONDS": "300"}), \
                patch("skills_agent.providers.retry.time.sleep") as sleep:
            with self.assertRaises(ProviderError) as caught:
                retry_call(operation)
        self.assertIs(caught.exception, error)
        self.assertEqual(operation.call_count, 5)
        self.assertEqual(sleep.call_args_list, [call(5)] * 4)
        self.assertEqual(error.category, "bad_gateway")
        self.assertEqual(error.diagnostics["retry"]["reason"], "attempt_limit")
        self.assertTrue(error.diagnostics["retry"]["exhausted"])

    def test_retry_after_is_respected_and_shown_in_retry_event(self):
        error = failure({"Retry-After": "60"})
        events = []
        with patch("skills_agent.providers.retry.time.sleep") as sleep:
            self.assertEqual(retry_call(Mock(side_effect=[error, "ok"]), max_attempts=5,
                                        max_elapsed_seconds=300,
                                        on_retry=lambda exc, attempt: events.append(retry_event(exc, attempt, operation="complete"))), "ok")
        sleep.assert_called_once_with(60)
        self.assertEqual(events[0]["retry_in_seconds"], 60)

    def test_http_date_and_invalid_retry_after_values(self):
        with patch("skills_agent.providers.base.time.time", return_value=0):
            self.assertEqual(failure({"retry-after": "Thu, 01 Jan 1970 00:01:00 GMT"}).retry_after_seconds, 60)
        for value in ("garbage", "nan", "inf", None, True):
            with self.subTest(value=value):
                self.assertIsNone(failure({"retry-after": value}).retry_after_seconds)

    def test_retry_after_that_cannot_fit_budget_does_not_sleep_or_retry(self):
        error = failure({"retry-after": "300"})
        operation = Mock(side_effect=error)
        with patch("skills_agent.providers.retry.time.sleep") as sleep:
            with self.assertRaises(ProviderError) as caught:
                retry_call(operation, max_attempts=5, max_elapsed_seconds=300)
        self.assertIs(caught.exception, error)
        operation.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(error.diagnostics["retry"]["reason"], "retry_after_exceeds_budget")

    def test_expired_elapsed_budget_starts_no_next_request(self):
        error = failure()
        operation = Mock(side_effect=error)
        with patch("skills_agent.providers.retry.time.monotonic", side_effect=[0, 2, 300]), \
                patch("skills_agent.providers.retry.time.sleep") as sleep:
            with self.assertRaises(ProviderError):
                retry_call(operation, max_attempts=5, max_elapsed_seconds=300)
        operation.assert_called_once()
        sleep.assert_called_once_with(5)
        self.assertEqual(error.diagnostics["retry"]["reason"], "elapsed_limit")

    def test_retry_after_wait_is_cancellable(self):
        operation = Mock(side_effect=failure({"retry-after": "60"}))
        with patch("skills_agent.providers.retry.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                retry_call(operation, max_attempts=5, max_elapsed_seconds=300)
        operation.assert_called_once()

    def test_upstream_stream_error_is_retryable_but_terminal_request_errors_are_not(self):
        upstream = ProviderError("stream interrupted", provider_error={"code": "upstream_stream_read_error", "type": "upstream_error"})
        with patch("skills_agent.providers.retry.time.sleep"):
            self.assertEqual(retry_call(Mock(side_effect=[upstream, "ok"]), max_attempts=5), "ok")
        for status in (400, 401, 402, 403, 501, 505, 511, 599):
            operation = Mock(side_effect=failure(status=status))
            with self.subTest(status=status), patch("skills_agent.providers.retry.time.sleep") as sleep:
                with self.assertRaises(ProviderError):
                    retry_call(operation, max_attempts=5)
                operation.assert_called_once()
                sleep.assert_not_called()
