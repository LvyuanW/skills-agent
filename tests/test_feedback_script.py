import base64
import hashlib
import json
import os
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry


BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
SKILL_ROOT = BUILTIN_ROOT / "feedback"
SCRIPT = SKILL_ROOT / "scripts" / "feedback.py"


def png(width=1, height=1):
    """Generate a real PNG without making the test depend on Pillow."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    pixels = b"".join(b"\0" + b"\x20\x40\x80\xff" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class FeedbackScriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()

    def feedback(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True, cwd=self.workspace, timeout=15,
        )

    def envelope(self, result, kind, *, success=True):
        if success:
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        else:
            self.assertNotEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["skills_agent_feedback"], 1)
        self.assertEqual(data["kind"], kind)
        self.assertIsInstance(data["source"], str)
        self.assertIsInstance(data["observations"], dict)
        self.assertIsInstance(data["attachments"], list)
        return data

    def resolved_path(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.workspace / path

    def failure(self, *arguments):
        result = self.feedback(*arguments)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr.strip() or result.stdout.strip(),
                        "Failures must report what prevented feedback acquisition")
        return result

    def test_file_feedback_reports_actual_metadata_without_modifying_source(self):
        target = self.workspace / "产物 with spaces.txt"
        original = "文字\r\n\t内容\n最后".encode("utf-8")
        target.write_bytes(original)
        result = self.feedback("file", target)
        data = self.envelope(result, "file")
        serialized = json.dumps(data["observations"], ensure_ascii=False)
        self.assertIn(hashlib.sha256(original).hexdigest(), serialized)
        self.assertIn(str(len(original)), serialized)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(data["attachments"], [])

    def test_directory_listing_is_bounded_and_does_not_recurse(self):
        directory = self.workspace / "outputs"
        directory.mkdir()
        for index in range(20):
            (directory / f"file-{index:02d}.txt").write_text(str(index))
        nested = directory / "nested"
        nested.mkdir()
        (nested / "hidden-child.txt").write_text("not a direct child")
        data = self.envelope(self.feedback("file", directory, "--limit", 3), "file")
        serialized = json.dumps(data["observations"])
        self.assertNotIn("hidden-child.txt", serialized)
        self.assertLessEqual(sum(f"file-{index:02d}.txt" in serialized for index in range(20)), 3)
        self.assertTrue(data["observations"].get("truncated"),
                        "A limited listing must tell the agent that more entries exist")
        self.assertEqual(len(list(directory.iterdir())), 21)

    def test_text_range_preserves_unicode_tabs_and_mixed_line_endings(self):
        target = self.workspace / "文本.txt"
        original = "第一行\r\n\t第二行\n最后无换行".encode("utf-8")
        target.write_bytes(original)
        data = self.envelope(self.feedback("text", target, "--start", 2, "--end", 3), "text")
        lines = data["observations"]["lines"]
        self.assertEqual([line["line"] for line in lines], [2, 3])
        self.assertEqual([line["text"] for line in lines], ["\t第二行\n", "最后无换行"])
        self.assertEqual([line["column"] for line in lines], [1, 1])
        self.assertFalse(data["observations"]["truncated"])
        self.assertEqual(target.read_bytes(), original)

    def test_bounded_text_continuation_recovers_long_lines_without_skipping_content(self):
        target = self.workspace / "long.txt"
        original = "前" + "长文本\t" * 45 + "末\r\n第二行\n第三行末"
        target.write_bytes(original.encode("utf-8"))
        recovered = []
        start, column = 1, 1
        for _ in range(100):
            result = self.feedback("text", target, "--start", start, "--column", column,
                                   "--end", 3, "--max-chars", 11)
            data = self.envelope(result, "text")
            observations = data["observations"]
            pieces = observations["lines"]
            self.assertLessEqual(sum(len(line["text"]) for line in pieces), 11)
            recovered.extend(line["text"] for line in pieces)
            self.assertLess(len(result.stdout), 4000)
            if not observations["truncated"]:
                break
            next_position = observations["next_line"], observations["next_column"]
            self.assertGreater(next_position, (start, column),
                               "Truncation must provide a position that makes forward progress")
            start, column = next_position
        else:
            self.fail("Bounded reads did not complete despite following the continuation position")
        self.assertEqual("".join(recovered), original)
        self.assertEqual(target.read_bytes(), original.encode("utf-8"))

    def test_invalid_inputs_fail_without_mutating_files(self):
        text = self.workspace / "keep.txt"
        binary = self.workspace / "binary.bin"
        nul_text = self.workspace / "nul.txt"
        text.write_bytes(b"keep\r\nunchanged")
        binary.write_bytes(b"\xff\x00content")
        nul_text.write_bytes(b"looks like text\x00but is binary")
        cases = [
            ("file", "absent"),
            ("text", "absent"),
            ("text", binary),
            ("text", nul_text),
            ("text", text, "--start", 0),
            ("text", text, "--start", 2, "--end", 1),
            ("text", text, "--column", 0),
            ("text", text, "--max-chars", 0),
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.failure(*arguments)
                self.assertEqual(text.read_bytes(), b"keep\r\nunchanged")
                self.assertEqual(binary.read_bytes(), b"\xff\x00content")
                self.assertEqual(nul_text.read_bytes(), b"looks like text\x00but is binary")

    def test_run_retains_full_binary_logs_and_returns_bounded_observations(self):
        stdout = ("first 中文\r\n" + "long stdout\n" * 5000).encode("utf-8") + b"\xffstdout tail\n"
        stderr = b"first stderr\n" + b"long stderr\n" * 5000 + b"\xfestderr tail\n"
        stdout_source, stderr_source = self.workspace / "out.bin", self.workspace / "err.bin"
        stdout_source.write_bytes(stdout)
        stderr_source.write_bytes(stderr)
        code = ("import os, sys; from pathlib import Path; "
                "os.write(1, Path(sys.argv[1]).read_bytes()); "
                "os.write(2, Path(sys.argv[2]).read_bytes())")
        result = self.feedback("run", "--log-dir", "logs", "--", sys.executable, "-c", code,
                               stdout_source, stderr_source)
        data = self.envelope(result, "run")
        observations = data["observations"]
        self.assertEqual(observations["exit_code"], 0)
        self.assertFalse(observations["timed_out"])
        self.assertEqual(self.resolved_path(observations["stdout_log"]).read_bytes(), stdout)
        self.assertEqual(self.resolved_path(observations["stderr_log"]).read_bytes(), stderr)
        self.assertIn("stdout tail", observations["stdout_tail"])
        self.assertIn("stderr tail", observations["stderr_tail"])
        self.assertNotIn("first 中文", observations["stdout_tail"])
        self.assertLess(len(result.stdout), 25000)
        self.assertEqual(data["attachments"], [])

    def test_run_failure_preserves_actual_exit_status_and_stderr(self):
        code = "import os, sys; os.write(2, b'assertion failed\\n'); print('SUCCESS'); sys.exit(7)"
        result = self.feedback("run", "--log-dir", "logs", "--", sys.executable, "-c", code)
        data = self.envelope(result, "run", success=False)
        self.assertEqual(data["observations"]["exit_code"], 7)
        self.assertFalse(data["observations"]["timed_out"])
        self.assertEqual(self.resolved_path(data["observations"]["stderr_log"]).read_bytes(),
                         b"assertion failed\n")
        self.assertIn("SUCCESS", data["observations"]["stdout_tail"])

    def test_run_timeout_retains_output_produced_before_interruption(self):
        code = "import os, time; os.write(1, b'before timeout\\n'); time.sleep(20)"
        result = self.feedback("run", "--log-dir", "logs", "--timeout", "0.2", "--",
                               sys.executable, "-c", code)
        data = self.envelope(result, "run", success=False)
        self.assertEqual(result.returncode, 124)
        self.assertTrue(data["observations"]["timed_out"])
        self.assertNotEqual(data["observations"]["exit_code"], 0)
        self.assertEqual(self.resolved_path(data["observations"]["stdout_log"]).read_bytes(),
                         b"before timeout\n")

    @unittest.skipUnless(os.name == "posix", "POSIX descendant termination is required")
    def test_run_timeout_also_stops_the_commands_descendant_process(self):
        marker = self.workspace / "descendant-finished.txt"
        child_code = ("import sys, time; from pathlib import Path; time.sleep(1.5); "
                      "Path(sys.argv[1]).write_text('should not survive timeout')")
        parent_code = ("import subprocess, sys, time; "
                       "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
                       "print('child started', flush=True); time.sleep(20)")
        result = self.feedback("run", "--log-dir", "logs", "--timeout", "0.4", "--",
                               sys.executable, "-c", parent_code, child_code, marker)
        data = self.envelope(result, "run", success=False)
        self.assertTrue(data["observations"]["timed_out"])
        # Waiting in a harmless child keeps the test independent of scheduler timing.
        subprocess.run([sys.executable, "-c", "import time; time.sleep(1.7)"], timeout=5)
        self.assertFalse(marker.exists(), "A descendant must not keep working after its stage timed out")

    def test_run_startup_failure_is_not_reported_as_a_completed_command(self):
        missing = self.workspace / "missing executable"
        result = self.feedback("run", "--log-dir", "logs", "--", missing)
        data = self.envelope(result, "run", success=False)
        self.assertIsNone(data["observations"]["exit_code"])
        self.assertTrue(data["observations"]["startup_error"])
        self.assertFalse(data["observations"]["timed_out"])

    def test_image_uses_header_for_native_attachment_instead_of_filename_suffix(self):
        target = self.workspace / "actual image.jpg"
        original = png(3, 2)
        target.write_bytes(original)
        data = self.envelope(self.feedback("image", target, "--label", "检查场景"), "image")
        self.assertEqual(len(data["attachments"]), 1)
        attachment = data["attachments"][0]
        self.assertEqual(attachment["mime_type"], "image/png")
        self.assertEqual(self.resolved_path(attachment["path"]), target)
        self.assertEqual(attachment["label"], "检查场景")
        self.assertEqual(target.read_bytes(), original)
        self.assertNotIn(base64.b64encode(original).decode(), json.dumps(data),
                         "The script declares a native attachment instead of flooding shell output")

    def test_image_can_return_multiple_real_sources(self):
        first, second = self.workspace / "first.png", self.workspace / "second.bin"
        first.write_bytes(png(2, 1))
        second.write_bytes(png(1, 2))
        data = self.envelope(self.feedback("image", first, second), "image")
        self.assertEqual({self.resolved_path(item["path"]) for item in data["attachments"]},
                         {first, second})
        self.assertEqual({item["mime_type"] for item in data["attachments"]}, {"image/png"})

    def test_image_identifies_gif_pixels_without_requiring_an_image_library(self):
        target = self.workspace / "one pixel.dat"
        original = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        target.write_bytes(original)
        data = self.envelope(self.feedback("image", target), "image")
        self.assertEqual(data["attachments"][0]["mime_type"], "image/gif")
        self.assertEqual(target.read_bytes(), original)

    def test_image_rejects_missing_or_misleading_nonimage_sources(self):
        target = self.workspace / "pretend.png"
        target.write_text("This is text, not pixels.")
        for source in (target, self.workspace / "absent.png", self.workspace):
            with self.subTest(source=source):
                self.failure("image", source)
                self.assertEqual(target.read_text(), "This is text, not pixels.")

    @unittest.skipUnless(os.name == "posix", "Executable browser fixture needs POSIX permissions")
    def test_render_runs_browser_and_returns_generated_pixels(self):
        html = self.workspace / "scene with spaces.html"
        html.write_text('<!doctype html><svg><circle r="10"/></svg>')
        screenshot = self.workspace / "rendered.png"
        capture = self.workspace / "browser args.json"
        browser = self.workspace / "test browser"
        rendered = png(4, 3)
        browser.write_text(
            f"#!{sys.executable}\n"
            "import base64, json, sys\nfrom pathlib import Path\n"
            f"Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            "target = next(value.split('=', 1)[1] for value in sys.argv if value.startswith('--screenshot='))\n"
            f"Path(target).write_bytes(base64.b64decode({base64.b64encode(rendered).decode()!r}))\n"
        )
        browser.chmod(0o755)
        data = self.envelope(self.feedback("render", html, "--output", screenshot,
                                          "--width", 400, "--height", 300, "--wait-ms", 25,
                                          "--browser", browser), "render")
        self.assertEqual(screenshot.read_bytes(), rendered)
        self.assertEqual(len(data["attachments"]), 1)
        self.assertEqual(self.resolved_path(data["attachments"][0]["path"]), screenshot)
        self.assertEqual(data["attachments"][0]["mime_type"], "image/png")
        arguments = json.loads(capture.read_text())
        self.assertTrue(any(argument.startswith("--headless") for argument in arguments))
        self.assertIn("--window-size=400,300", arguments)
        self.assertIn(html.as_uri(), arguments)
        self.assertEqual(html.read_text(), '<!doctype html><svg><circle r="10"/></svg>')

    @unittest.skipUnless(os.name == "posix", "Executable browser fixture needs POSIX permissions")
    def test_render_does_not_return_stale_pixels_when_browser_fails_or_omits_output(self):
        html = self.workspace / "scene.html"
        html.write_text("<p>new scene</p>")
        browser = self.workspace / "failed-browser"
        for browser_code in ("import sys; sys.exit(3)", "pass"):
            with self.subTest(browser_code=browser_code):
                output = self.workspace / "existing.png"
                output.write_bytes(png(1, 1))
                browser.write_text(f"#!{sys.executable}\n{browser_code}\n")
                browser.chmod(0o755)
                result = self.failure("render", html, "--output", output, "--browser", browser)
                if result.stdout.strip():
                    data = json.loads(result.stdout)
                    self.assertFalse(data.get("attachments"),
                                     "A failed render must not expose an old screenshot as current feedback")

    def test_feedback_skill_is_discoverable_through_existing_catalog(self):
        catalog = SkillRegistry([BUILTIN_ROOT]).catalog()
        self.assertIn("feedback:", catalog)
        self.assertIn(str(SKILL_ROOT / "SKILL.md"), catalog)
        tools = ToolRegistry(self.workspace)
        self.assertEqual([schema["name"] for schema in tools.schemas], ["shell"])
        target = self.workspace / "artifact 'quoted'.txt"
        target.write_text("literal $(printf bad) `pwd` 中文")
        command = shlex.join([sys.executable, str(SCRIPT), "text", str(target)])
        result = json.loads(tools.execute("shell", json.dumps({
            "name": "Inspect the saved artifact using feedback", "command": command,
            "result_mode": "direct",
        })))
        self.assertEqual(result["exit_code"], 0, result["stderr"])
        data = json.loads(result["stdout"])
        self.assertEqual(data["kind"], "text")
        self.assertIn("literal $(printf bad) `pwd` 中文", json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
