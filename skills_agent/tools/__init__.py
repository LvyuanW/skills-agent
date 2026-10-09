"""The small tool boundary: arbitrary scripts and named delegated tasks."""

import json
import os
import signal
import subprocess
from pathlib import Path


class ToolRegistry:
    def __init__(self, workspace: Path, sandbox=None, subagent=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.sandbox, self.subagent = sandbox, subagent
        script = {
            "name": {"type": "string", "description": "Short action and purpose, shown to the user."},
            "command": {"type": "string", "description": "Arbitrary shell command or multiline script."},
            "result_mode": {"type": "string", "enum": ["auto", "direct", "report"],
                            "description": "auto: original up to 8192 tokens, otherwise report. "
                            "direct: exact source (skills/code/data), or a source reference if it cannot fit. "
                            "report: concise conclusions with evidence. Default auto."},
        }
        self.schemas = [self._schema("shell", "Run arbitrary scripts on the host in the workspace. "
                                    "Read skills and reference source, write task-specific code, inspect results. "
                                    "Filter large data in your script and print relevant evidence.", script, ["name", "command"])]
        if sandbox:
            self.schemas.append(self._schema("sandbox", "Run arbitrary scripts inside the configured Docker sandbox.",
                                             script, ["name", "command"]))
        if subagent:
            self.schemas.append(self._schema("subagent", "Delegate a named task; only its final report returns. "
                                             "For history retrieval, set history=true to include the complete current "
                                             "working context as read-only background and use the history skill.", {
                "name": {"type": "string", "description": "Short task name, shown to the user."},
                "task": {"type": "string", "description": "Goal, input, boundaries and required evidence."},
                "history": {"type": "boolean", "description": "Retrieve details from this session's original history. Default false."},
            }, ["name", "task"]))

    @staticmethod
    def _schema(name, description, properties, required):
        return {"name": name, "description": description, "parameters": {
            "type": "object", "properties": properties, "required": required, "additionalProperties": False,
        }}

    def execute(self, name: str, arguments: str) -> str:
        try:
            schema = next((item for item in self.schemas if item["name"] == name), None)
            if schema is None:
                raise ValueError(f"Unknown tool: {name}")
            values = json.loads(arguments)
            expected, allowed = schema["parameters"]["required"], schema["parameters"]["properties"]
            if not isinstance(values, dict) or not set(expected) <= set(values) <= set(allowed):
                raise ValueError(f"Expected arguments: {', '.join(expected)}")
            for key, value in values.items():
                field = allowed[key]
                expected_type = bool if field["type"] == "boolean" else str
                if not isinstance(value, expected_type):
                    raise ValueError(f"Argument {key} must be a {field['type']}")
                if isinstance(value, str) and not value.strip():
                    raise ValueError(f"Argument {key} must not be empty")
                if "enum" in field and value not in field["enum"]:
                    raise ValueError(f"Invalid {key}: {value}")
            if name == "shell":
                with subprocess.Popen(values["command"], shell=True, cwd=self.workspace,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, errors="replace", start_new_session=True) as process:
                    try:
                        stdout, stderr = process.communicate()
                    except BaseException:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                        raise
                result = {"exit_code": process.returncode, "stdout": stdout, "stderr": stderr}
            elif name == "sandbox":
                result = self.sandbox.run(values["command"])
            else:
                result = {"result": self.subagent(values["name"], values["task"], history=values.get("history", False))}
        except Exception as exc:
            result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False)
