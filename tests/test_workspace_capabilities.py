import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from skills_agent.skills import SkillRegistry


BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
ROOT = BUILTIN_ROOT / "workspace"
PROBE = ROOT / "scripts" / "probe_environment.py"
EXTRACT = ROOT / "scripts" / "extract.py"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class WorkspaceCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()

    def invoke(self, script, *args, env=None, isolated=False):
        return subprocess.run(
            [sys.executable, *( ["-S"] if isolated else []), str(script), *map(str, args)],
            cwd=self.workspace, capture_output=True, text=True, env=env, timeout=15,
        )

    def extract(self, *args, success=True, **kwargs):
        result = self.invoke(EXTRACT, *args, **kwargs)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        if not success:
            self.assertTrue(payload["error"])
        return payload

    def make_docx(self, body):
        target = self.workspace / "中文 report.docx"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        return target

    def make_xlsx(self):
        target = self.workspace / "中文 sheet.xlsx"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="销售" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml" Type="worksheet"/></Relationships>')
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{S}"><si><r><t>中文</t></r><r><t>字段</t></r></si></sst>')
            archive.writestr("xl/worksheets/sheet1.xml", f'''<worksheet xmlns="{S}"><sheetData>
              <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>内联</t></is></c><c r="C1" t="b"><v>1</v></c></row>
              <row r="7"><c r="A7" s="1"><v>45292</v></c><c r="B7"><f>A7*2</f><v>90584</v></c><c r="C7"><f>SUM(A1:A3)</f></c><c r="D7" s="2"/></row>
            </sheetData></worksheet>''')
        return target

    def recover(self, format_name, target, *, max_chars=7, **options):
        collected = {}
        start, offset = 1, 0
        for _ in range(100):
            args = [format_name, target, "--start", start, "--offset", offset,
                    "--limit", 2, "--max-chars", max_chars]
            for key, value in options.items():
                args.extend(["--" + key.replace("_", "-"), value])
            payload = self.extract(*args)
            self.assertLessEqual(sum(len(record["text"]) for record in payload["records"]), max_chars)
            for record in payload["records"]:
                collected.setdefault(record["index"], []).append(record["text"])
            if not payload["truncated"]:
                return {index: "".join(chunks) for index, chunks in collected.items()}
            cursor = payload["next"]
            self.assertGreater((cursor["start"], cursor["offset"]), (start, offset))
            start, offset = cursor["start"], cursor["offset"]
        self.fail("Continuation failed to finish")

    def test_environment_inspection_is_targeted_and_does_not_execute_programs(self):
        executable = self.workspace / "probe-target"
        executable.write_text("#!/bin/sh\ntouch should-not-run\n")
        executable.chmod(0o755)
        env = {**os.environ, "PATH": str(self.workspace)}
        result = self.invoke(PROBE, "--executable", "probe-target", "--module", "json", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(len(data["checks"]), 2)
        self.assertEqual(data["checks"][0]["path"], str(executable))
        self.assertTrue(all(check["available"] for check in data["checks"]))
        self.assertFalse((self.workspace / "should-not-run").exists())

    def test_missing_capability_is_reported_with_failure_status(self):
        result = self.invoke(PROBE, "--module", "skills_agent_absent_module_942819",
                             "--executable", "skills-agent-absent-program-942819")
        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        self.assertEqual(len(data["missing"]), 2)
        self.assertFalse(any(item["available"] for item in data["checks"]))

    def test_invalid_probe_requests_fail(self):
        for args in ((), ("--module", "a/b"), ("--module", "json.decoder")):
            with self.subTest(args=args):
                result = self.invoke(PROBE, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr)

    def test_module_discovery_does_not_execute_package_initialization(self):
        package = self.workspace / "probe_package"
        package.mkdir()
        marker = self.workspace / "module-executed"
        (package / "__init__.py").write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nprint("unwanted output")\n')
        result = self.invoke(PROBE, "--module", "probe_package",
                             env={**os.environ, "PYTHONPATH": str(self.workspace)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["checks"][0]["available"])
        self.assertFalse(marker.exists())

    def test_workspace_scripts_do_not_shadow_standard_library_imports(self):
        result = subprocess.run(
            [sys.executable, "-c", "from dataclasses import make_dataclass; "
             "print(make_dataclass('Probe', [('value', int)])(3).value)"],
            cwd=PROBE.parent, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3")

    def test_json_pointer_decodes_escaped_keys_and_distinguishes_null_from_missing(self):
        path = self.workspace / "data.json"
        original = '{"a/b":{"~key":["中文",null]},"":42}'
        path.write_text(original)
        data = self.extract("json", path, "--pointer", "/a~1b/~0key/0")
        self.assertEqual(json.loads(data["text"]), "中文")
        self.assertEqual(data["source"], str(path))
        self.assertFalse(data["truncated"])
        self.assertEqual(self.extract("json", path, "--pointer", "/a~1b/~0key/1")["text"], "null")
        self.extract("json", path, "--pointer", "/a~1b/missing", success=False)
        self.assertEqual(self.extract("json", path, "--pointer", "/")["text"], "42")
        self.assertEqual(path.read_text(), original)

    def test_json_pointer_rejects_malformed_escapes_and_array_indices(self):
        path = self.workspace / "data.json"
        path.write_text('{"items":[1]}')
        for pointer in ("items", "/~", "/~2", "/items/01", "/items/-", "/items/１", "/items/2", "/items/0/no"):
            with self.subTest(pointer=pointer):
                self.extract("json", path, "--pointer", pointer, success=False)

    def test_json_continuation_recovers_complete_unicode_value(self):
        path = self.workspace / "large.json"
        value = {"text": "长字段\t" * 30, "count": 7}
        path.write_text(json.dumps(value, ensure_ascii=False))
        pieces, offset = [], 0
        for _ in range(100):
            data = self.extract("json", path, "--max-chars", 13, "--offset", offset)
            self.assertLessEqual(len(data["text"]), 13)
            pieces.append(data["text"])
            if not data["truncated"]:
                break
            self.assertGreater(data["next"]["offset"], offset)
            offset = data["next"]["offset"]
        self.assertEqual(json.loads("".join(pieces)), value)

    def test_json_selection_preserves_exact_decimal_values_as_numbers(self):
        path = self.workspace / "precise.json"
        original = ('{"n":9007199254740993.0,"small":0.0000000000000000000000000000000000000123456789,'
                    '"large":1.234567890123456789e+9000,"negative_zero":-0.000,'
                    '"nested":[{"数值":123456789.123456789123456789},true,null]}')
        path.write_text(original)
        expected = json.loads(original, parse_float=Decimal)
        data = self.extract("json", path)
        recovered = json.loads(data["text"], parse_float=Decimal)
        self.assertEqual(recovered, expected)
        self.assertIsInstance(recovered["n"], Decimal)
        self.assertEqual(recovered["n"], Decimal("9007199254740993.0"))
        self.assertEqual(recovered["large"], Decimal("1.234567890123456789e+9000"))
        self.assertEqual(recovered["negative_zero"].as_tuple(), Decimal("-0.000").as_tuple())
        selected = self.extract("json", path, "--pointer", "/nested/0/数值")
        self.assertEqual(json.loads(selected["text"], parse_float=Decimal), expected["nested"][0]["数值"])
        self.assertEqual(path.read_text(), original)

    def test_precise_json_continuation_preserves_numbers_and_nested_types(self):
        path = self.workspace / "precise.json"
        original = '{"values":[9007199254740993.0,1e-9000,1.234567890123456789e9000],"text":"中文"}'
        path.write_text(original)
        pieces, offset = [], 0
        for _ in range(100):
            data = self.extract("json", path, "--offset", offset, "--max-chars", 9)
            pieces.append(data["text"])
            if not data["truncated"]:
                break
            self.assertGreater(data["next"]["offset"], offset)
            offset = data["next"]["offset"]
        else:
            self.fail("Precise JSON continuation failed to finish")
        self.assertEqual(json.loads("".join(pieces), parse_float=Decimal),
                         json.loads(original, parse_float=Decimal))

    def test_nonstandard_json_constants_fail_even_outside_selected_pointer(self):
        path = self.workspace / "nonstandard.json"
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                path.write_text('{"keep":1,"bad":' + token + '}')
                data = self.extract("json", path, "--pointer", "/keep", success=False)
                self.assertIn(token, data["error"])

    def test_csv_multiline_records_preserve_fields_and_physical_line_locations(self):
        path = self.workspace / "data.csv"
        original = '\ufeffname,description,empty\r\n中文,"首行\r\n次行",\r\n第三,末尾,x\r\n'
        path.write_bytes(original.encode("utf-8"))
        data = self.extract("csv", path, "--start", 2, "--limit", 1)
        self.assertEqual(len(data["records"]), 1)
        record = data["records"][0]
        self.assertEqual(record["location"], {"row": 2, "line_start": 2, "line_end": 3})
        self.assertEqual(json.loads(record["text"]), ["中文", "首行\r\n次行", ""])
        self.assertEqual(data["next"], {"start": 3, "offset": 0})
        self.assertEqual(path.read_bytes(), original.encode("utf-8"))

    def test_csv_character_budget_can_resume_inside_large_field_without_loss(self):
        path = self.workspace / "large.csv"
        path.write_text('name,value\n中文,"' + "长字段" * 30 + '"\nlast,\n')
        records = self.recover("csv", path)
        self.assertEqual(json.loads(records[1]), ["name", "value"])
        self.assertEqual(json.loads(records[2]), ["中文", "长字段" * 30])
        self.assertEqual(json.loads(records[3]), ["last", ""])

    def test_csv_custom_delimiter_and_encoding_are_explicit(self):
        path = self.workspace / "latin.csv"
        path.write_bytes("caf\xe9;2\n".encode("latin-1"))
        data = self.extract("csv", path, "--encoding", "latin-1", "--delimiter", ";")
        self.assertEqual(json.loads(data["records"][0]["text"]), ["café", "2"])
        self.extract("csv", path, success=False)

    def test_docx_reports_paragraph_and_table_sources_with_tabs_and_breaks(self):
        path = self.make_docx('''<w:p><w:r><w:t>第一</w:t><w:tab/><w:t>段</w:t><w:br/><w:t>下一行</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>单元格</w:t></w:r></w:p><w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>右侧</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          <w:p><w:r><w:t>末段</w:t></w:r></w:p>''')
        records = self.extract("docx", path)["records"]
        self.assertEqual([item["text"] for item in records], ["第一\t段\n下一行", "单元格\n第二段", "右侧", "末段"])
        self.assertEqual(records[1]["location"], {"part": "word/document.xml", "table": 1, "row": 1, "column": 1})
        self.assertEqual(records[-1]["location"]["paragraph"], 2)
        self.assertEqual(self.recover("docx", path), {index: item["text"] for index, item in enumerate(records, 1)})

    def test_xlsx_reads_shared_inline_boolean_raw_numeric_and_formula_cache(self):
        path = self.make_xlsx()
        data = self.extract("xlsx", path, "--sheet", "销售")
        records = data["records"]
        self.assertEqual([item["text"] for item in records], ["中文字段", "内联", "true", "45292", "90584", ""])
        self.assertEqual(records[4]["location"], {"sheet": "销售", "cell": "B7", "part": "xl/worksheets/sheet1.xml"})
        self.assertEqual(records[4]["formula"], "A7*2")
        self.assertEqual(records[4]["cached_value"], "90584")
        self.assertEqual(records[5]["formula"], "SUM(A1:A3)")
        self.assertIsNone(records[5]["cached_value"])
        self.assertEqual(self.recover("xlsx", path, sheet="销售"), {index: item["text"] for index, item in enumerate(records, 1)})

    def test_unknown_xlsx_sheet_reports_available_names(self):
        data = self.extract("xlsx", self.make_xlsx(), "--sheet", "missing", success=False)
        self.assertIn("销售", data["error"])

    def test_missing_pdf_reader_is_explicit_and_does_not_install(self):
        path = self.workspace / "report.pdf"
        path.write_bytes(b"%PDF-1.4\n")
        env = {**os.environ, "PATH": ""}
        # -S excludes any site-packages, making absence portable across hosts.
        data = self.extract("pdf", path, env=env, isolated=True, success=False)
        self.assertIn("No supported PDF reader", data["error"])
        self.assertIn("Nothing was installed", data["error"])
        self.assertEqual(list(self.workspace.iterdir()), [path])

    def test_existing_pdftotext_page_output_is_located_and_bounded(self):
        path = self.workspace / "report.pdf"
        path.write_bytes(b"%PDF-1.4\n")
        executable = self.workspace / "pdftotext"
        executable.write_text(f'#!{sys.executable}\nimport pathlib,sys\nassert sys.argv[sys.argv.index("-f")+1] == "3"\npathlib.Path(sys.argv[-1]).write_text("第三页内容\\f第四页\\f第五页\\f", encoding="utf-8")\n')
        executable.chmod(0o755)
        env = {**os.environ, "PATH": str(self.workspace)}
        data = self.extract("pdf", path, "--reader", "pdftotext", "--start", 3, "--limit", 2, env=env)
        self.assertEqual([item["text"] for item in data["records"]], ["第三页内容", "第四页"])
        self.assertEqual([item["location"]["page"] for item in data["records"]], [3, 4])
        self.assertEqual(data["next"], {"start": 5, "offset": 0})

    def test_reader_failure_is_preserved(self):
        path = self.workspace / "report.pdf"
        path.write_bytes(b"%PDF-1.4\n")
        executable = self.workspace / "pdftotext"
        executable.write_text(f'#!{sys.executable}\nimport sys\nsys.stderr.write("corrupt document")\nsys.exit(7)\n')
        executable.chmod(0o755)
        data = self.extract("pdf", path, "--reader", "pdftotext",
                            env={**os.environ, "PATH": str(self.workspace)}, success=False)
        self.assertIn("corrupt document", data["error"])

    def test_corrupt_or_incomplete_documents_return_structured_failures(self):
        path = self.workspace / "bad.docx"
        path.write_bytes(b"not a zip")
        self.extract("docx", path, success=False)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("unrelated", "not a document")
        self.extract("docx", path, success=False)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", "<broken")
        self.extract("docx", path, success=False)
        self.extract("json", self.workspace / "absent", success=False)

    def test_invalid_ranges_and_offsets_fail_instead_of_dropping_content(self):
        path = self.workspace / "data.json"
        path.write_text('"short"')
        self.extract("json", path, "--offset", 100, success=False)
        csv_path = self.workspace / "data.csv"
        csv_path.write_text("a,b\n")
        self.extract("csv", csv_path, "--offset", 100, success=False)
        self.extract("csv", csv_path, "--start", 8, "--offset", 1, success=False)
        for args in (("json", path, "--max-chars", 0), ("csv", csv_path, "--start", 0),
                     ("csv", csv_path, "--limit", 0), ("csv", csv_path, "--delimiter", "::")):
            with self.subTest(args=args):
                result = self.invoke(EXTRACT, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr)

    def test_skill_remains_discoverable_and_scripts_compile(self):
        registry = SkillRegistry([BUILTIN_ROOT])
        self.assertIn("- workspace:", registry.catalog())
        for script in (PROBE, EXTRACT):
            compile(script.read_text(), str(script), "exec")


if __name__ == "__main__":
    unittest.main()
