"""The small tool boundary: shell, sandbox, and subagents."""

import json
import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

from skills_agent.sandbox import DockerSandbox


class ToolRegistry:
    def __init__(
        self,
        workspace: Path,
        sandbox: DockerSandbox | None = None,
        subagent: Callable[[str, str], str] | None = None,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self.sandbox = sandbox
        self.subagent = subagent
        definitions = [
            ("shell", "Run arbitrary shell commands on the host in the workspace. "
             "Read skill instructions and reference source code, execute multiline inline code, "
             "or write and run new scripts for the current task.",
             {"command": "Shell command or multiline script to execute."}),
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
        for schema in self.schemas:
            if schema["name"] in {"shell", "sandbox"}:
                schema["parameters"]["properties"]["summary"] = {
                    "type": "string",
                    "description": "Brief user-facing description of the action, in the user's language; no code or detailed reasoning.",
                }

    def execute(self, name: str, arguments: str) -> str:
        try:
            schema = next((item for item in self.schemas if item["name"] == name), None)
            if schema is None:
                raise ValueError(f"Unknown tool: {name}")
            values = json.loads(arguments)
            expected = schema["parameters"]["required"]
            allowed = schema["parameters"]["properties"]
            if not isinstance(values, dict) or not set(expected) <= set(values) <= set(allowed):
                raise ValueError(f"Expected arguments: {', '.join(expected)}")
            for key, value in values.items():
                if not isinstance(value, str):
                    raise ValueError(f"Argument {key} must be a string")
            if name == "shell":
                with subprocess.Popen(
                    values["command"], shell=True, cwd=self.workspace,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, errors="replace", start_new_session=True,
                ) as process:
                    try:
                        stdout, stderr = process.communicate()
                    except BaseException:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                        raise
                result = {
                    "exit_code": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            elif name == "sandbox":
                result = self.sandbox.run(values["command"])
            else:
                result = {"result": self.subagent(values["goal"], values["task"])}
        except Exception as exc:
            result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False)
