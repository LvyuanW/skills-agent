import gzip
import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPTS = (Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
           / "investigation" / "scripts")
SCRIPT = SCRIPTS / "source.py"
FETCH = SCRIPTS / "fetch.py"


class Handler(BaseHTTPRequestHandler):
    requests = 0
    html = b'<html><p>CPU    usage is 418m.</p><script>not evidence()</script></html>'

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).requests += 1
        if self.path == "/gzip":
            body = gzip.compress(self.html, mtime=0)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
        elif self.path == "/duplicate-json":
            body = b'{"limit": 128, "limit": 256}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = b"actual missing page"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SourceEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.cwd = self.root / "unrelated working directory"
        self.cwd.mkdir()
        self.store = self.root / "sources with spaces"
        self.number = 0

    def file(self, name, data):
        path = self.root / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def json_file(self, value):
        self.number += 1
        return self.file(f"request-{self.number}.json", json.dumps(value, ensure_ascii=False))

    def run_cli(self, *args, script=SCRIPT):
        process = subprocess.run([sys.executable, "-I", str(script), *map(str, args)],
                                 cwd=self.cwd, capture_output=True, timeout=10)
        self.assertTrue(process.stdout, process.stderr)
        return process, json.loads(process.stdout)

    def capture(self, data, name="original.txt", *args):
        path = self.file(name, data)
        process, result = self.run_cli("capture", path, "--store", self.store, *args)
        self.assertEqual(process.returncode, 0, process.stderr)
        return path, Path(result["source"]), result

    def locate(self, source, request, *args):
        return self.run_cli("locate", "--source", source, "--request", self.json_file(request), *args)

    def verify(self, result, *args):
        return self.run_cli("verify", "--bindings", self.json_file(result), *args)

    def snapshot(self, source, key="raw"):
        manifest = json.loads(source.read_text())
        return source.parent / manifest["objects"][key]["path"]

    def test_capture_is_independent_of_cwd_and_retains_exact_raw_bytes(self):
        raw = b'\xef\xbb\xbf' + "第一行\r\n  真实引用\r\n尾行".encode()
        path, source, captured = self.capture(raw)
        self.assertEqual(self.snapshot(source).read_bytes(), raw)
        self.assertEqual(captured["objects"]["raw"]["sha256"], hashlib.sha256(raw).hexdigest())
        process, result = self.locate(source, {"quote": "  真实引用\r\n", "claim_id": "C1"})
        self.assertEqual(process.returncode, 0, process.stderr)
        match = result["matches"][0]
        self.assertEqual(match["locator"]["start_line"], 2)
        self.assertEqual(match["locator"]["start_column"], 1)
        self.assertEqual(match["excerpt"]["text"], "  真实引用\r\n")
        self.assertEqual(raw[match["locator"]["byte_start"]:match["locator"]["byte_end"]],
                         "  真实引用\r\n".encode())
        self.assertEqual(match["locator"]["start_offset"], len("第一行\r\n"))
        self.assertEqual(result["semantic_support"], "not_evaluated")
        process, verified = self.verify(result)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(verified["checks"][0]["claim_id"], "C1")
        self.assertEqual(path.read_bytes(), raw)

    def test_repeated_quote_is_ambiguous_and_range_disambiguates(self):
        _, source, _ = self.capture("first context\nlimit: 256\nsecond context\nlimit: 256\n")
        process, result = self.locate(source, {"quote": "limit: 256"})
        self.assertEqual(process.returncode, 1)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual([m["locator"]["start_line"] for m in result["matches"]], [2, 4])
        self.assertNotIn("binding", result)
        process, verified = self.verify(result)
        self.assertEqual(process.returncode, 1)
        process, selected = self.locate(source, {"quote": "limit: 256",
                                                "range": {"start_line": 4, "end_line": 4}})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(selected["matches"][0]["locator"]["start_line"], 4)

    def test_match_limit_does_not_claim_complete_search_or_first_match_unique(self):
        _, source, _ = self.capture("same\nsame\nsame\nsame\n")
        process, result = self.locate(source, {"quote": "same"}, "--max-matches", 1)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["matches"]), 1)
        self.assertFalse(result["search"]["complete"])
        self.assertTrue(result["search"]["count_is_lower_bound"])

    def test_whitespace_matching_returns_candidate_without_exact_binding(self):
        _, source, _ = self.capture("CPU\t  usage\r\nis 418m\n")
        process, exact = self.locate(source, {"quote": "CPU usage is 418m"})
        self.assertEqual(process.returncode, 1)
        self.assertEqual(exact["status"], "not_found")
        process, normalized = self.locate(source, {"quote": "CPU usage is 418m", "match": "whitespace"})
        self.assertEqual(process.returncode, 1)
        self.assertEqual(normalized["status"], "normalized_candidate")
        self.assertEqual(normalized["matches"][0]["excerpt"]["text"], "CPU\t  usage\r\nis 418m")
        self.assertNotIn("binding", normalized)
        process, _ = self.verify(normalized)
        self.assertEqual(process.returncode, 1)

    def test_text_ranges_and_columns_are_end_exclusive(self):
        _, source, _ = self.capture("αβγ\r\nsecond\n")
        process, result = self.locate(source, {"range": {"start_line": 1, "end_line": 1,
                                                         "start_column": 2, "end_column": 3}})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(result["matches"][0]["excerpt"]["text"], "β")
        process, _ = self.verify(result)
        self.assertEqual(process.returncode, 0)

    def test_excerpt_budget_does_not_truncate_the_bound_content(self):
        _, source, _ = self.capture("before\n" + "x" * 1000 + "\nafter\n")
        process, result = self.locate(source, {"range": {"start_line": 2, "end_line": 2}}, "--max-chars", 12)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(result["preview"]["truncated"])
        self.assertEqual(result["matches"][0]["excerpt"]["text"], "x" * 12)
        self.assertEqual(result["binding"]["content_sha256"], hashlib.sha256(("x" * 1000 + "\n").encode()).hexdigest())
        process, result = self.verify(result, "--max-chars", 7)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(result["preview"]["truncated"])

    def test_original_changes_do_not_retarget_historical_evidence(self):
        original, source, _ = self.capture("old observation\n")
        _, located = self.locate(source, {"quote": "old observation"})
        original.write_text("new observation\n")
        process, historical = self.verify(located)
        self.assertEqual(process.returncode, 0)
        process, current = self.verify(located, "--check-origin")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(current["checks"][0]["status"], "historical_only")
        self.assertEqual(current["checks"][0]["snapshot"], "unchanged")
        self.assertEqual(current["checks"][0]["origin"]["status"], "changed")
        _, second = self.run_cli("capture", original, "--store", self.store)
        self.assertNotEqual(second["source"], str(source))
        self.assertEqual(self.snapshot(source).read_text(), "old observation\n")

    def test_snapshot_tampering_is_an_integrity_error(self):
        _, source, _ = self.capture("old observation\n")
        _, located = self.locate(source, {"quote": "old observation"})
        self.snapshot(source).write_text("new observation\n")
        process, bad = self.locate(source, {"quote": "new observation"})
        self.assertEqual(process.returncode, 2)
        self.assertIn("integrity mismatch", bad["error"])
        process, bad = self.verify(located)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(bad["checks"][0]["status"], "unresolved")

    def test_manifest_rewrite_cannot_silently_change_an_existing_binding(self):
        _, source, _ = self.capture("observation\n")
        _, located = self.locate(source, {"quote": "observation"})
        manifest = json.loads(source.read_text())
        manifest["origin"]["path"] = "/a/different/origin"
        source.write_text(json.dumps(manifest))
        process, bad = self.verify(located)
        self.assertEqual(process.returncode, 1)
        self.assertIn("version changed", bad["checks"][0]["error"])

    def test_malformed_origin_and_acquisition_cannot_create_a_binding(self):
        for field, value in [("origin", {"kind": "file", "path": {}}),
                             ("acquisition", []),
                             ("acquisition", {"scope": "full_file", "complete": "yes"})]:
            with self.subTest(field=field, value=value):
                _, source, _ = self.capture("observation\n")
                manifest = json.loads(source.read_text())
                manifest[field] = value
                source.write_text(json.dumps(manifest))
                process, result = self.locate(source, {"quote": "observation"})
                self.assertEqual(process.returncode, 2)
                self.assertEqual(result["status"], "error")
                self.assertNotIn("binding", result)
                self.assertNotIn(b"Traceback", process.stderr)

    def test_origin_check_error_cannot_leave_a_resolved_overall_result(self):
        _, source, _ = self.capture("observation\n")
        _, located = self.locate(source, {"quote": "observation"})
        spec = importlib.util.spec_from_file_location("source_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        arguments = SimpleNamespace(bindings=self.json_file(located), check_origin=True,
                                    max_bytes=1024 * 1024)
        # A post-snapshot origin acquisition failure must invalidate the requested current check.
        with mock.patch.object(module.COMMON, "fingerprint", side_effect=TypeError("origin acquisition failed")):
            result = module.verify(arguments, module.Budget(12000))
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["checks"][0]["status"], "unresolved")
        self.assertEqual(result["checks"][0]["snapshot"], "unchanged")
        self.assertIn("origin acquisition failed", result["checks"][0]["error"])

    def test_explicit_prefix_has_independent_acquisition_search_and_preview_status(self):
        _, source, captured = self.capture("abc\nnot captured\n", "log.txt", "--prefix-bytes", 4)
        self.assertFalse(captured["acquisition"]["complete"])
        process, result = self.locate(source, {"quote": "missing"}, "--max-chars", 2)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(result["source"]["acquisition"]["complete"])
        self.assertTrue(result["search"]["complete"])
        self.assertEqual(result["search"]["scope"]["end_offset"], 4)
        self.assertFalse(result["preview"]["truncated"])

    def test_capture_byte_budget_does_not_silently_drop_tail(self):
        path = self.file("large.txt", b"a" * 100)
        process, result = self.run_cli("capture", path, "--store", self.store, "--max-bytes", 10)
        self.assertEqual(process.returncode, 2)
        self.assertIn("byte budget", result["error"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        path = self.root / "not-a-file"
        os.mkfifo(path)
        process = subprocess.run([sys.executable, "-I", str(SCRIPT), "capture", str(path),
                                  "--store", str(self.store)], capture_output=True,
                                 cwd=self.cwd, timeout=2)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertIn("regular file", json.loads(process.stdout)["error"])

    def test_text_byte_positions_include_bom_only_when_it_is_part_of_decoded_text(self):
        raw = b'\xef\xbb\xbf' + "αβ\r\nγ".encode()
        _, source, _ = self.capture(raw)
        for encoding in ["utf-8", "utf-8-sig"]:
            with self.subTest(encoding=encoding):
                process, located = self.locate(source, {"quote": "β\r\nγ", "encoding": encoding})
                self.assertEqual(process.returncode, 0)
                where = located["matches"][0]["locator"]
                self.assertEqual(where["byte_start"], 5)
                self.assertEqual(raw[where["byte_start"]:where["byte_end"]].decode(), "β\r\nγ")
                self.assertEqual(where["start_offset"], 2 if encoding == "utf-8" else 1)

    def test_json_pointer_preserves_value_precision_and_distinguishes_missing_from_null(self):
        _, source, _ = self.capture('{"a/b":{"~key":[null,1.234567890123456789,true]},"escape":"a\\nb"}', "data.json")
        process, result = self.locate(source, {"format": "json", "pointer": "/a~1b/~0key/1"})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(result["matches"][0]["excerpt"]["text"], "1.234567890123456789")
        self.assertEqual(result["matches"][0]["representation"], "parsed_json_value")
        process, _ = self.verify(result)
        self.assertEqual(process.returncode, 0)
        process, null = self.locate(source, {"format": "json", "pointer": "/a~1b/~0key/0"})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(null["matches"][0]["excerpt"]["text"], "null")
        process, missing = self.locate(source, {"format": "json", "pointer": "/missing"})
        self.assertEqual(process.returncode, 1)
        self.assertEqual(missing["status"], "not_found")

    def test_json_duplicate_keys_invalid_pointers_and_nonstandard_numbers_are_rejected(self):
        for text, pointer in [('{"outer":{"x":1,"x":2}}', "/outer/x"),
                              ('{"x":NaN}', "/x"), ('{"x":1}', "/bad~2escape")]:
            with self.subTest(text=text, pointer=pointer):
                _, source, _ = self.capture(text, "data.json")
                process, result = self.locate(source, {"format": "json", "pointer": pointer})
                self.assertEqual(process.returncode, 2, result)

    def test_csv_multiline_record_has_logical_row_and_physical_lines(self):
        _, source, _ = self.capture('name,note\r\nalpha,"line one\r\nline two"\r\nbeta,"x,y"\r\n', "table.csv")
        process, result = self.locate(source, {"format": "csv", "row": 2, "column": 2})
        self.assertEqual(process.returncode, 0, process.stderr)
        match = result["matches"][0]
        self.assertEqual(match["locator"]["row"], 2)
        self.assertEqual((match["locator"]["line_start"], match["locator"]["line_end"]), (2, 3))
        self.assertEqual(json.loads(match["excerpt"]["text"]), "line one\r\nline two")
        self.assertIn('alpha,"line one\r\nline two"', match["context"]["text"])
        process, _ = self.verify(result)
        self.assertEqual(process.returncode, 0)

    def test_tsv_row_and_empty_field_are_preserved(self):
        _, source, _ = self.capture("name\tvalue\tother\na\t\t中\n", "table.tsv")
        process, result = self.locate(source, {"format": "tsv", "row": 2})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(result["matches"][0]["excerpt"]["text"]), ["a", "", "中"])
        process, _ = self.verify(result)
        self.assertEqual(process.returncode, 0)

    def test_csv_explicit_key_selects_a_unique_record_and_pins_its_snapshot_row(self):
        _, source, _ = self.capture('id,note\nalpha,"one\ntwo"\nbeta,other\n', "data.csv")
        process, result = self.locate(source, {"format": "csv", "key": {"column": 1, "equals": "alpha"},
                                              "column": 2, "context": 1})
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["matches"][0]["locator"]["row"], 2)
        self.assertEqual(json.loads(result["matches"][0]["excerpt"]["text"]), "one\ntwo")
        self.assertEqual(result["binding"]["selector"]["row"], 2)
        process, _ = self.verify(result)
        self.assertEqual(process.returncode, 0)

    def test_duplicate_csv_keys_are_ambiguous_even_with_one_returned_match(self):
        _, source, _ = self.capture("id,note\nalpha,first\nbeta,middle\nalpha,last\n", "data.csv")
        request = {"format": "csv", "key": {"column": 1, "equals": "alpha"}, "column": 2}
        process, result = self.locate(source, request, "--max-matches", 1)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["search"]["matches"], 2)
        self.assertTrue(result["search"]["complete"])
        self.assertEqual(len(result["matches"]), 1)
        self.assertNotIn("binding", result)
        process, _ = self.locate(source, {**request, "row": 2})
        self.assertEqual(process.returncode, 2)
        process, absent = self.locate(source, {"format": "csv", "key": {"column": 1, "equals": "none"}})
        self.assertEqual(process.returncode, 1)
        self.assertEqual(absent["status"], "not_found")

    def test_csv_missing_column_is_not_found_and_malformed_csv_is_error(self):
        _, source, _ = self.capture("a,b\n1,2\n", "data.csv")
        process, _ = self.locate(source, {"format": "csv", "row": 2, "column": 3})
        self.assertEqual(process.returncode, 1)
        _, source, _ = self.capture('a,b\n1,"unclosed', "data.csv")
        process, result = self.locate(source, {"format": "csv", "row": 2})
        self.assertEqual(process.returncode, 2, result)

    def test_jsonl_uses_physical_line_not_timestamp_identity(self):
        _, source, _ = self.capture('{"time":"same","message":"first"}\n{"time":"same","message":"second"}\n', "events.jsonl")
        process, result = self.locate(source, {"format": "jsonl", "line": 2, "pointer": "/message"})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(result["matches"][0]["excerpt"]["text"], '"second"')
        self.assertEqual(result["matches"][0]["locator"]["line"], 2)
        process, _ = self.verify(result)
        self.assertEqual(process.returncode, 0)

    def test_unknown_selector_duplicate_request_keys_and_binary_input_fail_explicitly(self):
        _, source, _ = self.capture(b"%PDF-1.7\nnot extracted text\n", "file.pdf")
        process, result = self.locate(source, {"quote": "text"})
        self.assertEqual(process.returncode, 2)
        self.assertIn("does not perform OCR", result["error"])
        process, _ = self.locate(source, {"format": "pdf", "page": 1})
        self.assertEqual(process.returncode, 2)
        _, source, _ = self.capture("hello")
        process, _ = self.locate(source, {"quote": "hello", "misspelled_option": True})
        self.assertEqual(process.returncode, 2)
        for request in [{"format": {}, "quote": "hello"}, {"view": [], "quote": "hello"}]:
            process, result = self.locate(source, request)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["status"], "error")
            self.assertNotIn(b"Traceback", process.stderr)
        request = self.file("bad-request.json", '{"quote":"hello","quote":"other"}')
        process, _ = self.run_cli("locate", "--source", source, "--request", request)
        self.assertEqual(process.returncode, 2)

    def fetch(self, endpoint):
        process, result = self.run_cli(self.url + endpoint, "--output-dir", self.root / "fetched", script=FETCH)
        return process, result

    def test_import_fetch_uses_original_body_without_network_and_separates_transformation(self):
        process, fetched = self.fetch("/gzip")
        self.assertEqual(process.returncode, 0, process.stderr)
        # The original acquired body is still valid; distrust independently changed extraction text.
        Path(fetched["preview"]["path"]).write_text("tampered derived text")
        before = Handler.requests
        process, imported = self.run_cli("import-fetch", fetched["metadata_path"], "--store", self.store)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(Handler.requests, before)
        source = Path(imported["source"])
        self.assertEqual(self.snapshot(source).read_bytes(), Path(fetched["body_path"]).read_bytes())
        self.assertIn("CPU usage is 418m.", self.snapshot(source, "text").read_text())
        self.assertNotEqual(imported["objects"]["raw"]["sha256"], imported["objects"]["text"]["sha256"])
        process, located = self.locate(source, {"quote": "CPU usage is 418m."})
        self.assertEqual(process.returncode, 0)
        self.assertEqual(located["source"]["view"], "text")
        self.assertEqual(located["source"]["representation"], "derived_text")
        self.assertEqual(located["semantic_support"], "not_evaluated")
        process, _ = self.verify(located)
        self.assertEqual(process.returncode, 0)

    def test_import_fetch_rejects_changed_body_and_replay_checks_raw_parent(self):
        _, fetched = self.fetch("/gzip")
        _, imported = self.run_cli("import-fetch", fetched["metadata_path"], "--store", self.store)
        source = Path(imported["source"])
        _, located = self.locate(source, {"quote": "CPU usage is 418m."})
        self.snapshot(source).write_bytes(b"changed")
        process, _ = self.verify(located)
        self.assertEqual(process.returncode, 1)
        Path(fetched["body_path"]).write_bytes(b"changed")
        process, result = self.run_cli("import-fetch", fetched["metadata_path"], "--store", self.store)
        self.assertEqual(process.returncode, 2)
        self.assertIn("SHA-256/size", result["error"])

    def test_import_fetch_preserves_http_error_and_does_not_claim_http_refresh(self):
        process, fetched = self.fetch("/missing")
        self.assertEqual(process.returncode, 1)
        process, imported = self.run_cli("import-fetch", fetched["metadata_path"], "--store", self.store)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(imported["acquisition"]["http_status"], 404)
        self.assertTrue(imported["acquisition"]["complete"])
        self.assertIn("404", imported["acquisition"]["acquisition_error"])
        _, located = self.locate(imported["source"], {"quote": "actual missing page"})
        process, verified = self.verify(located, "--check-origin")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(verified["checks"][0]["origin"]["status"], "not_checked")

    def test_existing_fetch_duplicate_key_compatibility_does_not_weaken_locator(self):
        process, fetched = self.fetch("/duplicate-json")
        self.assertEqual(process.returncode, 0)
        process, imported = self.run_cli("import-fetch", fetched["metadata_path"], "--store", self.store)
        self.assertEqual(process.returncode, 0)
        process, result = self.locate(imported["source"], {"format": "json", "pointer": "/limit"})
        self.assertEqual(process.returncode, 2)
        self.assertIn("Duplicate JSON key", result["error"])

    def test_binding_content_changes_and_batch_unresolved_items_are_reported(self):
        _, source, _ = self.capture("one\ntwo\n")
        _, located = self.locate(source, {"quote": "one"})
        bad = json.loads(json.dumps(located))
        bad["binding"]["content_sha256"] = "0" * 64
        process, result = self.verify([located, bad])
        self.assertEqual(process.returncode, 1)
        self.assertEqual([check["status"] for check in result["checks"]], ["resolved", "unresolved"])


if __name__ == "__main__":
    unittest.main()
