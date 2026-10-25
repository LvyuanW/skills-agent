import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = (Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
          / "investigation" / "scripts" / "source.py")


class SourceShortcutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.cwd = self.root / "cwd with spaces"
        self.cwd.mkdir()
        self.store = self.root / "saved sources"

    def file(self, name, value):
        path = self.root / name
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def cli(self, *args, stdin=None):
        process = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                                 input=stdin.encode() if isinstance(stdin, str) else stdin,
                                 cwd=self.cwd, capture_output=True, timeout=10)
        self.assertTrue(process.stdout, process.stderr)
        self.assertNotIn(b"Traceback", process.stderr)
        return process.returncode, json.loads(process.stdout)

    def test_read_only_quote_identifies_consumed_bytes_without_claiming_a_snapshot(self):
        raw = b'\xef\xbb\xbf' + "第一行\r\n  actual quote\r\nlast".encode()
        path = self.file("input with spaces.txt", raw)
        before = set(self.root.rglob("*"))
        status, result = self.cli("locate", "--file", path, "--quote", "actual quote")
        self.assertEqual(status, 0)
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertEqual(result["source"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertFalse(result["retention"]["snapshot_saved"])
        self.assertFalse(result["retention"]["replayable"])
        self.assertNotIn("manifest", result["source"])
        self.assertNotIn("binding", result)
        position = result["matches"][0]["locator"]
        self.assertEqual((position["start_line"], position["start_column"]), (2, 3))
        self.assertEqual(raw[position["byte_start"]:position["byte_end"]], b"actual quote")
        status, verified = self.cli("verify", "--bindings", "-", stdin=json.dumps(result))
        self.assertEqual(status, 1)
        self.assertEqual(verified["checks"][0]["status"], "unresolved")

    def test_one_call_saved_lookup_replays_original_after_origin_changes(self):
        path = self.file("original.txt", "old evidence\n")
        status, result = self.cli("locate", "--file", path, "--quote", "old evidence", "--store", self.store)
        self.assertEqual(status, 0)
        self.assertTrue(result["retention"]["replayable"])
        saved = Path(result["saved_result"])
        self.assertEqual(json.loads(saved.read_text()), result)
        path.write_text("new evidence\n")
        status, _ = self.cli("verify", "--bindings", saved)
        self.assertEqual(status, 0)
        status, current = self.cli("verify", "--bindings", saved, "--check-origin")
        self.assertEqual(status, 1)
        self.assertEqual(current["checks"][0]["status"], "historical_only")
        self.assertEqual(current["checks"][0]["origin"]["status"], "changed")

    def test_ambiguous_and_normalized_shortcuts_never_make_exact_bindings(self):
        path = self.file("quotes.txt", "same\nsame\nCPU\t usage\n")
        status, repeated = self.cli("locate", "--file", path, "--quote", "same", "--max-matches", 1)
        self.assertEqual(status, 1)
        self.assertEqual(repeated["status"], "ambiguous")
        self.assertFalse(repeated["search"]["complete"])
        status, normalized = self.cli("locate", "--file", path, "--quote", "CPU usage",
                                      "--match", "whitespace", "--store", self.store)
        self.assertEqual(status, 1)
        self.assertEqual(normalized["status"], "normalized_candidate")
        self.assertNotIn("binding", normalized)
        self.assertTrue(normalized["retention"]["snapshot_saved"])
        self.assertFalse(normalized["retention"]["replayable"])

    def test_stdin_selectors_work_on_direct_files_and_existing_sources(self):
        path = self.file("values.json", '{"exact": 9007199254740993, "empty": null}')
        status, result = self.cli("locate", "--file", path, "--request", "-", "--store", self.store,
                                  stdin='{"format":"json","pointer":"/exact"}')
        self.assertEqual(status, 0)
        self.assertEqual(result["matches"][0]["excerpt"]["text"], "9007199254740993")
        status, existing = self.cli("locate", "--source", result["source"]["manifest"],
                                    "--request", "-", stdin='{"format":"json","pointer":"/empty"}')
        self.assertEqual(status, 0)
        self.assertEqual(existing["matches"][0]["excerpt"]["text"], "null")
        status, duplicate = self.cli("locate", "--file", path, "--request", "-",
                                     stdin='{"quote":"exact","quote":"empty"}')
        self.assertEqual(status, 2)
        self.assertIn("Duplicate JSON key", duplicate["error"])

    def test_batch_uses_explicit_sources_and_reports_partial_failures_without_picking_matches(self):
        first = self.file("first.txt", "delay: 5\nrepeat\nrepeat\n")
        second = self.file("values.csv", "name,value\napi,256\n")
        requests = [{"file": str(first), "quote": "delay: 5", "claim_id": "delay"},
                    {"file": str(second), "format": "csv", "key": {"column": 1, "equals": "api"}, "column": 2},
                    {"file": str(first), "quote": "repeat"},
                    {"file": str(first), "quote": "missing"}]
        status, result = self.cli("locate", "--batch", "-", stdin=json.dumps(requests))
        self.assertEqual(status, 1)
        self.assertEqual(result["source_views"], 2)
        self.assertEqual([x["status"] for x in result["results"]],
                         ["resolved", "resolved", "ambiguous", "not_found"])
        self.assertEqual(result["results"][1]["matches"][0]["excerpt"]["text"], '"256"')
        self.assertFalse(self.store.exists())

    def test_saved_batch_reuses_one_snapshot_for_many_quotes_and_verifies_as_a_whole(self):
        path = self.file("logs.txt", "".join(f"unique observation {i:02d}\n" for i in range(54)))
        requests = [{"file": str(path), "quote": f"unique observation {i:02d}", "claim_id": str(i)}
                    for i in range(54)]
        status, result = self.cli("locate", "--batch", "-", "--store", self.store,
                                  "--max-chars", 20, stdin=json.dumps(requests))
        self.assertEqual(status, 0)
        self.assertEqual(result["source_views"], 1)
        self.assertEqual(len(list(self.store.glob("source-*/source.json"))), 1)
        self.assertEqual(len({x["source"]["manifest"] for x in result["results"]}), 1)
        self.assertTrue(result["preview"]["truncated"])
        self.assertLessEqual(sum(len(y[k]["text"]) for x in result["results"]
                                 for y in x["matches"] for k in ("excerpt", "context")), 20)
        status, verified = self.cli("verify", "--bindings", result["saved_result"])
        self.assertEqual(status, 0)
        self.assertEqual(len(verified["checks"]), 54)

    def test_saved_unresolved_batch_cannot_verify_and_snapshot_view_aliases_share_budget(self):
        path = self.file("repeat.txt", "same\nsame\n")
        requests = [{"file": str(path), "quote": "same"},
                    {"file": str(path), "quote": "absent"}]
        status, result = self.cli("locate", "--batch", "-", "--store", self.store,
                                  stdin=json.dumps(requests))
        self.assertEqual(status, 1)
        self.assertTrue(all("binding" not in item for item in result["results"]))
        status, verified = self.cli("verify", "--bindings", result["saved_result"])
        self.assertEqual(status, 1)
        self.assertEqual([item["status"] for item in verified["checks"]], ["unresolved", "unresolved"])
        source = result["results"][0]["source"]["manifest"]
        requests = [{"source": source, "view": "raw", "range": {"start_line": 1}},
                    {"source": source, "range": {"start_line": 2}}]
        status, result = self.cli("locate", "--batch", "-", "--max-total-bytes", path.stat().st_size,
                                  stdin=json.dumps(requests))
        self.assertEqual(status, 0)
        self.assertEqual(result["source_views"], 1)
        self.assertEqual(result["source_bytes"], path.stat().st_size)

    def test_batch_paths_resolve_relative_to_request_file_or_stdin_cwd(self):
        self.file("relative.txt", "from request directory")
        request = self.file("batch.json", json.dumps([{"file": "relative.txt", "quote": "request"}]))
        status, result = self.cli("locate", "--batch", request)
        self.assertEqual(status, 0)
        self.assertEqual(result["results"][0]["source"]["path"], str(self.root / "relative.txt"))
        (self.cwd / "relative.txt").write_text("from stdin directory")
        status, result = self.cli("locate", "--batch", "-",
                                  stdin=json.dumps([{"file": "relative.txt", "quote": "stdin"}]))
        self.assertEqual(status, 0)
        self.assertEqual(result["results"][0]["source"]["path"], str(self.cwd / "relative.txt"))

    def test_batch_input_errors_and_resource_limits_remain_visible(self):
        first = self.file("one.txt", "1234567890")
        second = self.file("two.txt", "abcdefghij")
        requests = [{"file": str(first), "quote": "123"},
                    {"file": str(first), "quote": "456"},
                    {"file": str(second), "quote": "abc"},
                    {"file": str(first), "source": "other", "quote": "123"}]
        status, result = self.cli("locate", "--batch", "-", "--max-total-bytes", 15,
                                  stdin=json.dumps(requests))
        self.assertEqual(status, 2)
        self.assertEqual(result["source_bytes"], 10)
        self.assertEqual([x["status"] for x in result["results"]], ["resolved", "resolved", "error", "error"])
        status, result = self.cli("locate", "--batch", "-", stdin=json.dumps(requests[:1] * 101))
        self.assertEqual(status, 2)
        self.assertIn("between 1 and 100", result["error"])
        status, result = self.cli("locate", "--file", first, "--request", "-", stdin=" " * 65537)
        self.assertEqual(status, 2)
        self.assertIn("Request exceeds byte budget", result["error"])

    def test_direct_snapshot_and_batch_locations_use_the_same_consumed_buffer(self):
        spec = importlib.util.spec_from_file_location("source_shortcut_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = self.file("changing.txt", "observed before edit")
        read = module.read_stable
        source_reads = []

        def read_then_edit(candidate, *args, **kwargs):
            raw, observation = read(candidate, *args, **kwargs)
            if Path(candidate) == path:
                source_reads.append(candidate)
                path.write_text("changed after read")
            return raw, observation

        with mock.patch.object(module, "read_stable", side_effect=read_then_edit):
            sources = module.LocationSources(1024, 1024, self.store)
            identity, raw = sources.get("file", path, {"quote": "observed"})
            again, second = sources.get("file", path, {"quote": "before"})
            self.assertEqual(len(source_reads), 1)
            self.assertEqual(raw, second)
            self.assertEqual(identity, again)
            result = module.locate_content(identity, raw, {"quote": "observed"}, module.Budget(100))
        self.assertEqual(result["status"], "resolved")
        manifest = json.loads(Path(identity["manifest"]).read_text())
        snapshot = Path(identity["manifest"]).parent / manifest["objects"]["raw"]["path"]
        self.assertEqual(snapshot.read_text(), "observed before edit")
        status, verified = self.cli("verify", "--bindings", "-", "--check-origin", stdin=json.dumps(result))
        self.assertEqual(status, 1)
        self.assertEqual(verified["checks"][0]["status"], "historical_only")


if __name__ == "__main__":
    unittest.main()
