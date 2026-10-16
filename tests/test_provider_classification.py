"""Classification uses protocol fields, independently of error prose or retries."""

from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from skills_agent.providers import AnthropicProvider, OpenAIProvider
from skills_agent.providers.base import (
    ProviderConnectionError, ProviderError, ProviderOutputLimitError, post_json, post_stream,
)


class ProviderClassificationTests(unittest.TestCase):
    def error(self, *, status=None, error=None, body=None, **kwargs):
        response = {}
        if status is not None:
            response["status"] = status
        if body is not None:
            response["body"] = body
        elif error is not None:
            response["body"] = json.dumps({"error": error})
        return ProviderError("arbitrary prose", diagnostics={"kind": "stream", "response": response}, **kwargs)

    def stream_failure(self, provider, events):
        response = io.BytesIO(b"".join(
            b"data: " + (b"[DONE]" if event is None else json.dumps(event).encode()) + b"\n\n"
            for event in events
        ))
        response.status = 200
        response.headers = {}
        with patch("skills_agent.providers.base.request.urlopen", return_value=response), \
                self.assertRaises(ProviderError) as caught:
            provider.complete("", [], [])
        self.assertTrue(response.closed)
        return caught.exception

    def test_http_categories_and_exact_retry_allowlist(self):
        categories = {
            400: "invalid_request", 401: "authentication", 402: "insufficient_balance",
            403: "permission", 404: "not_found", 422: "invalid_request", 429: "rate_limited",
            500: "server_error", 502: "bad_gateway", 503: "service_unavailable",
            504: "gateway_timeout", 524: "gateway_timeout",
        }
        for status in range(400, 600):
            with self.subTest(status=status):
                failure = self.error(status=status)
                self.assertEqual(failure.http_status, status)
                self.assertEqual(failure.category, categories.get(status, "http_error"))
                self.assertEqual(failure.retryable, status in {429, 500, 502, 503, 504, 524})

    def test_http_status_precedes_body_codes_and_terminal_reason(self):
        for status, expected in ((401, "authentication"), (501, "http_error"), (502, "bad_gateway"),
                                 (503, "service_unavailable"), (504, "gateway_timeout"), (524, "gateway_timeout")):
            with self.subTest(status=status):
                failure = self.error(status=status, error={"code": "429", "type": "overloaded_error"},
                                     finish_reason="length")
                self.assertEqual(failure.category, expected)
                self.assertEqual(failure.retryable, status in {502, 503, 504, 524})
                self.assertEqual(failure.provider_code, "429")
                self.assertEqual(failure.provider_type, "overloaded_error")
                self.assertEqual(failure.finish_reason, "length")

    def test_both_http_transports_expose_error_fields_without_retrying(self):
        for transport in (post_json, post_stream):
            for status in (400, 401, 402, 422, 429, 500, 502, 503, 504, 524):
                with self.subTest(transport=transport.__name__, status=status):
                    body = io.BytesIO(b'{"error":{"code":"provider_code","type":"provider_type"}}')
                    failure = HTTPError("https://api.example.test/v1/messages", status, "failure", {}, body)
                    with patch("skills_agent.providers.base.request.urlopen", side_effect=failure) as send, \
                            self.assertRaises(ProviderError) as caught:
                        result = transport("https://api.example.test/v1/messages", {}, {})
                        if transport is post_stream:
                            list(result)
                    send.assert_called_once()
                    self.assertEqual(caught.exception.http_status, status)
                    self.assertEqual(caught.exception.provider_code, "provider_code")
                    self.assertEqual(caught.exception.provider_type, "provider_type")
                    self.assertEqual(caught.exception.retryable, status in {429, 500, 502, 503, 504, 524})

    def test_structured_error_allowlist_for_http_200_and_missing_status(self):
        cases = (
            ("code", 429, "rate_limited"), ("code", "429", "rate_limited"),
            ("code", "rate_limit_exceeded", "rate_limited"),
            ("type", "rate_limit_error", "rate_limited"),
            ("code", "500", "server_error"), ("type", "server_error", "server_error"),
            ("type", "api_error", "server_error"),
            ("code", "internal_server_error", "server_error"),
            ("code", 502, "bad_gateway"), ("code", "502", "bad_gateway"),
            ("code", "503", "service_unavailable"),
            ("code", 504, "gateway_timeout"), ("code", "504", "gateway_timeout"),
            ("code", 524, "gateway_timeout"), ("code", "524", "gateway_timeout"),
            ("type", "overloaded_error", "service_unavailable"),
            ("code", "service_unavailable", "service_unavailable"),
            ("code", "service_unavailable_error", "service_unavailable"),
            ("type", "service_unavailable_error", "service_unavailable"),
            ("code", "upstream_stream_read_error", "connection"),
            ("type", "upstream_error", "server_error"),
        )
        for status in (None, 200):
            for field, value, category in cases:
                with self.subTest(status=status, field=field, value=value):
                    failure = self.error(status=status, error={field: value, "message": "arbitrary"})
                    self.assertEqual(failure.category, category)
                    self.assertTrue(failure.retryable)

    def test_known_permanent_code_precedes_broader_type(self):
        for code, category in (("invalid_api_key", "authentication"),
                               ("insufficient_quota", "insufficient_balance"),
                               ("context_length_exceeded", "output_limit")):
            with self.subTest(code=code):
                failure = self.error(status=200, error={"code": code, "type": "server_error"})
                self.assertEqual(failure.category, category)
                self.assertFalse(failure.retryable)

    def test_unknown_errors_never_use_message_substrings(self):
        prose = "HTTP 502 503 504 524 rate_limit_error server_error overloaded_error 服务繁忙 retry soon"
        failures = (
            ProviderError(prose), self.error(body=prose),
            self.error(error={"code": "new_code", "type": "new_type", "message": prose}),
            self.error(error={"code": "prefix_service_unavailable_suffix", "message": prose}),
            self.error(body=json.dumps({"message": prose, "status": 503})),
        )
        for failure in failures:
            with self.subTest(diagnostics=failure.diagnostics):
                self.assertEqual(failure.category, "provider_error")
                self.assertFalse(failure.retryable)

    def test_malformed_diagnostics_do_not_raise_or_invent_classification(self):
        for body in ("<html>503 unavailable</html>", '{"error":{"code":"503"', "[]", "null",
                     '{"error":{"code":true,"type":[]}}', '{"error":{"type":503}}'):
            with self.subTest(body=body):
                failure = self.error(body=body)
                self.assertIsNone(failure.provider_code)
                self.assertIsNone(failure.provider_type)
                self.assertFalse(failure.retryable)
        failure = ProviderError("bad response", diagnostics={"kind": "protocol", "response": None})
        self.assertEqual(failure.category, "protocol")
        self.assertIsNone(failure.http_status)
        self.assertFalse(failure.retryable)

    def test_legacy_diagnostics_and_diagnostic_identity_are_preserved(self):
        diagnostic = {"status": 503, "body": '{"error":{"code":"old_code","type":"old_type"}}'}
        original = deepcopy(diagnostic)
        failure = ProviderError("unchanged message", diagnostics=diagnostic)
        self.assertEqual((failure.http_status, failure.category, failure.provider_code, failure.provider_type,
                          failure.finish_reason, failure.retryable),
                         (503, "service_unavailable", "old_code", "old_type", None, True))
        self.assertEqual(str(failure), "unchanged message")
        self.assertIs(failure.diagnostics, diagnostic)
        self.assertEqual(diagnostic, original)

    def test_connection_and_output_limit_subclasses_keep_distinct_policy(self):
        self.assertEqual(ProviderConnectionError("disconnected").category, "connection")
        self.assertTrue(ProviderConnectionError("disconnected").retryable)
        self.assertEqual(ProviderOutputLimitError("truncated").category, "output_limit")
        self.assertFalse(ProviderOutputLimitError("truncated").retryable)
        failure = ProviderConnectionError("misleading message", diagnostics={"response": {"status": 401}})
        self.assertEqual(failure.category, "authentication")
        self.assertFalse(failure.retryable)

    def test_chat_sse_error_is_classified_from_sanitized_body(self):
        failure = self.stream_failure(OpenAIProvider("test", "test-secret", api_format="chat"), [
            {"error": {"code": "unrecognized_code", "type": "overloaded_error",
                       "message": "arbitrary", "api_key": "test-secret"}},
        ])
        self.assertEqual(failure.http_status, 200)
        self.assertEqual(failure.provider_code, "unrecognized_code")
        self.assertEqual(failure.provider_type, "overloaded_error")
        self.assertEqual(failure.category, "service_unavailable")
        self.assertTrue(failure.retryable)
        self.assertNotIn("test-secret", json.dumps(failure.diagnostics))
        self.assertNotIn("test-secret", json.dumps(failure.raw))

    def test_anthropic_sse_error_is_classified(self):
        failure = self.stream_failure(AnthropicProvider("test", "test-secret"), [
            {"type": "error", "error": {"type": "rate_limit_error", "message": "arbitrary"}},
        ])
        self.assertEqual(failure.provider_type, "rate_limit_error")
        self.assertEqual(failure.category, "rate_limited")
        self.assertTrue(failure.retryable)

    def test_responses_nested_and_top_level_errors_are_classified(self):
        events = (
            {"type": "response.failed", "response": {
                "status": "failed", "error": {"code": "server_error", "message": "arbitrary"}}},
            {"type": "error", "code": "server_error", "message": "arbitrary"},
        )
        for event in events:
            with self.subTest(event=event):
                failure = self.stream_failure(OpenAIProvider("test", "test-secret", api_format="responses"), [event])
                self.assertEqual(failure.http_status, 200)
                self.assertEqual(failure.provider_code, "server_error")
                self.assertEqual(failure.category, "server_error")
                self.assertTrue(failure.retryable)

    def test_nonstream_api_error_is_classified(self):
        response = io.BytesIO(b'{"error":{"code":"503","type":"some_type","message":"arbitrary"}}')
        response.status = 200
        response.headers = {}
        with patch("skills_agent.providers.base.request.urlopen", return_value=response), \
                self.assertRaises(ProviderError) as caught:
            post_json("https://api.example.test/v1/messages", {}, {})
        self.assertEqual(caught.exception.category, "service_unavailable")
        self.assertTrue(caught.exception.retryable)

    def test_oversized_responses_error_keeps_sanitized_classification_fields(self):
        failure = self.stream_failure(OpenAIProvider("test", "test-secret", api_format="responses"), [
            {"type": "response.failed", "response": {
                "status": "failed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "x" * 70000 + "test-secret"}]}],
                "error": {"code": "server_error", "type": "test-secret", "api_key": "test-secret"}}},
        ])
        diagnostic = failure.diagnostics["response"]
        self.assertTrue(diagnostic["body_truncated"])
        self.assertLessEqual(len(diagnostic["body"]), 65536)
        with self.assertRaises(ValueError):
            json.loads(diagnostic["body"])
        self.assertEqual(failure.provider_code, "server_error")
        self.assertEqual(failure.provider_type, "[REDACTED]")
        self.assertEqual(failure.category, "server_error")
        self.assertTrue(failure.retryable)
        self.assertNotIn("test-secret", json.dumps(vars(failure)))

    def test_oversized_nonstream_error_keeps_classification_fields(self):
        response = io.BytesIO(json.dumps({"padding": "x" * 70000, "error": {
            "code": "503", "type": "service_unavailable_error", "api_key": "test-secret",
        }}).encode())
        response.status = 200
        response.headers = {}
        with patch("skills_agent.providers.base.request.urlopen", return_value=response), \
                self.assertRaises(ProviderError) as caught:
            post_json("https://api.example.test/v1/messages", {}, {"Authorization": "Bearer test-secret"})
        failure = caught.exception
        self.assertTrue(failure.diagnostics["response"]["body_truncated"])
        self.assertEqual(failure.provider_code, "503")
        self.assertEqual(failure.provider_type, "service_unavailable_error")
        self.assertEqual(failure.category, "service_unavailable")
        self.assertTrue(failure.retryable)
        self.assertNotIn("test-secret", json.dumps(vars(failure)))

    def test_retained_fields_are_bounded_and_do_not_override_http_or_terminal_reason(self):
        fields = {"code": "server_error", "type": "x" * 3000, "message": "unretained", "secret": "unretained"}
        failure = ProviderError("failure", diagnostics={"response": {"status": 401}}, provider_error=fields)
        self.assertEqual(len(failure.provider_type), 2048)
        self.assertNotIn("unretained", json.dumps(vars(failure)))
        self.assertEqual(failure.category, "authentication")
        self.assertFalse(failure.retryable)
        failure = ProviderError("failure", finish_reason="content_filter", provider_error=fields)
        self.assertEqual(failure.category, "content_filter")
        self.assertFalse(failure.retryable)

    def test_chat_terminal_reasons_do_not_become_transient_errors(self):
        for reason, category in (("length", "output_limit"), ("content_filter", "content_filter"),
                                 ("insufficient_system_resource", "aborted"), ("new_reason", "provider_error")):
            with self.subTest(reason=reason):
                failure = self.stream_failure(OpenAIProvider("test", "test-secret", api_format="chat"), [
                    {"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": reason}]}, None,
                ])
                self.assertEqual(failure.finish_reason, reason)
                self.assertEqual(failure.category, category)
                self.assertFalse(failure.retryable)
                self.assertEqual(failure.partial_content, [{"type": "text", "text": "partial"}])

    def test_anthropic_terminal_reasons_are_structured(self):
        for reason, category in (("max_tokens", "output_limit"),
                                 ("model_context_window_exceeded", "output_limit"), ("aborted", "aborted")):
            with self.subTest(reason=reason):
                failure = self.stream_failure(AnthropicProvider("test", "test-secret"), [
                    {"type": "message_start", "message": {"role": "assistant", "content": [], "stop_reason": None}},
                    {"type": "message_delta", "delta": {"stop_reason": reason}}, {"type": "message_stop"},
                ])
                self.assertEqual(failure.finish_reason, reason)
                self.assertEqual(failure.category, category)
                self.assertFalse(failure.retryable)

    def test_responses_incomplete_reason_precedes_nested_error_type(self):
        for reason, category in (("max_output_tokens", "output_limit"), ("content_filter", "content_filter")):
            with self.subTest(reason=reason):
                failure = self.stream_failure(OpenAIProvider("test", "test-secret", api_format="responses"), [
                    {"type": "response.incomplete", "response": {
                        "status": "incomplete", "incomplete_details": {"reason": reason},
                        "error": {"code": "server_error", "message": "arbitrary"}}},
                ])
                self.assertEqual(failure.finish_reason, reason)
                self.assertEqual(failure.category, category)
                self.assertFalse(failure.retryable)


if __name__ == "__main__":
    unittest.main()
