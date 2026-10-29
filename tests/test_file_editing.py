import base64
import codecs
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from skills_agent.file_editing import EditError, SafeFile, prepare_batch, prepare_edit


class FileEditingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()

    def prepare(self, edits):
        return prepare_batch(self.workspace, edits, excluded_roots=(self.runtime,))

    def apply(self, edits):
        with self.prepare(edits) as plan:
            states = plan.apply() if plan.changed else plan.before
            return plan, states

    def edit(self, operation, path, **kwargs):
        return self.apply([{"operation": operation, "path": path, **kwargs}])

    def test_batch_multiline_replacements_and_insert_publish_each_file_once(self):
        target = self.workspace / "shader.glsl"
        original = "#version 330\nvoid main() {\n  float x = 1.;\n  gl_Position = vec4(x);\n}\n"
        target.write_text(original)
        calls = []
        publish = SafeFile.write_atomically
        def capture(target, *args, **kwargs):
            calls.append(str(target.path))
            return publish(target, *args, **kwargs)
        edits = [
            {"operation": "replace", "path": "shader.glsl", "old_text": "  float x = 1.;\n",
             "new_text": "  float x = 2.;\n  float y = 3.;\n"},
            {"operation": "replace", "path": "shader.glsl", "old_text": "vec4(x)", "new_text": "vec4(x, y, 0., 1.)"},
            {"operation": "insert", "path": "shader.glsl", "anchor": "#version 330\n", "position": "after",
             "content": "// generated shader\n"},
            {"operation": "create", "path": "new/assets/config.json", "content": '{"shader":"shader.glsl"}'},
        ]
        with patch.object(SafeFile, "write_atomically", capture):
            plan, states = self.apply(edits)
        self.assertEqual(calls.count(str(target)), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(plan.before[str(target)].data, original.encode())
        self.assertIn(b"float y = 3.;", states[str(target)].data)
        self.assertIn("vec4(x, y, 0., 1.)", target.read_text())
        self.assertEqual((self.workspace / "new/assets/config.json").read_text(), '{"shader":"shader.glsl"}')
        self.assertEqual(plan.review["edit_count"], 4)
        json.dumps(plan.review)

    def test_late_validation_failure_creates_no_files_or_directories(self):
        target = self.workspace / "existing.txt"
        target.write_text("original")
        edits = [
            {"operation": "create", "path": "missing/deep/new.txt", "content": "first"},
            {"operation": "replace", "path": "existing.txt", "old_text": "absent", "new_text": "second"},
        ]
        with self.assertRaises(EditError), self.prepare(edits):
            self.fail("invalid batch must not yield a plan")
        self.assertFalse((self.workspace / "missing").exists())
        self.assertEqual(target.read_text(), "original")

    def test_conflicting_parent_file_is_rejected_before_any_directory_creation(self):
        for edits in ([{"operation": "write", "path": "new/child", "content": "child"},
                       {"operation": "write", "path": "new", "content": "parent"}],
                      [{"operation": "write", "path": "new", "content": "parent"},
                       {"operation": "write", "path": "new/child", "content": "child"}]):
            with self.subTest(edits=edits), self.assertRaises(EditError), self.prepare(edits):
                self.fail("conflicting entries must fail during validation")
            self.assertFalse((self.workspace / "new").exists())

    def test_same_batch_reads_prior_virtual_results_and_applies_only_final_paths(self):
        edits = [
            {"operation": "create", "path": "a.txt", "content": "one"},
            {"operation": "replace", "path": "a.txt", "old_text": "one", "new_text": "two"},
            {"operation": "copy", "path": "a.txt", "destination": "b.txt"},
            {"operation": "move", "path": "a.txt", "destination": "c.txt"},
            {"operation": "replace", "path": "c.txt", "old_text": "two", "new_text": "three"},
            {"operation": "delete", "path": "b.txt"},
        ]
        plan, states = self.apply(edits)
        self.assertEqual(plan.changed_paths, [str(self.workspace / "c.txt")])
        self.assertFalse((self.workspace / "a.txt").exists())
        self.assertFalse((self.workspace / "b.txt").exists())
        self.assertEqual((self.workspace / "c.txt").read_text(), "three")
        self.assertIsNone(states[str(self.workspace / "a.txt")])

    def test_case_aliases_share_virtual_state_on_case_insensitive_filesystems(self):
        target = self.workspace / "MixedName"
        target.write_text("old")
        if not (self.workspace / "mixedname").exists():
            self.skipTest("requires a case-insensitive filesystem")
        for first, second in (("MixedName", "mixedname"), ("NewName", "newname")):
            with self.subTest(first=first):
                plan, _ = self.apply([
                    {"operation": "write", "path": first, "content": "first"},
                    {"operation": "replace", "path": second, "old_text": "first", "new_text": "last"},
                ])
                self.assertEqual(len(plan.changed_paths), 1)
                self.assertEqual((self.workspace / first).read_text(), "last")

    def test_replacement_count_all_and_unique_matching(self):
        target = self.workspace / "text"
        target.write_text("a a a")
        for kwargs in ({}, {"expected_count": 2}, {"expected_count": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(EditError):
                self.edit("replace", "text", old_text="a", new_text="b", **kwargs)
            self.assertEqual(target.read_text(), "a a a")
        with self.assertRaises(EditError):
            self.edit("replace", "text", old_text="a", new_text="b", expected_count=3)
        self.edit("replace", "text", old_text="a", new_text="b", replace_all=True, expected_count=3)
        self.assertEqual(target.read_text(), "b b b")
        self.edit("replace", "text", old_text="b", new_text="c", replace_all=True)
        self.assertEqual(target.read_text(), "c c c")
        target.write_text("aaa")
        with self.assertRaises(EditError):
            self.edit("replace", "text", old_text="aa", new_text="x")
        self.edit("replace", "text", old_text="aa", new_text="x", replace_all=True, expected_count=1)
        self.assertEqual(target.read_text(), "xa")

    def test_insert_requires_unique_nonempty_anchor_and_preserves_surroundings(self):
        target = self.workspace / "text"
        target.write_text("head\nmiddle\ntail")
        self.edit("insert", "text", anchor="middle", position="before", content="before\n")
        self.edit("insert", "text", anchor="middle", position="after", content="\nafter")
        self.assertEqual(target.read_text(), "head\nbefore\nmiddle\nafter\ntail")
        for anchor in ("", "missing", "\n"):
            with self.subTest(anchor=anchor), self.assertRaises(EditError):
                self.edit("insert", "text", anchor=anchor, position="after", content="bad")

    def test_write_create_and_explicit_copy_move_overwrite(self):
        self.edit("create", "a", content="first")
        with self.assertRaises(EditError):
            self.edit("create", "a", content="bad")
        self.edit("write", "a", content="whole replacement")
        self.edit("write", "b", content="occupied")
        for operation in ("move", "copy"):
            with self.subTest(operation=operation), self.assertRaises(EditError):
                self.edit(operation, "a", destination="b")
        self.edit("copy", "a", destination="b", overwrite=True)
        self.assertEqual((self.workspace / "a").read_text(), "whole replacement")
        self.assertEqual((self.workspace / "b").read_text(), "whole replacement")
        self.edit("move", "a", destination="b", overwrite=True)
        self.assertFalse((self.workspace / "a").exists())
        self.assertEqual((self.workspace / "b").read_text(), "whole replacement")

    def test_binary_and_encoded_text_operations(self):
        binary = bytes(range(256)) + b"\x00\xff"
        self.edit("write", "blob", content_base64=base64.b64encode(binary).decode())
        self.assertEqual((self.workspace / "blob").read_bytes(), binary)
        self.edit("copy", "blob", destination="copied")
        self.edit("move", "copied", destination="moved")
        self.assertEqual((self.workspace / "moved").read_bytes(), binary)
        self.edit("write", "text", content="café\x00fin", encoding="latin-1")
        self.edit("replace", "text", old_text="café", new_text="thé", encoding="latin-1")
        self.edit("insert", "text", anchor="\x00", position="after", content="ok", encoding="latin-1")
        self.assertEqual((self.workspace / "text").read_bytes(), b"th\xe9\x00okfin")
        self.edit("write", "utf16", content="第一行\r\n第二行", encoding="utf-16")
        self.edit("replace", "utf16", old_text="第二行", new_text="末行", encoding="utf-16")
        self.assertEqual((self.workspace / "utf16").read_text(encoding="utf-16"), "第一行\n末行")

    def test_empty_files_and_noop_preserve_existing_inode(self):
        self.edit("create", "empty", content="")
        path = self.workspace / "empty"
        initial = path.stat().st_ino
        plan, _ = self.edit("replace", "empty", old_text="", new_text="")
        self.assertFalse(plan.changed)
        self.assertEqual(path.stat().st_ino, initial)
        self.edit("replace", "empty", old_text="", new_text="filled")
        with self.assertRaises(EditError):
            self.edit("replace", "empty", old_text="", new_text="bad")
        initial = path.stat().st_ino
        plan, _ = self.edit("write", "empty", content="filled")
        self.assertFalse(plan.changed)
        self.assertEqual(path.stat().st_ino, initial)

    def test_encoded_replacements_preserve_bom_and_original_byte_order(self):
        target = self.workspace / "encoded"
        for encoding, marker, precise in (("utf-16", codecs.BOM_UTF16_BE, "utf-16-be"),
                                           ("utf-32", codecs.BOM_UTF32_BE, "utf-32-be")):
            with self.subTest(encoding=encoding):
                target.write_bytes(marker + "前缀 old 后缀".encode(precise))
                self.edit("replace", "encoded", old_text="old", new_text="new", encoding=encoding)
                self.assertEqual(target.read_bytes(), marker + "前缀 new 后缀".encode(precise))
        target.write_bytes(b"old without BOM")
        self.edit("replace", "encoded", old_text="old", new_text="new", encoding="utf-8-sig")
        self.assertEqual(target.read_bytes(), b"new without BOM")

    def test_unicode_tabs_crlf_and_permissions_are_preserved(self):
        target = self.workspace / "字面量.py"
        original = "\t前缀\r\nreplace-me\n后缀\r\nlast\t尾".encode()
        target.write_bytes(original)
        target.chmod(0o751)
        replacement = "\t新值\r\n第二行"
        self.edit("replace", target, old_text="replace-me", new_text=replacement)
        self.assertEqual(target.read_bytes(), original.replace(b"replace-me", replacement.encode()))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o751)

    def test_arbitrary_directories_relative_parent_and_project_metadata_are_allowed(self):
        external = self.root / "outside" / "nested" / "file"
        self.edit("write", external, content="absolute")
        self.edit("replace", "../outside/nested/file", old_text="absolute", new_text="relative")
        self.assertEqual(external.read_text(), "relative")
        for path in (".git/config", ".agent/log", "node_modules/pkg/index.js", ".venv/config"):
            self.edit("write", path, content="allowed")
            self.assertEqual((self.workspace / path).read_text(), "allowed")

    def test_only_current_runtime_paths_are_protected_including_aliases(self):
        (self.runtime / "receipt").write_text("keep")
        (self.workspace / "runtime-link").symlink_to(self.runtime, target_is_directory=True)
        for path in (self.runtime / "receipt", self.workspace / "runtime-link" / "receipt"):
            with self.subTest(path=path), self.assertRaises(EditError):
                self.edit("write", path, content="bad")
        self.assertEqual((self.runtime / "receipt").read_text(), "keep")
        with self.assertRaises(EditError):
            self.edit("delete", self.runtime)
        self.assertTrue(self.runtime.is_dir())

    def test_protected_attachments_may_be_copied_but_never_modified_or_moved(self):
        attachment = self.runtime / "uploaded.bin"
        attachment.write_bytes(b"\xffinput\x00")
        self.edit("copy", attachment, destination="input/data.bin")
        self.assertEqual((self.workspace / "input/data.bin").read_bytes(), attachment.read_bytes())
        for operation, path, kwargs in (("move", attachment, {"destination": "moved.bin"}),
                                        ("copy", "input/data.bin", {"destination": attachment, "overwrite": True}),
                                        ("delete", attachment, {})):
            if operation == "copy":
                (self.workspace / "input/data.bin").write_bytes(b"different")
            with self.subTest(operation=operation), self.assertRaises(EditError):
                self.edit(operation, path, **kwargs)
        self.assertEqual(attachment.read_bytes(), b"\xffinput\x00")
        self.assertFalse((self.workspace / "moved.bin").exists())

    def test_parent_and_leaf_symlink_text_edits_follow_targets(self):
        outside = self.root / "external"
        outside.mkdir()
        real = outside / "file"
        real.write_text("old")
        (self.workspace / "folder").symlink_to(outside, target_is_directory=True)
        (self.workspace / "leaf").symlink_to(real)
        plan, _ = self.edit("replace", "folder/file", old_text="old", new_text="updated")
        self.assertEqual(plan.changed_paths, [str(real)])
        self.edit("insert", "leaf", anchor="updated", position="after", content="!")
        self.assertEqual(real.read_text(), "updated!")
        self.assertTrue((self.workspace / "leaf").is_symlink())
        self.edit("write", "leaf", content="rewritten")
        self.assertEqual(real.read_text(), "rewritten")

    def test_delete_move_copy_operate_on_symlink_entries_and_keep_targets(self):
        real = self.workspace / "real"
        real.write_text("target")
        (self.workspace / "link").symlink_to("real")
        self.edit("copy", "link", destination="copy")
        self.assertTrue((self.workspace / "copy").is_symlink())
        self.assertEqual(os.readlink(self.workspace / "copy"), "real")
        self.edit("move", "copy", destination="moved")
        self.assertFalse((self.workspace / "copy").exists())
        self.assertTrue((self.workspace / "moved").is_symlink())
        self.edit("delete", "link")
        self.assertFalse((self.workspace / "link").is_symlink())
        self.assertEqual(real.read_text(), "target")
        self.edit("write", "occupied", content="old")
        self.edit("copy", "moved", destination="occupied", overwrite=True)
        self.assertTrue((self.workspace / "occupied").is_symlink())
        self.assertEqual(real.read_text(), "target")

    def test_dangling_symlink_create_refuses_but_write_follows_target(self):
        link = self.workspace / "dangling"
        destination = self.root / "new" / "target"
        link.symlink_to(destination)
        with self.assertRaises(EditError):
            self.edit("create", "dangling", content="bad")
        self.edit("write", "dangling", content="written")
        self.assertTrue(link.is_symlink())
        self.assertEqual(destination.read_text(), "written")

    def test_batch_resolves_links_created_by_earlier_virtual_copy(self):
        target = self.workspace / "real"
        target.write_text("before")
        (self.workspace / "source-link").symlink_to("real")
        self.apply([
            {"operation": "copy", "path": "source-link", "destination": "new-link"},
            {"operation": "replace", "path": "new-link", "old_text": "before", "new_text": "after"},
        ])
        self.assertTrue((self.workspace / "new-link").is_symlink())
        self.assertEqual(target.read_text(), "after")

    def test_concurrent_file_or_followed_link_change_aborts_before_any_write(self):
        target = self.workspace / "file"
        target.write_text("before")
        with self.prepare([{"operation": "write", "path": "new/deep/file", "content": "first"},
                           {"operation": "replace", "path": "file", "old_text": "before", "new_text": "after"}]) as plan:
            target.write_text("external")
            with self.assertRaises(EditError):
                plan.apply()
            self.assertFalse(plan.applied)
        self.assertFalse((self.workspace / "new").exists())
        other = self.workspace / "other"
        other.write_text("other")
        link = self.workspace / "link"
        link.symlink_to(target)
        with self.prepare([{"operation": "write", "path": "link", "content": "changed"}]) as plan:
            link.unlink()
            link.symlink_to(other)
            with self.assertRaises(EditError):
                plan.apply()
        self.assertEqual(target.read_text(), "external")
        self.assertEqual(other.read_text(), "other")

    def test_parent_replacement_after_prepare_cannot_redirect_edit(self):
        folder = self.workspace / "folder"
        folder.mkdir()
        (folder / "file").write_text("inside")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "file").write_text("outside")
        with self.prepare([{"operation": "replace", "path": "folder/file",
                            "old_text": "inside", "new_text": "changed"}]) as plan:
            folder.rename(self.workspace / "saved")
            folder.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(EditError):
                plan.apply()
            self.assertFalse(plan.applied)
        self.assertEqual((outside / "file").read_text(), "outside")

    def test_partial_move_records_publication_and_keeps_source(self):
        (self.workspace / "source").write_text("recoverable")
        with self.prepare([{"operation": "move", "path": "source", "destination": "dest"}]) as plan:
            with patch.object(SafeFile, "delete", side_effect=PermissionError("source deletion failed")):
                with self.assertRaises(PermissionError):
                    plan.apply()
            self.assertTrue(plan.applied)
            self.assertEqual(plan.applied_paths, [str(self.workspace / "dest")])
        self.assertEqual((self.workspace / "source").read_text(), "recoverable")
        self.assertEqual((self.workspace / "dest").read_text(), "recoverable")

    def test_syscall_return_interrupt_preserves_attempted_path(self):
        target = self.workspace / "file"
        target.write_text("old")
        replace = os.replace
        def interrupted(*args, **kwargs):
            result = replace(*args, **kwargs)
            if kwargs.get("src_dir_fd") is not None:
                raise KeyboardInterrupt
            return result
        with self.prepare([{"operation": "write", "path": "file", "content": "new"}]) as plan:
            with patch("skills_agent.file_editing.os.replace", side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    plan.apply()
            self.assertEqual(plan.attempted_paths, [str(target)])
            self.assertFalse(plan.applied)
            self.assertEqual(plan.targets[str(target)].read().data, b"new")
        self.assertEqual(target.read_text(), "new")

    def test_directories_special_files_loops_and_invalid_inputs_fail_without_changes(self):
        (self.workspace / "directory").mkdir()
        os.mkfifo(self.workspace / "fifo")
        (self.workspace / "cycle").symlink_to("cycle")
        invalid = [
            {"operation": "write", "path": "/", "content": "bad"},
            {"operation": "write", "path": "directory", "content": "bad"},
            {"operation": "delete", "path": "fifo"},
            {"operation": "write", "path": "cycle", "content": "bad"},
            {"operation": "write", "path": "new", "content_base64": "%%%"},
            {"operation": "write", "path": "new", "content": "x", "content_base64": "eA=="},
            {"operation": "write", "path": "new", "content": "x", "encoding": "missing-encoding"},
            {"operation": "replace", "path": "new", "old_text": "x", "new_text": "y", "replace_all": 1},
        ]
        for edit in invalid:
            with self.subTest(edit=edit), self.assertRaises(EditError), self.prepare([edit]):
                self.fail("invalid request must not produce a plan")
        self.assertFalse((self.workspace / "new").exists())
        self.assertTrue((self.workspace / "directory").is_dir())

    def test_preview_is_bounded_and_single_edit_wrapper_remains_compatible(self):
        (self.workspace / "large").write_text("x" * 10000 + "old" + "y" * 10000)
        with prepare_edit(self.workspace, "replace", "large", old_text="old", new_text="new",
                          protected_names={".git"}) as plan:
            self.assertEqual(plan.review["operation"], "replace")
            self.assertLess(len(json.dumps(plan.review["excerpt"])), 1900)
            self.assertIn("new", str(plan.review["excerpt"]))
            plan.apply()
        self.assertIn("new", (self.workspace / "large").read_text())


if __name__ == "__main__":
    unittest.main()
