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
                    for status in (429, 500, 503)]
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
        self.assertEqual(send.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(5)] * 3)
        self.assertEqual([entry.args[0].http_status for entry in observed.call_args_list], [429, 500, 503])
        self.assertEqual(len({entry.args[0].data for entry in send.call_args_list}), 1)
        self.assertTrue(response.closed)

    def test_http_failures_and_connections_retry_past_three_attempts(self):
        failures = [ProviderError("Request failed", diagnostics={"response": {"status": status}})
                    for status in (429, 500, 503, 503)]
        failures.append(ProviderConnectionError("Connection reset"))
        operation = Mock(side_effect=[*failures, "complete"])
        observed = Mock()
        with patch("skills_agent.providers.retry.time.sleep") as sleep:
            self.assertEqual(retry_call(operation, on_retry=observed), "complete")
        self.assertEqual(sleep.call_args_list, [call(5)] * len(failures))
        self.assertEqual(observed.call_args_list, [call(exc, index) for index, exc in enumerate(failures, 1)])
        self.assertEqual(operation.call_count, len(failures) + 1)

    def test_nonretryable_status_does_not_wait_or_retry(self):
        for status in (400, 401, 402, 403, 404, 422, 502, 504):
            with self.subTest(status=status):
                failure = ProviderError("503 in an unrelated message", diagnostics={"response": {"status": status}})
                operation = Mock(side_effect=failure)
                with patch("skills_agent.providers.retry.time.sleep") as sleep:
                    with self.assertRaises(ProviderError) as raised:
                        retry_call(operation)
                self.assertIs(raised.exception, failure)
                operation.assert_called_once()
                sleep.assert_not_called()

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
