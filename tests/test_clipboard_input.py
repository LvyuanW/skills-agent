import base64
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from skills_agent.cli import ConsoleRenderer
from tests import test_cli_terminal as terminal_helpers
from tests.test_cli_terminal import Terminal, terminal_text


# The child process reads fixtures, never the user's system clipboard.
BOOTSTRAP = """
import json
from pathlib import Path
import shutil
import sys
from skills_agent import cli
from skills_agent import input as agent_input

fixtures = iter(json.loads(Path(sys.argv[1]).read_text()))
def read_clipboard(directory):
    fixture = next(fixtures)
    files = [Path(path) for path in fixture.get('files', [])]
    if fixture.get('image'):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        image = directory / 'clipboard.png'
        shutil.copyfile(fixture['image'], image)
        files.append(image)
        if fixture.get('record'):
            Path(fixture['record']).write_text(str(image))
    return fixture.get('text', ''), files

agent_input.read_clipboard = read_clipboard
raise SystemExit(cli.main(sys.argv[2:]))
"""

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+cWZsAAAAASUVORK5CYII="
)


@unittest.skipUnless(os.name == "posix", "PTY interaction requires POSIX")
class ClipboardInputTests(unittest.TestCase):
    setUp = terminal_helpers.TerminalTests.setUp
    server = terminal_helpers.TerminalTests.server
    response = terminal_helpers.TerminalTests.response
    events = terminal_helpers.TerminalTests.events

    def terminal(self, fixtures):
        fixture_path = self.workspace / "clipboard-fixtures.json"
        fixture_path.write_text(json.dumps(fixtures))
        endpoint, requests = self.server(lambda payload, count: self.response(f"REPLIED_{count}"))
        start_process = subprocess.Popen

        def start(command, **kwargs):
            return start_process(
                [command[0], "-c", BOOTSTRAP, str(fixture_path), *command[3:]], **kwargs)

        with patch("tests.test_cli_terminal.subprocess.Popen", side_effect=start):
            terminal = Terminal(self.workspace, endpoint)
        self.addCleanup(terminal.close)
        terminal.prompt()
        return terminal, requests

    def users(self):
        return [event["message"] for event in self.events()
                if event.get("type") == "message" and event["message"]["role"] == "user"]

    def test_clipboard_text_inserts_at_cursor_and_terminal_text_paste_still_works(self):
        terminal, requests = self.terminal([{"text": "剪贴板文字"}])
        terminal.send("前后\x1b[D\x16\n")
        terminal.prompt("REPLIED_1")
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": "前剪贴板文字后"}])
        terminal.send("\x1b[200~普通终端粘贴\n第二行\x1b[201~\n")
        terminal.prompt("REPLIED_2")
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": "普通终端粘贴\n第二行"}])
        self.assertEqual(len(self.users()), 2)

    def test_images_and_files_use_inline_markers_and_deleted_marker_is_not_sent(self):
        image = self.workspace / "source.png"
        image.write_bytes(PNG)
        pdf = self.workspace / "报告.pdf"
        pdf.write_bytes(b"%PDF-1.7\nclipboard document")
        deleted = self.workspace / "删除.txt"
        deleted.write_text("DELETED_ATTACHMENT_BODY")
        terminal, requests = self.terminal([
            {"image": str(image)}, {"files": [str(pdf), str(deleted)]},
        ])
        terminal.send("检查 \x16 参考 \x16")
        terminal.wait_for(lambda output: "删除.txt" in output)
        # Delete the final marker while keeping the two preceding attachments.
        terminal.send("\x7f" * len("[文件3:删除.txt]") + "\n")
        live = terminal.prompt("REPLIED_1")
        user = self.users()[0]
        text = user["content"][0]["text"]
        self.assertIn("[图片1]", text)
        self.assertIn("[文件2:报告.pdf]", text)
        self.assertNotIn("删除.txt", text)
        attachments = [block for block in user["content"] if block.get("path")]
        self.assertEqual([block["type"] for block in attachments], ["image", "file"])
        self.assertEqual([Path(block["path"]).read_bytes() for block in attachments],
                         [PNG, pdf.read_bytes()])
        for block in attachments:
            self.assertEqual(Path(block["path"]).parent,
                             (self.workspace / ".agent/sessions/terminal/attachments").resolve())
        image.write_bytes(b"changed original image")
        pdf.write_bytes(b"changed original PDF")
        self.assertEqual(Path(attachments[0]["path"]).read_bytes(), PNG)
        self.assertTrue(Path(attachments[1]["path"]).read_bytes().startswith(b"%PDF-1.7"))
        native = requests[-1]["input"][-1]["content"]
        self.assertEqual([block["type"] for block in native], ["input_text", "input_image", "input_file"])
        self.assertEqual(native[1]["image_url"],
                         "data:image/png;base64," + base64.b64encode(PNG).decode())
        self.assertEqual(native[2]["filename"], "报告.pdf")
        visible = terminal_text(live)
        self.assertEqual(visible.count("[图片1]"), 1)
        self.assertEqual(visible.count("报告.pdf"), 1)
        self.assertNotIn("删除.txt", visible)
        self.assertNotIn("[附件]", visible)
        replay = io.StringIO()
        renderer = ConsoleRenderer(replay, live=False)
        for event in self.events():
            renderer(event)
        renderer.close()
        self.assertIn(text, replay.getvalue())
        self.assertEqual(replay.getvalue().count("[图片1]"), 1)
        self.assertEqual(replay.getvalue().count("报告.pdf"), 1)
        self.assertNotIn("[附件]", replay.getvalue())

    def test_attachment_only_message_does_not_leak_attachments_into_next_turn(self):
        image = self.workspace / "source.png"
        image.write_bytes(PNG)
        terminal, requests = self.terminal([{"image": str(image)}])
        terminal.send("\x16\n")
        terminal.prompt("REPLIED_1")
        self.assertEqual([block["type"] for block in requests[-1]["input"][-1]["content"]],
                         ["input_text", "input_image"])
        terminal.send("接着聊\n")
        terminal.prompt("REPLIED_2")
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": "接着聊"}])
        self.assertEqual(len(self.users()), 2)

    def test_cancelled_paste_draft_does_not_attach_to_next_message(self):
        image = self.workspace / "source.png"
        image.write_bytes(PNG)
        terminal, requests = self.terminal([{"image": str(image)}])
        terminal.send("\x16")
        terminal.wait_for(lambda output: "[图片1]" in output)
        terminal.send("\x03")
        terminal.prompt()
        terminal.send("重新开始\n")
        terminal.prompt("REPLIED_1")
        self.assertEqual(requests[-1]["input"][-1]["content"],
                         [{"type": "input_text", "text": "重新开始"}])
        self.assertEqual(len(self.users()), 1)

    def test_pasted_image_and_file_command_share_one_user_message(self):
        image = self.workspace / "source.png"
        image.write_bytes(PNG)
        attachment = self.workspace / "input.txt"
        attachment.write_text("COMMAND_ATTACHMENT_BODY")
        terminal, requests = self.terminal([{"image": str(image)}])
        terminal.send("/file input.txt\n")
        terminal.prompt()
        self.assertEqual(requests, [])
        terminal.send("一起看 \x16\n")
        live = terminal.prompt("REPLIED_1")
        content = self.users()[0]["content"]
        attachments = [block for block in content if block.get("path")]
        self.assertEqual([block["type"] for block in attachments], ["image", "text"])
        self.assertIn("COMMAND_ATTACHMENT_BODY", attachments[1]["text"])
        visible = terminal_text(live)
        self.assertEqual(visible.count("user:"), 1)
        self.assertEqual(visible.count("[图片1]"), 1)
        self.assertEqual(visible.count("[附件] input.txt"), 1)
        self.assertNotIn("COMMAND_ATTACHMENT_BODY", visible)

    def test_recalled_message_keeps_image_and_exit_removes_only_temporary_copy(self):
        image = self.workspace / "source.png"
        image.write_bytes(PNG)
        record = self.workspace / "clipboard-image-path"
        terminal, requests = self.terminal([{"image": str(image), "record": str(record)}])
        terminal.send("看图 \x16\n")
        terminal.prompt("REPLIED_1")
        terminal.send("\x1b[A\n")
        terminal.prompt("REPLIED_2")
        self.assertEqual(len(requests), 2)
        first, second = [request["input"][-1]["content"] for request in requests]
        self.assertEqual(first, second)
        self.assertEqual([block["type"] for block in second], ["input_text", "input_image"])
        saved = [Path(block["path"]) for user in self.users() for block in user["content"]
                 if block["type"] == "image"]
        self.assertEqual(len(saved), 2)
        temporary_image = Path(record.read_text())
        self.assertTrue(temporary_image.is_file())
        terminal.send("/exit\n")
        terminal.wait_exit()
        self.assertEqual(terminal.process.returncode, 0)
        self.assertFalse(temporary_image.parent.exists())
        self.assertEqual([path.read_bytes() for path in saved], [PNG, PNG])


if __name__ == "__main__":
    unittest.main()
