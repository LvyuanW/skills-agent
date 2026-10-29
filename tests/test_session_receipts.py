import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from skills_agent.background import BackgroundResults, RUNTIME_EVENT, UNCERTAINTY_EVENT
from skills_agent.session import Session


class SessionReceiptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.session = Session(self.root, "main")

    def test_stream_fragments_skip_decoding_without_changing_raw_receipts(self):
        expected = []
        receipts = [
            {"type": "message", "message": {"role": "tool", "name": "shell", "content": "done"}},
            {"type": "message", "message": {"role": "user", "runtime_event": RUNTIME_EVENT}},
            {"type": "subagent_summary", "name": "child"},
        ]
        for index, event in enumerate(receipts):
            for stream_kind in ("text_delta", "thinking_delta", "tool_call_delta"):
                self.session.append_event({"type": stream_kind, "text": f"chunk {index}"})
            expected.append(self.session.append_event(event))
        self.session.append_event({"type": "text_delta", "text": "stream-only tail"})
        before = self.session._events_path.read_bytes()
        with patch("skills_agent.session.json.loads", wraps=json.loads) as decode:
            events = list(self.session.iter_receipt_events())
        self.assertEqual(decode.call_count, len(expected))
        self.assertEqual([event for _, event in events if event is not None], expected)
        self.assertEqual(events[-1], (len(before), None))
        self.assertEqual(self.session._events_path.read_bytes(), before)
        self.assertEqual(len(self.session.events()), 13)

    def test_large_standard_assistant_raw_payload_is_not_decoded(self):
        self.session.append({"role": "user", "content": "hello"})
        self.session.append({"role": "assistant", "content": "done",
                             "_raw": {"payload": "x" * (4 * 1024 * 1024)}})
        self.session.append_event({"type": "error", "message": "unrelated failure"})
        before = self.session._events_path.read_bytes()
        with patch("skills_agent.session.json.loads", wraps=json.loads) as decode:
            events = list(self.session.iter_receipt_events())
        decode.assert_not_called()
        self.assertEqual(events, [(len(before), None)])
        self.assertEqual(self.session._events_path.read_bytes(), before)

    def test_legacy_tool_without_call_id_and_all_runtime_receipt_kinds_are_retained(self):
        expected = [self.session.append({"role": "tool", "name": "shell", "content": json.dumps({
            "status": "started", "completed": False, "job_id": "legacy-job"})})]
        for kind in (RUNTIME_EVENT, UNCERTAINTY_EVENT):
            expected.append(self.session.append({"role": "user", "runtime_event": kind,
                                                  "detail": "all fields retained"}))
        expected.append(self.session.append_event({"type": "subagent_summary", "status": "failed"}))
        self.assertEqual([event for _, event in self.session.iter_receipt_events()], expected)
        results = BackgroundResults(self.session, lambda: [])
        results._receipts()
        self.assertIn("legacy-job", results._known)

    def test_nested_text_and_escaped_markers_conservatively_use_full_decoder(self):
        expected = [
            self.session.append({"role": "assistant", "content": "background_job_result"}),
            self.session.append({"role": "assistant", "content": "done", "_raw": {"nested": "tool"}}),
        ]
        stamp = '"timestamp":"2026-10-30T01:02:03.123456+00:00"'
        legacy = [
            ('{' + stamp + ',"type":"message","message":{"role":"to\\u006fl","name":"shell"}}\n').encode(),
            ('{' + stamp + ',"type":"message","message":{"runtime_ev\\u0065nt":"background_job_res\\u0075lt"}}\n').encode(),
        ]
        with self.session._events_path.open("ab") as stream:
            for line in legacy:
                offset = stream.tell()
                expected.append({**json.loads(line), "event_id": f"event-{offset}"})
                stream.write(line)
        with patch("skills_agent.session.json.loads", wraps=json.loads) as decode:
            events = list(self.session.iter_receipt_events())
        self.assertEqual(decode.call_count, len(expected))
        self.assertEqual([event for _, event in events], expected)

    def test_filtered_parent_and_child_state_matches_full_raw_decode(self):
        child = Session(self.session.path / "subagents", "child")
        owner = hashlib.sha256(str(child.path).encode()).hexdigest()
        for session, prefix in ((self.session, "main"), (child, "child")):
            session.append({"role": "assistant", "content": "working", "_raw": "x" * 10000})
            session.append({"role": "tool", "name": "shell", "content": json.dumps({
                "status": "started", "completed": False, "job_id": prefix + "-pending"})})
            session.append({"role": "user", "runtime_event": UNCERTAINTY_EVENT,
                            "background_uncertainty_id": prefix + "-uncertainty",
                            "background_uncertainty_job": {"id": prefix + "-unknown", "state": "unknown"}})
            session.append({"role": "user", "runtime_event": RUNTIME_EVENT,
                            "background_job_id": prefix + "-finished",
                            "background_result_id": prefix + "-terminal"})
        self.session.append_event({"type": "subagent_summary", "name": "child", "status": "completed",
                                   "child_session_id": "child", "child_session_path": str(child.path),
                                   "background_owner_session_id": owner})
        self.session.append({"role": "tool", "name": "subagent", "child_session_id": "child",
                             "content": "child completed"})
        for session in (self.session, child):
            session.append_event({"type": "thinking_delta", "text": "tail"})
        before = {session.path: session._events_path.read_bytes() for session in (self.session, child)}
        baseline = BackgroundResults(self.session, lambda: [])
        with patch.object(Session, "iter_receipt_events", Session.iter_events):
            baseline._receipts()
        filtered = BackgroundResults(self.session, lambda: [])
        filtered._receipts()
        def normalize(value):
            if isinstance(value, Session): return str(value.path)
            if isinstance(value, set): return sorted(value)
            if isinstance(value, dict): return {key: normalize(item) for key, item in value.items()
                                               if key != "scope_paths"}
            return value
        self.assertEqual(normalize(vars(filtered)), normalize(vars(baseline)))
        self.assertEqual(filtered._delivered, {"main-terminal", "child-terminal"})
        self.assertIn("main-pending", filtered._known)
        self.assertIn("child-pending", filtered._known)
        for session in (self.session, child):
            self.assertEqual(session._events_path.read_bytes(), before[session.path])

    def test_stream_only_cursor_advances_and_later_appends_are_not_skipped(self):
        self.session.append_event({"type": "thinking_delta", "text": "still thinking"})
        first = list(self.session.iter_receipt_events())
        self.assertEqual(first, [(self.session._events_path.stat().st_size, None)])
        cursor = first[-1][0]
        self.assertEqual(list(self.session.iter_receipt_events(cursor)), [])
        expected = self.session.append_event({"type": "message", "message": {"role": "tool",
                                                                                "content": "done"}})
        self.session.append_event({"type": "tool_call_delta", "index": 0, "arguments": "next"})
        later = list(self.session.iter_receipt_events(cursor))
        self.assertEqual(later[0][1], expected)
        self.assertEqual(later[-1], (self.session._events_path.stat().st_size, None))
        self.assertEqual(list(self.session.iter_receipt_events(later[-1][0])), [])

    def test_nonstandard_headers_and_duplicate_type_keys_use_full_decoder(self):
        stamp = b'"timestamp":"2026-10-30T01:02:03.123456+00:00"'
        lines = [
            b'{"type":"text_delta",' + stamp + b',"text":"legacy"}\n',
            b'{' + stamp + b', "type": "thinking_delta", "text": "spaced"}\n',
            b'{' + stamp + b',"payload":{"type":"text_delta"},"type":"message"}\n',
            b'{' + stamp + b',"type":"text_delta_more","message":{"role":"tool"}}\n',
            b'{' + stamp + b',"type":"text_delta","type":"message","message":{"role":"tool"}}\n',
            b'{' + stamp + b',"type":"text_delta","text":"first","ty\\u0070e":"error"}\n',
        ]
        self.session._events_path.write_bytes(b"".join(lines))
        with patch("skills_agent.session.json.loads", wraps=json.loads) as decode:
            events = list(self.session.iter_receipt_events())
        self.assertEqual(decode.call_count, len(lines))
        actual = [event for _, event in events if event is not None]
        self.assertEqual([event["type"] for event in actual],
                         ["message", "text_delta_more", "message", "error"])
        offset = sum(map(len, lines[:2]))
        self.assertEqual(actual[0]["event_id"], f"event-{offset}")

    def test_partial_tail_is_not_consumed_until_the_line_is_complete(self):
        self.session.append_event({"type": "text_delta", "text": "earlier"})
        start = self.session._events_path.stat().st_size
        final = b'{"type":"message","message":{"role":"assistant","content":"done"}}\n'
        split = len(final) // 2
        with self.session._events_path.open("ab") as stream:
            stream.write(final[:split])
        self.assertEqual(list(self.session.iter_receipt_events()), [(start, None)])
        self.assertEqual(list(self.session.iter_receipt_events(start)), [])
        with self.session._events_path.open("ab") as stream:
            stream.write(final[split:])
        later = list(self.session.iter_receipt_events(start))
        self.assertEqual(len(later), 1)
        self.assertEqual(later[0][0], start + len(final))
        self.assertEqual(later[0][1]["event_id"], f"event-{start}")
        self.assertEqual(later[0][1]["message"]["content"], "done")

    def test_large_legacy_line_crlf_and_blank_lines_keep_byte_offsets(self):
        event = {"type": "message", "message": {"role": "assistant", "content": "结果" * 400000}}
        line = json.dumps(event, ensure_ascii=False).encode() + b"\r\n"
        raw = b"\r\n" + line + b"\n"
        self.session._events_path.write_bytes(raw)
        events = list(self.session.iter_receipt_events())
        self.assertEqual(events[0], (2 + len(line), {**event, "event_id": "event-2"}))
        self.assertEqual(events[-1], (len(raw), None))
        self.assertEqual(self.session._events_path.read_bytes(), raw)

    def test_complete_legacy_event_without_final_newline_is_preserved(self):
        event = {"type": "message", "message": {"role": "assistant", "content": "完整"}}
        raw = json.dumps(event, ensure_ascii=False).encode()
        for partial in (raw[:-3], raw[:raw.index("整".encode()) + 1]):
            self.session._events_path.write_bytes(partial)
            self.assertEqual(list(self.session.iter_receipt_events()), [])
        self.session._events_path.write_bytes(raw)
        self.assertEqual(list(self.session.iter_receipt_events()),
                         [(len(raw), {**event, "event_id": "event-0"})])

    def test_invalid_nonstandard_event_still_reports_a_decode_error(self):
        self.session._events_path.write_bytes(b'{"type":"message",invalid}\n')
        with self.assertRaises(json.JSONDecodeError):
            list(self.session.iter_receipt_events())

    def test_parent_and_child_receipts_advance_past_stream_tails_without_ui_cache(self):
        child = Session(self.session.path / "subagents", "child")
        owner = hashlib.sha256(str(child.path).encode()).hexdigest()
        self.session.append_event({"type": "subagent_summary", "name": "child", "status": "failed",
                                   "child_session_id": "child", "child_session_path": str(child.path),
                                   "background_owner_session_id": owner})
        child.append({"role": "user", "runtime_event": RUNTIME_EVENT,
                      "background_result_id": "child-terminal", "background_job_id": "child-job"})
        for session in (self.session, child):
            session.append_event({"type": "thinking_delta", "text": "stream tail"})
        result = BackgroundResults(self.session, lambda: [])
        with patch.object(Session, "iter_events", side_effect=AssertionError("full raw decode used")), \
                patch.object(Session, "replay_events", side_effect=AssertionError("UI cache used")):
            result._receipts()
            self.assertEqual(result._cursor, self.session._events_path.stat().st_size)
            self.assertEqual(result._delegated[owner]["cursor"], child._events_path.stat().st_size)
            self.assertEqual(result._delegated[owner]["terminal_receipts"], {"child-terminal"})
            with patch("skills_agent.session.json.loads", wraps=json.loads) as decode:
                result._receipts()
            decode.assert_not_called()
            self.session.append({"role": "user", "runtime_event": RUNTIME_EVENT,
                                 "background_result_id": "main-terminal", "background_job_id": "main-job"})
            child.append({"role": "user", "runtime_event": RUNTIME_EVENT,
                          "background_result_id": "child-next-terminal", "background_job_id": "child-next"})
            result._receipts()
        self.assertIn("main-terminal", result._delivered)
        self.assertEqual(result._delegated[owner]["terminal_receipts"],
                         {"child-terminal", "child-next-terminal"})


if __name__ == "__main__":
    unittest.main()
