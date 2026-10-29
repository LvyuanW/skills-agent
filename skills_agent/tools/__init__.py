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
                "edit_file", "Edit one or more files in a single recorded batch. Use edits with one item "
                "for a simple change, or several items for related changes in the same or different files. "
                "Items run in order against the preceding items' results. All are validated before any "
                "file is written; a successful changed batch creates one history commit and retains its "
                "original contents. An execution error can leave partial changes; inspect the result. "
                "Text fields accept complete multiline code, including GLSL shaders, HTML, Python and "
                "configuration; use JSON newline escapes, not shell quoting or heredocs. "
                "create makes a new file; write creates or replaces the complete contents; replace "
                "changes literal text (one match by default, all matches with replace_all=true); insert "
                "adds text beside an exact anchor; delete removes a file; move/copy use destination. "
                "Use content for text or content_base64 for arbitrary binary contents. Read existing "
                "contents before editing and preserve unrelated work; use write when a full replacement "
                "is intended. Absolute paths may be in any directory; relative paths use the working "
                "directory. Missing parent directories are created as needed. Content edits follow "
                "symlinks; delete/move/copy act on the link itself. The runtime's own active history "
                "and session files are protected. Use this tool for direct file changes, including "
                "temporary scripts, instead of shell editing commands.", {
                    "name": {"type": "string", "description": "Short action and purpose, shown to the user."},
                    "edits": {"type": "array", "minItems": 1, "description":
                              "One or more edits, applied in the listed order as one history entry.",
                              "items": {"type": "object", "additionalProperties": False,
                                        "required": ["operation", "path"], "properties": {
                        "operation": {"type": "string", "enum": ["create", "write", "replace", "insert", "delete", "move", "copy"]},
                        "path": {"type": "string", "description": "Absolute path anywhere, or relative to the working directory."},
                        "content": {"type": "string", "description": "create/write: complete text; insert: inserted text. May contain multiple lines or be empty."},
                        "content_base64": {"type": "string", "description": "create/write: base64-encoded bytes instead of content, including binary files."},
                        "old_text": {"type": "string", "description": "replace: exact current text, including multiline blocks. Empty only when the file is empty."},
                        "new_text": {"type": "string", "description": "replace: replacement text, including multiline blocks; empty removes the matched text."},
                        "replace_all": {"type": "boolean", "description": "replace: replace every non-overlapping literal match. Default false requires one match."},
                        "expected_count": {"type": "integer", "minimum": 1, "description": "replace: optional expected match count; a mismatch rejects the batch."},
                        "anchor": {"type": "string", "description": "insert: exact nonempty text occurring once; can be multiline."},
                        "position": {"type": "string", "enum": ["before", "after"], "description": "insert: which side of the anchor receives content."},
                        "encoding": {"type": "string", "description": "Text encoding, default utf-8; available for content, replace and insert. Not with content_base64."},
                        "destination": {"type": "string", "description": "move/copy: destination path, absolute or relative to the working directory."},
                        "overwrite": {"type": "boolean", "description": "move/copy: replace an existing destination when true; default false."},
                    }}},
                }, ["name", "edits"]))
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
            if name == "edit_file" and "edits" not in values and "operation" in values:
                # Older saved calls remain executable; new schemas expose one
                # uniform batch form instead of two competing interfaces.
                values = {**({"name": values["name"]} if "name" in values else {}),
                          "edits": [{key: value for key, value in values.items() if key != "name"}]}
            expected, allowed = schema["parameters"]["required"], schema["parameters"]["properties"]
            if not set(expected) <= set(values) <= set(allowed):
                raise ValueError(f"Expected arguments: {', '.join(expected)}")
            for key, value in values.items():
                field = allowed[key]
                if key == "images":
                    values[key] = validate_images(value)
                    continue
                if name == "edit_file" and key == "edits":
                    self._validate_edits(value, field["items"]["properties"])
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
            try:
                self.on_activity({"kind": "tool", "name": name, "task_name": values["name"]})
                executed = True
                if name == "shell":
                    environment = {**os.environ, "PWD": str(self.workspace)}
                    environment.pop("OLDPWD", None)
                    if self.process_scope is not None:
                        environment[SCOPE_ENV] = str(self.process_scope.path)
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
                    result = self.file_history.edit_batch(
                        values["edits"], tool_call_id=self.tool_call_id, session_id=self.edit_session_id,
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

    @staticmethod
    def _validate_edits(edits, fields):
        if not isinstance(edits, list) or not edits:
            raise ValueError("edits must be a nonempty array")
        for index, edit in enumerate(edits, 1):
            if not isinstance(edit, dict) or not {"operation", "path"} <= set(edit) <= set(fields):
                raise ValueError(f"Edit {index}: provide operation and path with supported fields")
            for key, value in edit.items():
                field = fields[key]
                expected = {"string": str, "boolean": bool, "integer": int}[field["type"]]
                if not isinstance(value, expected) or expected is int and isinstance(value, bool):
                    raise ValueError(f"Edit {index}: {key} must be a {field['type']}")
                if "enum" in field and value not in field["enum"]:
                    raise ValueError(f"Edit {index}: invalid {key}: {value}")
                if key in {"path", "destination", "encoding", "anchor"} and not value:
                    raise ValueError(f"Edit {index}: {key} must not be empty")
                if key == "expected_count" and value < 1:
                    raise ValueError(f"Edit {index}: expected_count must be positive")
            operation = edit["operation"]
            required, optional = {
                "create": (set(), {"content", "content_base64", "encoding"}),
                "write": (set(), {"content", "content_base64", "encoding"}),
                "replace": ({"old_text", "new_text"}, {"replace_all", "expected_count", "encoding"}),
                "insert": ({"anchor", "position", "content"}, {"encoding"}),
                "delete": (set(), set()),
                "move": ({"destination"}, {"overwrite"}),
                "copy": ({"destination"}, {"overwrite"}),
            }[operation]
            arguments = set(edit) - {"operation", "path"}
            if not required <= arguments <= required | optional:
                raise ValueError(f"Edit {index}: invalid fields for {operation}")
            if operation in {"create", "write"}:
                if len(arguments & {"content", "content_base64"}) != 1:
                    raise ValueError(f"Edit {index}: provide exactly one of content or content_base64")
                if "content_base64" in edit and "encoding" in edit:
                    raise ValueError(f"Edit {index}: encoding applies to text content, not content_base64")
