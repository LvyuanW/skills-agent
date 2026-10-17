"""Declared shell images survive arbitrary logs and reach actual model requests."""

import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.background import RUNTIME_EVENT
from skills_agent.context.history import _message_text
from skills_agent.feedback import feedback_content, validate_images
from skills_agent.images import MAX_IMAGE_BYTES
from skills_agent.providers import OpenAIProvider
from skills_agent.providers.base import ProviderConnectionError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry
from tests.test_agent_context import ScriptedModel, call, final
from tests.test_feedback_providers import png_bytes


def native_images(content):
    return [block for block in content if isinstance(block, dict) and block.get("type") == "image"] \
        if isinstance(content, list) else []


def execution(content):
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
    for block in blocks:
        if block.get("type") != "text":
            continue
        try:
            value = json.loads(block["text"])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and any(key in value for key in ("exit_code", "feedback_image_count", "job_id")):
            return value
    raise AssertionError("No structured execution result in image feedback")


class DeclaredImagesTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root / "sessions", "main")
        self.pixels = png_bytes((24, 85, 171))
        self.image = self.root / "existing image.png"
        self.image.write_bytes(self.pixels)
        self.declaration = {"path": str(self.image), "label": "Observed pixels",
                            "source_url": "https://example.test/source.png"}

    def agent(self, replies, **options):
        model = ScriptedModel(replies)
        agent = Agent(model, self.session, SkillRegistry([]), self.root, **options)
        self.addCleanup(agent.close)
        return agent, model

    def envelope(self, path=None):
        return {"skills_agent_feedback": 1, "attachments": [
            {"type": "image", "path": str(path or self.image), "label": "Legacy pixels"}]}

    def output(self, stdout, **values):
        return json.dumps({"exit_code": 0, "stdout": stdout, "stderr": "", **values})

    def command(self, source):
        return shlex.join([sys.executable, "-u", "-c", source])

    def payload_images(self, system, messages, tools):
        payload = OpenAIProvider("test-model", "unused", api_format="chat")._chat_payload(
            system, messages, tools)
        attached = [block for message in payload["messages"]
                    for block in (message.get("content") or [])
                    if isinstance(block, dict) and block.get("type") == "image_url"]
        return payload, attached

    def test_structured_arguments_are_optional_and_validate_without_opening_future_files(self):
        future = self.root / "not-generated-yet.png"
        declared = [{"path": future.as_uri(), "label": "Future image", "source_url": future.as_uri()}]
        before = deepcopy(declared)
        checked = validate_images(declared)
        self.assertIsInstance(checked, list)
        self.assertEqual(len(checked), 1)
        self.assertEqual(len(validate_images(declared * 10)), 10)
        self.assertFalse(future.exists())
        self.assertEqual(declared, before)
        schema = next(schema for schema in ToolRegistry(self.root).schemas if schema["name"] == "shell")
        self.assertIn("images", schema["parameters"]["properties"])
        self.assertNotIn("images", schema["parameters"]["required"])

    def test_bad_declarations_are_rejected_before_the_command_executes(self):
        marker = self.root / "must-not-execute"
        command = self.command(f"from pathlib import Path; Path({str(marker)!r}).touch()")
        invalid = [None, "image.png", {}, [str(self.image)], [{}],
                   [{"path": "relative.png"}], [{"path": True}],
                   [{"path": "https://example.test/image.png"}],
                   [{"path": "file://remote-host/image.png"}],
                   [{"path": "file:///tmp/image%00.png"}],
                   [{"path": self.image.as_uri() + "?query=1"}],
                   [{"path": str(self.image), "label": 42}],
                   [{"path": str(self.image), "source_url": "not-a-url"}],
                   [{"path": str(self.image), "unknown": "field"}],
                   [{"path": str(self.image)}] * 11]
        tools = ToolRegistry(self.root)
        for declared in invalid:
            with self.subTest(images=declared):
                result = json.loads(tools.execute("shell", json.dumps({
                    "name": "invalid images", "command": command,
                    "run_in_background": False, "images": declared,
                })))
                self.assertIn("error", result)
                self.assertFalse(marker.exists())

    def test_explicit_images_accept_arbitrary_stdout_and_do_not_duplicate_legacy_feedback(self):
        legacy = json.dumps(self.envelope())
        for stdout in ("diagnostic text\n", legacy, f"prefix\n{legacy}\ntrailer\n",
                       json.dumps([self.envelope(), self.envelope()]), legacy + "\n" + legacy):
            with self.subTest(stdout=stdout[:50]):
                result = feedback_content(self.output(stdout, exit_code=7, stderr="diagnostic\n"),
                                          self.session, images=[self.declaration])
                data = execution(result)
                self.assertEqual(data["feedback_image_count"], 1)
                self.assertEqual((data["stdout"], data["stderr"], data["exit_code"]),
                                 (stdout, "diagnostic\n", 7))
                self.assertNotIn("feedback_errors", data)
                self.assertEqual(len(native_images(result)), 1)
                image = native_images(result)[0]
                self.assertEqual(Path(image["path"]).read_bytes(), self.pixels)
                self.assertEqual(image["source_url"], self.declaration["source_url"])

    def test_empty_images_suppresses_stdout_attachment_and_omission_preserves_legacy(self):
        stdout = json.dumps(self.envelope())
        explicit = feedback_content(self.output(stdout), self.session, images=[])
        self.assertEqual(execution(explicit)["feedback_image_count"], 0)
        self.assertEqual(native_images(explicit), [])
        self.assertEqual(execution(explicit)["stdout"], stdout)
        legacy = feedback_content(self.output(stdout), self.session)
        self.assertEqual(len(native_images(legacy)), 1)
        ordinary = self.output("source code mentions skills_agent_feedback, but declares nothing\n")
        self.assertEqual(feedback_content(ordinary, self.session), ordinary)
        mixed = self.output("ordinary log\n" + stdout)
        self.assertEqual(native_images(feedback_content(mixed, self.session)), [])

    def test_execution_metadata_routes_declarations_without_rescanning_stdout(self):
        stdout = "a log before JSON\n" + json.dumps(self.envelope()) + "\na trailing log"
        content = feedback_content(self.output(stdout, images=[self.declaration]), self.session)
        self.assertEqual(execution(content)["feedback_image_count"], 1)
        self.assertEqual(len(native_images(content)), 1)
        suppressed = feedback_content(self.output(json.dumps(self.envelope()), images=[]), self.session)
        self.assertEqual(execution(suppressed)["feedback_image_count"], 0)
        self.assertEqual(native_images(suppressed), [])

    def test_ordinary_json_and_source_mentioning_protocol_are_not_feedback_errors(self):
        for stdout in ("null", "false", "42", "[]", '{"ordinary":"data"}',
                       json.dumps("skills_agent_feedback is a protocol name"),
                       'return {"skills_agent_feedback": 1, "attachments": []}\n',
                       'print("skills_agent_feedback")\n'):
            with self.subTest(stdout=stdout):
                original = self.output(stdout, stderr="unchanged\n")
                self.assertEqual(feedback_content(original, self.session), original)
        self.assertFalse((self.session.path / "attachments").exists())

    def test_malformed_legacy_feedback_is_diagnosed_without_extracting_or_loading_images(self):
        legacy = json.dumps(self.envelope())
        malformed = (json.dumps([self.envelope()]), legacy + "\n" + legacy,
                     "a diagnostic prefix\n" + legacy, legacy + "\na diagnostic suffix")
        with patch.object(self.session, "save_attachment") as save:
            for stdout in malformed:
                with self.subTest(stdout=stdout[:60]):
                    result = feedback_content(self.output(stdout, exit_code=4, stderr="diagnostic\n"),
                                              self.session)
                    data = execution(result)
                    self.assertEqual(data["feedback_image_count"], 0)
                    self.assertTrue(data["feedback_errors"])
                    self.assertEqual(native_images(result), [])
                    self.assertEqual((data["stdout"], data["stderr"], data["exit_code"]),
                                     (stdout, "diagnostic\n", 4))
            save.assert_not_called()

        truncated = self.output(legacy, stdout_truncated=True)
        self.assertEqual(native_images(feedback_content(truncated, self.session)), [])

    def test_deep_ordinary_json_does_not_make_feedback_detection_abort_the_agent(self):
        stdout = "[" * 600 + "0" + "]" * 600
        json.loads(stdout)  # Valid JSON, below the decoder's normal nesting limit.
        original = self.output(stdout)
        self.assertEqual(feedback_content(original, self.session), original)

    def test_partial_success_counts_only_actual_native_images_and_retains_errors(self):
        content = feedback_content(self.output("original log", exit_code=9), self.session,
                                   images=[self.declaration, {"path": str(self.root / "missing.png")}])
        self.assertEqual(execution(content)["feedback_image_count"], 1)
        self.assertTrue(execution(content)["feedback_errors"])
        self.assertEqual(execution(content)["exit_code"], 9)
        self.assertEqual(len(native_images(content)), 1)

    def test_missing_oversized_and_invalid_image_files_report_zero_and_preserve_execution(self):
        oversized = self.root / "oversized.png"
        oversized.write_bytes(self.pixels + b"x" * (MAX_IMAGE_BYTES + 1 - len(self.pixels)))
        invalid = self.root / "not-image.png"
        invalid.write_text("This file contains text, not pixels.")
        for image in (self.root / "missing.png", oversized, invalid):
            with self.subTest(path=image.name):
                result = feedback_content(self.output("saved something\n", exit_code=13,
                                                      stderr="original stderr\n"), self.session,
                                          images=[{"path": str(image)}])
                data = execution(result)
                self.assertEqual(data["feedback_image_count"], 0)
                self.assertTrue(data["feedback_errors"])
                self.assertEqual(native_images(result), [])
                self.assertEqual((data["exit_code"], data["stdout"], data["stderr"]),
                                 (13, "saved something\n", "original stderr\n"))

    def test_exact_size_limit_is_allowed_and_a_declared_image_ignores_truncated_logs(self):
        bounded = self.root / "one-megabyte.png"
        bounded.write_bytes(self.pixels + bytes(MAX_IMAGE_BYTES - len(self.pixels)))
        result = feedback_content(self.output("partial stdout", stdout_truncated=True), self.session,
                                  images=[{"path": str(bounded)}])
        self.assertEqual(execution(result)["feedback_image_count"], 1)
        self.assertTrue(execution(result)["stdout_truncated"])
        self.assertEqual(Path(native_images(result)[0]["path"]).stat().st_size, MAX_IMAGE_BYTES)

    def test_real_shell_can_generate_a_declared_image_then_send_immutable_native_pixels(self):
        target = self.root / "generated during shell.png"
        stdout = "before capture\n" + json.dumps([self.envelope(target)]) + "\nafter capture\n"
        stderr = "first diagnostic\nsecond diagnostic\n"
        command = self.command("import base64,sys\nfrom pathlib import Path\n"
                               f"Path({str(target)!r}).write_bytes(base64.b64decode({base64.b64encode(self.pixels).decode()!r}))\n"
                               f"sys.stdout.write({stdout!r}); sys.stderr.write({stderr!r})\n")

        def inspect(system, messages, tools, on_event):
            tool = next(message for message in reversed(messages) if message["role"] == "tool")
            data = execution(tool["content"])
            self.assertEqual((data["feedback_image_count"], data["stdout"], data["stderr"]),
                             (1, stdout, stderr))
            image = native_images(tool["content"])[0]
            self.assertNotEqual(Path(image["path"]), target)
            target.unlink()
            payload, images = self.payload_images(system, messages, tools)
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]["image_url"]["url"],
                             "data:image/png;base64," + base64.b64encode(self.pixels).decode())
            self.assertIn(Path(image["path"]).as_uri(), json.dumps(payload))
            return final("Observed the actual pixels")

        agent, model = self.agent([call("shell", {"name": "generate and attach", "command": command,
                                                  "images": [{"path": target.as_uri(), "label": "Generated image"}]}),
                                   inspect])
        self.assertFalse(target.exists())
        self.assertEqual(agent.run(self.session.make_user_message("Inspect the capture", [])),
                         "Observed the actual pixels")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(list((self.session.path / "attachments").iterdir())), 1)

    def test_real_agent_empty_images_disables_a_valid_stdout_envelope(self):
        command = self.command(f"print({json.dumps(self.envelope())!r})")

        def inspect(system, messages, tools, on_event):
            self.assertEqual(execution(messages[-1]["content"])["feedback_image_count"], 0)
            self.assertEqual(self.payload_images(system, messages, tools)[1], [])
            return final("No image was declared")

        agent, _ = self.agent([call("shell", {"name": "explicitly no images", "command": command,
                                             "images": []}), inspect])
        self.assertEqual(agent.run(self.session.make_user_message("Read ordinary output", [])),
                         "No image was declared")
        self.assertFalse((self.session.path / "attachments").exists())

    def test_report_success_and_failure_keep_image_count_errors_and_native_pixels(self):
        content = feedback_content(self.output("image diagnostics " * 9000), self.session,
                                   images=[self.declaration])
        failed_content = feedback_content(self.output("image diagnostics " * 9000), self.session,
                                          images=[{"path": str(self.root / "missing.png")}])
        for original in (content, failed_content):
            for report_failure in (False, True):
                with self.subTest(images=bool(native_images(original)), report_failure=report_failure):
                    agent, _ = self.agent([])
                    record = {"event_id": "event-image", "message": {"role": "tool", "name": "shell",
                              "tool_call_id": "call-1", "content": deepcopy(original)}}
                    options = {"side_effect": ProviderConnectionError("report unavailable")} if report_failure \
                        else {"return_value": "A concise report"}
                    with patch.object(agent.auxiliary, "tool_report", **options):
                        selected = agent._select(record, {"name": "shell", "arguments": json.dumps({
                            "name": "inspect", "command": "already ran", "result_mode": "report"})})
                    data = execution(selected["message"]["content"])
                    source = execution(original)
                    self.assertEqual(data["feedback_image_count"], source["feedback_image_count"])
                    self.assertEqual(data.get("feedback_errors"), source.get("feedback_errors"))
                    self.assertEqual(native_images(selected["message"]["content"]), native_images(original))

    def test_direct_budget_fallback_keeps_image_count_error_and_pixels(self):
        for image in (self.image, self.root / "missing.png"):
            with self.subTest(path=image.name):
                session = Session(self.root / "sessions", image.stem.replace(" ", "-"))
                command = self.command(f"print({'LARGE_IMAGE_LOG ' * 9000!r})")

                def inspect(system, messages, tools, on_event):
                    content = messages[-1]["content"]
                    data = execution(content)
                    self.assertIn("does not fit", data["notice"])
                    self.assertEqual(data["feedback_image_count"], int(image.exists()))
                    self.assertEqual(bool(data.get("feedback_errors")), not image.exists())
                    self.assertEqual(len(native_images(content)), int(image.exists()))
                    return final("Retained image metadata")

                model = ScriptedModel([call("shell", {"name": "large direct result", "command": command,
                                                       "result_mode": "direct", "images": [{"path": str(image)}]}),
                                       inspect])
                model.count_tokens = lambda system, messages, tools: (
                    2000 if any("LARGE_IMAGE_LOG" in _message_text(message)
                                for message in messages if message["role"] == "tool") else 100)
                agent = Agent(model, session, SkillRegistry([]), self.root, context_tokens=1000)
                self.addCleanup(agent.close)
                self.assertEqual(agent.run(session.make_user_message("Inspect", [])), "Retained image metadata")

    def test_total_request_limit_and_history_removal_apply_to_declared_images(self):
        paths = []
        for index in range(11):
            path = self.root / f"image-{index}.png"
            path.write_bytes(png_bytes((index, 60, 120)))
            paths.append(path)
        first = call("shell", {"name": "first views", "command": self.command("print('first')"),
                                "images": [{"path": str(path)} for path in paths[:5]]})
        first["tool_calls"] += call("shell", {"name": "second views", "command": self.command("print('second')"),
                                             "images": [{"path": str(path)} for path in paths[5:]]}, 2)["tool_calls"]

        def inspect(system, messages, tools, on_event):
            payload, images = self.payload_images(system, messages, tools)
            self.assertEqual(len(images), 10)
            self.assertEqual([block["image_url"]["url"] for block in images], [
                "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
                for path in paths[-10:]])
            self.assertEqual([execution(message["content"])["feedback_image_count"]
                              for message in messages if message["role"] == "tool"], [5, 6])
            self.assertIn("request_limit", json.dumps(messages))
            self.assertTrue(all(str(path) in json.dumps(messages) for path in paths))
            return final("Reviewed current views")

        def resumed(system, messages, tools, on_event):
            self.assertEqual(self.payload_images(system, messages, tools)[1], [])
            self.assertIn("Image URL:", json.dumps(messages))
            return final("History retains references")

        agent, _ = self.agent([first, inspect, resumed])
        self.assertEqual(agent.run(self.session.make_user_message("Review views", [])), "Reviewed current views")
        self.assertEqual(agent.run(self.session.make_user_message("Continue", [])), "History retains references")
        raw_images = [image for message in self.session.messages() for image in native_images(message.get("content"))]
        self.assertEqual(len(raw_images), 11)
        self.assertEqual(len(list((self.session.path / "attachments").iterdir())), 11)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "WNOWAIT"), "POSIX registered jobs")
    def test_background_receipt_does_not_read_future_images_but_completion_attaches_them(self):
        target = self.root / "background-generated.png"
        gate = self.root / "release"
        command = self.command("import base64,time\nfrom pathlib import Path\n"
                               f"while not Path({str(gate)!r}).exists(): time.sleep(.01)\n"
                               f"Path({str(target)!r}).write_bytes(base64.b64decode({base64.b64encode(self.pixels).decode()!r}))\n"
                               "print('verbose log ' * 3000)\n")

        def receipt(system, messages, tools, on_event):
            self.assertFalse(target.exists())
            tool = next(message for message in reversed(messages) if message["role"] == "tool")
            self.assertEqual(execution(tool["content"])["status"], "started")
            self.assertEqual(native_images(tool["content"]), [])
            self.assertEqual(self.payload_images(system, messages, tools)[1], [])
            gate.touch()
            return final("Job still running; awaiting its output")

        def completed(system, messages, tools, on_event):
            observations = [message for message in messages if message.get("runtime_event") == RUNTIME_EVENT]
            self.assertEqual(len(observations), 1)
            result = execution(observations[0]["content"])
            self.assertEqual(result["feedback_image_count"], 1)
            self.assertTrue(result["stdout_truncated"])
            self.assertEqual(len(native_images(observations[0]["content"])), 1)
            self.assertEqual(len(self.payload_images(system, messages, tools)[1]), 1)
            return final("Background pixels observed")

        agent, _ = self.agent([call("shell", {"name": "background image generation", "command": command,
                                             "run_in_background": True, "images": [{"path": str(target)}]}),
                              receipt, completed])
        self.assertEqual(agent.run(self.session.make_user_message("Generate and inspect", [])),
                         "Background pixels observed")
        self.assertEqual(len(list((self.session.path / "attachments").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
