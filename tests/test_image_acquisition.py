import json
from pathlib import Path
import tempfile
import unittest

from skills_agent.feedback import feedback_content, replace_result_text
from skills_agent.images import MAX_IMAGE_BYTES, image_reference
from skills_agent.session import Session
from tests.test_feedback_providers import png_bytes


class ImageAcquisitionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.session = Session(self.root / "sessions", "images")
        self.path = self.root / "image with spaces.png"
        self.path.write_bytes(png_bytes((20, 40, 60)))

    def feedback(self, paths, **metadata):
        payload = {"skills_agent_feedback": 1, "attachments": [
            {"type": "image", "path": str(path), **metadata} for path in paths]}
        execution = {"exit_code": 0, "stdout": json.dumps(payload), "stderr": ""}
        return feedback_content(json.dumps(execution), self.session)

    def test_feedback_has_immutable_url_and_preserves_remote_source(self):
        result = self.feedback([self.path], source_url="https://example.org/image.png")
        image = next(block for block in result if block["type"] == "image")
        saved = Path(image["path"])
        self.assertEqual(image["url"], saved.as_uri())
        self.assertEqual(image["source_url"], "https://example.org/image.png")
        self.assertIn(saved.as_uri(), next(block["text"] for block in result
                                          if "Image URL:" in block.get("text", "")))
        self.path.write_bytes(b"changed")
        self.assertEqual(saved.read_bytes(), png_bytes((20, 40, 60)))

    def test_feedback_accepts_boundary_and_returns_actionable_error_above_it(self):
        header = self.path.read_bytes()
        self.path.write_bytes(header + bytes(MAX_IMAGE_BYTES - len(header)))
        accepted = self.feedback([self.path])
        self.assertEqual(len([block for block in accepted if block["type"] == "image"]), 1)
        before = set((self.session.path / "attachments").iterdir())
        self.path.write_bytes(self.path.read_bytes() + b"x")
        rejected = json.loads(self.feedback([self.path]))
        self.assertIn(str(MAX_IMAGE_BYTES), rejected["feedback_errors"][0])
        self.assertEqual(set((self.session.path / "attachments").iterdir()), before)

    def test_six_feedback_images_do_not_create_native_attachments(self):
        rejected = json.loads(self.feedback([self.path] * 6))
        self.assertIn("at most 5", rejected["feedback_errors"][0])
        self.assertFalse((self.session.path / "attachments").exists())
        accepted = self.feedback([self.path] * 5)
        self.assertEqual(len([block for block in accepted if block["type"] == "image"]), 5)

    def test_user_images_are_validated_before_snapshots_and_keep_urls(self):
        with self.assertRaisesRegex(ValueError, "at most 5"):
            self.session.make_user_message("inspect", [self.path] * 6)
        self.assertFalse((self.session.path / "attachments").exists())
        self.path.write_bytes(bytes(MAX_IMAGE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, str(MAX_IMAGE_BYTES)):
            self.session.make_user_message("inspect", [self.path])
        self.assertFalse((self.session.path / "attachments").exists())
        self.path.write_bytes(png_bytes((20, 40, 60)))
        image = self.session.make_user_message("inspect", [self.path])["content"][1]
        self.assertEqual(image["url"], Path(image["path"]).as_uri())
        self.assertEqual(image["source_url"], self.path.as_uri())

    def test_report_replacement_retains_history_reference(self):
        reference = image_reference({"type": "image", "path": str(self.path)})
        message = {"role": "tool", "content": [{"type": "text", "text": "large output"}, reference]}
        replace_result_text(message, "concise report")
        self.assertEqual(message["content"], [{"type": "text", "text": "concise report"}, reference])


if __name__ == "__main__":
    unittest.main()
