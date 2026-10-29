"""Display-cache recovery preserves the original public replay semantics."""

from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from skills_agent.cli import ConsoleRenderer
from skills_agent.session import Session
from skills_agent.session import replay
from skills_agent.terminal import TerminalView


def render(events, *, full=False):
    output = io.StringIO()
    renderer = ConsoleRenderer(output, full=full, live=False)
    for event in events:
        renderer(event)
    renderer.close()
    return output.getvalue()


def transcript(events):
    with create_pipe_input() as source:
        view = TerminalView(input=source, output=DummyOutput())
        renderer = ConsoleRenderer(view, live=False)
        for event in events:
            renderer(event)
        renderer.close()
        lines = view.control.create_content(100, 30)
        result = [[(style, text) for style, text, *rest in lines.get_line(i)]
                  for i in range(lines.line_count)]
        view.close()
        return result


class SessionReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Session(Path(self.directory.name), "example")
        self.source = self.session.path / "events.jsonl"
        self.cache = self.session.path / "replay-cache.json"

    def append(self, *events):
        for event in events:
            self.session.append_event(event)

    def fixture(self):
        arguments = json.dumps({"name": "检查文件", "command": "printf evidence"}, ensure_ascii=False)
        events = [
            {"type": "message", "message": {"role": "user", "content": "开始"}},
            {"type": "thinking_delta", "text": "先"},
            {"type": "thinking_delta", "text": "检查。"},
            {"type": "text_delta", "text": "当前"},
            {"type": "text_delta", "text": "进展。"},
            {"type": "tool_call_delta", "index": 0, "id": "call1", "name": "shell", "arguments": ""},
            *[{"type": "tool_call_delta", "index": 0, "arguments": part} for part in arguments],
            {"type": "message", "streamed": True, "message": {
                "role": "assistant", "content": [{"type": "thinking", "thinking": "先检查。"},
                                                   {"type": "text", "text": "当前进展。"}],
                "tool_calls": [{"id": "call1", "name": "shell", "arguments": arguments}],
                "_raw": {"stream_events": ["unneeded provider bytes" * 1000]},
            }},
            {"type": "message", "message": {"role": "tool", "name": "shell", "tool_call_id": "call1",
                "content": json.dumps({"exit_code": 0, "stdout": "evidence\n", "stderr": ""})}},
            {"type": "subagent_summary", "name": "复核", "status": "completed"},
            {"type": "text_delta", "text": "尚未"},
            {"type": "text_delta", "text": "完成"},
            {"type": "retry", "category": "connection", "message": "retry", "attempt": 1,
             "retry_in_seconds": 5, "_raw": {"private": "payload"}},
            {"type": "thinking_delta", "text": "重新"},
            {"type": "thinking_delta", "text": "思考"},
            {"type": "error", "message": "Interrupted", "response_interrupted": True,
             "partial_content": [{"type": "thinking", "thinking": "重新思考"}]},
            {"type": "message", "message": {"role": "assistant", "content": "最终结果"}},
            {"type": "text_delta", "text": "崩溃前"},
            {"type": "text_delta", "text": "未完成正文"},
        ]
        self.append(*events)

    def test_cached_and_raw_replays_have_same_visible_history(self):
        self.fixture()
        original_bytes = self.source.read_bytes()
        raw = self.session.events()
        compact = self.session.replay_events()
        for full in (False, True):
            self.assertEqual(render(raw, full=full), render(compact, full=full))
        self.assertEqual(transcript(raw), transcript(compact))
        self.assertIn("接收调用", render(compact))
        self.assertIn("崩溃前未完成正文", render(compact))
        self.assertLess(len(compact), len(raw) // 2)
        self.assertNotIn("unneeded provider bytes", self.cache.read_text())
        self.assertEqual(self.source.read_bytes(), original_bytes)

    def test_incremental_cache_never_decodes_the_previous_raw_prefix(self):
        self.fixture()
        first = self.session.replay_events()
        cached_cursor = self.source.stat().st_size
        self.append({"type": "text_delta", "text": "，现在继续"})
        decoded_raw_offsets = []
        original_loads = replay.json.loads

        def decode(value, *args, **kwargs):
            result = original_loads(value, *args, **kwargs)
            if isinstance(result, dict) and result.get("event_id", "").startswith("event-"):
                decoded_raw_offsets.append(int(result["event_id"][6:]))
            return result

        with patch.object(replay.json, "loads", side_effect=decode):
            updated = self.session.replay_events()
        self.assertEqual(decoded_raw_offsets, [cached_cursor])
        self.assertEqual(render(updated), render(self.session.events()))
        self.assertEqual(len(updated), len(first))
        self.assertEqual(updated[-1]["text"], "崩溃前未完成正文，现在继续")

    def test_replacement_truncation_and_same_size_rewrite_rebuild_cache(self):
        self.fixture()
        self.session.replay_events()
        for replacement in ("replace", "truncate", "same_size"):
            with self.subTest(replacement=replacement):
                value = {"type": "message", "message": {"role": "user", "content": "fresh 1"}}
                data = (json.dumps(value) + "\n").encode()
                if replacement == "replace":
                    temporary = self.source.with_suffix(".replacement")
                    temporary.write_bytes(data)
                    temporary.replace(self.source)
                elif replacement == "truncate":
                    self.source.write_bytes(data)
                else:
                    self.source.write_bytes(data.replace(b"fresh 1", b"fresh 2"))
                self.assertEqual(render(self.session.replay_events()), render(self.session.events()))

    def test_corrupt_or_obsolete_cache_is_rebuilt(self):
        self.fixture()
        expected = render(self.session.events())
        self.session.replay_events()
        valid = json.loads(self.cache.read_bytes())
        corruptions = [b"not json", b"[]"]
        changed = deepcopy(valid); changed["events"][0]["message"]["content"] = "lost history"
        corruptions.append(json.dumps(changed).encode())
        changed = deepcopy(valid); changed["version"] = 0
        corruptions.append(json.dumps(changed).encode())
        for content in corruptions:
            self.cache.write_bytes(content)
            self.assertEqual(render(self.session.replay_events()), expected)

    def test_partial_append_is_replayed_only_after_newline_and_cache_cursor_is_safe(self):
        self.append({"type": "text_delta", "text": "before"})
        self.session.replay_events()
        cursor = self.source.stat().st_size
        encoded = json.dumps({"type": "text_delta", "text": " after"}).encode()
        with self.source.open("ab") as stream:
            stream.write(encoded[:17])
        self.assertEqual(render(self.session.replay_events()), "assistant:\nbefore\n")
        self.assertEqual(json.loads(self.cache.read_bytes())["source"]["cursor"], cursor)
        with self.source.open("ab") as stream:
            stream.write(encoded[17:] + b"\n")
        self.assertEqual(render(self.session.replay_events()), "assistant:\nbefore after\n")
        self.assertEqual(json.loads(self.cache.read_bytes())["source"]["cursor"], self.source.stat().st_size)

    def test_empty_session_does_not_create_cache_and_unwritable_cache_does_not_break_replay(self):
        self.assertEqual(self.session.replay_events(), [])
        self.assertFalse(self.cache.exists())
        self.source.write_bytes(b"")
        self.assertEqual(self.session.replay_events(), [])
        self.assertFalse(self.cache.exists())
        self.fixture()
        with patch.object(replay.os, "replace", side_effect=PermissionError("read-only cache")):
            self.assertEqual(render(self.session.replay_events()), render(self.session.events()))
        self.assertFalse(self.cache.exists())
        self.assertEqual(list(self.session.path.glob("*.tmp")), [])

    def test_concurrent_append_stays_after_the_snapshot_cursor(self):
        self.append({"type": "text_delta", "text": "first"})
        boundary = self.source.stat().st_size
        original_loads = replay.json.loads
        appended = False

        def decode(value, *args, **kwargs):
            nonlocal appended
            event = original_loads(value, *args, **kwargs)
            if not appended and isinstance(event, dict) and event.get("type") == "text_delta":
                appended = True
                self.append({"type": "text_delta", "text": " second"})
            return event

        with patch.object(replay.json, "loads", side_effect=decode):
            first = self.session.replay_events()
        self.assertEqual(first[0]["text"], "first")
        self.assertEqual(json.loads(self.cache.read_bytes())["source"]["cursor"], boundary)
        self.assertEqual(self.session.replay_events()[0]["text"], "first second")

    def test_source_replaced_during_read_retries_without_showing_stale_history(self):
        self.append({"type": "text_delta", "text": "stale"})
        original_loads = replay.json.loads
        replaced = False

        def decode(value, *args, **kwargs):
            nonlocal replaced
            event = original_loads(value, *args, **kwargs)
            if not replaced and isinstance(event, dict) and event.get("type") == "text_delta":
                replaced = True
                other = self.source.with_suffix(".new")
                other.write_text(json.dumps({"type": "text_delta", "text": "fresh"}) + "\n")
                other.replace(self.source)
            return event

        with patch.object(replay.json, "loads", side_effect=decode):
            events = self.session.replay_events()
        self.assertEqual(events[0]["text"], "fresh")
        self.assertEqual(self.session.replay_events()[0]["text"], "fresh")

    def test_complete_intermediate_tool_arguments_keep_their_visible_boundary(self):
        self.append(
            {"type": "tool_call_delta", "index": 0, "name": "shell", "id": "c1"},
            {"type": "tool_call_delta", "index": 0, "arguments": "{"},
            {"type": "tool_call_delta", "index": 0, "arguments": "}"},
            {"type": "tool_call_delta", "index": 0, "arguments": " "},
            {"type": "error", "message": "Interrupted"},
        )
        raw = self.session.events()
        compact = self.session.replay_events()
        for full in (False, True):
            self.assertEqual(render(raw, full=full), render(compact, full=full))
        self.assertEqual(transcript(raw), transcript(compact))


if __name__ == "__main__":
    unittest.main()
