import base64
import errno
import io
import json
import socket
import ssl
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from skills_agent.providers import AnthropicProvider, OpenAIProvider, ProviderError, ProviderOutputLimitError
from skills_agent.providers.base import ProviderConnectionError, post_json, post_stream


class RecordingBody(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class BrokenResponse(io.BytesIO):
    status = 200
    headers = {"x-request-id": "request-read-failure"}

    def __init__(self, failure):
        super().__init__(b"")
        self.failure = failure

    def read(self, size=-1):
        raise self.failure

    def __iter__(self):
        yield b'data: {"choices": []}\n'
        yield b'\n'
        raise self.failure


class CloseFailureBody(RecordingBody):
    def close(self):
        super().close()
        raise OSError(errno.EIO, "close failed AUTHHEADERSECRET")


class ProviderDiagnosticsTests(unittest.TestCase):
    url = (
        "https://URLUSERNAME:URLPASSWORD@api.example.test/v1/chat/completions"
        "?api_key=QUERYAPIKEY&access_token=QUERYTOKEN&model=model-id#FRAGMENTSECRET"
    )
    payload = {"messages": [{"role": "user", "content": "PRIVATE_REQUEST_CONTENT"}]}
    headers = {
        "Authorization": "Bearer AUTHHEADERSECRET",
        "x-api-key": "APIHEADERSECRET",
        "Cookie": "session=COOKIEHEADERSECRET",
    }
    credentials = (
        "URLUSERNAME", "URLPASSWORD", "QUERYAPIKEY", "QUERYTOKEN", "FRAGMENTSECRET",
        "AUTHHEADERSECRET", "APIHEADERSECRET", "COOKIEHEADERSECRET",
        "JSONPASSWORDSECRET", "JSONTOKENSECRET", "JSONKEYSECRET", "JSONCOOKIESECRET",
        "RESPONSECOOKIESECRET", "RESPONSEHEADERSECRET", "FREEBEARERSECRET",
    )

    def invoke(self, transport, *, url=None, headers=None):
        result = transport(url or self.url, self.payload, headers or self.headers)
        return list(result) if transport is post_stream else result

    def assert_diagnostics(self, exc, *, kind, phase):
        diagnostic = exc.diagnostics
        self.assertIsInstance(diagnostic, dict)
        self.assertEqual(diagnostic["kind"], kind)
        self.assertEqual(diagnostic["phase"], phase)
        self.assertIn("api.example.test/v1/chat/completions", diagnostic["endpoint"])
        self.assertGreaterEqual(diagnostic["elapsed_seconds"], 0)
        self.assertEqual(diagnostic["timeout_seconds"], 120)
        self.assertEqual(diagnostic["request_bytes"], len(json.dumps(
            self.payload, ensure_ascii=False,
        ).encode("utf-8")))
        self.assert_private(diagnostic)
        return diagnostic

    def assert_private(self, diagnostic, extra=()):
        serialized = json.dumps(diagnostic, ensure_ascii=False)
        for secret in (*self.credentials, "PRIVATE_REQUEST_CONTENT", *extra):
            with self.subTest(secret=secret):
                self.assertTrue(secret not in serialized, "Diagnostics contain a credential sentinel")

    def test_provider_error_defaults_to_no_diagnostics(self):
        exc = ProviderError("existing message")
        self.assertIsNone(exc.diagnostics)
        self.assertEqual(str(exc), "existing message")

    def test_http_error_preserves_business_details_and_redacts_all_diagnostics(self):
        body = json.dumps({
            "error": {
                "code": "quota_exceeded",
                "type": "rate_limit_error",
                "message": "Quota exhausted; retry soon. AUTHHEADERSECRET Bearer FREEBEARERSECRET",
                "password": "JSONPASSWORDSECRET",
                "nested": {"access_token": "JSONTOKENSECRET", "api_key": "JSONKEYSECRET",
                           "cookie": "JSONCOOKIESECRET"},
            },
        }).encode()
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                source = RecordingBody(body)
                failure = HTTPError(self.url, 429, "rate limited", {
                    "X-Request-ID": "request-quota-42", "Retry-After": "3",
                    "Authorization": "Bearer RESPONSEHEADERSECRET",
                    "X-Api-Key": "RESPONSEHEADERSECRET",
                    "Set-Cookie": "session=RESPONSECOOKIESECRET",
                }, source)
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure) as send:
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                self.assertEqual(str(caught.exception), "API HTTP error 429")
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                response = diagnostic["response"]
                self.assertEqual(response["status"], 429)
                self.assertEqual(response["headers"]["x-request-id"], "request-quota-42")
                self.assertFalse(response["body_truncated"])
                self.assertIn("quota_exceeded", response["body"])
                self.assertIn("rate_limit_error", response["body"])
                self.assertIn("Quota exhausted; retry soon.", response["body"])
                self.assertTrue(source.closed)
                send.assert_called_once()

    def test_http_error_body_reads_are_bounded_and_mark_truncation(self):
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                source = RecordingBody(b"upstream overloaded\n" + b"x" * (2 * 1024 * 1024))
                failure = HTTPError(self.url, 503, "unavailable", {}, source)
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assertTrue(diagnostic["response"]["body_truncated"])
                self.assertIn("upstream overloaded", diagnostic["response"]["body"])
                self.assertLessEqual(len(diagnostic["response"]["body"]), 65536)
                self.assertTrue(source.read_sizes)
                self.assertTrue(all(0 < size <= 65537 for size in source.read_sizes))
                self.assertTrue(source.closed)

    def test_http_body_read_failure_still_keeps_status_and_closes_response(self):
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                source = BrokenResponse(ConnectionResetError(errno.ECONNRESET, "body read reset"))
                failure = HTTPError(self.url, 502, "bad gateway", {"X-Request-ID": "request-reset"}, source)
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                self.assertEqual(str(caught.exception), "API HTTP error 502")
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assertEqual(diagnostic["response"]["status"], 502)
                self.assertIn("body_read_error", diagnostic["response"])
                self.assertIn("ConnectionResetError", json.dumps(diagnostic["response"]["body_read_error"]))
                self.assertTrue(source.closed)

    def test_http_error_close_failure_cannot_replace_the_original_status(self):
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                source = CloseFailureBody(b'{"error":{"code":"upstream_failure"}}')
                failure = HTTPError(self.url, 502, "bad gateway", {}, source)
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                self.assertEqual(str(caught.exception), "API HTTP error 502")
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assertEqual(diagnostic["response"]["status"], 502)
                self.assertIn("upstream_failure", diagnostic["response"]["body"])
                self.assertEqual(diagnostic["response"]["close_error"]["errno"], errno.EIO)
                self.assertTrue(source.closed)

    def test_truncated_error_body_cannot_reveal_a_partial_known_credential(self):
        secret = "AUTHHEADERSECRET"
        visible_prefix = secret[:12]
        # The bounded read includes only the first 13 bytes of this credential.
        # Redacting only complete matches would persist its first 12 bytes.
        body = b"x" * (65536 - len(visible_prefix)) + secret.encode() + b" trailing data"
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                source = RecordingBody(body)
                failure = HTTPError(self.url, 503, "busy", {}, source)
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assertTrue(diagnostic["response"]["body_truncated"])
                self.assert_private(diagnostic, extra=(visible_prefix,))
                self.assertTrue(all(0 < size <= 65537 for size in source.read_sizes))

    def test_escaped_known_key_is_redacted_inside_error_message(self):
        secret = 'ESCAPED"KEY\\WITH雪'
        bodies = (
            json.dumps({"error": {"code": "bad_auth", "message": f"Rejected {secret}"}}),
            "bad_auth: Rejected escaped credential " + json.dumps(secret),
        )
        for transport in (post_json, post_stream):
            for body in bodies:
                with self.subTest(transport=transport.__name__, valid_json=body.startswith("{")):
                    failure = HTTPError(self.url, 401, "unauthorized", {}, RecordingBody(body.encode()))
                    with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                        with self.assertRaises(ProviderError) as caught:
                            self.invoke(transport, headers={"x-api-key": secret})
                    diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                    self.assert_private(diagnostic, extra=(secret, json.dumps(secret)[1:-1]))
                    self.assertIn("bad_auth", diagnostic["response"]["body"])
                    self.assertIn("Rejected", diagnostic["response"]["body"])

    def test_unknown_basic_credential_is_redacted_in_body_and_exception(self):
        credential = base64.b64encode(b"unknown-user:unknown-password").decode()
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                body = f"upstream rejected Basic {credential}".encode()
                failure = HTTPError(self.url, 401, f"Basic {credential}", {}, RecordingBody(body))
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assert_private(diagnostic, extra=(credential,))
                self.assertIn("upstream rejected", diagnostic["response"]["body"])

    def test_non_json_body_removes_unknown_query_and_fragment_after_known_key(self):
        body = (
            "Please retry https://other.example.test/errors?key=AUTHHEADERSECRET"
            "&opaque=UNKNOWNURLVALUE#UNKNOWNURLFRAGMENT"
        )
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                failure = HTTPError(self.url, 502, body, {}, RecordingBody(body.encode()))
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assert_private(diagnostic, extra=("UNKNOWNURLVALUE", "UNKNOWNURLFRAGMENT"))
                self.assertIn("other.example.test/errors", diagnostic["response"]["body"])

    def test_cookie_message_redacts_an_unknown_second_cookie(self):
        body = "gateway rejected Cookie: session=COOKIEHEADERSECRET; refresh_cookie=UNKNOWNREFRESHCOOKIE"
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                failure = HTTPError(self.url, 401, body, {}, RecordingBody(body.encode()))
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assert_private(diagnostic, extra=("UNKNOWNREFRESHCOOKIE",))
                self.assertIn("gateway rejected", diagnostic["response"]["body"])

    def test_truncated_json_redacts_escaped_sensitive_keys_and_nested_values(self):
        body = (
            b'{"error":{"code":"bad_auth","\\u0074oken":{"nested":"UNKNOWNESCAPEDTOKEN"},'
            b'"details":{"password":"UNKNOWNNESTEDPASSWORD","access_token":"JSONTOKENSECRET"}},'
            b'"padding":"' + b"x" * 100000 + b'"}'
        )
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                failure = HTTPError(self.url, 401, "unauthorized", {}, RecordingBody(body))
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="http", phase="open_request")
                self.assertTrue(diagnostic["response"]["body_truncated"])
                self.assert_private(diagnostic, extra=("UNKNOWNESCAPEDTOKEN", "UNKNOWNNESTEDPASSWORD"))
                self.assertIn("bad_auth", diagnostic["response"]["body"])

    def test_connect_errors_preserve_nested_dns_ssl_and_reset_causes(self):
        failures = (
            (URLError(socket.gaierror(socket.EAI_NONAME, "Name or service not known")),
             "gaierror", socket.EAI_NONAME),
            (URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
             "SSLCertVerificationError", 1),
            (URLError(ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")),
             "ConnectionResetError", errno.ECONNRESET),
        )
        for transport in (post_json, post_stream):
            for failure, reason_type, reason_errno in failures:
                with self.subTest(transport=transport.__name__, reason=reason_type):
                    with patch("skills_agent.providers.base.request.urlopen", side_effect=failure) as send:
                        with self.assertRaises(ProviderConnectionError) as caught:
                            self.invoke(transport)
                    self.assertEqual(str(caught.exception), "API connection failed")
                    diagnostic = self.assert_diagnostics(caught.exception, kind="connection", phase="open_request")
                    self.assertEqual(diagnostic["exception"]["type"], "URLError")
                    reason = diagnostic["exception"]["reason"]
                    self.assertEqual(reason["type"], reason_type)
                    self.assertEqual(reason["errno"], reason_errno)
                    self.assertTrue(reason["message"])
                    send.assert_called_once()

    def test_connection_exception_messages_redact_credentials_and_sensitive_urls(self):
        for transport in (post_json, post_stream):
            with self.subTest(transport=transport.__name__):
                failure = URLError(ConnectionResetError(
                    errno.ECONNRESET,
                    f"reset calling {self.url}; Authorization: Bearer FREEBEARERSECRET; "
                    "x-api-key=APIHEADERSECRET Cookie: session=COOKIEHEADERSECRET",
                ))
                with patch("skills_agent.providers.base.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderConnectionError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="connection", phase="open_request")
                self.assertIn("reset calling", diagnostic["exception"]["reason"]["message"])
                self.assertEqual(diagnostic["exception"]["reason"]["errno"], errno.ECONNRESET)

    def test_response_read_failures_record_the_transport_phase(self):
        for transport, phase in ((post_json, "read_response"), (post_stream, "read_stream")):
            with self.subTest(transport=transport.__name__):
                response = BrokenResponse(ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"))
                with patch("skills_agent.providers.base.request.urlopen", return_value=response):
                    with self.assertRaises(ProviderConnectionError) as caught:
                        self.invoke(transport)
                diagnostic = self.assert_diagnostics(caught.exception, kind="connection", phase=phase)
                self.assertEqual(diagnostic["exception"]["type"], "ConnectionResetError")
                self.assertEqual(diagnostic["exception"]["errno"], errno.ECONNRESET)
                self.assertTrue(response.closed)

    def test_provider_sse_error_diagnostics_preserve_business_error_and_redact_credentials(self):
        event = {"error": {
            "code": "provider_overloaded", "type": "overloaded_error",
            "message": "Provider is busy. AUTHHEADERSECRET Bearer FREEBEARERSECRET",
            "password": "JSONPASSWORDSECRET", "token": "JSONTOKENSECRET",
        }}
        response = io.BytesIO(b"data: " + json.dumps(event).encode() + b"\n\n")
        response.status = 200
        response.headers = {"X-Request-ID": "request-stream-error"}
        with patch("skills_agent.providers.base.request.urlopen", return_value=response):
            with self.assertRaises(ProviderError) as caught:
                OpenAIProvider("model", "AUTHHEADERSECRET", api_format="chat").complete("", [], [])
        self.assertEqual(str(caught.exception), "API returned an error")
        diagnostic = caught.exception.diagnostics
        self.assertEqual(diagnostic["kind"], "stream")
        self.assertEqual(diagnostic["phase"], "read_stream")
        self.assert_private(diagnostic)
        serialized = json.dumps(diagnostic, ensure_ascii=False)
        self.assertIn("provider_overloaded", serialized)
        self.assertIn("overloaded_error", serialized)
        self.assertIn("Provider is busy.", serialized)
        self.assert_private(caught.exception.raw)
        self.assertTrue(response.closed)

    def test_anthropic_sse_error_redacts_the_persisted_raw_event(self):
        event = {"type": "error", "error": {
            "type": "overloaded_error", "message": "Please retry AUTHHEADERSECRET",
            "api_key": "JSONKEYSECRET", "password": "JSONPASSWORDSECRET",
        }}
        response = io.BytesIO(b"event: error\ndata: " + json.dumps(event).encode() + b"\n\n")
        with patch("skills_agent.providers.base.request.urlopen", return_value=response):
            with self.assertRaises(ProviderError) as caught:
                AnthropicProvider("model", "AUTHHEADERSECRET").complete("", [], [])
        self.assertEqual(str(caught.exception), "Anthropic stream returned an error")
        self.assertEqual(caught.exception.diagnostics["kind"], "stream")
        self.assert_private(caught.exception.diagnostics)
        self.assert_private(caught.exception.raw)
        serialized = json.dumps(caught.exception.diagnostics)
        self.assertIn("overloaded_error", serialized)
        self.assertIn("Please retry", serialized)
        self.assertTrue(response.closed)

    def test_responses_failed_and_incomplete_events_preserve_error_diagnostics(self):
        for kind, status, reason in (("response.failed", "failed", "response.failed"),
                                     ("response.incomplete", "incomplete", "max_output_tokens")):
            with self.subTest(kind=kind):
                event = {"type": kind, "response": {"status": status, "error": {
                    "code": "upstream_error", "type": "server_error",
                    "message": "Provider stopped AUTHHEADERSECRET", "token": "JSONTOKENSECRET",
                }}}
                if kind == "response.incomplete":
                    event["response"]["incomplete_details"] = {"reason": reason}
                response = io.BytesIO(b"data: " + json.dumps(event).encode() + b"\n\n")
                with patch("skills_agent.providers.base.request.urlopen", return_value=response):
                    with self.assertRaises(ProviderError) as caught:
                        OpenAIProvider("model", "AUTHHEADERSECRET", api_format="responses").complete("", [], [])
                self.assertEqual(str(caught.exception), f"OpenAI Responses stopped: {reason}")
                if kind == "response.incomplete":
                    self.assertIsInstance(caught.exception, ProviderOutputLimitError)
                self.assertEqual(caught.exception.diagnostics["kind"], "stream")
                self.assert_private(caught.exception.diagnostics)
                self.assert_private(caught.exception.raw)
                serialized = json.dumps(caught.exception.diagnostics)
                self.assertIn("upstream_error", serialized)
                self.assertIn("server_error", serialized)
                self.assertIn("Provider stopped", serialized)
                self.assertTrue(response.closed)

    def test_real_stream_eof_keeps_transport_metadata_for_all_providers(self):
        cases = (
            (OpenAIProvider("model", "AUTHHEADERSECRET", api_format="chat"), {
                "id": "eof", "choices": [{"index": 0, "delta": {"content": "partial"}}],
            }),
            (AnthropicProvider("model", "AUTHHEADERSECRET"), {
                "type": "message_start", "message": {"id": "eof", "role": "assistant",
                                                       "content": [], "stop_reason": None},
            }),
            (OpenAIProvider("model", "AUTHHEADERSECRET", api_format="responses"), {
                "type": "response.created", "response": {"id": "eof", "status": "in_progress"},
            }),
        )
        for provider, event in cases:
            with self.subTest(provider=type(provider).__name__, event_type=event.get("type", "chat")):
                wire = b"data: " + json.dumps(event).encode() + b"\n\n"
                response = io.BytesIO(wire)
                response.status = 200
                response.headers = {"X-Request-ID": "request-eof"}
                with patch("skills_agent.providers.base.request.urlopen", return_value=response):
                    with self.assertRaises(ProviderConnectionError) as caught:
                        provider.complete("", [], [])
                diagnostic = caught.exception.diagnostics
                self.assertEqual(diagnostic["kind"], "connection")
                self.assertEqual(diagnostic["failure"], "unexpected_eof")
                self.assertEqual(diagnostic["phase"], "read_stream")
                self.assertEqual(diagnostic["response"]["status"], 200)
                self.assertEqual(diagnostic["response"]["headers"]["x-request-id"], "request-eof")
                self.assertEqual(diagnostic["response_bytes"], len(wire))
                self.assertEqual(diagnostic["stream_frames"], 1)
                self.assertGreater(diagnostic["request_bytes"], 0)
                self.assertGreaterEqual(diagnostic["elapsed_seconds"], 0)
                self.assert_private(diagnostic)
                self.assertTrue(response.closed)

    def test_malformed_stream_json_preserves_redacted_protocol_diagnostics(self):
        invalid = '{"error":{"code":"malformed","message":"AUTHHEADERSECRET", "\\u0074oken":"UNKNOWNPROTOCOLTOKEN"}'
        wire = b"data: " + invalid.encode() + b"\n\n"
        response = io.BytesIO(wire)
        response.status = 200
        response.headers = {"X-Request-ID": "request-malformed"}
        with patch("skills_agent.providers.base.request.urlopen", return_value=response):
            with self.assertRaises(ProviderError) as caught:
                self.invoke(post_stream)
        self.assertEqual(str(caught.exception), "API returned invalid stream JSON")
        diagnostic = self.assert_diagnostics(caught.exception, kind="protocol", phase="read_stream")
        self.assert_private(diagnostic, extra=("UNKNOWNPROTOCOLTOKEN",))
        self.assertIn("malformed", diagnostic["response"]["body"])
        self.assertEqual(diagnostic["response"]["status"], 200)
        self.assertEqual(diagnostic["response_bytes"], len(wire))
        self.assertTrue(response.closed)

    def test_http_success_status_with_json_error_keeps_api_diagnostics(self):
        body = json.dumps({"error": {
            "code": "quota_exceeded", "type": "rate_limit_error",
            "message": "Quota exhausted AUTHHEADERSECRET", "api_key": "JSONKEYSECRET",
        }}).encode()
        response = io.BytesIO(body)
        response.status = 200
        response.headers = {"X-Request-ID": "request-api-error"}
        with patch("skills_agent.providers.base.request.urlopen", return_value=response):
            with self.assertRaises(ProviderError) as caught:
                self.invoke(post_json)
        self.assertEqual(str(caught.exception), "API returned an error")
        diagnostic = self.assert_diagnostics(caught.exception, kind="api", phase="read_response")
        self.assertEqual(diagnostic["response"]["status"], 200)
        self.assertEqual(diagnostic["response_bytes"], len(body))
        self.assertIn("quota_exceeded", diagnostic["response"]["body"])
        self.assertIn("Quota exhausted", diagnostic["response"]["body"])
        self.assertTrue(response.closed)

    def test_provider_supplied_responses_failure_redacts_dynamic_reason_without_invented_timings(self):
        event = {"type": "response.failed", "response": {
            "status": "failed", "incomplete_details": {
                "reason": "provider failed AUTHHEADERSECRET Bearer UNKNOWNREASONTOKEN",
            }, "error": {"code": "upstream_failure", "password": "JSONPASSWORDSECRET"},
        }}
        with patch("skills_agent.providers.responses.post_stream", return_value=(value for value in [event])):
            with self.assertRaises(ProviderError) as caught:
                OpenAIProvider("model", "AUTHHEADERSECRET", api_format="responses").complete("", [], [])
        self.assert_private(str(caught.exception), extra=("UNKNOWNREASONTOKEN",))
        self.assertIn("provider failed", str(caught.exception))
        diagnostic = caught.exception.diagnostics
        self.assertEqual(diagnostic["kind"], "stream")
        self.assertEqual(diagnostic["source"], "provider")
        self.assertEqual(diagnostic["phase"], "read_stream")
        for field in ("elapsed_seconds", "timeout_seconds", "request_bytes", "response_bytes", "stream_frames"):
            self.assertNotIn(field, diagnostic)
        self.assert_private(diagnostic, extra=("UNKNOWNREASONTOKEN",))
        self.assert_private(caught.exception.raw, extra=("UNKNOWNREASONTOKEN",))
        self.assertIn("upstream_failure", json.dumps(diagnostic))


if __name__ == "__main__":
    unittest.main()
