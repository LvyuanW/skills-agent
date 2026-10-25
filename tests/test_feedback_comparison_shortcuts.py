import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "skills_agent/builtin_skills/feedback/scripts/experiment.py"


class ShortcutCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="comparison shortcuts ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def command(self, *arguments, python_flags=(), input=None):
        result = subprocess.run([sys.executable, "-I", *python_flags, str(SCRIPT), *arguments],
                                input=input, cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)["observations"]

    def compare(self, left, right, *options, **kwargs):
        return self.command("compare", str(left), str(right), "--basis", "Known input contract",
                            *options, **kwargs)

    def write(self, name, contents):
        path = self.root / name
        path.write_text(contents)
        return path

    def numeric(self, alignment="position"):
        return ("--kind", "numeric", "--alignment", alignment, "--atol", "0", "--rtol", "0", "--unit", "unitless")


class ComparisonShortcutTests(ShortcutCase):
    def test_direct_comparison_is_read_only_and_can_select_existing_check_results(self):
        left = self.write("actual.json", '{"metrics":{"count":12}}')
        right = self.write("reference.json", '{"expected":12}')
        before = sorted(self.root.iterdir())
        code, result = self.compare(left, right, "--left-pointer", "/metrics/count", "--right-pointer", "/expected")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["recording"], "not_requested")
        self.assertNotIn("record", result)
        self.assertEqual({row["path"] for row in result["consumed"]}, {str(left), str(right)})
        self.assertEqual(sorted(self.root.iterdir()), before)
        self.assertEqual(result["checks"][0]["inputs"]["left"]["pointer"], "/metrics/count")

    def test_optional_record_retains_generated_spec_and_actual_inputs(self):
        left = self.write("a.txt", "same\n")
        right = self.write("b.txt", "same\n")
        code, result = self.compare(left, right, "--format", "text", "--root", str(self.root / "evidence"))
        self.assertEqual(code, 0, result)
        record = json.loads(Path(result["record"]).read_text())
        self.assertEqual(record["experiment"]["comparison"]["checks"][0]["left"]["format"], "text")
        captured = {entry["path"] for entry in record["capture"]["consumed"]}
        self.assertTrue({str(left), str(right)} <= captured)

    def test_csv_numeric_key_alignment_and_decimal_precision(self):
        left = self.write("a.csv", 'id,value\na,0.10000000000000000000001\nb,2\n')
        right = self.write("b.csv", 'id,value\nb,2\na,0.1\n')
        options = ("--format", "csv", "--column", "value", "--key", "id", "--kind", "numeric",
                   "--atol", "0", "--rtol", "0", "--unit", "count")
        code, result = self.compare(left, right, *options)
        self.assertEqual(code, 1, result)
        self.assertEqual(result["checks"][0]["mismatches"][0]["key"], "a")
        code, result = self.compare(left, right, *options, "--atol", "1e-22")
        self.assertEqual(code, 0, result)

    def test_csv_rejects_ambiguous_or_unavailable_numeric_evidence(self):
        reference = self.write("reference.csv", "id,value\na,1\nb,2\n")
        options = ("--format", "csv", "--column", "value", "--key", "id", "--kind", "numeric",
                   "--atol", "0", "--rtol", "0", "--unit", "count")
        for contents in ("id,value,value\na,1,1\n", "id,value\na,1\na,2\n",
                         "id,value\na,1,extra\n", "id,value\na,\n", "id,value\na,NaN\n",
                         "id,value\na,Infinity\n", "id,value\na,1\n", "id,value\n,1\n"):
            with self.subTest(contents=contents):
                actual = self.write("actual.csv", contents)
                code, result = self.compare(actual, reference, *options)
                self.assertEqual(code, 2, result)
                self.assertEqual(result["status"], "inconclusive")

    def test_csv_text_does_not_silently_coerce_identifiers_or_numbers(self):
        left = self.write("a.csv", "value\n01\n")
        right = self.write("b.csv", "value\n1\n")
        code, result = self.compare(left, right, "--format", "csv", "--column", "value")
        self.assertEqual(code, 1, result)

    def test_numeric_shortcut_still_requires_tolerances_units_and_alignment(self):
        left = self.write("a.json", "[1]")
        right = self.write("b.json", "[1]")
        code, result = self.compare(left, right, "--kind", "numeric")
        self.assertEqual(code, 2, result)
        self.assertIn("explicit alignment", result["error"])

    def test_spec_shortcut_options_cannot_silently_override_a_spec(self):
        source = self.write("spec.json", json.dumps({"schema_version": 1, "basis": "test", "checks": [
            {"id": "one", "kind": "exact", "left": {"value": 1}, "right": {"value": 1}}]}))
        code, result = self.command("compare", "--spec", str(source), "--basis", "different")
        self.assertEqual(code, 2)
        self.assertIn("Do not mix", result["error"])

    def test_spec_comparison_without_root_does_not_write_files(self):
        source = self.write("spec.json", json.dumps({"schema_version": 1, "basis": "test", "checks": [
            {"id": "one", "kind": "exact", "left": {"value": 1}, "right": {"value": 1}}]}))
        code, result = self.command("compare", "--spec", str(source))
        self.assertEqual(code, 0, result)
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_failed_reference_producer_is_not_a_successful_empty_file_comparison(self):
        actual = self.write("actual.txt", "")
        expected = self.root / "expected.txt"
        manifest = self.write("manifest.json", json.dumps({"outputs": [str(expected)]}))
        run = subprocess.run([sys.executable, "-I", str(SCRIPT.with_name("feedback.py")), "run",
                              "--log-dir", str(self.root / "logs"), "--record-dir", str(self.root / "records"),
                              "--manifest", str(manifest), "--", sys.executable, "-c",
                              f"from pathlib import Path; import sys; Path({str(expected)!r}).write_text(''); sys.exit(127)"],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 127, run.stderr)
        record = json.loads(run.stdout)["observations"]["record"]
        code, result = self.compare(actual, expected, "--format", "text", "--right-record", record)
        self.assertEqual(code, 2, result)
        self.assertIn("expected exit status", result["checks"][0]["error"])

    def test_existing_successful_producer_record_and_changed_output(self):
        actual = self.root / "actual.json"
        expected = self.write("expected.json", '{"count":2}')
        manifest = self.write("manifest.json", json.dumps({"outputs": [str(actual)]}))
        run = subprocess.run([sys.executable, "-I", str(SCRIPT.with_name("feedback.py")), "run",
                              "--log-dir", str(self.root / "logs"), "--record-dir", str(self.root / "records"),
                              "--manifest", str(manifest), "--", sys.executable, "-c",
                              f"from pathlib import Path; Path({str(actual)!r}).write_text('{{\"count\":2}}')"],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        record = json.loads(run.stdout)["observations"]["record"]
        code, result = self.compare(actual, expected, "--left-record", record)
        self.assertEqual(code, 0, result)
        original = json.loads(Path(record).read_text())
        for field, value in (("cleanup_required", True), ("cleanup_forced", True),
                             ("cleanup_confirmed", False)):
            with self.subTest(field=field):
                modified = {**original, "execution": {**original["execution"], field: value}}
                uncertain = self.write("uncertain-record.json", json.dumps(modified))
                code, result = self.compare(actual, expected, "--left-record", str(uncertain))
                self.assertEqual(code, 2, result)
                self.assertIn("Producer completion is unproven", result["checks"][0]["error"])
        legacy = {**original, "execution": {key: value for key, value in original["execution"].items()
                                            if not key.startswith("cleanup_")}}
        legacy_record = self.write("legacy-record.json", json.dumps(legacy))
        code, result = self.compare(actual, expected, "--left-record", str(legacy_record))
        self.assertEqual(code, 0, result)
        actual.write_text('{"count":3}')
        code, result = self.compare(actual, expected, "--left-record", record)
        self.assertEqual(code, 2, result)
        self.assertIn("no longer matches", result["checks"][0]["error"])

    def test_fixture_cwd_exercises_real_relative_input_entry(self):
        entry = self.write("entry.py", "from pathlib import Path\nimport sys\n"
                           "value=int(Path('input/value.txt').read_text())\n"
                           "Path(sys.argv[1]).write_text(str(value*2))\n")
        spec = {"schema_version": 1, "purpose": "Real relative input contract", "cwd": "{fixture}",
                "command": [sys.executable, str(entry), "{output}/result.json"],
                "fixtures": [{"path": "input/value.txt", "text": "3"}],
                "outputs": [{"name": "result", "path": "result.json"}],
                "manifest": {"subjects": [str(entry)]}}
        code, result = self.command("run", "--spec", "-", "--root", str(self.root / "evidence"),
                                    input=json.dumps(spec))
        self.assertEqual(code, 0, result)
        self.assertEqual(Path(result["experiment"]["outputs"][0]["path"]).read_text(), "6")
        record = json.loads(Path(result["record"]).read_text())
        self.assertEqual(record["command"]["cwd"], result["experiment"]["fixture"])

    def test_numpy_is_optional_with_actionable_missing_dependency_error(self):
        left = self.root / "a.npy"
        left.write_bytes(b"not loaded before checking dependency")
        code, result = self.compare(left, left, "--format", "npy", python_flags=("-S",))
        self.assertEqual(code, 2, result)
        self.assertIn("existing task interpreter", result["checks"][0]["error"])


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Optional NumPy readers require a NumPy test interpreter")
class NumpyComparisonShortcutTests(ShortcutCase):
    def setUp(self):
        super().setUp()
        import numpy
        self.np = numpy

    def array_file(self, name, value):
        path = self.root / name
        self.np.save(path, value)
        return path

    def test_arrays_preserve_shape_and_support_explicit_slices(self):
        left = self.array_file("a.npy", self.np.arange(6).reshape(2, 3))
        right = self.array_file("b.npy", self.np.arange(6).reshape(3, 2))
        code, result = self.compare(left, right, "--format", "npy", *self.numeric())
        self.assertEqual(code, 2, result)
        self.assertIn("shapes", result["checks"][0]["error"])
        right = self.array_file("b.npy", self.np.array([[0, 1, 2], [9, 9, 9]]))
        code, result = self.compare(left, right, "--format", "npy", "--slice", "0:1,:", *self.numeric())
        self.assertEqual(code, 0, result)
        self.assertEqual(result["checks"][0]["compared"], 3)
        self.assertEqual(result["checks"][0]["inputs"]["left"]["slice"], "0:1,:")

    def test_npz_selects_exact_member_without_silently_choosing_a_different_array(self):
        left, right = self.root / "a.npz", self.root / "b.npz"
        self.np.savez(left, x=self.np.array([100]), **{"x.npy": self.np.array([1, 2])})
        self.np.savez(right, **{"x.npy": self.np.array([1, 2])})
        code, result = self.compare(left, right, "--format", "npz", "--array", "x.npy", *self.numeric())
        self.assertEqual(code, 0, result)
        code, result = self.compare(left, right, "--format", "npz", "--array", "absent", *self.numeric())
        self.assertEqual(code, 2, result)

    def test_empty_axes_retain_full_shapes_for_exact_and_set_comparisons(self):
        for a_shape, b_shape in (((0, 2), (0, 3)), ((2, 0, 3), (2, 0, 4))):
            with self.subTest(a_shape=a_shape, b_shape=b_shape):
                left = self.array_file("a.npy", self.np.empty(a_shape))
                right = self.array_file("b.npy", self.np.empty(b_shape))
                code, result = self.compare(left, right, "--format", "npy")
                self.assertEqual(code, 1, result)
                self.assertEqual(result["checks"][0]["left_shape"], list(a_shape))
                self.assertEqual(result["checks"][0]["right_shape"], list(b_shape))
                code, result = self.compare(left, left, "--format", "npy")
                self.assertEqual(code, 0, result)
                for kind in ("set", "multiset"):
                    code, result = self.compare(left, right, "--format", "npy", "--kind", kind)
                    self.assertEqual(code, 2, result)
                    self.assertIn("one-dimensional", result["checks"][0]["error"])

    def test_nonfinite_object_and_empty_arrays_do_not_pass_numeric_checks(self):
        for value in (self.np.array([float("nan")]), self.np.array([float("inf")]),
                      self.np.array([{"not": "numeric"}], dtype=object), self.np.array([])):
            with self.subTest(dtype=value.dtype, shape=value.shape):
                path = self.array_file("a.npy", value)
                code, result = self.compare(path, path, "--format", "npy", *self.numeric())
                self.assertEqual(code, 2, result)

    def test_huge_declared_shape_is_rejected_before_numpy_allocation(self):
        header = io.BytesIO()
        self.np.lib.format.write_array_header_1_0(header, {"descr": "<f8", "fortran_order": False,
                                                        "shape": (10**12,)})
        path = self.root / "oversized.npz"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("x.npy", header.getvalue())
        code, result = self.compare(path, path, "--format", "npz", "--array", "x", *self.numeric())
        self.assertEqual(code, 2, result)
        self.assertIn("acquisition budget", result["checks"][0]["error"])

    def test_selection_budget_and_dimension_limit_are_explicit(self):
        path = self.array_file("large.npy", self.np.arange(250001))
        code, result = self.compare(path, path, "--format", "npy", *self.numeric())
        self.assertEqual(code, 2, result)
        self.assertIn("explicit slice", result["checks"][0]["error"])
        code, result = self.compare(path, path, "--format", "npy", "--slice", "0:10", *self.numeric())
        self.assertEqual(code, 0, result)
        path = self.array_file("dimensions.npy", self.np.zeros((1,) * 17))
        code, result = self.compare(path, path, "--format", "npy", *self.numeric())
        self.assertEqual(code, 2, result)
        self.assertIn("dimensions", result["checks"][0]["error"])


if __name__ == "__main__":
    unittest.main()
