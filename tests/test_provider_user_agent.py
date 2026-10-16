from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError
import io
import json
import platform
import threading
import unittest
from unittest.mock import patch

from skills_agent.providers import AnthropicProvider, OpenAIProvider
from skills_agent.providers import base


USER_AGENT = "skills-agent/9.8.7 (TestOS; test-machine) Python/3.99.1"


def sse(*events):
    return b"".join(b"data: " + (b"[DONE]" if event is None else json.dumps(event).encode()) + b"\n\n"
                    for event in events)


class ProviderUserAgentTests(unittest.TestCase):
    def invoke(self, transport, headers, url="https://gateway.example.test/v1/test"):
        result = transport(url, {"prompt": "test"}, headers)
        return list(result) if transport is base.post_stream else result

    def capture(self, transport, headers):
        response = io.BytesIO(sse({"ok": True}, None) if transport is base.post_stream else b'{"ok":true}')
        with patch("skills_agent.providers.base.request.urlopen", return_value=response) as send:
            self.invoke(transport, headers)
        send.assert_called_once()
        self.assertTrue(response.closed)
        return send.call_args.args[0]

    def test_identity_uses_distribution_version_and_current_runtime(self):
        with patch("skills_agent.providers.base.metadata.version", return_value="9.8.7") as version, \
                patch("skills_agent.providers.base.platform.system", return_value="TestOS"), \
                patch("skills_agent.providers.base.platform.machine", return_value="test-machine"), \
                patch("skills_agent.providers.base.platform.python_version", return_value="3.99.1"):
            self.assertEqual(base._user_agent(), USER_AGENT)
        version.assert_called_once_with("skills-cli-agent")
        with patch("skills_agent.providers.base.metadata.version", return_value="2.0.0"):
            self.assertEqual(base._user_agent(),
                             f"skills-agent/2.0.0 ({platform.system()}; {platform.machine()}) "
                             f"Python/{platform.python_version()}")

    def test_uninstalled_source_uses_dev_version(self):
        with patch("skills_agent.providers.base.metadata.version", side_effect=PackageNotFoundError) as version:
            self.assertEqual(base._user_agent(),
                             f"skills-agent/dev ({platform.system()}; {platform.machine()}) "
                             f"Python/{platform.python_version()}")
        version.assert_called_once_with("skills-cli-agent")

    def test_both_transports_add_identity_without_mutating_other_headers(self):
        for transport in (base.post_json, base.post_stream):
            with self.subTest(transport=transport.__name__), \
                    patch("skills_agent.providers.base._user_agent", return_value=USER_AGENT):
                headers = {"Authorization": "Bearer test-key", "X-Request-ID": "test-request"}
                original = deepcopy(headers)
                request = self.capture(transport, headers)
                sent = {name.lower(): value for name, value in request.header_items()}
                self.assertEqual(sent["user-agent"], USER_AGENT)
                self.assertEqual(sent["authorization"], "Bearer test-key")
                self.assertEqual(sent["x-request-id"], "test-request")
                self.assertEqual(sent["content-type"], "application/json")
                self.assertEqual(sent.get("accept"), "text/event-stream" if transport is base.post_stream else None)
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(json.loads(request.data), {"prompt": "test"})
                self.assertEqual(headers, original)

    def test_explicit_identity_and_content_headers_override_defaults_case_insensitively(self):
        for transport in (base.post_json, base.post_stream):
            for name in ("User-Agent", "user-agent", "USER-AGENT", "uSeR-aGeNt"):
                with self.subTest(transport=transport.__name__, header=name), \
                        patch("skills_agent.providers.base._user_agent", return_value=USER_AGENT):
                    headers = {name: "custom-client/2", "content-type": "application/custom+json", "accept": "*/*"}
                    original = deepcopy(headers)
                    request = self.capture(transport, headers)
                    sent = request.header_items()
                    self.assertEqual([value for key, value in sent if key.lower() == "user-agent"], ["custom-client/2"])
                    self.assertEqual([value for key, value in sent if key.lower() == "content-type"], ["application/custom+json"])
                    self.assertEqual([value for key, value in sent if key.lower() == "accept"], ["*/*"])
                    self.assertEqual(headers, original)

    def test_generation_and_remote_token_counting_all_use_the_shared_identity(self):
        chat = OpenAIProvider("test", "test-key", "https://api.deepseek.com", api_format="chat")
        responses = OpenAIProvider("test", "test-key", api_format="responses")
        anthropic = AnthropicProvider("test", "test-key")
        cases = (
            (chat.complete, "/chat/completions", sse({"choices": [{"index": 0, "delta": {"content": "done"},
                                                                   "finish_reason": "stop"}]}, None)),
            (responses.complete, "/responses", sse({"type": "response.completed", "response": {
                "status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "done"}]}]}})),
            (responses.count_tokens, "/responses/input_tokens", b'{"input_tokens":42}'),
            (anthropic.complete, "/messages", sse(
                {"type": "message_start", "message": {"role": "assistant", "content": [], "stop_reason": None}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"})),
            (anthropic.count_tokens, "/messages/count_tokens", b'{"input_tokens":42}'),
        )
        for call, path, body in cases:
            with self.subTest(path=path), patch("skills_agent.providers.base._user_agent", return_value=USER_AGENT), \
                    patch("skills_agent.providers.base.request.urlopen", return_value=io.BytesIO(body)) as send:
                call("", [], [])
                request = send.call_args.args[0]
                self.assertTrue(request.full_url.endswith(path))
                sent = {name.lower(): value for name, value in request.header_items()}
                self.assertEqual(sent["user-agent"], USER_AGENT)
                if call.__self__ is anthropic:
                    self.assertEqual(sent["x-api-key"], "test-key")
                    self.assertEqual(sent["anthropic-version"], "2023-06-01")
                else:
                    self.assertEqual(sent["authorization"], "Bearer test-key")

    def test_local_gateway_receives_one_identity_header_on_the_wire(self):
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                observed.append((self.path, self.headers.get_all("User-Agent")))
                body = sse({"ok": True}, None) if self.path == "/stream" else b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if self.path == "/stream" else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("skills_agent.providers.base._user_agent", return_value=USER_AGENT):
                for transport, path in ((base.post_json, "/json"), (base.post_stream, "/stream")):
                    self.invoke(transport, {}, f"http://127.0.0.1:{server.server_port}{path}")
                self.invoke(base.post_json, {"user-agent": "custom-client/2"},
                            f"http://127.0.0.1:{server.server_port}/override")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(observed, [("/json", [USER_AGENT]), ("/stream", [USER_AGENT]),
                                    ("/override", ["custom-client/2"])])


if __name__ == "__main__":
    unittest.main()
