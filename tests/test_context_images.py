"""Images belong to one model request, while their evidence URLs remain durable."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.context import WorkingContext, record_groups
from skills_agent.context.auxiliary import Auxiliary, _messages
from skills_agent.providers.base import ProviderConnectionError
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final
from tests.test_context import Counter, Summarizer, pair
from tests.test_context_auxiliary import Model
from tests.test_feedback_providers import png_bytes


def images(messages):
    return [block for message in messages
            for block in (message.get("content") or []) if isinstance(block, dict) and block.get("type") == "image"]


def references(messages):
    return [block["image_reference"] for message in messages
            for block in (message.get("content") or []) if isinstance(block, dict) and "image_reference" in block]


class ImageTestCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session = Session(self.root, "main")
        self.model = Counter()
        self.summary = Summarizer()

    def image(self, name):
        path = self.root / name
        path.write_bytes(png_bytes((80, 120, 160)))
        return {"type": "image", "path": str(path), "name": name, "mime_type": "image/png",
                "source_url": f"https://example.invalid/evidence/{name}"}

    def context(self):
        return WorkingContext(self.session, self.model, self.summary)

    def append(self, *messages):
        return [self.session.append(message) for message in messages]


class ContextImageTests(ImageTestCase):
    def test_images_expire_after_each_assistant_even_within_one_user_turn(self):
        user_image, tool_image = self.image("user.png"), self.image("tool.png")
        self.append({"role": "user", "content": [{"type": "text", "text": "Compare the evidence"}, user_image]})
        context = self.context()
        context.sync()
        self.assertEqual(images(context.prepare("system", [])[1]), [user_image])

        first_call, first_result = pair(1)
        first_result["content"] = [{"type": "text", "text": "tool evidence"}, tool_image]
        self.append(first_call)
        context.sync()
        self.assertFalse(images([context.latest_user()]))
        selected = []

        def select(record, tool_call):
            # Tool reporting happens during sync, before this result is appended.
            self.assertFalse(images([context.latest_user()]))
            selected.append(tool_call["id"])
            self.assertEqual(images([record["message"]]), [tool_image])
            return record

        self.append(first_result)
        context.sync(select)
        self.assertEqual(selected, ["call-1"])
        self.assertEqual(images(context.prepare("system", [])[1]), [tool_image])
        self.append(*pair(2))
        context.sync()
        sent = context.prepare("system", [])[1]
        self.assertFalse(images(sent))
        self.assertEqual([message["tool_call_id"] for message in sent if message["role"] == "tool"],
                         ["call-1", "call-2"])
        record_groups(context.records)
        refs = references(sent)
        self.assertEqual({ref["url"] for ref in refs}, {Path(block["path"]).as_uri() for block in (user_image, tool_image)})
        self.assertTrue(all(ref["event_id"].startswith("event-") for ref in refs))
        self.assertEqual(images(self.session.messages()), [user_image, tool_image])
        self.assertFalse(self.summary.calls, "Image expiry must not depend on token compaction")

    def test_repeated_prepare_does_not_consume_images_without_an_assistant_response(self):
        image = self.image("pending.png")
        self.append({"role": "user", "content": [image]})
        context = self.context()
        context.sync()
        first = context.prepare("system", [])[1]
        second = context.prepare("system", [])[1]
        self.assertEqual(images(first), [image])
        self.assertEqual(first, second)
        restored = self.context()
        restored.sync()
        self.assertEqual(restored.prepare("system", [])[1], first)
        self.assertEqual(len(references(first)), 1)

    def test_new_user_image_is_active_after_earlier_images_have_expired(self):
        old, new = self.image("old.png"), self.image("new.png")
        self.append({"role": "user", "content": [old]}, final("observed"),
                    {"role": "user", "content": [{"type": "text", "text": "Inspect another image"}, new]})
        context = self.context()
        context.sync()
        self.assertEqual(images(context.prepare("system", [])[1]), [new])
        self.assertEqual(images([context.latest_user()]), [new])

    def test_parallel_tool_images_keep_newest_five_without_breaking_pairs(self):
        supplied = [self.image(f"parallel-{number}.png") for number in range(7)]
        calls = [{"id": f"call-{number}", "name": "shell", "arguments": "{}"} for number in range(7)]
        self.append({"role": "user", "content": "Inspect outputs"},
                    {"role": "assistant", "content": "", "tool_calls": calls})
        for number, image in enumerate(supplied):
            self.append({"role": "tool", "name": "shell", "tool_call_id": f"call-{number}", "content": [image]})
        context = self.context()
        context.sync()
        sent = context.prepare("system", [])[1]
        self.assertEqual(images(sent), supplied[-5:])
        self.assertEqual(len(references(sent)), 7)
        self.assertEqual([ref["name"] for ref in references(sent) if ref.get("omitted_reason") == "request_limit"],
                         ["parallel-0.png", "parallel-1.png"])
        self.assertEqual([len(group) for group in record_groups(context.records)], [1, 8])
        self.assertEqual(images(self.session.messages()), supplied)
        self.assertEqual(images(self.context().prepare("system", [])[1]), supplied[-5:])

    def test_loading_legacy_context_expires_history_and_preserves_raw_evidence(self):
        old = self.image("legacy.png")
        self.append({"role": "user", "content": [old]}, final("Already examined"))
        raw = (self.session.path / "events.jsonl").read_bytes()
        records = [{"event_id": event["event_id"], "message": event["message"]}
                   for event in self.session.events()]
        state = {"version": 1, "cursor": len(raw), "summary": "", "covered_event_ids": [],
                 "records": records, "token_count": 100}
        (self.session.path / "context.json").write_text(json.dumps(state))
        context = self.context()
        self.assertFalse(images([record["message"] for record in context.records]))
        self.assertFalse(images([context.latest_user()]))
        self.assertIsNone(context.state["token_count"])
        snapshot = context.snapshot("system", [])
        self.assertFalse(images([record["message"] for record in snapshot["records"]]))
        self.assertIn(Path(old["path"]).as_uri(), json.dumps(snapshot))
        context.sync()
        self.assertEqual(self.context().state, context.state)
        self.assertEqual((self.session.path / "events.jsonl").read_bytes(), raw)
        self.assertEqual(Path(old["path"]).read_bytes(), png_bytes((80, 120, 160)))

    def test_restart_keeps_an_unconsumed_tool_image(self):
        image = self.image("unfinished-request.png")
        assistant, result = pair(1)
        result["content"] = [image]
        self.append({"role": "user", "content": "Inspect"}, assistant, result)
        context = self.context()
        context.sync()
        context.prepare("system", [])
        restored = self.context()
        restored.sync()
        self.assertEqual(images(restored.prepare("system", [])[1]), [image])

    def test_history_subagent_inherits_addresses_without_reviving_consumed_images(self):
        image = self.image("history.png")
        self.append({"role": "user", "content": [image]}, final("Image was examined"))

        def inspect(system, messages, tools, on_event):
            self.assertFalse(images(messages))
            self.assertIn(Path(image["path"]).as_uri(), json.dumps(messages))
            return final("Found the image reference")

        agent = Agent(ScriptedModel([inspect]), self.session, SkillRegistry([]), self.root)
        self.addCleanup(agent.close)
        agent.context.sync()
        self.assertEqual(agent._subagent("Find earlier image", "Retrieve evidence", history=True),
                         "Found the image reference")
        background = next((self.session.path / "subagents").glob("*/background.json"))
        self.assertFalse(images([record["message"] for record in json.loads(background.read_text())["records"]]))
        self.assertEqual(images(self.session.messages()), [image])


class AuxiliaryImageTests(ImageTestCase):
    def test_report_uses_new_attachments_and_references_consumed_background_images(self):
        old_user, old_tool, fresh = (self.image(name) for name in ("task.png", "background.png", "fresh.png"))
        assistant, result = pair(1)
        result["content"] = [old_tool]
        background = {"current_user_task": {"role": "user", "content": [old_user]},
                      "records": [{"event_id": f"event-{number}", "message": message}
                                  for number, message in enumerate([
                                      {"role": "user", "content": [old_user]}, assistant, result, final("reviewed")])],
                      "messages": [{"role": "user", "content": [old_user]}, final("reviewed")]}
        original = deepcopy(background)
        model = Model(["The current visual evidence was inspected."])
        auxiliary = Auxiliary(model, self.session)
        auxiliary.tool_report("new result", {}, {"event_id": "event-new"}, background, attachments=[fresh])
        sent = model.calls[0]["messages"]
        self.assertEqual(images(sent), [fresh])
        self.assertIn(sent, model.counted)
        for old in (old_user, old_tool):
            self.assertIn(Path(old["path"]).as_uri(), json.dumps(sent))
        self.assertEqual(background, original)

    def test_compression_projects_full_history_before_splitting_batches(self):
        old_user, old_tool = self.image("summary-user.png"), self.image("summary-tool.png")
        assistant, result = pair(1)
        result["content"] = [old_tool]
        records = [{"event_id": f"event-{number}", "message": message}
                   for number, message in enumerate([
                       {"role": "user", "content": [old_user]}, assistant, result, final("consumed")])]
        original = deepcopy(records)
        model = Model(["first summary", "tool summary", "final summary"])
        auxiliary = Auxiliary(model, self.session)

        def batches(projected, *args, **kwargs):
            yield projected[:1]
            yield projected[1:3]
            yield projected[3:]

        with patch.object(auxiliary, "_batches", side_effect=batches):
            self.assertEqual(auxiliary.compress("", records, {"role": "user", "content": [old_user]}),
                             "final summary")
        self.assertEqual(len(model.calls), 3)
        for request in model.calls:
            self.assertFalse(images(request["messages"]))
            self.assertIn(request["messages"], model.counted)
        self.assertIn(Path(old_tool["path"]).as_uri(), json.dumps(model.calls[1]["messages"]))
        self.assertEqual(records, original)

    def test_auxiliary_request_limit_retains_urls_for_omitted_images(self):
        supplied = [self.image(f"auxiliary-{number}.png") for number in range(7)]
        model = Model(["report"])
        Auxiliary(model, self.session).tool_report("output", {}, {}, {}, attachments=supplied)
        sent = model.calls[0]["messages"]
        self.assertEqual(images(sent), supplied[-5:])
        self.assertEqual(len(references(sent)), 7)
        self.assertIn(sent, model.counted)

    def test_summary_drafts_do_not_reattach_images_from_other_batches(self):
        image = self.image("summary-current.png")
        assistant, result = pair(1)
        result["content"] = [image]
        records = [{"event_id": f"event-{number}", "message": message}
                   for number, message in enumerate([
                       {"role": "user", "content": "Inspect the returned image"}, assistant, result])]
        model = Model(["Earlier context", "Image observation"])
        auxiliary = Auxiliary(model, self.session)

        def batches(projected, *args, **kwargs):
            yield projected[:1]
            yield projected[1:]

        with patch.object(auxiliary, "_batches", side_effect=batches):
            auxiliary.compress("", records, None)
        self.assertEqual([len(images(call["messages"])) for call in model.calls], [0, 1])
        self.assertIn("Earlier context", json.dumps(model.calls[1]["messages"]))

    def test_retry_reuses_unconsumed_images_until_a_response_succeeds(self):
        image = self.image("retry.png")
        model = Model([ProviderConnectionError("interrupted stream"), "Observed image"])
        with patch("skills_agent.providers.retry.time.sleep") as sleep:
            Auxiliary(model, self.session).tool_report("output", {}, {}, {}, attachments=[image])
        sleep.assert_called_once_with(5)
        self.assertEqual([len(images(call["messages"])) for call in model.calls], [1, 1])
        self.assertEqual(model.calls[0]["messages"], model.calls[1]["messages"])

    def test_combining_partial_reports_does_not_reattach_source_images(self):
        image = self.image("merge.png")
        messages = _messages({"input_kind": "partial_reports", "output": "observed findings",
                              "tool_attachments": [image]})
        self.assertFalse(images(messages))
        self.assertIn(Path(image["path"]).as_uri(), json.dumps(messages))

    def test_chunked_report_attaches_images_only_until_first_successful_report(self):
        image = self.image("chunked.png")
        model = Model(["first observation", "second observation", "combined report"])
        auxiliary = Auxiliary(model, self.session)
        fits = auxiliary._fits

        def force_chunks(system, payload, **kwargs):
            counted = fits(system, payload, **kwargs)
            return counted and payload["output"] != "large output"

        def chunks(content, payload, **kwargs):
            for number, text in enumerate(("first part", "second part"), 1):
                # Preflight the same candidate that the following call sends.
                self.assertTrue(auxiliary._fits("system", payload(text, number, number)))
                yield text, number, number

        with patch.object(auxiliary, "_fits", side_effect=force_chunks), \
                patch.object(auxiliary, "_text_chunks", side_effect=chunks):
            result = auxiliary.tool_report("large output", {}, {}, {}, attachments=[image])
        self.assertEqual(result, "combined report")
        self.assertEqual([len(images(call["messages"])) for call in model.calls], [1, 0, 0])
        for call in model.calls:
            self.assertIn(call["messages"], model.counted)
            self.assertIn(Path(image["path"]).as_uri(), json.dumps(call["messages"]))

    def test_tool_report_background_never_reattaches_pending_parallel_images(self):
        earlier, current = self.image("parallel-earlier.png"), self.image("parallel-current.png")
        assistant, result = pair(1)
        result["content"] = [earlier]
        background = {"records": [{"event_id": f"event-{number}", "message": message}
                                  for number, message in enumerate([assistant, result])],
                      "messages": [assistant, result]}
        model = Model(["Current observation"])
        Auxiliary(model, self.session).tool_report("output", {}, {}, background, attachments=[current])
        messages = model.calls[0]["messages"]
        self.assertEqual(images(messages), [current])
        self.assertIn(Path(earlier["path"]).as_uri(), json.dumps(messages))


if __name__ == "__main__":
    unittest.main()
