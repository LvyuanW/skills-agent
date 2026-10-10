import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
          / "investigation" / "scripts" / "fetch.py")
HTML = ("<!doctype html><html><head><title>原始来源</title>"
        "<style>.secret {display:none}</style></head><body><h1>事实 &amp; 方法</h1>"
        "<script>not evidence()</script><p>第一段 <strong>正文</strong>。</p>"
        "<template>template content</template></body></html>").encode("utf-8")
TEXT = "第一行\r\n\t第二行\n最后无换行".encode("utf-8")
JSON = b'{"duplicate": 1, "duplicate": 2, "number": 1.00, "values": [1, 2, 3]}'


class SourceHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *arguments):
        pass

    def do_GET(self):
        type(self).requests.append({"path": self.path, "headers": dict(self.headers.items())})
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/html")
            self.end_headers()
            return
        if self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
            return
        if self.path == "/credential-redirect":
            self.send_response(302)
            self.send_header("Location", f"http://name:private-password@{self.headers['Host']}/html")
            self.end_headers()
            return
        if self.path in {"/slow-redirect-one", "/slow-redirect-two"}:
            time.sleep(0.08)
            self.send_response(302)
            self.send_header("Location", "/slow-redirect-two" if self.path.endswith("one") else "/text")
            self.end_headers()
            return
        if self.path == "/redirect-with-drip-body":
            self.send_response(302)
            self.send_header("Location", "/text")
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.path == "/slow":
            time.sleep(0.3)
        if self.path == "/chunked-partial":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"7\r\npartial\r\n")
            return
        if self.path == "/drip":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        sources = {
            "/html": (200, "text/html; charset=utf-8", HTML),
            "/text": (200, "text/plain; charset=utf-8", TEXT),
            "/json": (200, "application/json", JSON),
            "/invalid-json": (200, "application/json", b'{"bad": }'),
            "/invalid-utf8": (200, "text/plain", b"not utf8 \xff"),
            "/binary": (200, "application/octet-stream", b"\x00\xff\x01binary"),
            "/large": (200, "text/plain", b"begin\n" + b"continued\n" * 10000 + b"end\n"),
            "/partial": (200, "text/plain", b"saved partial bytes"),
            "/gzip": (200, "text/html; charset=utf-8", gzip.compress(HTML, mtime=0)),
            "/slow": (200, "text/plain", b"late response"),
        }
        status, media_type, body = sources.get(self.path, (404, "text/plain", b"actual missing page"))
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body) + (10 if self.path == "/partial" else 0)))
        self.send_header("X-Origin", "local-fixture")
        if self.path == "/gzip":
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


class InvestigationAcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.evidence = self.workspace / "evidence with spaces"

    def fetch(self, path, *arguments, environment=None):
        url = path if "://" in path else self.origin + path
        process = subprocess.run([sys.executable, str(SCRIPT), url,
                                  "--output-dir", str(self.evidence), *map(str, arguments)],
                                 capture_output=True, cwd=self.workspace, timeout=10,
                                 env=environment)
        return process, json.loads(process.stdout)

    def metadata(self, result):
        return json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))

    def test_redirect_preserves_original_body_real_headers_and_source_metadata(self):
        before = datetime.now(timezone.utc)
        process, result = self.fetch("/redirect", "--max-chars", 12)
        self.assertEqual(process.returncode, 0, process.stderr)
        metadata = self.metadata(result)
        self.assertEqual(metadata["requested_url"], self.origin + "/redirect")
        self.assertEqual(metadata["final_url"], self.origin + "/html")
        self.assertEqual(metadata["status"], 200)
        self.assertEqual(metadata["redirects"], [{"from": self.origin + "/redirect",
                                                "status": 302, "to": self.origin + "/html"}])
        self.assertIn(["X-Origin", "local-fixture"], metadata["headers"])
        self.assertEqual(Path(result["body_path"]).read_bytes(), HTML)
        self.assertEqual(result["sha256"], hashlib.sha256(HTML).hexdigest())
        self.assertTrue(result["response_body_complete"])
        acquired = datetime.fromisoformat(metadata["retrieved_at"].replace("Z", "+00:00"))
        self.assertGreaterEqual(acquired, before)
        self.assertLessEqual(acquired, datetime.now(timezone.utc))
        self.assertTrue(result["preview"]["truncated"])
        self.assertEqual(len(result["preview"]["text"]), 12)
        extracted = Path(result["preview"]["path"]).read_text(encoding="utf-8")
        self.assertIn("事实 & 方法", extracted)
        self.assertIn("第一段 正文。", extracted)
        self.assertNotIn("not evidence", extracted)
        self.assertNotIn("template content", extracted)

    def test_http_error_is_failure_and_retains_actual_response(self):
        process, result = self.fetch("/missing")
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result["status"], 404)
        self.assertFalse(result["ok"])
        self.assertTrue(result["response_body_complete"])
        self.assertEqual(Path(result["body_path"]).read_bytes(), b"actual missing page")
        self.assertIn("404", result["error"])
        self.assertIn("actual missing page", result["preview"]["text"])

    def test_multiple_fetches_keep_independent_evidence_without_overwriting(self):
        _, first = self.fetch("/text")
        files = {path: path.read_bytes() for path in Path(first["metadata_path"]).parent.iterdir()}
        _, second = self.fetch("/text")
        self.assertNotEqual(first["metadata_path"], second["metadata_path"])
        self.assertNotEqual(first["body_path"], second["body_path"])
        for path, original in files.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list(self.evidence.iterdir())), 2)

    def test_text_and_json_preserve_characters_line_endings_keys_and_numbers(self):
        for source, expected in [("/text", TEXT), ("/json", JSON)]:
            with self.subTest(source=source):
                process, result = self.fetch(source)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(result["preview"]["text"], expected.decode("utf-8"))
                self.assertEqual(Path(result["preview"]["path"]).read_bytes(), expected)
                self.assertFalse(result["preview"]["truncated"])

    def test_bounded_preview_keeps_full_source_and_extracted_text(self):
        process, result = self.fetch("/large", "--max-chars", 23)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(result["preview"]["text"]), 23)
        self.assertTrue(result["preview"]["truncated"])
        source = Path(result["body_path"]).read_bytes()
        self.assertTrue(source.endswith(b"end\n"))
        self.assertGreater(len(source), 90000)
        self.assertEqual(Path(result["preview"]["path"]).read_bytes(), source)
        self.assertLess(len(process.stdout), 4000)

    def test_extraction_limit_retains_whole_response_and_reports_the_gap(self):
        for mode, expected_code in [("auto", 0), ("text", 1)]:
            with self.subTest(mode=mode):
                process, result = self.fetch("/large", "--extract", mode, "--max-extract-bytes", 100)
                self.assertEqual(process.returncode, expected_code, process.stderr)
                self.assertTrue(result["response_body_complete"])
                self.assertTrue(Path(result["body_path"]).read_bytes().endswith(b"end\n"))
                self.assertIn("extraction byte limit", result["preview"]["error"])
                self.assertNotIn("path", result["preview"])

    def test_body_size_limit_preserves_exact_prefix_as_incomplete_failure(self):
        process, result = self.fetch("/large", "--max-body-bytes", 31)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(result["ok"])
        self.assertFalse(result["response_body_complete"])
        self.assertEqual(result["status"], 200)
        prefix = (b"begin\n" + b"continued\n" * 10000)[:31]
        self.assertEqual(Path(result["body_path"]).read_bytes(), prefix)
        self.assertEqual(result["sha256"], hashlib.sha256(prefix).hexdigest())
        self.assertEqual(result["body_bytes"], 31)
        self.assertIn("body byte limit", result["error"])
        self.assertIsNone(result["preview"])

    def test_exact_body_size_limit_allows_complete_response(self):
        process, result = self.fetch("/text", "--max-body-bytes", len(TEXT))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(result["response_body_complete"])
        self.assertEqual(Path(result["body_path"]).read_bytes(), TEXT)

    def test_gzip_preserves_received_compressed_bytes_and_extracts_actual_text(self):
        process, result = self.fetch("/gzip")
        self.assertEqual(process.returncode, 0, process.stderr)
        raw = Path(result["body_path"]).read_bytes()
        self.assertEqual(raw, gzip.compress(HTML, mtime=0))
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertIn("事实 & 方法", result["preview"]["text"])
        self.assertEqual(self.metadata(result)["content_encoding"], "gzip")

    def test_binary_and_disabled_extraction_do_not_claim_text_contents(self):
        for path, extra in [("/binary", ()), ("/html", ("--extract", "none"))]:
            with self.subTest(path=path):
                process, result = self.fetch(path, *extra)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertTrue(result["preview"]["skipped"])
                self.assertNotIn("text", result["preview"])
                self.assertTrue(Path(result["body_path"]).is_file())

    def test_invalid_extraction_preserves_source_and_never_invents_readable_content(self):
        for path, extra, expected_code in [("/invalid-json", ("--extract", "json"), 1),
                                           ("/invalid-utf8", (), 0)]:
            with self.subTest(path=path):
                process, result = self.fetch(path, *extra)
                self.assertEqual(process.returncode, expected_code, process.stderr)
                self.assertTrue(result["response_body_complete"])
                self.assertEqual(result["status"], 200)
                self.assertIn("error", result["preview"])
                self.assertNotIn("text", result["preview"])
                self.assertTrue(Path(result["body_path"]).is_file())

    def test_incomplete_transfer_records_partial_bytes_and_actual_status(self):
        process, result = self.fetch("/partial")
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(result["response_body_complete"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(Path(result["body_path"]).read_bytes(), b"saved partial bytes")
        self.assertEqual(result["body_bytes"], len(b"saved partial bytes"))
        self.assertIn("Incomplete response body", result["error"])
        self.assertIsNone(result["preview"])

    def test_timeout_is_explicit_failure_with_preserved_attempt_metadata(self):
        process, result = self.fetch("/slow", "--timeout", 0.05)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(result["response_body_complete"])
        self.assertIsNone(result["status"])
        self.assertIsNone(result["retrieved_at"])
        self.assertIsNotNone(result["finished_at"])
        self.assertTrue(Path(result["metadata_path"]).is_file())
        self.assertIn("timed out", result["error"])

    def test_incomplete_chunked_transfer_retains_obtainable_partial_bytes(self):
        process, result = self.fetch("/chunked-partial")
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(result["response_body_complete"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(Path(result["body_path"]).read_bytes(), b"partial")
        self.assertEqual(result["sha256"], hashlib.sha256(b"partial").hexdigest())
        self.assertIn("IncompleteRead", result["error"])

    def test_total_deadline_stops_continuous_drip_and_keeps_acquired_bytes(self):
        started = time.monotonic()
        process, result = self.fetch("/drip", "--timeout", 0.12)
        elapsed = time.monotonic() - started
        self.assertNotEqual(process.returncode, 0)
        self.assertLess(elapsed, 0.6, "Continuous arrivals must not reset the total network deadline")
        self.assertFalse(result["response_body_complete"])
        self.assertEqual(result["status"], 200)
        self.assertGreater(result["body_bytes"], 0)
        self.assertLess(result["body_bytes"], 100)
        self.assertEqual(Path(result["body_path"]).read_bytes(), b"x" * result["body_bytes"])
        self.assertIn("timed out", result["error"])

    def test_redirects_share_one_deadline(self):
        started = time.monotonic()
        process, result = self.fetch("/slow-redirect-one", "--timeout", 0.12)
        self.assertNotEqual(process.returncode, 0)
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertFalse(result["response_body_complete"])
        self.assertIn("timed out", result["error"])
        self.assertEqual(len(self.metadata(result)["redirects"]), 1)

    def test_redirect_body_is_not_drained_without_a_deadline(self):
        started = time.monotonic()
        process, result = self.fetch("/redirect-with-drip-body", "--timeout", 0.3)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertEqual(Path(result["body_path"]).read_bytes(), TEXT)
        self.assertEqual(result["final_url"], self.origin + "/text")

    def test_redirect_loop_has_bounded_attempts_and_preserves_actual_http_error(self):
        process, result = self.fetch("/loop")
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result["status"], 302)
        self.assertTrue(result["response_body_complete"])
        self.assertIn("Redirect limit reached", result["error"])
        self.assertLessEqual(len(self.metadata(result)["redirects"]), 6)

    def test_urls_with_credentials_and_non_http_schemes_fail_without_sending_them(self):
        urls = [f"http://name:private-password@127.0.0.1:{self.server.server_address[1]}/html",
                "file:///etc/passwd", "ftp://example.org/source"]
        count = len(SourceHandler.requests)
        for url in urls:
            with self.subTest(url=url):
                process, result = self.fetch(url)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(result["ok"])
                self.assertNotIn(b"private-password", process.stdout + process.stderr)
        self.assertEqual(len(SourceHandler.requests), count)
        self.assertFalse(self.evidence.exists())

    def test_credential_redirect_is_blocked_before_a_second_request(self):
        count = len(SourceHandler.requests)
        process, result = self.fetch("/credential-redirect")
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(len(SourceHandler.requests), count + 1)
        metadata = self.metadata(result)
        self.assertTrue(metadata["redirects"][0]["blocked"])
        self.assertNotIn("private-password", json.dumps(metadata))
        self.assertIn("Redirect target", result["error"])

    def test_does_not_inherit_proxy_credentials_or_send_authorization_and_cookies(self):
        environment = {**os.environ,
                       "http_proxy": "http://secret-name:secret-password@127.0.0.1:1",
                       "HTTP_PROXY": "http://secret-name:secret-password@127.0.0.1:1",
                       "NO_PROXY": "", "no_proxy": ""}
        process, result = self.fetch("/text", environment=environment)
        self.assertEqual(process.returncode, 0, process.stderr)
        headers = {key.lower(): value for key, value in SourceHandler.requests[-1]["headers"].items()}
        self.assertNotIn("authorization", headers)
        self.assertNotIn("proxy-authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertTrue(result["response_body_complete"])


if __name__ == "__main__":
    unittest.main()
