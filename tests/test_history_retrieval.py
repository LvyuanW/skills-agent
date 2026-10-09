import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from skills_agent.context.history import HistoryIndex


class HistoryRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.events = self.path / "events.jsonl"
        self.index = HistoryIndex(self.path)

    def append(self, message=None, *, event=None, explicit_id=False):
        offset = self.events.stat().st_size if self.events.exists() else 0
        item = event if event is not None else {"type": "message", "message": message}
        if explicit_id:
            item["event_id"] = f"event-{offset}"
        with self.events.open("ab") as stream:
            stream.write((json.dumps(item, ensure_ascii=False) + "\n").encode())
        return f"event-{offset}"

    def test_incremental_unicode_offsets_and_restart_preserve_raw_log(self):
        first = self.append({"role": "user", "content": "先检查剪贴板。"})
        second = self.append({"role": "assistant", "content": "实现 Ctrl-V 粘贴图片"}, explicit_id=True)
        original = self.events.read_bytes()
        self.assertEqual(self.index.sync(), 2)
        self.assertEqual(self.index.sync(), 0)
        reopened = HistoryIndex(self.events)
        third = self.append({"role": "user", "content": "保留原来的快捷键"})
        self.assertEqual(reopened.sync(), 1)
        self.assertTrue(self.events.read_bytes().startswith(original))
        items = reopened.list()
        self.assertEqual([item["event_id"] for item in items], [first, second, third])
        self.assertEqual(items[1]["turn_id"], first)
        self.assertEqual(items[2]["turn_id"], third)
        self.assertEqual(items[1]["raw_ref"], f"{self.events.resolve()}:2")
        with closing(sqlite3.connect(reopened.database_path)) as connection:
            self.assertEqual(int(connection.execute("SELECT value FROM state WHERE key='cursor'").fetchone()[0]), self.events.stat().st_size)

    def test_partial_appended_line_is_read_only_after_completion(self):
        self.append({"role": "user", "content": "first"})
        self.index.sync()
        offset = self.events.stat().st_size
        pending = json.dumps({"type": "message", "message": {"role": "assistant", "content": "second"}}).encode()
        with self.events.open("ab") as stream:
            stream.write(pending)
        self.assertEqual(self.index.sync(), 0)
        with self.events.open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(self.index.sync(), 1)
        self.assertEqual(self.index.read(f"event-{offset}")["text"], "second")

    def test_chinese_bigrams_and_mixed_language_match_without_exact_phrase(self):
        wanted = self.append({"role": "user", "content": "图片如何粘贴到输入行？使用CtrlV粘贴。"})
        self.append({"role": "assistant", "content": "数据库执行迁移。"})
        hits = self.index.search("粘贴图片")
        self.assertEqual(hits[0]["event_id"], wanted)
        self.assertEqual(self.index.search("CtrlV 图片", role="user")[0]["event_id"], wanted)
        self.assertFalse(self.index.search("根本没出现的极罕见词xyz", role="system"))

    def test_raw_tool_lines_middle_chunks_and_call_pairing_are_recoverable(self):
        origin = self.append({"role": "user", "content": "检查完整日志"})
        call = self.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": "call-log-7", "name": "shell", "arguments": json.dumps({
                "name": "检查构建日志", "command": "cat build.log"}, ensure_ascii=False),
        }]})
        lines = [f"record {number}\n" for number in range(1, 242)]
        lines[134] = "关键错误 /tmp/a_b/report-42.txt 中间证据\n"
        result = self.append({"role": "tool", "name": "shell", "tool_call_id": "call-log-7", "content": json.dumps({
            "exit_code": 2, "stdout": "".join(lines), "stderr": "真实错误\n第二行\n",
        }, ensure_ascii=False)})
        hit = self.index.search("/tmp/a_b/report-42.txt")[0]
        self.assertEqual(hit["event_id"], result)
        self.assertEqual(hit["task"], "检查构建日志")
        self.assertEqual(hit["turn_id"], origin)
        self.assertGreater(hit["start_line"], 1)
        selected = self.index.read(result, hit["start_line"], hit["end_line"])
        self.assertIn(lines[134], selected["text"])
        self.assertEqual(selected["paired_events"], [call])
        whole = self.index.read(result)
        self.assertIn("".join(lines), whole["text"])
        self.assertIn("真实错误\n第二行\n", whole["text"])
        self.assertEqual(self.index.read(call)["paired_events"], [result])
        paired = {item["event_id"] for item in self.index.neighbors(result, before=0, after=0)}
        self.assertEqual(paired, {call, result})
        self.assertEqual({hit["event_id"] for hit in self.index.search("call-log-7")}, {call, result})

    def test_search_retains_main_subagent_report_but_excludes_internal_records_and_deltas(self):
        self.append(event={"type": "text_delta", "text": "delta-secret-only"})
        self.append(event={"type": "tool_call_delta", "arguments": "call-delta-secret-only"})
        self.append(event={"type": "context_selection", "content": "derived-selection-only"})
        call = self.append({"role": "assistant", "content": "", "_raw": {"private": "provider-secret-only"}, "tool_calls": [{
            "id": "child-1", "name": "subagent", "arguments": json.dumps({"name": "核查预算", "task": "核查官方规定"}, ensure_ascii=False),
        }]})
        report = self.append({"role": "tool", "name": "subagent", "tool_call_id": "child-1", "content": json.dumps({"result": "官方预算核查报告"}, ensure_ascii=False)})
        child = self.path / "subagents" / "private"
        child.mkdir(parents=True)
        (child / "events.jsonl").write_text(json.dumps({"type": "message", "message": {"role": "assistant", "content": "child-secret-only"}}))
        self.assertEqual(self.index.sync(), 2)
        self.assertEqual(self.index.search("官方预算核查报告")[0]["event_id"], report)
        self.assertIn("核查官方规定", self.index.read(call)["text"])
        searchable_text = "\n".join(self.index.read(item["event_id"])["text"] for item in self.index.list())
        for token in ("delta-secret-only", "call-delta-secret-only", "derived-selection-only", "provider-secret-only", "child-secret-only"):
            self.assertNotIn(token, searchable_text)
        self.assertFalse(self.index.search("secret"))

    def test_exact_paths_and_ids_rank_first_and_queries_cannot_inject_fts(self):
        first = self.append({"role": "user", "content": "Do not edit /tmp/a-b/config.py"})
        self.append({"role": "assistant", "content": "I propose editing config.py and files a or b."})
        latest = self.append({"role": "user", "content": "Correction: /tmp/a-b/config.py may now be edited."})
        hits = self.index.search("/tmp/a-b/config.py")
        self.assertEqual([hit["event_id"] for hit in hits[:2]], [latest, first])
        self.assertEqual(self.index.search(first)[0]["event_id"], first)
        self.assertEqual(self.index.search('" * :'), [])
        self.assertEqual(len(self.index.search('" OR * :')), 1)
        nearby = self.index.neighbors(first, before=0, after=2)
        self.assertEqual(nearby[-1]["event_id"], latest)
        self.assertEqual([item["event_id"] for item in self.index.list(after=first, role="user")], [latest])

    def test_disconnected_partial_answer_and_error_remain_searchable_without_raw_payload(self):
        turn = self.append({"role": "user", "content": "检查网络连接"})
        self.append(event={"type": "text_delta", "text": "连接测试发现异常"})
        error = self.append(event={
            "type": "error", "message": "API connection failed",
            "partial_content": [
                {"type": "text", "text": "连接测试发现异常，需要检查代理地址"},
                {"type": "thinking", "thinking": "已有可读分析", "signature": "PRIVATE_SIGNATURE"},
                {"type": "redacted_thinking", "data": "PRIVATE_OPAQUE"},
            ],
            "_raw": {"secret": "PRIVATE_PROVIDER"},
        })
        original = self.events.read_bytes()
        self.assertEqual(self.index.sync(), 2)
        self.assertEqual(self.index.search("API connection failed")[0]["event_id"], error)
        self.assertEqual(self.index.search("代理地址")[0]["event_id"], error)
        result = self.index.read(error)
        self.assertEqual(result["turn_id"], turn)
        self.assertEqual(result["kind"], "error")
        self.assertIn("已有可读分析", result["text"])
        self.assertNotIn("PRIVATE_", result["text"])
        self.assertEqual(result["text"].count("连接测试发现异常"), 1)
        self.assertEqual(self.events.read_bytes(), original)

    def test_reused_call_id_pairs_only_with_its_own_response(self):
        expected = []
        for name in ("第一次读取", "第二次读取"):
            call = self.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": "reused-id", "name": "shell", "arguments": json.dumps({"name": name, "command": "pwd"}),
            }]})
            result = self.append({"role": "tool", "name": "shell", "tool_call_id": "reused-id", "content": "output"})
            expected.append((call, result, name))
        for call, result, name in expected:
            self.assertEqual(self.index.read(call)["paired_events"], [result])
            self.assertEqual(self.index.read(result)["paired_events"], [call])
            self.assertEqual(self.index.read(result)["task"], name)

    def test_reference_script_reads_range_from_any_working_directory(self):
        event = self.append({"role": "assistant", "content": "first\nsecond\nthird"})
        project = Path(__file__).resolve().parents[1]
        script = project / "skills_agent/context/history_skill/scripts/history.py"
        completed = subprocess.run(
            [sys.executable, str(script), str(self.path), "read", event, "--start", "2", "--end", "2"],
            cwd=self.path, env={**os.environ, "PYTHONPATH": str(project)}, text=True, capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["text"], "second\n")
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["line_count"], 3)


if __name__ == "__main__":
    unittest.main()
