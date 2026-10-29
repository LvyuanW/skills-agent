"""Execution, recorded file edits and named delegated tasks."""

import json
import math
import os
from pathlib import Path

from ..execution import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, ToolCleanupError, run_command, validate_timeout
from ..feedback import validate_images
from ..images import MAX_REQUEST_IMAGES
from ..processes import CALL_ENV, CONTEXT_ENV, SCOPE_ENV


class ToolRegistry:
    def __init__(self, workspace: Path, sandbox=None, subagent=None, *, on_activity=None, log_dir=None,
                 file_history=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.sandbox, self.subagent = sandbox, subagent
        self.file_history = file_history
        self.edit_session_id = None
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
                            "description": "auto: original up to 8192 tokens, otherwise report when token counts "
                            "are available; preserve the original when length is unknown. "
                            "direct: exact source (skills/code/data), or a source reference if advance "
                            "counting shows it cannot fit. "
                            "report: concise conclusions with evidence. Default auto."},
        }
        shell_script = {**script,
            "images": {"type": "array", "maxItems": MAX_REQUEST_IMAGES, "description":
                "Optional images to inspect after this command completes. Read the images skill first. "
                f"Use at most {MAX_REQUEST_IMAGES} images, each at most 1 MB. Paths are absolute host paths or file:// URLs; "
                "they may be produced by the command. Background images are attached only on completion, "
                "never to the launch receipt. An explicit list, including [], takes precedence over "
                "image declarations in stdout.", "items": {
                    "type": "object", "required": ["path"], "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string", "description": "Absolute host path or file:// URL."},
                        "label": {"type": "string", "description": "Optional image label."},
                        "source_url": {"type": "string", "description": "Optional original file:// or HTTP(S) URL."},
                    },
                }},
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
                                    "Read files, run programs and inspect results. Use edit_file to create or "
                                    "change scripts and other files; do not edit files through shell commands. "
                                    "Filter large data in your script and print relevant evidence.",
                                    shell_script, ["name", "command", "run_in_background"])]
        if file_history is not None:
            self.schemas.append(self._schema(
                "edit_file", "Create, precisely edit, delete or move a file in the workspace. "
                "The runtime saves the original and resulting contents in separate file history. "
                "Read current contents with shell before changing an existing file. "
                "replace requires one exact match (empty old_text is allowed only for an empty file); "
                "refine ambiguous matches instead of "
                "rewriting unrelated contents. create and move refuse to overwrite an existing path. "
                "Parent directories must exist; shell may create directories with mkdir. "
                "Paths must be inside the workspace and must not traverse symbolic links. "
                "Use this tool for direct file changes, including scripts and temporary text files.", {
                    "name": {"type": "string", "description": "Short action and purpose, shown to the user."},
                    "operation": {"type": "string", "enum": ["create", "replace", "delete", "move"]},
                    "path": {"type": "string", "description": "Workspace-relative or absolute file path."},
                    "content": {"type": "string", "description": "create only: complete UTF-8 text; may be empty."},
                    "old_text": {"type": "string", "description": "replace only: exact current text occurring once; empty only if the file is empty."},
                    "new_text": {"type": "string", "description": "replace only: replacement text; empty deletes the match."},
                    "destination": {"type": "string", "description": "move only: unused destination file path."},
                }, ["name", "operation", "path"]))
        if sandbox:
            self.schemas.append(self._schema("sandbox", "Run programs inside the configured Docker sandbox. "
                                             "Use edit_file for direct file changes; do not use sandbox commands "
                                             "to bypass the file editing tool.",
                                             script, ["name", "command"]))
        if subagent:
            self.schemas.append(self._schema("subagent", "Delegate a named task; only its final report returns. "
                                             "Prefer delegation for a bounded task that can be completed from "
                                             "explicit inputs and handed back as findings with concrete evidence, "
                                             "such as multi-step investigation, log analysis or codebase exploration. "
                                             "Use direct tools for simple targeted lookups; "
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
        executed = False
        display_error = None
        try:
            schema = next((item for item in self.schemas if item["name"] == name), None)
            if schema is None:
                raise ValueError(f"Unknown tool: {name}")
            try:
                values = json.loads(arguments)
            except (ValueError, TypeError, RecursionError):
                raise ValueError("Tool arguments must be valid JSON; correct them and call the tool again") from None
            if not isinstance(values, dict):
                raise ValueError("Tool arguments must be a JSON object; correct them and call the tool again")
            expected, allowed = schema["parameters"]["required"], schema["parameters"]["properties"]
            if not set(expected) <= set(values) <= set(allowed):
                raise ValueError(f"Expected arguments: {', '.join(expected)}")
            for key, value in values.items():
                field = allowed[key]
                if key == "images":
                    values[key] = validate_images(value)
                    continue
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
                if (isinstance(value, str) and not value.strip()
                        and not (name == "edit_file" and key in {"content", "old_text", "new_text"})):
                    raise ValueError(f"Argument {key} must not be empty")
                if "enum" in field and value not in field["enum"]:
                    raise ValueError(f"Invalid {key}: {value}")
            if name == "shell" and values.get("background_lifecycle") == "service" and not values["run_in_background"]:
                raise ValueError("background_lifecycle=service requires run_in_background=true")
            if name == "edit_file":
                fields = {"create": {"content"}, "replace": {"old_text", "new_text"},
                          "delete": set(), "move": {"destination"}}[values["operation"]]
                if set(values) != {"name", "operation", "path"} | fields:
                    raise ValueError(f"{values['operation']} requires exactly: " +
                                     ", ".join(sorted({"name", "operation", "path"} | fields)))
            try:
                self.on_activity({"kind": "tool", "name": name, "task_name": values["name"]})
                executed = True
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
                            images=values.get("images"),
                        )
                    else:
                        result = run_command(
                            values["command"], shell=True, cwd=self.workspace,
                            env=environment,
                            timeout_seconds=values.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                            log_dir=self.log_dir,
                        )
                        if "images" in values:
                            result["images"] = values["images"]
                elif name == "edit_file":
                    result = self.file_history.edit(
                        values["operation"], values["path"], tool_call_id=self.tool_call_id,
                        session_id=self.edit_session_id,
                        **{key: values[key] for key in ("content", "old_text", "new_text", "destination")
                           if key in values},
                    )
                elif name == "sandbox":
                    result = self.sandbox.run(
                        values["command"], timeout_seconds=values.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                        log_dir=self.log_dir,
                    )
                else:
                    result = {"result": self.subagent(values["name"], values["task"], history=values.get("history", False))}
            finally:
                if name == "edit_file":
                    try:
                        self.on_activity(None)
                    except Exception as exc:
                        # A failed display update must not erase an applied
                        # edit's result or its recovery references.
                        display_error = str(exc)
                else:
                    self.on_activity(None)
        except ToolCleanupError:
            raise
        except Exception as exc:
            result = {"error": str(exc)}
            if name == "edit_file" and isinstance(getattr(exc, "details", None), dict):
                result.update(exc.details)
            if not executed:
                result["executed"] = False
        if display_error is not None:
            result["display_error"] = display_error
        return json.dumps(result, ensure_ascii=name == "edit_file")
