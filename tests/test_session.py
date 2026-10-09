import tempfile
import unittest
from pathlib import Path

from skills_agent.session import Session


class SessionTests(unittest.TestCase):
    def test_append_and_reopen_preserve_messages_and_event_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session(root, "test-1")
            first = {"role": "assistant", "content": "完整\n记录", "_raw": {"unknown": [1, 2]}}
            session.append(first)
            prefix = (session.path / "events.jsonl").read_bytes()
            session.append_event({"type": "subagent_summary", "goal": "read a file", "status": "complete"})
            second = {"role": "user", "content": [{"type": "text", "text": "next"}]}
            session.append(second)
            self.assertTrue((session.path / "events.jsonl").read_bytes().startswith(prefix))
            reopened = Session(root, "test-1")
            self.assertEqual(reopened.messages(), [first, second])
            self.assertEqual([event["type"] for event in reopened.events()], ["message", "subagent_summary", "message"])

    def test_attachments_keep_full_files_and_multimodal_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "notes.txt": ("全文\n" * 10000).encode(),
                "picture.png": b"\x89PNG\r\n\x1a\n",
                "paper.pdf": b"%PDF-1.7\n",
                "data.bin": b"\0\xff\x01",
            }
            paths = []
            for name, data in files.items():
                path = root / name
                path.write_bytes(data)
                paths.append(path)
            session = Session(root / "sessions", "with-files")
            message = session.make_user_message("inspect these", paths)
            blocks = message["content"]
            self.assertEqual([block["type"] for block in blocks], ["text", "text", "image", "file", "text"])
            self.assertIn(files["notes.txt"].decode(), blocks[1]["text"])
            for block, path in zip(blocks[1:], paths):
                self.assertEqual(Path(block["path"]).read_bytes(), files[path.name])
                self.assertEqual(path.read_bytes(), files[path.name])
                self.assertNotEqual(Path(block["path"]), path)
            original_copy = Path(blocks[1]["path"])
            paths[0].write_text("modified source")
            self.assertEqual(original_copy.read_bytes(), files["notes.txt"])
            session.append(message)
            self.assertEqual(Session(root / "sessions", "with-files").messages(), [message])

    def test_duplicate_attachment_names_use_distinct_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "file.txt"
            source.write_text("one")
            session = Session(root / "sessions", "s")
            first = session.make_user_message("", [source])
            source.write_text("two")
            second = session.make_user_message("", [source])
            self.assertNotEqual(first["content"][0]["path"], second["content"][0]["path"])
            self.assertEqual(Path(first["content"][0]["path"]).read_text(), "one")

    def test_unsupported_native_image_is_a_script_readable_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "drawing.svg"
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            block = Session(root / "sessions", "s").make_user_message("", [source])["content"][0]
            self.assertEqual(block["type"], "text")
            self.assertIn(str(Path(block["path"])), block["text"])
            self.assertEqual(Path(block["path"]).read_text(), source.read_text())

    def test_session_id_cannot_escape_root(self):
        for session_id in ("", "..", "../escape", "/absolute", "a/b", "a\\b"):
            with self.subTest(session_id=session_id), self.assertRaises(ValueError):
                Session(Path("unused"), session_id)


if __name__ == "__main__":
    unittest.main()
