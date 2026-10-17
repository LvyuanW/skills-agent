"""UI batching preserves content and ordering without sharing rendering locks."""

from copy import deepcopy
import hashlib
import threading
import unittest

from skills_agent.display import DisplayQueue


class DisplayQueueTests(unittest.TestCase):
    def test_text_chunks_merge_without_crossing_control_or_channel_boundaries(self):
        seen, wakes = [], []
        queue = DisplayQueue(lambda: wakes.append(True))
        renderer = seen.append
        events = [
            {"type": "thinking_delta", "text": "one "},
            {"type": "thinking_delta", "text": "two"},
            {"type": "tool_call_delta", "index": 0, "arguments": "{"},
            {"type": "tool_call_delta", "index": 1, "arguments": "}"},
            {"type": "thinking_delta", "text": "three"},
            {"type": "text_delta", "text": "body"},
            {"type": "message", "message": {"role": "assistant", "content": "body"}},
        ]
        original = deepcopy(events)
        for event in events:
            queue.post_event(renderer, event)
        self.assertEqual(wakes, [True])
        self.assertEqual(queue.drain(), 6)
        self.assertEqual(seen, [{"type": "thinking_delta", "text": "one two"}, *events[2:]])
        self.assertEqual(events, original)

    def test_public_calls_and_events_share_one_fifo(self):
        seen = []
        queue = DisplayQueue()
        renderer = lambda event: seen.append(event["text"])
        queue.post_event(renderer, {"type": "text_delta", "text": "before"})
        queue.post(seen.append, "finish")
        queue.post_event(renderer, {"type": "text_delta", "text": "after"})
        queue.drain()
        self.assertEqual(seen, ["before", "finish", "after"])

    def test_large_backlog_retains_exact_text_and_only_one_pending_delta(self):
        seen = []
        queue = DisplayQueue()
        renderer = seen.append
        parts = [f"第{i}段 **literal**\n" for i in range(10000)]
        for part in parts:
            queue.post_event(renderer, {"type": "thinking_delta", "text": part})
        self.assertEqual(queue.drain(), 1)
        expected = hashlib.sha256("".join(parts).encode()).digest()
        self.assertEqual(hashlib.sha256(seen[0]["text"].encode()).digest(), expected)

    def test_display_snapshot_does_not_follow_context_mutation_or_copy_raw_stream(self):
        event = {"type": "message", "_raw": object(), "message": {
            "role": "tool", "content": [{"type": "text", "text": "original"}],
            "tool_calls": [{"id": "call-1", "arguments": "original"}],
            "_raw": object(), "_responses_output": object(), "_anthropic_content": object(),
        }}
        seen = []
        queue = DisplayQueue()
        queue.post_event(seen.append, event)
        event["message"]["content"][0]["text"] = "selected report"
        event["message"]["tool_calls"][0]["arguments"] = "changed"
        queue.drain()
        message = seen[0]["message"]
        self.assertEqual(message["content"][0]["text"], "original")
        self.assertEqual(message["tool_calls"][0]["arguments"], "original")
        self.assertFalse(any(key.startswith("_") for key in message))
        self.assertNotIn("_raw", seen[0])

    def test_slow_consumer_does_not_hold_producer_lock(self):
        entered, release, posted = threading.Event(), threading.Event(), threading.Event()
        seen = []
        queue = DisplayQueue()

        def slow():
            entered.set()
            self.assertTrue(release.wait(5))

        queue.post(slow)
        consumer = threading.Thread(target=queue.drain)
        consumer.start()
        try:
            self.assertTrue(entered.wait(5))

            def post():
                queue.post(seen.append, "next")
                posted.set()

            producer = threading.Thread(target=post)
            producer.start()
            self.assertTrue(posted.wait(2), "Producer must not await the blocked renderer")
            producer.join(2)
        finally:
            release.set()
            consumer.join(5)
        queue.drain()
        self.assertEqual(seen, ["next"])

    def test_close_keeps_accepted_results_and_rejects_later_posts(self):
        seen = []
        queue = DisplayQueue()
        self.assertTrue(queue.post(seen.append, "final"))
        queue.close()
        self.assertFalse(queue.post(seen.append, "late"))
        self.assertFalse(queue.post_event(seen.append, {"type": "text_delta", "text": "late"}))
        queue.drain()
        self.assertEqual(seen, ["final"])

    def test_failed_callback_retains_later_control_events(self):
        seen = []
        queue = DisplayQueue()

        def fail():
            queue.post(seen.append, "new")
            raise RuntimeError("UI failed")

        queue.post(fail)
        queue.post(seen.append, "accepted")
        with self.assertRaisesRegex(RuntimeError, "UI failed"):
            queue.drain()
        queue.drain()
        self.assertEqual(seen, ["accepted", "new"])


if __name__ == "__main__":
    unittest.main()
