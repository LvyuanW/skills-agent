"""The small tool boundary: arbitrary scripts and named delegated tasks."""

import json
import os
import signal
import subprocess
from pathlib import Path


class ToolRegistry:
    def __init__(self, workspace: Path, sandbox=None, subagent=None, *, on_activity=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.sandbox, self.subagent = sandbox, subagent
        self.on_activity = on_activity or (lambda activity: None)
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
                                             "Ordinary subagents do not inherit the parent conversation; provide "
                                             "the necessary background in task. For conversation-history retrieval, "
                                             "use history=true. Only this mode supplies the complete current working "
                                             "context as read-only background. The child searches original "
                                             "evidence, checks relevance and later corrections, and returns a report.", {
                "name": {"type": "string", "description": "Short task name, shown to the user."},
                "task": {"type": "string", "description": "Self-contained goal, relevant constraints, necessary materials "
                         "(content or accessible paths), operation boundaries (what may be read or changed), "
                         "and expected deliverable with required evidence."},
                "history": {"type": "boolean", "description": "Retrieve original session history with the complete current "
                            "working context as read-only background. Default false: no parent conversation is inherited."},
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
            try:
                self.on_activity({"kind": "tool", "name": name, "task_name": values["name"]})
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
            finally:
                self.on_activity(None)
        except Exception as exc:
            result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False)
