import io
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError

from skills_agent.providers.base import ProviderConnectionError, ProviderError
from skills_agent.providers.openai import OpenAIProvider
from skills_agent.providers.retry import retry_call
from tests.sse import stream_response


class ProviderRetryTests(unittest.TestCase):
    def test_real_http_error_path_recovers_to_a_complete_sse_response(self):
        url = "https://api.example.test/chat/completions"
        failures = [HTTPError(url, status, "Request failed", {}, io.BytesIO(b"unparseable error body"))
                    for status in (429, 500, 502, 503, 504, 524)]
        response = io.BytesIO(stream_response({"choices": [{
            "message": {"role": "assistant", "content": "Recovered"}, "finish_reason": "stop",
        }]}))
        response.status, response.headers = 200, {}
        provider = OpenAIProvider("test", "test-key", "https://api.example.test", api_format="chat")
        observed = Mock()
        with patch("skills_agent.providers.base.request.urlopen", side_effect=[*failures, response]) as send, \
                patch("skills_agent.providers.retry.time.sleep") as sleep:
            result = retry_call(lambda: provider.complete("", [{"role": "user", "content": "Continue"}], []),
                                on_retry=observed)
        self.assertEqual(result["content"], [{"type": "text", "text": "Recovered"}])
        self.assertEqual(send.call_count, 7)
        self.assertEqual(sleep.call_args_list, [call(5)] * 6)
        self.assertEqual([entry.args[0].http_status for entry in observed.call_args_list], [429, 500, 502, 503, 504, 524])
        self.assertEqual(len({entry.args[0].data for entry in send.call_args_list}), 1)
        self.assertTrue(response.closed)

    def test_http_failures_and_connections_retry_past_three_attempts(self):
        failures = [ProviderError("Request failed", diagnostics={"response": {"status": status}})
                    for status in (429, 500, 502, 503, 504, 524)]
        failures.append(ProviderConnectionError("Connection reset"))
        operation = Mock(side_effect=[*failures, "complete"])
        observed = Mock()
        with patch("skills_agent.providers.retry.time.sleep") as sleep:
            self.assertEqual(retry_call(operation, on_retry=observed), "complete")
        self.assertEqual(sleep.call_args_list, [call(5)] * len(failures))
        self.assertEqual(observed.call_args_list, [call(exc, index) for index, exc in enumerate(failures, 1)])
        self.assertEqual(operation.call_count, len(failures) + 1)

    def test_nonretryable_status_does_not_wait_or_retry(self):
        for status in (400, 401, 402, 403, 404, 422, 501, 505, 599):
            with self.subTest(status=status):
                failure = ProviderError("503 in an unrelated message", diagnostics={"response": {"status": status}})
                operation = Mock(side_effect=failure)
                with patch("skills_agent.providers.retry.time.sleep") as sleep:
                    with self.assertRaises(ProviderError) as raised:
                        retry_call(operation)
                self.assertIs(raised.exception, failure)
                operation.assert_called_once()
                sleep.assert_not_called()

    def test_gateway_http_failures_retry_past_three_attempts_without_json_bodies(self):
        cases = (
            (502, b"<html><body>Bad Gateway</body></html>", "bad_gateway"),
            (504, b"", "gateway_timeout"),
            (524, b"upstream timed out", "gateway_timeout"),
        )
        for status, body, category in cases:
            with self.subTest(status=status):
                url = "https://api.example.test/chat/completions"
                sources = [io.BytesIO(body) for _ in range(4)]
                failures = [HTTPError(url, status, "Request failed", {}, source) for source in sources]
                response = io.BytesIO(stream_response({"choices": [{
                    "message": {"role": "assistant", "content": "Recovered"}, "finish_reason": "stop",
                }]}))
                response.status, response.headers = 200, {}
                provider = OpenAIProvider("test", "test-key", "https://api.example.test", api_format="chat")
                observed = Mock()
                with patch("skills_agent.providers.base.request.urlopen", side_effect=[*failures, response]) as send, \
                        patch("skills_agent.providers.retry.time.sleep") as sleep:
                    result = retry_call(lambda: provider.complete("", [{"role": "user", "content": "Continue"}], []),
                                        on_retry=observed)
                self.assertEqual(result["content"], [{"type": "text", "text": "Recovered"}])
                self.assertEqual(send.call_count, 5)
                self.assertEqual(sleep.call_args_list, [call(5)] * 4)
                self.assertEqual([entry.args[0].http_status for entry in observed.call_args_list], [status] * 4)
                self.assertEqual([entry.args[0].category for entry in observed.call_args_list], [category] * 4)
                self.assertEqual([entry.args[1] for entry in observed.call_args_list], [1, 2, 3, 4])
                self.assertEqual(len({entry.args[0].data for entry in send.call_args_list}), 1)
                self.assertTrue(all(source.closed for source in sources))
                self.assertTrue(response.closed)

    def test_gateway_retry_wait_remains_cancellable(self):
        for status in (502, 504, 524):
            for interruption in (KeyboardInterrupt(), SystemExit(143)):
                with self.subTest(status=status, interruption=type(interruption).__name__):
                    failure = ProviderError("Request failed", diagnostics={"response": {"status": status}})
                    operation = Mock(side_effect=failure)
                    observed = Mock()
                    with patch("skills_agent.providers.retry.time.sleep", side_effect=interruption) as sleep:
                        with self.assertRaises(type(interruption)) as raised:
                            retry_call(operation, on_retry=observed)
                    self.assertIs(raised.exception, interruption)
                    operation.assert_called_once()
                    observed.assert_called_once_with(failure, 1)
                    sleep.assert_called_once_with(5)

    def test_non_provider_errors_are_not_retried(self):
        for failure in (RuntimeError("bug"), ValueError("bad input"), KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(error=type(failure).__name__):
                operation = Mock(side_effect=failure)
                with patch("skills_agent.providers.retry.time.sleep") as sleep:
                    with self.assertRaises(type(failure)) as raised:
                        retry_call(operation)
                self.assertIs(raised.exception, failure)
                operation.assert_called_once()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
