"""Optional content-version records for the existing native shell boundary.

This module also loads by file path in the standalone background supervisor.
No record or hashing is performed unless a caller supplies an evidence declaration.
"""

import importlib.util
import json
import os
from pathlib import Path
import tempfile


def _backend():
    path = Path(__file__).parent / "builtin_skills/feedback/scripts/_evidence.py"
    spec = importlib.util.spec_from_file_location("_native_version_evidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def schema():
    return {
        "type": "object", "additionalProperties": False,
        "description": "Optional file-version record for this command. Use when later edits or expensive "
                       "checks make prior evidence worth reusing. Declare absolute file paths; shell cd "
                       "does not change these bindings. This records execution, not correctness, and "
                       "never reruns a check. Omit for ordinary commands.",
        "properties": {
            **{role: {"type": "array", "items": {"type": "string"}, "description": description}
               for role, description in (
                   ("subjects", "Absolute paths of files being checked."),
                   ("dependencies", "Absolute paths of relevant scripts, fixtures or references."),
                   ("outputs", "Absolute paths of produced files; captured after completion."))},
            "scope": {"type": "string", "description": "The particular observation or property this run covers."},
            "environment": {"type": "array", "items": {"type": "string"},
                            "description": "Optional nonsecret environment variable names to record at launch."},
            "record_dir": {"type": "string", "description": "Optional absolute record directory; defaults to runtime logs."},
        },
    }


def validate_declaration(value):
    if not isinstance(value, dict) or set(value) - set(schema()["properties"]):
        raise ValueError("evidence must contain only subjects, dependencies, outputs, scope, environment or record_dir")
    result = {}
    for role in ("subjects", "dependencies", "outputs", "environment"):
        entries = value.get(role, [])
        if not isinstance(entries, list) or any(not isinstance(item, str) or not item.strip() or "\0" in item
                                                 for item in entries):
            raise ValueError(f"evidence.{role} must be a list of nonempty strings")
        if role != "environment" and any(not Path(item).is_absolute() for item in entries):
            raise ValueError(f"evidence.{role} must use absolute paths; shell cd cannot resolve bindings")
        result[role] = list(dict.fromkeys(entries))
    for key in ("scope", "record_dir"):
        if key in value:
            if not isinstance(value[key], str) or not value[key].strip() or "\0" in value[key]:
                raise ValueError(f"evidence.{key} must be a nonempty string")
            result[key] = value[key]
    if "record_dir" in result and not Path(result["record_dir"]).is_absolute():
        raise ValueError("evidence.record_dir must be an absolute path")
    return result


def begin(declaration, command, cwd, *, log_dir=None, environment=None):
    value = validate_declaration(declaration)
    backend = _backend()
    root = (value.get("record_dir") or
            (str(Path(log_dir) / "evidence") if log_dir is not None else
             tempfile.mkdtemp(prefix="skills-agent-evidence-")))
    manifest = {key: value[key] for key in ("subjects", "dependencies", "outputs", "environment")}
    handle = backend.start_record(root, "native-shell", manifest, command, value.get("scope"), cwd,
                                  helper_paths=[__file__])
    # The shell can receive ownership variables absent in the parent's environment.
    environment = os.environ if environment is None else environment
    handle["record"]["environment"] = {name: environment.get(name) for name in value["environment"]}
    handle["record"]["links"] = {key: environment[key] for key in (
        "SKILLS_AGENT_PROCESS_SCOPE", "SKILLS_AGENT_PROCESS_CONTEXT", "SKILLS_AGENT_TOOL_CALL_ID")
        if key in environment}
    backend.atomic_json(handle["path"], handle["record"])
    return handle


def summary(handle, *, pending=False):
    record = handle["record"]
    count = sum(len(record["manifest"][role]) for role in ("subjects", "dependencies", "outputs"))
    return {"record": str(handle["path"]), "record_state": "pending" if pending else record["record_state"],
            "task_binding": "declared" if count else "unbound", "task_file_count": count,
            "scope": record.get("scope"), "verdict": "not_reported",
            "note": "Declared file versions only; execution success is not a correctness verdict."}


def load_handle(path):
    path = Path(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("kind") != "native-shell":
        raise ValueError("Not a native shell evidence record")
    return {"path": path, "record": record}


def finish(handle, result):
    """Capture after the native owner confirms termination, not at result delivery."""
    if handle["record"]["record_state"] == "finalized":
        return summary(handle)
    backend = _backend()
    execution = {key: result[key] for key in (
        "exit_code", "process_returncode", "timed_out", "duration_seconds", "startup_error", "error",
        "stdout_path", "stderr_path", "cleanup_confirmed", "cleanup_required", "cleanup_forced",
        "cleanup_reason", "cleanup_message", "cleanup_initial_process_count", "cleanup_process_count",
        "stop_reason", "started_at", "ended_at") if key in result}
    execution["state"] = ("start_failed" if result.get("startup_error") else
                          "timed_out" if result.get("timed_out") else
                          "stopped" if result.get("state") == "stopped" else
                          "exited" if result.get("exit_code") is not None else "unknown")
    handle["record"]["capture"].update(
        completion_boundary="native_process_group_cleanup",
        descendant_quiescence="owned_process_group_confirmed" if result.get("cleanup_confirmed") else "unobserved")
    backend.finish_record(handle, execution, {"verdict": "not_reported"})
    return summary(handle)


def finish_background(record):
    """Called before publishing a confirmed terminal process record."""
    path = record.get("evidence_record")
    if not path or record.get("evidence", {}).get("record_state") in {"finalized", "record_error"}:
        return
    try:
        handle = load_handle(path)
        result = {**record, "stdout_path": record["stdout"], "stderr_path": record["stderr"],
                  "process_returncode": record.get("returncode")}
        if record.get("child_pid") is None:
            result["startup_error"] = record.get("error") or "Command never started"
        record["evidence"] = finish(handle, result)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        record["evidence"] = {"record": path, "record_state": "record_error", "error": str(exc),
                              "verdict": "not_reported"}
