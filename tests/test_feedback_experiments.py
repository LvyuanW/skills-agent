import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills_agent/builtin_skills/feedback/scripts/experiment.py"


class FeedbackExperimentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="experiment tests ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.counter = 0

    def invoke(self, kind, spec, raw=None):
        self.counter += 1
        source = self.root / f"spec-{self.counter}.json"
        source.write_text(raw if raw is not None else json.dumps(spec), encoding="utf-8")
        # -I removes cwd/PYTHONPATH import assumptions. The task interpreter need not install skills_agent.
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), kind, "--spec", str(source),
                                 "--root", str(self.root / "evidence")], cwd=self.root,
                                capture_output=True, text=True, timeout=15)
        self.assertTrue(result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        return result, payload["observations"]

    def artifact(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def check(self, kind, left, right, **options):
        spec = {"schema_version": 1, "basis": "An explicit test contract, independent of the observed output.",
                "checks": [{"id": "condition", "kind": kind, "left": left, "right": right, **options}]}
        return self.invoke("compare", spec)

    def numeric_options(self, alignment=None):
        return {"alignment": alignment or {"kind": "scalar"}, "atol": 0, "rtol": 0, "unit": "unitless"}

    def run_spec(self, code, fixtures=None, outputs=None, **options):
        self.counter += 1
        program = self.root / f"actual entry {self.counter}.py"
        program.write_text(code, encoding="utf-8")
        return {"schema_version": 1, "purpose": "Exercise the actual CLI with known fixture inputs.",
                "command": [sys.executable, str(program), "{fixture}", "{output}"], "cwd": str(self.root),
                "fixtures": fixtures or [], "outputs": outputs or [],
                "manifest": {"subjects": [str(program)]}, **options}

    def test_actual_entry_gets_fresh_fixtures_output_cwd_and_complete_logs(self):
        original = self.root / "source input.json"
        original.write_text('{"value": 3}', encoding="utf-8")
        spec = self.run_spec(
            "import json,sys\nfrom pathlib import Path\n"
            "a=json.loads((Path(sys.argv[1])/'input.json').read_text())\n"
            "result={'value':a['value']*2,'cwd':str(Path.cwd())}\n"
            "(Path(sys.argv[2])/'nested/result.json').write_text(json.dumps(result))\n"
            "print('actual stdout');print('actual stderr',file=sys.stderr)\n",
            fixtures=[{"path": "input.json", "copy_from": str(original)}],
            outputs=[{"name": "result", "path": "nested/result.json"}])
        result, observed = self.invoke("run", spec)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(observed["verdict"], "not_compared")
        record = json.loads(Path(observed["record"]).read_text())
        self.assertEqual(record["execution"]["state"], "exited")
        self.assertEqual(record["capture"]["relation"], "same")
        artifact = observed["experiment"]["outputs"][0]["path"]
        self.assertEqual(json.loads(Path(artifact).read_text()), {"value": 6, "cwd": str(self.root)})
        self.assertEqual(Path(observed["execution"]["stdout_log"]).read_text(), "actual stdout\n")
        self.assertEqual(Path(observed["execution"]["stderr_log"]).read_text(), "actual stderr\n")
        self.assertEqual(original.read_text(), '{"value": 3}')
        again, second = self.invoke("run", spec)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotEqual(observed["experiment"]["output"], second["experiment"]["output"])
        result, checked = self.check("exact", {"path": artifact, "pointer": "/value",
                                               "run_record": observed["record"]}, {"value": 6})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(checked["passed"])

    def test_stdin_spec_uses_calling_cwd_and_saves_consumed_spec(self):
        program = self.root / "entry.py"
        program.write_text("import sys\nfrom pathlib import Path\n"
                           "(Path(sys.argv[2])/'result.json').write_bytes((Path(sys.argv[1])/'input.json').read_bytes())\n")
        (self.root / "original.json").write_text("12")
        spec = {"schema_version": 1, "purpose": "stdin fixture through the real relative entry",
                "command": [sys.executable, "entry.py", "{fixture}", "{output}"], "cwd": ".",
                "fixtures": [{"path": "input.json", "copy_from": "original.json"}],
                "outputs": [{"name": "result", "path": "result.json"}],
                "manifest": {"subjects": ["entry.py"]}}
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "run", "--spec", "-",
                                 "--root", "evidence"], input=json.dumps(spec), cwd=self.root,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(json.loads(Path(payload["source"]).read_text()), spec)
        output = payload["observations"]["experiment"]["outputs"][0]["path"]
        self.assertEqual(Path(output).read_text(), "12")

    def test_comparison_tracks_declared_symlink_and_later_retargeting(self):
        first = self.root / "first.json"
        second = self.root / "second.json"
        first.write_text("1")
        second.write_text("1")
        alias = self.root / "alias.json"
        alias.symlink_to(first.name)
        result, observed = self.check("exact", {"path": str(alias)}, {"value": 1})
        self.assertEqual(result.returncode, 0, result.stderr)
        status_command = [sys.executable, "-I", str(SCRIPT.with_name("feedback.py")),
                          "status", "--record", observed["record"]]
        status = subprocess.run(status_command, capture_output=True, text=True, timeout=10)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["observations"]["current_relation"], "same")
        alias.unlink()
        alias.symlink_to(second.name)
        status = subprocess.run(status_command, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(status.stdout)["observations"]["current_relation"], "different")

    def test_exact_keeps_boolean_number_and_missing_null_distinct(self):
        path = self.artifact("strict.json", {"value": True, "null": None})
        result, observed = self.check("exact", {"path": path, "pointer": "/value"}, {"value": 1})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(observed["checks"][0]["status"], "mismatch")
        result, observed = self.check("exact", {"path": path, "pointer": "/missing"}, {"value": None})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(observed["status"], "inconclusive")

    def test_decimal_exactness_and_tolerances(self):
        path = self.root / "decimal.json"
        path.write_text('{"value":0.100000000000000000000000000000000000001}')
        base = {"schema_version": 1, "basis": "Exact decimal contract", "checks": [{
            "id": "decimal", "kind": "numeric", "left": {"path": str(path), "pointer": "/value"},
            "right": {"value": 0.1}, **self.numeric_options()}]}
        result, observed = self.invoke("compare", base)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(observed["checks"][0]["mismatch_count"], 1)
        base["checks"][0]["atol"] = 1e-38
        result, _ = self.invoke("compare", base)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_numeric_requires_explicit_tolerance_unit_and_alignment(self):
        for omitted in ("atol", "rtol", "unit", "alignment"):
            options = self.numeric_options()
            del options[omitted]
            with self.subTest(omitted=omitted):
                result, observed = self.check("numeric", {"value": 1}, {"value": 1}, **options)
                self.assertEqual(result.returncode, 2)
                self.assertIn("require explicit", observed["error"])

    def test_key_alignment_matches_ids_and_rejects_duplicates_or_lost_rows(self):
        options = self.numeric_options({"kind": "key", "key": "/id", "value": "/value"})
        left = [{"id": "a", "value": 1}, {"id": "b", "value": 2}]
        for right, status in [(left[::-1], 0), ([left[0], left[0]], 2), ([left[0]], 2)]:
            with self.subTest(right=right):
                result, _ = self.check("numeric", {"value": left}, {"value": right}, **options)
                self.assertEqual(result.returncode, status, result.stderr)

    def test_empty_or_different_length_numeric_arrays_are_inconclusive(self):
        for left, right in [([], []), ([1], [1, 2])]:
            result, observed = self.check("numeric", {"value": left}, {"value": right},
                                          **self.numeric_options({"kind": "position"}))
            self.assertEqual(result.returncode, 2)
            self.assertFalse(observed["passed"])

    def test_set_and_multiset_make_duplicate_semantics_explicit(self):
        for kind, left, right, code in [("set", [1, 1, 2], [2, 1], 0),
                                        ("multiset", [1, 1, 2], [2, 1], 1),
                                        ("multiset", [1, 1, 2], [1, 2, 1], 0),
                                        ("set", [True], [1], 1)]:
            with self.subTest(kind=kind, left=left):
                result, _ = self.check(kind, {"value": left}, {"value": right})
                self.assertEqual(result.returncode, code, result.stderr)

    def test_set_containment_multiset_counts_and_typed_differences(self):
        cases = [("set", "subset", [1, 1], [1, 2], 0),
                 ("multiset", "subset", [1, 1], [1, 2], 1),
                 ("multiset", "superset", [1, 1, 2], [1, 1], 0),
                 ("multiset", "superset", [1, 2], [1, 1], 1)]
        for kind, relation, left, right, code in cases:
            result, _ = self.check(kind, {"value": left}, {"value": right}, relation=relation)
            self.assertEqual(result.returncode, code, result.stderr)
        result, observed = self.check("multiset", {"value": [True, 1, 1]}, {"value": [False, 1]})
        self.assertEqual(result.returncode, 1)
        check = observed["checks"][0]
        self.assertEqual((check["missing_count"], check["extra_count"]), (1, 2))
        self.assertEqual(check["missing"][0]["value"]["text"], "false")
        self.assertEqual({item["value"]["text"] for item in check["extra"]}, {"true", "1"})

    def test_paired_relations_use_declared_direction_and_parameter(self):
        for relation, extra, left, right, code in [
            ("delta", {"delta": 3}, 2, 5, 0), ("delta", {"delta": 3}, 5, 2, 1),
            ("scale", {"factor": 2}, 3, 6, 0), ("equal", {}, 3, 4, 1),
            ("nondecreasing", {}, 2, 4, 0), ("nonincreasing", {}, 4, 2, 0)]:
            with self.subTest(relation=relation, left=left):
                result, _ = self.check("paired", {"value": left}, {"value": right},
                                       relation=relation, **extra, **self.numeric_options())
                self.assertEqual(result.returncode, code, result.stderr)

    def test_nonempty_checks_strict_json_and_finite_numeric_values(self):
        for raw in ['{"schema_version":1,"basis":"x","checks":[]}',
                    '{"schema_version":1,"basis":"x","checks":[],"checks":[]}',
                    '{"schema_version":1,"basis":"x","checks":NaN}']:
            result, observed = self.invoke("compare", None, raw=raw)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(observed.get("passed", False))
        for value in (True, "12", None):
            result, _ = self.check("numeric", {"value": value}, {"value": 12}, **self.numeric_options())
            self.assertEqual(result.returncode, 2)
        path = self.root / "nan.json"
        path.write_text('{"value":NaN}')
        result, _ = self.check("exact", {"path": str(path)}, {"value": None})
        self.assertEqual(result.returncode, 2)

    def test_missing_and_unreadable_artifacts_are_not_mismatches(self):
        result, observed = self.check("exact", {"path": str(self.root / "absent")}, {"value": "ok"})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(observed["checks"][0]["status"], "inconclusive")
        path = self.root / "bad-utf8"
        path.write_bytes(b"\xff")
        result, _ = self.check("exact", {"path": str(path), "format": "text"}, {"value": "ok"})
        self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        path = self.root / "pipe"
        os.mkfifo(path)
        result, observed = self.check("exact", {"path": str(path), "format": "text"}, {"value": "ok"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("regular file", observed["checks"][0]["error"])

    def test_startup_setup_timeout_and_actual_nonzero_have_distinct_evidence(self):
        startup = self.run_spec("pass")
        startup["command"] = [str(self.root / "not-an-executable")]
        result, observed = self.invoke("run", startup)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(observed["phase"], "startup")
        self.assertIsNone(observed["execution"]["exit_code"])
        marker = self.root / "must-not-run"
        setup = self.run_spec(f"from pathlib import Path\nPath({str(marker)!r}).touch()",
                              fixtures=[{"path": "input", "copy_from": "absent"}])
        result, observed = self.invoke("run", setup)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(observed["phase"], "fixture")
        self.assertFalse(marker.exists())
        setup_record = json.loads(Path(observed["experiment"]["setup_record"]).read_text())
        self.assertEqual(setup_record["setup_state"], "failed")
        result, observed = self.invoke("run", self.run_spec("import sys\nsys.exit(7)"))
        self.assertEqual(result.returncode, 7)
        self.assertEqual(observed["execution"]["exit_code"], 7)
        self.assertNotIn("startup_error", observed["execution"])
        result, observed = self.invoke("run", self.run_spec("import time\ntime.sleep(5)", timeout=0.05))
        self.assertEqual(result.returncode, 124)
        self.assertTrue(observed["execution"]["timed_out"])

    def test_expected_nonzero_result_can_be_compared_but_default_does_not_accept_it(self):
        spec = self.run_spec("import sys\nfrom pathlib import Path\n"
                             "(Path(sys.argv[2])/'result.json').write_text('{\"error\":\"invalid input\"}')\n"
                             "sys.exit(2)\n", outputs=[{"name": "result", "path": "result.json"}])
        result, observed = self.invoke("run", spec)
        self.assertEqual(result.returncode, 2)
        selector = {"path": observed["experiment"]["outputs"][0]["path"], "run_record": observed["record"]}
        result, _ = self.check("exact", selector, {"value": {"error": "invalid input"}})
        self.assertEqual(result.returncode, 2)
        selector["expected_exit_codes"] = [2]
        result, checked = self.check("exact", selector, {"value": {"error": "invalid input"}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(checked["passed"])

    def test_changed_output_cannot_reuse_successful_execution_record(self):
        spec = self.run_spec("import sys\nfrom pathlib import Path\n"
                             "(Path(sys.argv[2])/'result.json').write_text('3')\n",
                             outputs=[{"name": "result", "path": "result.json"}])
        _, observed = self.invoke("run", spec)
        path = Path(observed["experiment"]["outputs"][0]["path"])
        path.write_text("4")
        result, checked = self.check("exact", {"path": str(path), "run_record": observed["record"]}, {"value": 4})
        self.assertEqual(result.returncode, 2)
        self.assertIn("no longer matches", checked["checks"][0]["error"])
        self.assertEqual(path.read_text(), "4", "compare must be read-only")

    def test_unknown_input_version_does_not_silently_establish_comparability(self):
        spec = self.run_spec("import sys\nfrom pathlib import Path\n"
                             "(Path(sys.argv[2])/'result.json').write_text('3')\n",
                             outputs=[{"name": "result", "path": "result.json"}])
        spec["manifest"]["dependencies"] = [str(self.root / "unavailable-dependency")]
        _, observed = self.invoke("run", spec)
        result, checked = self.check("exact", {"path": observed["experiment"]["outputs"][0]["path"],
                                               "run_record": observed["record"]}, {"value": 3})
        self.assertEqual(result.returncode, 2)
        self.assertIn("not established as stable", checked["checks"][0]["error"])

    def test_fixtures_cannot_escape_and_outputs_cannot_claim_external_symlinks(self):
        for invalid in ("../escape", str(self.root / "escape"), "."):
            spec = self.run_spec("pass", fixtures=[{"path": invalid, "text": "bad"}])
            result, _ = self.invoke("run", spec)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((self.root / "escape").exists())
        external = self.root / "external.json"
        external.write_text("3")
        spec = self.run_spec("import sys\nfrom pathlib import Path\n"
                             f"(Path(sys.argv[2])/'result.json').symlink_to({str(external)!r})\n",
                             outputs=[{"name": "result", "path": "result.json"}])
        result, observed = self.invoke("run", spec)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(observed["output_acquisition_errors"])

    def test_missing_outputs_are_acquisition_gaps_and_do_not_reuse_old_files(self):
        (self.root / "result.json").write_text("old output")
        spec = self.run_spec("pass", outputs=[{"name": "result", "path": "result.json"}])
        result, observed = self.invoke("run", spec)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(observed["execution"]["exit_code"], 0)
        self.assertEqual(observed["status"], "inconclusive")
        self.assertNotEqual(observed["experiment"]["outputs"][0]["path"], str(self.root / "result.json"))

    def test_invalid_hash_budget_does_not_start_command(self):
        marker = self.root / "must-not-run"
        spec = self.run_spec(f"from pathlib import Path; Path({str(marker)!r}).touch()")
        spec["manifest"]["limits"] = {"max_seconds": True}
        result, observed = self.invoke("run", spec)
        self.assertEqual(result.returncode, 2)
        self.assertIn("max_seconds", observed["error"])
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
