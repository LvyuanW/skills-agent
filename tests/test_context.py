import copy
import json
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
    def test_reasoning_is_recovered_from_older_sessions_without_changing_log(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Path(directory), "reasoning")
            messages = [{"role": "user", "content": "inspect"}, *tool_pair(1)]
            reasoning = "  original reasoning\n中文\n"
            messages[1]["_raw"] = {"choices": [{"message": {"reasoning_content": reasoning}}]}
            messages.append({"role": "assistant", "content": "done", "_raw": {
                "choices": [{"message": {"reasoning_content": ""}}],
            }})
            for message in messages:
                session.append(message)
            before = (session.path / "events.jsonl").read_bytes()
            selected = ContextBuilder().build(session.messages())
            self.assertEqual(selected[1]["reasoning_content"], reasoning)
            self.assertEqual(selected[-1]["reasoning_content"], "")
            self.assertTrue(all("_raw" not in message for message in selected))
            self.assertEqual((session.path / "events.jsonl").read_bytes(), before)

    def test_older_stream_reasoning_is_recovered_even_after_null_overwrite(self):
        messages = [{"role": "assistant", "content": "done", "_raw": {
            "choices": [{"message": {"reasoning_content": None}}],
            "stream_events": [
                {"choices": [{"delta": {"reasoning_content": "  first\n"}}]},
                {"choices": [{"delta": {"reasoning_content": None, "content": "done"}}]},
                {"choices": [{"delta": {"reasoning_content": "中文  "}}]},
                {"choices": []},
            ],
        }}]
        original = copy.deepcopy(messages)
        self.assertEqual(ContextBuilder().build(messages)[0]["reasoning_content"], "  first\n中文  ")
        self.assertEqual(messages, original)
        messages[0]["reasoning_content"] = "already normalized"
        self.assertEqual(ContextBuilder().build(messages)[0]["reasoning_content"], "already normalized")

    def test_reasoning_is_counted_but_never_excerpted_during_compaction(self):
        reasoning = "UNMODIFIED_REASONING\n" * 30
        messages = [{"role": "user", "content": "inspect"}, *tool_pair(1, 12000)]
        messages[1]["reasoning_content"] = reasoning
        original = copy.deepcopy(messages)
        builder = ContextBuilder(1500)
        selected = builder.build(messages)
        self.assertEqual(selected[1]["reasoning_content"], reasoning)
        self.assertLess(len(selected[-1]["content"]), len(messages[-1]["content"]))
        self.assertLessEqual(builder.size(selected), 1500)
        with self.assertRaisesRegex(ContextBudgetError, "reasoning_content"):
            ContextBuilder(400).build(messages)
        self.assertEqual(messages, original)

    def test_omitted_reasoning_is_not_added_to_extracted_summary(self):
        messages = [{"role": "user", "content": "earlier"}, {
            "role": "assistant", "content": "earlier answer", "reasoning_content": "PRIVATE_REASONING" * 1000,
        }, {"role": "user", "content": "current"}]
        selected = ContextBuilder(1000).build(messages)
        self.assertNotIn("PRIVATE_REASONING", str(selected))
        self.assertIn("earlier answer", str(selected))

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

    def test_completed_script_source_shrinks_without_changing_history_or_result(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Path(directory), "s")
            messages = [{"role": "user", "content": "run this"}, *tool_pair(1)]
            source = "python - <<'PY'\n" + 'print("你好\\n")\n' * 2000 + "PY"
            messages[1]["tool_calls"][0]["arguments"] = json.dumps({"command": source})
            for message in messages:
                session.append(message)
            before = (session.path / "events.jsonl").read_bytes()
            messages = session.messages()
            original = copy.deepcopy(messages)

            builder = ContextBuilder(2000)
            selected = builder.build(messages)

            self.assertLessEqual(builder.size(selected), 2000)
            self.assertEqual(selected[0], original[0])
            self.assertEqual(selected[-1], original[-1])
            command = json.loads(selected[1]["tool_calls"][0]["arguments"])["command"]
            self.assertIn("Already-executed command excerpt", command)
            self.assertIn("full source: session tool call call-1", command)
            self.assertLess(len(command), len(source))
            self.assertEqual(messages, original)
            self.assertEqual((session.path / "events.jsonl").read_bytes(), before)
            assert_paired(self, selected)

    def test_multiple_shell_and_sandbox_scripts_keep_their_ids_and_results(self):
        messages = [{"role": "user", "content": "run both scripts"}, {
            "role": "assistant", "content": "", "tool_calls": [
                {"id": "host", "name": "shell", "arguments": json.dumps({"command": "print('host')\n" * 2000})},
                {"id": "container", "name": "sandbox", "arguments": json.dumps({"command": "print('container')\n" * 2000})},
            ]},
            {"role": "tool", "tool_call_id": "container", "content": "container done"},
            {"role": "tool", "tool_call_id": "host", "content": "host done"},
        ]
        builder = ContextBuilder(2400)
        selected = builder.build(messages)
        self.assertLessEqual(builder.size(selected), 2400)
        self.assertEqual(selected[0], messages[0])
        self.assertEqual(selected[-2:], messages[-2:])
        for call in selected[1]["tool_calls"]:
            arguments = json.loads(call["arguments"])
            self.assertEqual(set(arguments), {"command"})
            self.assertIn(f"full source: session tool call {call['id']}", arguments["command"])
        assert_paired(self, selected)

    def test_repeated_script_compaction_keeps_one_source_reference(self):
        messages = [{"role": "user", "content": "run this"}, *tool_pair(1)]
        messages[1]["tool_calls"][0]["arguments"] = json.dumps({"command": "x" * 20000})
        selected = ContextBuilder(3000).build(messages)
        original = copy.deepcopy(selected)
        for budget in (600, 400):
            with self.subTest(budget=budget):
                tighter = ContextBuilder(budget)
                selected_again = tighter.build(selected)
                command = json.loads(selected_again[1]["tool_calls"][0]["arguments"])["command"]
                self.assertEqual(command.count("Already-executed command excerpt"), 1)
                self.assertEqual(command.count("full source: session tool call call-1"), 1)
                self.assertLessEqual(tighter.size(selected_again), budget)
                if budget == 400:
                    self.assertEqual(command, "[Already-executed command excerpt; full source: session tool call call-1]\n")
                assert_paired(self, selected_again)
        self.assertEqual(selected, original)

    def test_short_and_non_shell_arguments_are_not_rewritten(self):
        for name, arguments in (
            ("shell", '{ "command": "ls" }'),
            ("sandbox", '{ "command": "pwd" }'),
            ("subagent", '{ "goal": "inspect", "task": "inspect all files" }'),
        ):
            with self.subTest(name=name):
                messages = [{"role": "user", "content": "inspect"}, *tool_pair(1, 6000)]
                messages[1]["tool_calls"][0].update(name=name, arguments=arguments)
                selected = ContextBuilder(600).build(messages)
                self.assertEqual(selected[1]["tool_calls"][0]["arguments"], arguments)

        messages = [{"role": "user", "content": "delegate"}, *tool_pair(1)]
        messages[1]["tool_calls"][0].update(name="subagent", arguments=json.dumps({"task": "x" * 20000}))
        with self.assertRaisesRegex(ContextBudgetError, "latest tool call"):
            ContextBuilder(2000).build(messages)

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
        pending = tool_pair(1)[0]
        pending["tool_calls"][0]["arguments"] = json.dumps({"command": "x" * 20000})
        for messages in ([tool_pair(1)[1]], [pending]):
            original = copy.deepcopy(messages)
            self.assertEqual(ContextBuilder(2000).build(messages), [])
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
