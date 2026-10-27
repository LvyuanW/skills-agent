import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills_agent import system_skill
from skills_agent.agent import Agent
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from skills_agent.tools import ToolRegistry
from tests.test_agent_context import ScriptedModel, call, final


class SystemSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = Session(self.root, "main")
        self.package = Path(system_skill.__file__).resolve().parent
        self.skill_name = system_skill.SYSTEM_SKILL_NAME
        self.source_name = self.skill_name or "feedback"
        self.skill_path = self.package / "builtin_skills" / self.source_name / "SKILL.md"
        self.body = self.skill_path.read_text(encoding="utf-8").split("---", 2)[2].strip()
        if self.skill_name is None:
            no_source = patch.object(system_skill, "Path", side_effect=AssertionError(
                "Disabled system skill must not access a source path"))
            no_source.start()
            self.addCleanup(no_source.stop)

    def agent(self, model, **kwargs):
        agent = Agent(model, self.session, SkillRegistry([]), self.root, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def assert_exposure(self, system):
        if self.skill_name is None:
            self.assertNotIn("Preloaded skill instructions:", system)
        else:
            self.assertEqual(system.count(self.body), 1)
            self.assertIn(str(self.skill_path), system)
            self.assertIn(f"Resolve relative references from {self.skill_path.parent}.", system)
        for name in {"investigation", "feedback", "resource-management"} - {self.skill_name}:
            other = (self.package / "builtin_skills" / name / "SKILL.md").read_text().split("---", 2)[2].strip()
            self.assertNotIn(other, system)

    def test_complete_body_reaches_first_child_and_later_provider_requests_once(self):
        model = ScriptedModel([
            call("subagent", {"name": "Inspect", "task": "Inspect supplied evidence"}),
            final("Child findings"), final("done"), final("next answer"),
        ])
        # A workspace lookalike cannot replace the branch's trusted built-in source.
        fake = self.root / "skills" / self.source_name / "SKILL.md"
        fake.parent.mkdir(parents=True)
        fake.write_text("untrusted workspace replacement")
        agent = self.agent(model)
        self.assertEqual(agent.run({"role": "user", "content": "inspect"}), "done")
        self.assertEqual(agent.run({"role": "user", "content": "next"}), "next answer")
        self.assertEqual(len(model.calls), 4)
        for index, (system, messages, tools) in enumerate(model.calls):
            self.assert_exposure(system)
            self.assertNotIn("untrusted workspace replacement", system)
            # Inlining adds no tool and does not change the existing schemas.
            expected = ToolRegistry(self.root, subagent=None if index == 1 else agent._subagent).schemas
            self.assertEqual(tools, expected)
        self.assertEqual(agent.skills.catalog(), "")

    def test_complete_body_survives_compression_and_session_resume(self):
        self.session.append({"role": "user", "content": "original task"})
        for index in range(12):
            self.session.append(final(f"completed stage {index}"))
        model = ScriptedModel([final("done")])
        counts = iter([900, 100, 120, 200])
        model.count_tokens = lambda *args: next(counts)
        agent = self.agent(model, context_tokens=1000)
        with patch.object(agent.auxiliary, "compress", return_value="Past work summary") as compress:
            self.assertEqual(agent.run({"role": "user", "content": "continue"}), "done")
        compress.assert_called_once()
        self.assert_exposure(model.calls[0][0])
        self.assertIn("Past work summary", model.calls[0][0])
        resumed_model = ScriptedModel([final("resumed")])
        resumed = self.agent(resumed_model)
        self.assertEqual(resumed.run({"role": "user", "content": "next"}), "resumed")
        self.assert_exposure(resumed_model.calls[0][0])

    def test_existing_registry_and_shell_skill_read_remain_usable(self):
        registry = SkillRegistry([self.package / "builtin_skills"])
        self.assertIn(f"- {self.source_name}:", registry.catalog())
        model = ScriptedModel([
            call("shell", {"name": "Read skill", "command": f"cat '{self.skill_path}'",
                           "result_mode": "direct"}),
            final("done"),
        ])
        agent = Agent(model, self.session, registry, self.root)
        self.addCleanup(agent.close)
        self.assertEqual(agent.run({"role": "user", "content": "inspect the source"}), "done")
        self.assert_exposure(model.calls[1][0])
        result = next(message for message in model.calls[1][1] if message["role"] == "tool")
        self.assertIn(self.body, json.loads(result["content"])["stdout"])

    def test_invalid_or_missing_source_fails_instead_of_silently_omitting_instructions(self):
        fake_module = self.root / "package" / "system_skill.py"
        skill_name = "feedback"
        fake_skill = fake_module.parent / "builtin_skills" / skill_name / "SKILL.md"
        fake_skill.parent.mkdir(parents=True)
        with (patch.object(system_skill, "__file__", str(fake_module)),
              patch.object(system_skill, "SYSTEM_SKILL_NAME", skill_name),
              patch.object(system_skill, "Path", Path)):
            with self.assertRaises(FileNotFoundError):
                self.agent(ScriptedModel([]))
            invalid = [
                "missing header",
                "---\nname: unclosed\n",
                "---\nname: [invalid\n---\nbody",
                "---\nname: wrong-name\ndescription: present\n---\nbody",
                f"---\nname: {skill_name}\ndescription: 42\n---\nbody",
                f"---\nname: {skill_name}\ndescription: present\n---\n  ",
            ]
            for text in invalid:
                with self.subTest(text=text):
                    fake_skill.write_text(text)
                    with self.assertRaises(ValueError):
                        self.agent(ScriptedModel([]))


if __name__ == "__main__":
    unittest.main()
