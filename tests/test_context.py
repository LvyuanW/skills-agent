import copy
import tempfile
import unittest
from pathlib import Path

from skills_agent.context import ContextBudgetError, ContextBuilder
from skills_agent.session import Session


def tool_pair(number, length=100):
    call_id = f"call-{number}"
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "name": "shell", "arguments": '{"command":"ls"}'}]},
        {"role": "tool", "tool_call_id": call_id, "name": "shell", "content": f"result-{number}:" + "x" * length + f":end-{number}"},
    ]


def assert_paired(test, messages):
    outstanding = set()
    for message in messages:
        if message.get("tool_calls"):
            test.assertFalse(outstanding)
            outstanding = {call["id"] for call in message["tool_calls"]}
        elif message["role"] == "tool":
            test.assertIn(message["tool_call_id"], outstanding)
            outstanding.remove(message["tool_call_id"])
        else:
            test.assertFalse(outstanding)
    test.assertFalse(outstanding)


class ContextTests(unittest.TestCase):
    def test_build_never_changes_original_history_or_file(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Path(directory), "s")
            for index in range(15):
                session.append({"role": "user", "content": f"question {index}"})
                for message in tool_pair(index, 3000):
                    message["_raw"] = {"provider": "unmodified", "payload": "原文" * 2000}
                    session.append(message)
            session.append({"role": "user", "content": "next question"})
            before = (session.path / "events.jsonl").read_bytes()
            messages = session.messages()
            original = copy.deepcopy(messages)
            builder = ContextBuilder(4000)
            selected = builder.build(messages)
            self.assertLessEqual(builder.size(selected), 4000)
            self.assertEqual(messages, original)
            self.assertEqual((session.path / "events.jsonl").read_bytes(), before)
            self.assertTrue(all("_raw" not in message for message in selected))
            assert_paired(self, selected)

    def test_long_current_tool_chain_remains_within_budget(self):
        messages = [{"role": "user", "content": "Please inspect all files"}]
        for index in range(120):
            messages.extend(tool_pair(index, 4000))
        builder = ContextBuilder(5000)
        selected = builder.build(messages, "system instruction", [{"name": "shell", "description": "x" * 400}])
        self.assertLessEqual(builder.size(selected, "system instruction", [{"name": "shell", "description": "x" * 400}]), 5000)
        self.assertIn("result-119", str(selected))
        self.assertIn("Please inspect all files", str(selected))
        assert_paired(self, selected)

    def test_latest_oversized_tool_arguments_fail_instead_of_losing_the_result(self):
        messages = [{"role": "user", "content": "run this"}, *tool_pair(1)]
        messages[1]["tool_calls"][0]["arguments"] = '{"command":"' + "x" * 20000 + '"}'
        builder = ContextBuilder(2000)
        with self.assertRaisesRegex(ContextBudgetError, "latest tool call"):
            builder.build(messages)

    def test_text_attachment_can_shrink_while_path_and_source_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.txt"
            source.write_text("line\n" * 10000)
            session = Session(root / "sessions", "s")
            message = session.make_user_message("read the attachment", [source])
            original = copy.deepcopy(message)
            builder = ContextBuilder(1800)
            selected = builder.build([message])
            self.assertLessEqual(builder.size(selected), 1800)
            self.assertIn(message["content"][1]["path"], str(selected))
            self.assertEqual(message, original)
            self.assertEqual(Path(message["content"][1]["path"]).read_text(), "line\n" * 10000)

    def test_image_and_pdf_blocks_survive_context_selection(self):
        message = {"role": "user", "content": [
            {"type": "text", "text": "read"},
            {"type": "image", "path": "/tmp/image.png", "mime_type": "image/png"},
            {"type": "file", "path": "/tmp/file.pdf", "mime_type": "application/pdf", "name": "file.pdf"},
        ]}
        self.assertEqual(ContextBuilder().build([message]), [message])

    def test_multiple_parallel_tool_results_stay_paired(self):
        messages = [{"role": "user", "content": "inspect"}, {
            "role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "name": "shell", "arguments": "{}"},
                {"id": "b", "name": "shell", "arguments": "{}"},
            ]},
            {"role": "tool", "tool_call_id": "b", "content": "b" * 6000},
            {"role": "tool", "tool_call_id": "a", "content": "a" * 6000},
        ]
        selected = ContextBuilder(2200).build(messages)
        assert_paired(self, selected)

    def test_tight_budget_retains_the_latest_pair_even_with_empty_tool_text(self):
        messages = [{"role": "user", "content": "inspect"}]
        for index in range(5):
            messages.extend(tool_pair(index, 5000))
        builder = ContextBuilder(400)
        selected = builder.build(messages)
        self.assertLessEqual(builder.size(selected), 400)
        self.assertEqual(selected[-1]["tool_call_id"], "call-4")
        assert_paired(self, selected)

    def test_json_escaping_and_system_instructions_count_in_budget(self):
        messages = [{"role": "user", "content": "inspect"}, *tool_pair(1, 2000)]
        messages[-1]["content"] = '\n"\\' * 4000
        builder = ContextBuilder(1200)
        selected = builder.build(messages, "instructions" * 20)
        self.assertLessEqual(builder.size(selected, "instructions" * 20), 1200)
        assert_paired(self, selected)

    def test_interrupted_tool_units_are_filtered_only_in_model_context(self):
        for messages in ([tool_pair(1)[1]], [tool_pair(1)[0]]):
            original = copy.deepcopy(messages)
            self.assertEqual(ContextBuilder().build(messages), [])
            self.assertEqual(messages, original)
        user = {"role": "user", "content": "continue after interruption"}
        messages = [{"role": "user", "content": "first"}, tool_pair(1)[0], user, *tool_pair(2)]
        selected = ContextBuilder().build(messages)
        self.assertEqual(selected, [messages[0], user, *tool_pair(2)])
        assert_paired(self, selected)

    def test_large_user_text_and_schema_fail_explicitly(self):
        with self.assertRaisesRegex(ContextBudgetError, "Current user"):
            ContextBuilder(1000).build([{"role": "user", "content": "x" * 2000}])
        with self.assertRaisesRegex(ContextBudgetError, "schemas"):
            ContextBuilder(1000).build([], tools=[{"schema": "x" * 2000}])


if __name__ == "__main__":
    unittest.main()
