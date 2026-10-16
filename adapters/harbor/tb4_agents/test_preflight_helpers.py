"""Keyless checks for standalone live-preflight routing; no network requests."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ASSETS = Path(__file__).resolve().parents[1] / "assets"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("tb4_responses_probe", ASSETS / "codex/probe-responses.py")
relay = load("tb4_audit_proxy", ASSETS / "common/audit_proxy.py")


class PreflightRoutingTests(unittest.TestCase):
    def test_standalone_helpers_help_works_outside_repository_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment.pop("DEEPSEEK_API_KEY", None)
            environment.pop("PYTHONPATH", None)
            for path in (ASSETS / "codex/probe-responses.py", ASSETS / "codex/tls-auth-only-smoke.py",
                         ASSETS / "common/audit_proxy.py"):
                with self.subTest(script=path.name):
                    result = subprocess.run([sys.executable, str(path), "--help"], cwd=directory,
                                            env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_probe_defaults_to_current_gateway_despite_legacy_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            response = io.BytesIO(json.dumps({"model": probe.MODEL, "status": "completed", "output": []}).encode())
            response.status = 200
            opener = SimpleNamespace(open=Mock(return_value=response))
            path = Path(directory) / "report.json"
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "OFFLINE_SENTINEL",
                                          "DEEPSEEK_BASE_URL": "https://rehdasu.cn/v1"}), \
                    patch.object(probe.urllib.request, "build_opener", return_value=opener) as build, \
                    contextlib.redirect_stdout(io.StringIO()):
                probe.main(["--report", str(path)])
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://yuzhiapi.xyz/v1/responses")
            self.assertEqual(json.loads(request.data)["model"], "deepseek-v4.1-flash")
            self.assertIsInstance(build.call_args.args[0], probe.NoRedirect)
            self.assertNotIn("OFFLINE_SENTINEL", path.read_text())
            self.assertEqual(json.loads(path.read_text())["endpoint"], request.full_url)

    def test_probe_rejects_unapproved_host_before_reading_key_or_opening_network(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(probe.urllib.request, "build_opener") as build:
            with self.assertRaises(ValueError):
                probe.main(["--api-base", "https://unapproved.example"])
            build.assert_not_called()

    def test_preflight_credentials_cannot_follow_http_redirects(self):
        request = probe.urllib.request.Request("https://yuzhiapi.xyz/v1/responses")
        for handler in (probe.NoRedirect(), relay.NoRedirect()):
            with self.subTest(handler=type(handler).__module__):
                self.assertIsNone(handler.redirect_request(
                    request, None, 302, "Found", {}, "https://rehdasu.cn/v1/responses"))

    def test_relay_current_model_accepted_and_legacy_requires_explicit_selection(self):
        # Exercise rejection/routing without binding a socket or contacting an upstream.
        def exercise(model):
            server = SimpleNamespace(model="deepseek-v4.1-flash", upstream_base="https://yuzhiapi.xyz",
                                     record=Mock(), opener=SimpleNamespace(open=Mock(side_effect=OSError)))
            raw = json.dumps({"model": model}).encode()
            handler = SimpleNamespace(path="/v1/responses", headers={"Content-Length": str(len(raw))},
                                      rfile=io.BytesIO(raw), server=server, reject=Mock())
            relay.Relay.do_POST(handler)
            return handler, server
        handler, server = exercise("deepseek-v4.1-flash")
        self.assertEqual(server.opener.open.call_args.args[0].full_url,
                         "https://yuzhiapi.xyz/v1/responses")
        handler.reject.assert_called_once_with(502)
        handler, server = exercise("deepseek-v4-1-flash-260910")
        handler.reject.assert_called_once_with(400)
        server.opener.open.assert_not_called()
        self.assertIn("deepseek-v4-1-flash-260910", relay.MODELS)


if __name__ == "__main__":
    unittest.main()
