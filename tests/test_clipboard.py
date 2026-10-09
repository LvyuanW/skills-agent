from __future__ import annotations

import base64
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zlib

from skills_agent.clipboard import _SCRIPT, read_clipboard


def png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )


class ClipboardTests(unittest.TestCase):
    def test_text_and_file_paths_preserve_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory() as directory, patch("skills_agent.clipboard.sys.platform", "darwin"):
            response = {"text": "你好\nworld", "files": ["/tmp/资料 #1.pdf", "/tmp/image two.png"]}
            with patch("skills_agent.clipboard.subprocess.run") as run:
                run.return_value.stdout = json.dumps(response)
                text, paths = read_clipboard(Path(directory))
            self.assertEqual(text, response["text"])
            self.assertEqual(paths, [Path(path) for path in response["files"]])
            self.assertEqual(run.call_args.args[0][:4], ["osascript", "-l", "JavaScript", "-e"])
            self.assertEqual(Path(run.call_args.args[0][-1]).parent, Path(directory).resolve())

    def test_os_error_is_reported(self):
        with tempfile.TemporaryDirectory() as directory, patch("skills_agent.clipboard.sys.platform", "darwin"):
            with patch("skills_agent.clipboard.subprocess.run", side_effect=subprocess.CalledProcessError(
                1, "osascript", stderr="Cannot save clipboard image\n"
            )):
                with self.assertRaisesRegex(OSError, "Cannot save clipboard image"):
                    read_clipboard(Path(directory))

    def test_malformed_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch("skills_agent.clipboard.sys.platform", "darwin"):
            for value in ["not json", "[]", '{"text": "ok", "files": [3]}']:
                with self.subTest(value=value), patch("skills_agent.clipboard.subprocess.run") as run:
                    run.return_value.stdout = value
                    with self.assertRaisesRegex(ValueError, "Invalid clipboard response"):
                        read_clipboard(Path(directory))

    def test_unsupported_platform_does_not_invoke_osascript(self):
        with patch("skills_agent.clipboard.sys.platform", "linux"), patch("skills_agent.clipboard.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "macOS"):
                read_clipboard(Path("unused"))
            run.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "macOS clipboard integration")
class NativeClipboardTests(unittest.TestCase):
    def read_named_clipboard(self, setup: str, image_path: Path) -> dict:
        # Never access the general (user) clipboard, even during setup/cleanup.
        script = _SCRIPT.split("function run(argv)")[0] + """
function run(argv) {
    var pasteboard = $.NSPasteboard.pasteboardWithName(%s);
    pasteboard.clearContents;
    try {
        %s
        return JSON.stringify(readClipboard(pasteboard, argv[0]));
    } finally {
        pasteboard.releaseGlobally;
    }
}
""" % (json.dumps("skills-agent-tests-" + uuid4().hex), setup)
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script, str(image_path)],
            check=True, capture_output=True, text=True,
        )
        return json.loads(result.stdout)

    def test_native_text_and_empty_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "image.png"
            self.assertEqual(self.read_named_clipboard("", target), {"text": "", "files": []})
            text = "你好 world\n第二行"
            result = self.read_named_clipboard(
                f"pasteboard.setStringForType({json.dumps(text)}, $.NSPasteboardTypeString);", target
            )
            self.assertEqual(result, {"text": text, "files": []})
            self.assertFalse(target.exists())

    def test_native_image_is_saved_as_png(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "截图.png"
            data = base64.b64encode(png_bytes()).decode()
            result = self.read_named_clipboard(f"""
                var data = $.NSData.alloc.initWithBase64EncodedStringOptions({json.dumps(data)}, 0);
                var image = $.NSImage.alloc.initWithData(data);
                pasteboard.setDataForType(image.TIFFRepresentation, $.NSPasteboardTypeTIFF);
            """, target)
            self.assertEqual(result, {"text": "", "files": [str(target)]})
            output = target.read_bytes()
            self.assertTrue(output.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack(">II", output[16:24]), (1, 1))

    def test_native_multiple_file_urls_take_precedence_over_image_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("报告 #1.pdf", "image two.png")]
            for path in paths:
                path.write_bytes(b"example")
            target = Path(directory) / "unused.png"
            data = base64.b64encode(png_bytes()).decode()
            result = self.read_named_clipboard(f"""
                var urls = {json.dumps([path.as_uri() for path in paths])};
                var items = urls.map(function(url) {{
                    var item = $.NSPasteboardItem.alloc.init;
                    item.setStringForType(url, $.NSPasteboardTypeFileURL);
                    return item;
                }});
                var data = $.NSData.alloc.initWithBase64EncodedStringOptions({json.dumps(data)}, 0);
                items[0].setDataForType(data, $.NSPasteboardTypePNG);
                pasteboard.writeObjects($(items));
            """, target)
            self.assertEqual(result, {"text": "", "files": [str(path) for path in paths]})
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
