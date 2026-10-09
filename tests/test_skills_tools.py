import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills_agent.sandbox import DockerSandbox
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry


BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "skills_agent" / "builtin_skills"
SCRIPT = BUILTIN_ROOT / "workspace" / "scripts" / "workspace.py"


class SkillsToolsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        self.tools = ToolRegistry(self.workspace)

    def shell(self, command):
        return json.loads(self.tools.execute("shell", json.dumps({"command": command})))

    def script(self, *arguments):
        return self.shell(shlex.join([sys.executable, str(SCRIPT), *arguments]))

    def test_catalog_includes_metadata_and_path_without_instructions(self):
        root = self.workspace / "skills" / "example"
        root.mkdir(parents=True)
        path = root / "SKILL.md"
        header = "---\nname: example\ndescription: >-\n  Read example\n  files.\n---\n"
        path.write_text(header + "original body", encoding="utf-8")
        skills = SkillRegistry([root.parent])
        self.assertIn("example: Read example files.", skills.catalog())
        self.assertIn(str(path), skills.catalog())
        self.assertNotIn("original body", skills.catalog())

    def test_invalid_skill_metadata_is_reported(self):
        root = self.workspace / "bad"
        root.mkdir()
        (root / "SKILL.md").write_text("---\nname: bad\n---\nbody", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "description"):
            SkillRegistry([root])

    def test_invalid_yaml_reports_skill_path_as_value_error(self):
        root = self.workspace / "bad-yaml"
        root.mkdir()
        path = root / "SKILL.md"
        path.write_text("---\nname: [unclosed\n---\nbody", encoding="utf-8")
        with self.assertRaises(ValueError) as error:
            SkillRegistry([root])
        self.assertIn("Invalid YAML frontmatter", str(error.exception))
        self.assertIn(str(path), str(error.exception))

    def test_shell_preserves_output_and_exit_code(self):
        result = self.shell("printf 'full stdout'; printf 'full stderr' >&2; exit 7")
        self.assertEqual(result, {"exit_code": 7, "stdout": "full stdout", "stderr": "full stderr"})
        self.assertEqual(Path(self.shell("pwd")["stdout"].strip()), self.workspace)

    def test_shell_does_not_truncate_output(self):
        result = self.shell(shlex.join([sys.executable, "-c", "print('x' * 100000)"]))
        self.assertEqual(len(result["stdout"]), 100001)

    def test_workspace_script_reads_requested_lines(self):
        (self.workspace / "text.txt").write_text("first\nsecond\nthird\nfourth\n", encoding="utf-8")
        result = self.script("read", "text.txt", "--start", "2", "--end", "3")
        self.assertEqual(result, {"exit_code": 0, "stdout": "2: second\n3: third\n", "stderr": ""})

    def test_workspace_script_searches_text_and_skips_binary(self):
        (self.workspace / "notes.jsonl").write_text(
            '{"text": "before"}\n{"text": "needle"}\n', encoding="utf-8"
        )
        (self.workspace / "attachment.bin").write_bytes(b"\xff\xfe\x00")
        result = self.script("search", "needle", ".")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], 'notes.jsonl:2: {"text": "needle"}\n')
        self.assertEqual(result["stderr"], "")

    def test_history_exposes_only_public_events_and_preserves_source(self):
        events = [
            {"type": "message", "message": {"role": "user", "content": "public request"}},
            {"type": "message", "message": {
                "role": "assistant", "content": [{"type": "text", "text": "public answer"}],
                "_raw": {"secret": "HIDDEN_RAW_MESSAGE"},
                "tool_calls": [
                    {"id": "child", "name": "subagent", "arguments": '{"task": "HIDDEN_TASK"}'},
                    {"id": "host", "name": "shell", "arguments": '{"command": "pwd"}'},
                ],
            }},
            {"type": "message", "message": {
                "role": "tool", "name": "subagent", "content": "HIDDEN_RESULT",
            }},
            {"type": "subagent_summary", "goal": "public goal", "status": "completed"},
            {"type": "message", "message": {
                "role": "tool", "name": "shell", "content": "public tool result",
            }},
            {"type": "error", "message": "public failure", "_raw": {"secret": "HIDDEN_RAW_EVENT"},
             "partial_content": [{"type": "text", "text": "public partial answer"}]},
        ]
        path = self.workspace / "events.jsonl"
        original = "".join(json.dumps(event) + "\n" for event in events).encode("utf-8")
        path.write_bytes(original)
        result = self.script("history", str(path), "--start", "1", "--end", "6")
        self.assertEqual(result["exit_code"], 0, result["stderr"])
        self.assertNotIn("HIDDEN_", result["stdout"])
        self.assertNotIn('"_raw"', result["stdout"])
        rows = [line.split(": ", 1) for line in result["stdout"].splitlines()]
        self.assertEqual([number for number, _ in rows], ["1", "2", "4", "5", "6"])
        public = {number: json.loads(event) for number, event in rows}
        self.assertEqual(public["1"], events[0])
        self.assertEqual(public["2"]["message"]["content"], events[1]["message"]["content"])
        self.assertEqual(public["2"]["message"]["tool_calls"], [events[1]["message"]["tool_calls"][1]])
        self.assertEqual(public["4"], events[3])
        self.assertEqual(public["5"], events[4])
        self.assertEqual(public["6"]["partial_content"], events[5]["partial_content"])
        self.assertEqual(path.read_bytes(), original)
        selected = self.script("history", str(path), "--start", "3", "--end", "4")
        self.assertEqual(selected["stdout"], f"4: {json.dumps(events[3], ensure_ascii=False)}\n")

    def test_workspace_script_rejects_invalid_line_ranges(self):
        result = self.script("read", "missing.txt", "--start", "0")
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("1 <= start <= end", result["stderr"])

    def test_tool_errors_are_results(self):
        cases = [
            ("unknown", "{}"),
            ("shell", "not json"),
            ("shell", "[]"),
            ("shell", "{}"),
            ("shell", '{"command": 3}'),
            ("shell", '{"command": "pwd", "extra": "x"}'),
            ("read_skill", '{"name": "missing"}'),
            ("sandbox", '{"command": "pwd"}'),
            ("subagent", '{"goal": "x", "task": "x"}'),
        ]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                self.assertIn("error", json.loads(self.tools.execute(name, arguments)))

    def test_shell_reads_skill_instructions_and_reference_source(self):
        for path in [SCRIPT.parent.parent / "SKILL.md", SCRIPT]:
            with self.subTest(path=path):
                result = self.shell(shlex.join(["cat", str(path)]))
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(result["stdout"], path.read_text(encoding="utf-8"))

    def test_shell_executes_inline_code_and_writes_new_scripts(self):
        python = shlex.quote(sys.executable)
        result = self.shell(f"{python} - <<'PY'\nprint(sum([2, 3, 5]))\nPY")
        self.assertEqual(result, {"exit_code": 0, "stdout": "10\n", "stderr": ""})
        source = "from pathlib import Path\nPath('result.txt').write_text(str(sum(range(5))))\n"
        result = self.shell(f"cat > task.py <<'PY'\n{source}PY\n{python} task.py")
        self.assertEqual(result["exit_code"], 0, result["stderr"])
        self.assertEqual((self.workspace / "task.py").read_text(), source)
        self.assertEqual((self.workspace / "result.txt").read_text(), "10")

    def test_optional_tools_are_only_exposed_when_available(self):
        self.assertEqual([item["name"] for item in self.tools.schemas], ["shell"])
        calls = []

        def delegate(goal, task):
            calls.append((goal, task))
            return "complete result"

        tools = ToolRegistry(self.workspace, DockerSandbox("python:3.11", self.workspace), delegate)
        self.assertEqual([item["name"] for item in tools.schemas], ["shell", "sandbox", "subagent"])
        with patch("builtins.print") as output:
            result = json.loads(tools.execute("subagent", '{"goal": "review", "task": "inspect files"}'))
        output.assert_not_called()
        self.assertEqual(calls, [("review", "inspect files")])
        self.assertEqual(result, {"result": "complete result"})

    @patch("skills_agent.sandbox.subprocess.run")
    def test_sandbox_mounts_external_inputs_without_overriding_workspace(self, run):
        run.return_value = subprocess.CompletedProcess([], 3, "output", "error")
        inside = self.workspace / "skills"
        outside = self.workspace.parent / "external-skills"
        attachments = self.workspace.parent / "session"
        sandbox = DockerSandbox("python:3.11", self.workspace, [inside, outside], [attachments])
        self.assertEqual(sandbox.run("python script.py"), {"exit_code": 3, "stdout": "output", "stderr": "error"})
        args = run.call_args.args[0]
        self.assertEqual(args[:5], ["docker", "run", "--rm", "--network", "none"])
        self.assertIn(f"{self.workspace}:{self.workspace}:rw", args)
        self.assertIn(f"{outside}:{outside}:ro", args)
        self.assertIn(f"{attachments}:{attachments}:ro", args)
        self.assertNotIn(f"{inside}:{inside}:ro", args)
        self.assertEqual(args[-6:], ["-w", str(self.workspace), "python:3.11", "sh", "-lc", "python script.py"])
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("skills_agent.sandbox.subprocess.run", side_effect=FileNotFoundError("docker unavailable"))
    def test_missing_docker_returns_error_without_host_execution(self, run):
        tools = ToolRegistry(self.workspace, DockerSandbox("python:3.11", self.workspace))
        result = json.loads(tools.execute("sandbox", '{"command": "touch should-not-exist"}'))
        self.assertIn("docker unavailable", result["error"])
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][0], "docker")
        self.assertFalse((self.workspace / "should-not-exist").exists())


if __name__ == "__main__":
    unittest.main()
