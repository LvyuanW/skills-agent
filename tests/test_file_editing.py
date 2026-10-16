import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry


BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
SKILL_ROOT = BUILTIN_ROOT / "file-editing"
SCRIPT = SKILL_ROOT / "scripts" / "edit.py"


class FileEditingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()

    def edit(self, *arguments, input=b""):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            input=input, capture_output=True, cwd=self.workspace, timeout=10,
        )

    def success(self, *arguments, input=b"", changed=True):
        result = self.edit(*arguments, input=input)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(result.stderr, b"")
        summary = json.loads(result.stdout)
        self.assertEqual(summary["operation"], arguments[0])
        self.assertEqual(summary["changed"], changed)
        reported_path = Path(summary["path"])
        if not reported_path.is_absolute():
            reported_path = self.workspace / reported_path
        self.assertEqual(reported_path, (self.workspace / arguments[1]).absolute())
        return result, summary

    def failure(self, *arguments, input=b""):
        result = self.edit(*arguments, input=input)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip(), "Failures must explain what prevented the edit")
        return result

    def test_progressive_create_insert_replace_delete_and_remove(self):
        # Each invocation receives the previous invocation's persisted result.
        target = self.workspace / "scene.html"
        self.success("create", "scene.html", input=b"<svg>\n<circle/>\n</svg>\n")
        self.assertEqual(target.read_bytes(), b"<svg>\n<circle/>\n</svg>\n")

        self.success("insert", "scene.html", "--anchor", "<circle/>",
                     "--position", "before", "--text", '<g id="wheel">')
        self.assertEqual(target.read_bytes(), b'<svg>\n<g id="wheel"><circle/>\n</svg>\n')

        self.success("insert", "scene.html", "--anchor", "<circle/>",
                     "--position", "after", input=b"</g>")
        self.success("replace", "scene.html", "--old", "<circle/>",
                     "--new", '<circle r="12"/>')
        self.assertEqual(target.read_bytes(), b'<svg>\n<g id="wheel"><circle r="12"/></g>\n</svg>\n')

        self.success("delete-content", "scene.html", "--old", '<circle r="12"/>')
        self.assertEqual(target.read_bytes(), b'<svg>\n<g id="wheel"></g>\n</svg>\n')
        self.success("delete-file", "scene.html")
        self.assertFalse(target.exists())

    def test_ambiguous_missing_and_empty_matches_leave_content_unchanged(self):
        target = self.workspace / "notes.txt"
        original = b"aaa\nrepeat\nrepeat\n"
        cases = [
            ("replace", "--old", "absent", "--new", "replacement"),
            ("replace", "--old", "repeat", "--new", "replacement"),
            ("replace", "--old", "aa", "--new", "replacement"),
            ("replace", "--old", "", "--new", "replacement"),
            ("insert", "--anchor", "absent", "--position", "after", "--text", "inserted"),
            ("insert", "--anchor", "repeat", "--position", "before", "--text", "inserted"),
            ("insert", "--anchor", "aa", "--position", "after", "--text", "inserted"),
            ("insert", "--anchor", "", "--position", "before", "--text", "inserted"),
            ("delete-content", "--old", "absent"),
            ("delete-content", "--old", "repeat"),
            ("delete-content", "--old", "aa"),
            ("delete-content", "--old", ""),
        ]
        for operation, *options in cases:
            with self.subTest(operation=operation, options=options):
                target.write_bytes(original)
                self.failure(operation, "notes.txt", *options)
                self.assertEqual(target.read_bytes(), original)

    def test_exact_match_does_not_fall_back_to_whitespace_normalization(self):
        target = self.workspace / "tabs.py"
        original = b"\treturn value\r\nreturn end"
        target.write_bytes(original)
        self.failure("replace", "tabs.py", "--old", "    return value\n", "--new", "pass\n")
        self.assertEqual(target.read_bytes(), original)

    def test_edits_preserve_unicode_tabs_mixed_newlines_and_missing_final_newline(self):
        target = self.workspace / "字面量.txt"
        before = "\t前缀\r\nreplace-me\n后缀\r\nlast\t尾".encode("utf-8")
        target.write_bytes(before)
        replacement = "\t新值\r\n第二行".encode("utf-8")
        self.success("replace", "字面量.txt", "--old", "replace-me", input=replacement)
        expected = before.replace(b"replace-me", replacement)
        self.assertEqual(target.read_bytes(), expected)

        insertion = "插入\r\n".encode("utf-8")
        self.success("insert", "字面量.txt", "--anchor", "last\t尾", "--position", "before",
                     input=insertion)
        expected = expected.replace("last\t尾".encode("utf-8"), insertion + "last\t尾".encode("utf-8"))
        self.assertEqual(target.read_bytes(), expected)

        self.success("delete-content", "字面量.txt", "--old", "第二行")
        self.assertEqual(target.read_bytes(), expected.replace("第二行".encode("utf-8"), b""))

    def test_multiline_payload_files_and_empty_replacement(self):
        old = self.workspace / "old payload.txt"
        new = self.workspace / "new payload.txt"
        anchor = self.workspace / "anchor payload.txt"
        target = self.workspace / "document.txt"
        original = b"before\r\nold first\r\nold second\nafter"
        old.write_bytes(b"old first\r\nold second")
        new.write_bytes(b"new first\r\nnew second")
        anchor.write_bytes(b"new first\r\nnew second")
        source = self.workspace / "initial payload.txt"
        source.write_bytes(original)
        self.success("create", "document.txt", "--text-file", source)
        self.assertEqual(target.read_bytes(), original)
        self.success("replace", "document.txt", "--old-file", old, "--new-file", new)
        self.assertEqual(target.read_bytes(), b"before\r\nnew first\r\nnew second\nafter")
        self.success("insert", "document.txt", "--anchor-file", anchor,
                     "--position", "after", "--text-file", old)
        self.success("delete-content", "document.txt", "--old-file", old)
        self.success("replace", "document.txt", "--old-file", anchor, "--new", "")
        self.assertEqual(target.read_bytes(), b"before\r\n\nafter")

    def test_create_refuses_overwrite_and_does_not_create_parent_directories(self):
        target = self.workspace / "existing.txt"
        original = b"original"
        target.write_bytes(original)
        self.failure("create", "existing.txt", "--text", "replacement")
        self.assertEqual(target.read_bytes(), original)
        self.failure("create", "missing-parent/new.txt", "--text", "new file")
        self.assertFalse((self.workspace / "missing-parent").exists())

    def test_noop_replacement_reports_unchanged_and_retains_content(self):
        target = self.workspace / "unchanged.txt"
        original = "前缀\r\n\tkeep\n最后".encode("utf-8")
        target.write_bytes(original)
        self.success("replace", "unchanged.txt", "--old", "keep", "--new", "keep", changed=False)
        self.assertEqual(target.read_bytes(), original)

    def test_missing_files_and_directories_are_not_created_or_removed(self):
        directory = self.workspace / "directory"
        directory.mkdir()
        child = directory / "keep.txt"
        child.write_bytes(b"keep")
        for target in ("missing.txt", "directory"):
            for operation, *options in [
                ("replace", "--old", "keep", "--new", "changed"),
                ("insert", "--anchor", "keep", "--position", "after", "--text", "changed"),
                ("delete-content", "--old", "keep"),
                ("delete-file",),
            ]:
                with self.subTest(target=target, operation=operation):
                    self.failure(operation, target, *options)
                    self.assertFalse((self.workspace / "missing.txt").exists())
                    self.assertTrue(directory.is_dir())
                    self.assertEqual(child.read_bytes(), b"keep")

    def test_text_edits_reject_binary_files_without_modifying_them(self):
        target = self.workspace / "binary.dat"
        for original in (b"\xffkeep", b"keep\x00data"):
            for operation, *options in [
                ("replace", "--old", "keep", "--new", "changed"),
                ("insert", "--anchor", "keep", "--position", "before", "--text", "changed"),
                ("delete-content", "--old", "keep"),
            ]:
                with self.subTest(original=original, operation=operation):
                    target.write_bytes(original)
                    self.failure(operation, "binary.dat", *options)
                    self.assertEqual(target.read_bytes(), original)
        self.success("delete-file", "binary.dat")
        self.assertFalse(target.exists())

    def test_invalid_utf8_or_nul_payloads_leave_targets_unchanged(self):
        target = self.workspace / "existing.txt"
        for payload in (b"\xff", b"new\x00value"):
            with self.subTest(payload=payload):
                target.write_bytes(b"old")
                self.failure("replace", "existing.txt", "--old", "old", input=payload)
                self.assertEqual(target.read_bytes(), b"old")
                self.failure("create", "new.txt", input=payload)
                self.assertFalse((self.workspace / "new.txt").exists())

    def test_missing_or_invalid_payload_files_do_not_modify_target(self):
        target = self.workspace / "existing.txt"
        payload = self.workspace / "payload.txt"
        for contents in (None, b"\xff", b"old\x00", b""):
            for operation, *options in [
                ("replace", "--old-file", payload, "--new", "new"),
                ("insert", "--anchor-file", payload, "--position", "after", "--text", "new"),
                ("delete-content", "--old-file", payload),
            ]:
                with self.subTest(contents=contents, operation=operation):
                    target.write_bytes(b"old")
                    payload.unlink(missing_ok=True)
                    if contents is not None:
                        payload.write_bytes(contents)
                    self.failure(operation, "existing.txt", *options)
                    self.assertEqual(target.read_bytes(), b"old")

    @unittest.skipUnless(hasattr(os, "symlink"), "Symbolic links are unavailable")
    def test_edits_and_deletion_refuse_symlinks_and_preserve_their_targets(self):
        target = self.workspace / "real.txt"
        target.write_bytes(b"keep")
        link = self.workspace / "link.txt"
        link.symlink_to(target)
        for operation, *options in [
            ("create", "--text", "new"),
            ("replace", "--old", "keep", "--new", "changed"),
            ("insert", "--anchor", "keep", "--position", "before", "--text", "changed"),
            ("delete-content", "--old", "keep"),
            ("delete-file",),
        ]:
            with self.subTest(operation=operation):
                self.failure(operation, "link.txt", *options)
                self.assertTrue(link.is_symlink())
                self.assertEqual(target.read_bytes(), b"keep")
        dangling = self.workspace / "dangling.txt"
        dangling.symlink_to(self.workspace / "not-created.txt")
        self.failure("create", "dangling.txt", "--text", "new")
        self.assertTrue(dangling.is_symlink())
        self.assertFalse((self.workspace / "not-created.txt").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX executable permissions are required")
    def test_edits_preserve_executable_permissions(self):
        target = self.workspace / "run.py"
        target.write_bytes(b"#!/usr/bin/env python3\nprint('old')\n")
        target.chmod(0o751)
        self.success("replace", "run.py", "--old", "old", "--new", "new")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o751)
        self.assertEqual(target.read_bytes(), b"#!/usr/bin/env python3\nprint('new')\n")

    def test_success_output_is_bounded_without_truncating_saved_content(self):
        target = self.workspace / "large.txt"
        for original in (b"x" * 100000 + b"old", (b"line\n" * 30000) + b"old\n"):
            with self.subTest(length=len(original)):
                target.unlink(missing_ok=True)
                result, _ = self.success("create", "large.txt", input=original)
                self.assertLess(len(result.stdout), 20000)
                self.assertEqual(target.read_bytes(), original)
                result, summary = self.success("replace", "large.txt", "--old", "old", "--new", "new")
                self.assertLess(len(result.stdout), 20000)
                self.assertTrue(any("new" in line["text"] for line in summary["excerpt"]["lines"]),
                                "Bounded feedback should show the edited region, including long-line tails")
                self.assertEqual(target.read_bytes(), original.replace(b"old", b"new"))
                self.success("delete-file", "large.txt")

    def test_invalid_argument_combinations_do_not_mutate_files(self):
        target = self.workspace / "keep.txt"
        source = self.workspace / "payload.txt"
        target.write_bytes(b"old")
        source.write_bytes(b"new")
        for arguments in [
            ("replace", "keep.txt", "--new", "new"),
            ("replace", "keep.txt", "--old", "old", "--old-file", source, "--new", "new"),
            ("replace", "keep.txt", "--old", "old", "--new", "new", "--new-file", source),
            ("insert", "keep.txt", "--anchor", "old", "--text", "new"),
            ("insert", "keep.txt", "--anchor", "old", "--position", "middle", "--text", "new"),
            ("create", "new.txt", "--text", "new", "--text-file", source),
        ]:
            with self.subTest(arguments=arguments):
                self.failure(*arguments)
                self.assertEqual(target.read_bytes(), b"old")
                self.assertFalse((self.workspace / "new.txt").exists())

    def test_skill_is_discovered_and_script_runs_through_existing_shell_tool(self):
        catalog = SkillRegistry([BUILTIN_ROOT]).catalog()
        self.assertIn("file-editing:", catalog)
        self.assertIn(str(SKILL_ROOT / "SKILL.md"), catalog)
        tools = ToolRegistry(self.workspace)
        self.assertEqual([schema["name"] for schema in tools.schemas], ["shell"])
        command = shlex.join([sys.executable, str(SCRIPT), "create", "result 'quoted'.txt",
                              "--text", "literal $(printf wrong) `pwd` 中文"])
        result = json.loads(tools.execute("shell", json.dumps({
            "name": "Create an artifact using the file-editing skill", "command": command,
            "result_mode": "direct", "run_in_background": False,
        })))
        self.assertEqual(result["exit_code"], 0, result["stderr"])
        self.assertEqual(json.loads(result["stdout"])["operation"], "create")
        self.assertEqual((self.workspace / "result 'quoted'.txt").read_bytes(),
                         "literal $(printf wrong) `pwd` 中文".encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
