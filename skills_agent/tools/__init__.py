"""The small tool boundary: arbitrary scripts and named delegated tasks."""

import json
import math
import os
from pathlib import Path

from ..execution import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, ToolCleanupError, run_command, validate_timeout
from ..processes import CALL_ENV, CONTEXT_ENV, SCOPE_ENV


class ToolRegistry:
    def __init__(self, workspace: Path, sandbox=None, subagent=None, *, on_activity=None, log_dir=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.sandbox, self.subagent = sandbox, subagent
        self.process_scope = None
        self.process_context = None
        self.tool_call_id = None
        self.log_dir = log_dir
        self.on_activity = on_activity or (lambda activity: None)
        script = {
            "name": {"type": "string", "description": "Short action and purpose, shown to the user."},
            "command": {"type": "string", "description": "Arbitrary shell command or multiline script."},
            "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_TIMEOUT_SECONDS,
                                "description": f"Wall-clock limit; default {DEFAULT_TIMEOUT_SECONDS} seconds, "
                                               f"maximum {MAX_TIMEOUT_SECONDS}. Timeout stops this command. "
                                               "Use managed background processes for longer work."},
            "result_mode": {"type": "string", "enum": ["auto", "direct", "report"],
                            "description": "auto: original up to 8192 tokens, otherwise report. "
                            "direct: exact source (skills/code/data), or a source reference if it cannot fit. "
                            "report: concise conclusions with evidence. Default auto."},
        }
        shell_script = {**script,
            "run_in_background": {"type": "boolean", "description":
                "Required. false: wait for the entry process, clean remaining processes in its group, "
                "then return its result. true: return a registered job receipt while the entry process "
                "continues; background_lifecycle determines whether it must finish before a final reply. "
                "Read the background-tasks skill before using true. Scripts must wait for their own "
                "child work and must not daemonize or detach from the process group."},
            "background_lifecycle": {"type": "string", "enum": ["task", "service"], "description":
                "Background jobs only; default task. task: must finish before the Agent ends its work. "
                "service: intentionally stays online after delivery, does not block a final reply, "
                "and survives a successful CLI exit; failed or cancelled turns clean up new services. "
                "Verify service readiness before delivery. Persistent services are forbidden in benchmark runs. "
                "service requires run_in_background=true."},
            "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "description":
                f"Execution deadline. Foreground: default {DEFAULT_TIMEOUT_SECONDS} seconds, "
                f"maximum {MAX_TIMEOUT_SECONDS}. Background task: default 3600 seconds. Service: no default "
                "deadline. Background durations may be any positive finite number, independent of the "
                "10-second startup confirmation wait. Expiry stops the owned process group."},
        }
        self.schemas = [self._schema("shell", "Run arbitrary scripts on the host in the workspace. "
                                    "Set required run_in_background=false for synchronous execution or true "
                                    "to return a job ID and continue working while the runtime supervises it. "
                                    "The entry process must wait for internal work; its exit triggers cleanup "
                                    "of remaining processes in the same group, recorded in the result. "
                                    "Read skills and reference source, write task-specific code, inspect results. "
                                    "Filter large data in your script and print relevant evidence.",
                                    shell_script, ["name", "command", "run_in_background"])]
        if sandbox:
            self.schemas.append(self._schema("sandbox", "Run arbitrary scripts inside the configured Docker sandbox.",
                                             script, ["name", "command"]))
        if subagent:
            self.schemas.append(self._schema("subagent", "Delegate a named task; only its final report returns. "
                                             "Prefer delegation for a bounded task that can be completed from "
                                             "explicit inputs and handed back as findings with concrete evidence, "
                                             "such as multi-step investigation, log analysis, codebase exploration "
                                             "or independent review. Use direct tools for simple targeted lookups; "
                                             "keep tightly coupled steps that need frequent decisions or new "
                                             "information from the parent conversation in the main agent. "
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
                if key == "timeout_seconds":
                    if name == "shell" and values.get("run_in_background") is True:
                        try:
                            valid = type(value) in (int, float) and value > 0 and math.isfinite(value)
                        except OverflowError:
                            valid = False
                        if not valid:
                            raise ValueError("Background timeout_seconds must be a positive finite number")
                    else:
                        validate_timeout(value)
                    continue
                expected_type = bool if field["type"] == "boolean" else str
                if not isinstance(value, expected_type):
                    raise ValueError(f"Argument {key} must be a {field['type']}")
                if isinstance(value, str) and not value.strip():
                    raise ValueError(f"Argument {key} must not be empty")
                if "enum" in field and value not in field["enum"]:
                    raise ValueError(f"Invalid {key}: {value}")
            if name == "shell" and values.get("background_lifecycle") == "service" and not values["run_in_background"]:
                raise ValueError("background_lifecycle=service requires run_in_background=true")
            try:
                self.on_activity({"kind": "tool", "name": name, "task_name": values["name"]})
                if name == "shell":
                    environment = None
                    if self.process_scope is not None:
                        environment = {**os.environ, SCOPE_ENV: str(self.process_scope.path)}
                        for key, value in ((CONTEXT_ENV, self.process_context),
                                           (CALL_ENV, self.tool_call_id)):
                            if value is not None:
                                environment[key] = value
                    if values["run_in_background"]:
                        if self.process_scope is None:
                            raise ValueError("Background shell execution requires an active agent process scope")
                        result = self.process_scope.start_background(
                            values["command"], values["name"], cwd=self.workspace,
                            env=environment, timeout_seconds=values.get("timeout_seconds"),
                            background_lifecycle=values.get("background_lifecycle", "task"),
                        )
                    else:
                        result = run_command(
                            values["command"], shell=True, cwd=self.workspace,
                            env=environment,
                            timeout_seconds=values.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                            log_dir=self.log_dir,
                        )
                elif name == "sandbox":
                    result = self.sandbox.run(
                        values["command"], timeout_seconds=values.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                        log_dir=self.log_dir,
                    )
                else:
                    result = {"result": self.subagent(values["name"], values["task"], history=values.get("history", False))}
            finally:
                self.on_activity(None)
        except ToolCleanupError:
            raise
        except Exception as exc:
            result = {"error": str(exc)}
        return json.dumps(result, ensure_ascii=False)
