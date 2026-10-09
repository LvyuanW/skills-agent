from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skills_agent.cli import ConsoleRenderer, main, render_event
from skills_agent.session import Session
from tests.test_markdown import terminal_screen, terminal_text


TOOL_COLOR = "\x1b[34;107m"
RESET = "\x1b[0m"


class Output(io.StringIO):
    def __init__(self, *, tty=False):
        super().__init__()
        self.tty = tty
        self.flushes = 0

    def isatty(self):
        return self.tty

    def flush(self):
        self.flushes += 1
        super().flush()


def message(role, content, *, streamed=False, **fields):
    event = {"type": "message", "message": {"role": role, "content": content, **fields}}
    if streamed:
        event["streamed"] = True
    return event


def tool_call(name="shell", identifier="shell-1", **arguments):
    return {"id": identifier, "name": name, "arguments": json.dumps(arguments)}


def tool_delta(index=0, **fragments):
    return {"type": "tool_call_delta", "index": index, **fragments}


class StreamDisplayTests(unittest.TestCase):
    def setUp(self):
        terminal = patch.dict(os.environ, {
            "TERM": "xterm-256color", "COLUMNS": "80", "LINES": "24",
        })
        terminal.start()
        self.addCleanup(terminal.stop)

    def test_chunks_are_flushed_before_completion_and_body_is_not_repeated(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "first "})
        self.assertEqual(output.getvalue(), "assistant:\nfirst ")
        flushed = output.flushes
        renderer({"type": "text_delta", "text": "second"})
        self.assertEqual(output.getvalue(), "assistant:\nfirst second")
        self.assertGreater(output.flushes, flushed)
        event = message("assistant", [{"type": "text", "text": "first second"}],
                        _raw={"reasoning_content": "PRIVATE_REASONING"})
        original = copy.deepcopy(event)
        renderer(event)
        self.assertEqual(output.getvalue(), "assistant:\nfirst second\n")
        self.assertEqual(event, original)

    def test_streaming_body_stays_visible_when_tool_calls_arrive_later(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        body = "I will inspect these files."
        renderer({"type": "text_delta", "text": body})
        self.assertIn(body, output.getvalue())
        call = tool_call(command="printf HIDDEN_COMMAND", summary="检查文件")
        renderer(tool_delta(name="shell", arguments=call["arguments"]))
        self.assertIn("· shell：检查文件\n", output.getvalue())
        renderer(message("assistant", body, tool_calls=[call], streamed=True,
                         _raw={"reasoning_content": "PRIVATE_REASONING"}))
        renderer(message("tool", '{"exit_code":0,"stdout":"HIDDEN_STDOUT","stderr":""}',
                         name="shell", tool_call_id="shell-1"))
        self.assertEqual(output.getvalue().count(body), 1)
        self.assertIn("· shell：检查文件\n", output.getvalue())
        self.assertIn("· shell：完成，输出 1 行\n", output.getvalue())
        self.assertNotIn("HIDDEN_", output.getvalue())
        self.assertNotIn("PRIVATE_REASONING", output.getvalue())
        renderer({"type": "text_delta", "text": "Final result"})
        renderer(message("assistant", "Final result"))
        self.assertEqual(output.getvalue().count("assistant:"), 2)
        self.assertEqual(output.getvalue().count("Final result"), 1)

    def test_multiline_stream_renders_markdown_and_keeps_role_colors(self):
        output = Output(tty=True)
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "first\n"})
        renderer({"type": "text_delta", "text": "second"})
        call = tool_call(command="pwd", summary="检查目录")
        renderer(tool_delta(name="shell", arguments=call["arguments"]))
        renderer(message("assistant", "first\nsecond", tool_calls=[call], streamed=True))
        text = output.getvalue()
        self.assertEqual(terminal_text(text), "assistant:\nfirst second\n· shell：检查目录")
        screen = terminal_screen(text)
        self.assertEqual((screen.buffer[1][0].fg, screen.buffer[1][0].bg),
                         ("black", "brightwhite"))
        self.assertIn(TOOL_COLOR + "· shell：检查目录" + RESET + "\n", text)
        self.assertTrue(text.endswith(RESET + "\n"))

    def test_stream_ending_in_newline_does_not_add_a_blank_line(self):
        output = Output(tty=True)
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "complete\n"})
        renderer(message("assistant", "complete\n"))
        self.assertEqual(terminal_text(output.getvalue()), "assistant:\ncomplete")
        self.assertTrue(output.getvalue().endswith(RESET + "\n"))

    def test_replay_ending_during_stream_closes_line_and_resets_color(self):
        output = Output(tty=True)
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "last saved chunk"})
        renderer.close()
        self.assertEqual(terminal_text(output.getvalue()), "assistant:\nlast saved chunk")
        self.assertTrue(output.getvalue().endswith(RESET + "\n"))
        closed = output.getvalue()
        renderer.close()
        self.assertEqual(output.getvalue(), closed)

    def test_error_closes_stream_without_reprinting_received_text(self):
        for error in ("truncated", "Interrupted"):
            with self.subTest(error=error):
                output = Output(tty=True)
                renderer = ConsoleRenderer(output)
                renderer({"type": "text_delta", "text": "received partial"})
                renderer({"type": "error", "message": error, "partial_content": [
                    {"type": "text", "text": "received partial"},
                ], "_raw": {"reasoning_content": "PRIVATE_REASONING"}})
                plain = terminal_text(output.getvalue())
                self.assertEqual(plain.count("received partial"), 1)
                self.assertIn("received partial\n· 回答未完成，已保存 16 字符\n", plain)
                self.assertIn("error: " + error, plain)
                self.assertNotIn("PRIVATE_REASONING", plain)
                self.assertTrue(output.getvalue().endswith(RESET + "\n"))
                renderer({"type": "text_delta", "text": "next answer"})
                renderer(message("assistant", "next answer"))
                self.assertEqual(terminal_text(output.getvalue()).count("assistant:"), 2)

    def test_child_calls_results_and_raw_private_fields_remain_hidden(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        renderer({"type": "text_delta", "text": "Public parent text", "_raw": "PRIVATE_RAW"})
        renderer(tool_delta(name="sub"))
        renderer(tool_delta(name="agent", arguments='{"task":"PRIVATE_CHILD_TASK"}'))
        renderer(message("assistant", "Public parent text", tool_calls=[tool_call(
            name="subagent", identifier="child-1", goal="统计文件", task="PRIVATE_CHILD_TASK",
        )], _raw={"thinking": "PRIVATE_THINKING"}))
        renderer({"type": "subagent_summary", "status": "completed", "goal": "统计文件"})
        renderer(message("tool", '{"result":"PRIVATE_CHILD_RESULT"}', name="subagent", tool_call_id="child-1"))
        text = output.getvalue()
        self.assertEqual(text.count("Public parent text"), 1)
        self.assertIn("sub-agent 处理了：统计文件", text)
        self.assertNotIn("PRIVATE_", text)
        self.assertNotIn("tool_call", text)

    def test_tool_text_and_argument_arrival_order_is_kept_in_live_and_full_replay(self):
        arguments = '{"command":"pwd","summary":"检查目录"}'
        events = [
            tool_delta(name="sh"),
            tool_delta(name="ell", arguments='{"command":"pwd",'),
            {"type": "text_delta", "text": "Body after tool began"},
            tool_delta(arguments='"summary":"检查目录"}'),
            {"type": "text_delta", "text": "Body after tool args"},
            message("assistant", "Body after tool beganBody after tool args", streamed=True,
                    tool_calls=[{"id": "shell-1", "name": "shell", "arguments": arguments}]),
        ]
        original = copy.deepcopy(events)
        for full in (False, True):
            with self.subTest(full=full):
                output = Output()
                renderer = ConsoleRenderer(output, full=full)
                renderer(events[0])
                self.assertEqual(output.getvalue(), "")
                renderer(events[1])
                self.assertEqual(output.getvalue(), "· shell：接收调用\n")
                for event in events[2:]:
                    renderer(event)
                text = output.getvalue()
                detail = "tool_call shell: " + arguments if full else "· shell：检查目录"
                expected = ["· shell：接收调用", "Body after tool began", detail, "Body after tool args"]
                offsets = [text.index(item) for item in expected]
                self.assertEqual(offsets, sorted(offsets))
                for item in expected:
                    self.assertEqual(text.count(item), 1)
                if not full:
                    self.assertNotIn('"command"', text)
        self.assertEqual(events, original)

    def test_interleaved_tool_indices_display_each_summary_at_its_own_arrival(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        renderer(tool_delta(0, name="shell", arguments='{"command":"pwd",'))
        renderer(tool_delta(1, name="sandbox", arguments='{"command":"ls","summary":"容器检查"}'))
        renderer(tool_delta(0, arguments='"summary":"本地检查"}'))
        before = output.getvalue()
        renderer(message("assistant", "", streamed=True, tool_calls=[
            tool_call(command="pwd", summary="本地检查"),
            tool_call("sandbox", "sandbox-1", command="ls", summary="容器检查"),
        ]))
        self.assertEqual(output.getvalue(), before)
        self.assertEqual(before.splitlines(), ["· shell：接收调用", "· sandbox：容器检查", "· shell：本地检查"])

    def test_history_commands_replay_saved_arrival_order_without_modifying_log(self):
        call = tool_call(command="pwd", summary="检查目录")
        events = [
            message("user", "request"),
            tool_delta(name="shell", arguments=call["arguments"]),
            {"type": "text_delta", "text": "Body after tool"},
            message("assistant", "Body after tool", streamed=True, tool_calls=[call]),
            message("tool", '{"exit_code":0,"stdout":"RAW_STDOUT","stderr":""}',
                    name="shell", tool_call_id="shell-1"),
            {"type": "text_delta", "text": "unfinished final"},
        ]
        with TemporaryDirectory() as directory:
            session = Session(Path(directory) / ".agent/sessions", "default")
            for event in events:
                session.append_event(event)
            log = session.path / "events.jsonl"
            before = log.read_bytes()
            for full in (False, True):
                with self.subTest(full=full):
                    output = Output()
                    command = ["history", "--workspace", directory] + (["--full"] if full else [])
                    with redirect_stdout(output):
                        self.assertEqual(main(command), 0)
                    text = output.getvalue()
                    marker = "tool_call shell:" if full else "· shell：检查目录"
                    self.assertLess(text.index(marker), text.index("Body after tool"))
                    self.assertEqual(text.count("Body after tool"), 1)
                    self.assertTrue(text.endswith("unfinished final\n"))
                    self.assertEqual("RAW_STDOUT" in text, full)
                    self.assertEqual(log.read_bytes(), before)

    def test_full_stream_error_does_not_duplicate_partial_and_clears_tool_indices(self):
        output = Output()
        renderer = ConsoleRenderer(output, full=True)
        renderer(tool_delta(name="shell", arguments='{"command":'))
        renderer({"type": "text_delta", "text": "partial response"})
        error = {"type": "error", "message": "Interrupted", "partial_content": [
            {"type": "text", "text": "partial response"},
        ]}
        original = copy.deepcopy(error)
        renderer(error)
        renderer(tool_delta(name="shell", arguments='{"command":"next"}'))
        renderer(message("assistant", "", streamed=True, tool_calls=[tool_call(command="next")]))
        text = output.getvalue()
        self.assertEqual(text.count("partial response"), 1)
        self.assertIn('tool_call shell: {"command":"next"}', text)
        self.assertEqual(error, original)
        fresh = Output()
        ConsoleRenderer(fresh, full=True)(error)
        self.assertIn("partial response", fresh.getvalue())

    def test_no_delta_falls_back_to_existing_display_and_empty_delta_adds_nothing(self):
        events = [
            {"type": "text_delta", "text": ""},
            message("assistant", "Final without streaming"),
            message("assistant", "Hidden intermediate", tool_calls=[tool_call(command="pwd", summary="检查目录")]),
        ]
        streamed, ordinary = Output(), Output()
        renderer = ConsoleRenderer(streamed)
        for event in events:
            renderer(event)
            render_event(event, ordinary)
        self.assertEqual(streamed.getvalue(), ordinary.getvalue())
        self.assertEqual(streamed.getvalue().count("assistant:"), 2)

    def test_thinking_text_and_named_tools_keep_arrival_order_without_duplicates(self):
        call = {
            "id": "shell-1", "name": "shell",
            "arguments": json.dumps({"name": "核对文件", "command": "PRIVATE_COMMAND"}),
        }
        events = [
            {"type": "thinking_delta", "text": "先核对"},
            {"type": "thinking_delta", "text": "文件。"},
            {"type": "text_delta", "text": "开始检查。"},
            tool_delta(name="shell", arguments=call["arguments"]),
            {"type": "thinking_delta", "text": "等待结果。"},
            message("assistant", "开始检查。", streamed=True, tool_calls=[call],
                    reasoning_content="先核对文件。等待结果。"),
            message("tool", '{"exit_code":0,"stdout":"PRIVATE_OUTPUT","stderr":""}',
                    name="shell", tool_call_id="shell-1"),
        ]
        original = copy.deepcopy(events)
        outputs = []
        for live in (True, False):
            output = Output()
            renderer = ConsoleRenderer(output, live=live)
            for event in events:
                renderer(event)
            renderer.close()
            outputs.append(output.getvalue())
        expected = ("思考:\n先核对文件。\nassistant:\n开始检查。\n"
                    "· shell「核对文件」：调用\n思考:\n等待结果。\n"
                    "· shell「核对文件」：完成，输出 1 行\n")
        self.assertEqual(outputs, [expected, expected])
        self.assertEqual(events, original)

    def test_thinking_only_response_is_visible_once_and_uses_gray_on_default_background(self):
        output = Output(tty=True)
        renderer = ConsoleRenderer(output, live=False)
        renderer({"type": "thinking_delta", "text": "**检查**约束"})
        renderer(message("assistant", [{"type": "thinking", "thinking": "**检查**约束",
                                         "signature": "PRIVATE_SIGNATURE"}], streamed=True))
        rendered = terminal_text(output.getvalue())
        self.assertEqual(rendered, "思考:\n检查约束")
        self.assertNotIn("PRIVATE", output.getvalue())
        screen = terminal_screen(output.getvalue())
        self.assertEqual((screen.buffer[1][0].fg, screen.buffer[1][0].bg),
                         ("brightblack", "default"))

    def test_nonstream_thinking_blocks_preserve_content_order_and_hide_signatures(self):
        event = message("assistant", [
            {"type": "text", "text": "first body"},
            {"type": "thinking", "thinking": "readable thinking", "signature": "PRIVATE_SIGNATURE"},
            {"type": "redacted_thinking", "data": "PRIVATE_ENCRYPTED"},
            {"type": "text", "text": "second body"},
        ], _raw={"thinking": "PRIVATE_RAW"})
        for full in (False, True):
            output = Output()
            ConsoleRenderer(output, full=full)(event)
            self.assertEqual(output.getvalue(), "assistant:\nfirst body\n思考:\nreadable thinking\n"
                                               "assistant:\nsecond body\n")

    def test_empty_or_redacted_thinking_does_not_create_visible_message(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        renderer({"type": "thinking_delta", "text": ""})
        renderer(message("assistant", [{"type": "redacted_thinking", "data": "PRIVATE_ENCRYPTED"}],
                         reasoning_content=""))
        self.assertEqual(output.getvalue(), "")

    def test_thinking_saved_in_session_replays_once_in_stream_order(self):
        events = [
            {"type": "text_delta", "text": "before thinking"},
            {"type": "thinking_delta", "text": "readable reasoning"},
            {"type": "text_delta", "text": "after thinking"},
            message("assistant", "before thinkingafter thinking", streamed=True,
                    reasoning_content="readable reasoning"),
        ]
        with TemporaryDirectory() as directory:
            session = Session(Path(directory) / ".agent/sessions", "default")
            for event in events:
                session.append_event(event)
            path = session.path / "events.jsonl"
            original = path.read_bytes()
            for full in (False, True):
                output = Output()
                with redirect_stdout(output):
                    self.assertEqual(main(["history", "--workspace", directory]
                                          + (["--full"] if full else [])), 0)
                self.assertEqual(output.getvalue(), "assistant:\nbefore thinking\n"
                                                   "思考:\nreadable reasoning\n"
                                                   "assistant:\nafter thinking\n")
                self.assertEqual(path.read_bytes(), original)

    def test_thinking_stream_error_does_not_duplicate_partial_content(self):
        output = Output()
        renderer = ConsoleRenderer(output, full=True)
        renderer({"type": "thinking_delta", "text": "received thought"})
        renderer({"type": "error", "message": "Interrupted", "partial_content": [
            {"type": "thinking", "thinking": "received thought", "signature": "PRIVATE_SIGNATURE"},
        ]})
        self.assertEqual(output.getvalue(), "思考:\nreceived thought\nerror: Interrupted\n")

    def test_named_tool_result_uses_its_call_name_and_failure_status(self):
        output = Output()
        renderer = ConsoleRenderer(output)
        renderer(message("assistant", [], tool_calls=[{
            "id": "shell-1", "name": "shell", "arguments": json.dumps({
                "name": "运行测试", "summary": "旧名字", "command": "PRIVATE_COMMAND",
            }),
        }]))
        renderer(message("tool", '{"exit_code":3,"stdout":"","stderr":"PRIVATE_ERROR"}',
                         name="shell", tool_call_id="shell-1"))
        self.assertEqual(output.getvalue(), "· shell「运行测试」：调用\n"
                                           "· shell「运行测试」：失败（退出码 3），输出 1 行\n")

    def test_named_subagent_call_and_report_never_show_task_or_internal_content(self):
        call = {"id": "child-1", "name": "subagent", "arguments": json.dumps({
            "name": "查找历史决策", "task": "PRIVATE_TASK", "mode": "history",
        })}
        events = [
            tool_delta(name="subagent", arguments=call["arguments"]),
            message("assistant", [], tool_calls=[call], streamed=True),
            {"type": "subagent_summary", "name": "查找历史决策", "status": "completed",
             "report": "PRIVATE_REPORT"},
            message("tool", '{"report":"PRIVATE_REPORT"}', name="subagent", tool_call_id="child-1"),
        ]
        for full in (False, True):
            output = Output()
            renderer = ConsoleRenderer(output, full=full)
            for event in events:
                renderer(event)
            self.assertEqual(output.getvalue(), "· sub-agent「查找历史决策」：调用\n"
                                               "· sub-agent「查找历史决策」：已返回报告\n")

    def test_user_echo_setting_keeps_attachments_and_renderer_state_is_local(self):
        live, benchmark = Output(), Output()
        live_renderer = ConsoleRenderer(live, echo_user=False)
        benchmark_renderer = ConsoleRenderer(benchmark)
        event = message("user", [{"type": "text", "text": "user request"},
                                 {"type": "image", "name": "image.png", "path": "/sample/image.png"}])
        live_renderer(event)
        benchmark_renderer(event)
        self.assertEqual(live.getvalue(), "[附件] image.png\n")
        self.assertEqual(benchmark.getvalue(), "user:\nuser request\n[附件] image.png\n")
        live_renderer({"type": "text_delta", "text": "live partial"})
        benchmark_renderer(message("assistant", "benchmark full"))
        self.assertIn("assistant:\nbenchmark full\n", benchmark.getvalue())
        live_renderer(message("assistant", "live partial"))
        self.assertEqual(live.getvalue().count("live partial"), 1)


if __name__ == "__main__":
    unittest.main()
