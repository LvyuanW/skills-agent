"""The small tool boundary: skills, shell, sandbox, and subagents."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from skills_agent.sandbox import DockerSandbox
from skills_agent.skills import SkillRegistry


class ToolRegistry:
    def __init__(
        self,
        workspace: Path,
        skills: SkillRegistry,
        sandbox: DockerSandbox | None = None,
        subagent: Callable[[str, str], str] | None = None,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self.skills = skills
        self.sandbox = sandbox
        self.subagent = subagent
        definitions = [
            ("read_skill", "Read the complete instructions for a skill.", {"name": "Skill name from the catalog."}),
            ("shell", "Run a shell command on the host in the workspace.", {"command": "Shell command to execute."}),
        ]
        if sandbox is not None:
            definitions.append(("sandbox", "Run a command inside the configured Docker sandbox.", {"command": "Shell command to execute."}))
        if subagent is not None:
            definitions.append(("subagent", "Delegate a task and return its final result.", {
                "goal": "Short summary of the delegated goal, shown to the user.",
                "task": "Self-contained instructions and relevant context for the subagent.",
            }))
        self.schemas = [
            {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        key: {"type": "string", "description": detail}
                        for key, detail in properties.items()
                    },
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
            for name, description, properties in definitions
        ]

    def execute(self, name: str, arguments: str) -> str:
        try:
            schema = next((item for item in self.schemas if item["name"] == name), None)
            if schema is None:
                raise ValueError(f"Unknown tool: {name}")
            values = json.loads(arguments)
            expected = schema["parameters"]["required"]
            if not isinstance(values, dict) or set(values) != set(expected):
                raise ValueError(f"Expected arguments: {', '.join(expected)}")
            for key, value in values.items():
                if not isinstance(value, str):
                    raise ValueError(f"Argument {key} must be a string")
            if name == "read_skill":
                result = {"content": self.skills.read(values["name"])}
            elif name == "shell":
                process = subprocess.run(
                    values["command"], shell=True, cwd=self.workspace,
                    capture_output=True, text=True, errors="replace",
                )
                result = {
                    "exit_code": process.returncode,
                    "stdout": process.stdout,
                    "stderr": process.stderr,
                }
            elif name == "sandbox":
                result = self.sandbox.run(values["command"])
            else:
                result = {"result": self.subagent(values["goal"], values["task"])}
        except Exception as exc:
            result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False)
