"""Standard-library evidence primitives; no workspace imports or process management."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time


MAX_HASH_BYTES = 256 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def absolute(path, cwd=None):
    value = Path(path).expanduser()
    return Path(os.path.abspath(value if value.is_absolute() else Path(cwd or Path.cwd()) / value))


def stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def bytes_fingerprint(path, data):
    """Identify these consumed bytes, without claiming an atomic filesystem snapshot."""
    return {"path": str(absolute(path)), "status": "ok", "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "capture": "consumed_bytes"}


def fingerprint(path, max_bytes=MAX_HASH_BYTES, max_seconds=10):
    path = absolute(path)
    result = {"path": str(path), "status": "unknown", "capture": "file_boundary"}
    started = time.monotonic()
    try:
        link_before = path.lstat()
        if stat.S_ISLNK(link_before.st_mode):
            result["symlink_target"] = os.readlink(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            return {**result, "reason": "Not a regular file; declare individual files."}
        if info.st_size > max_bytes:
            return {**result, "reason": "hash_byte_budget", "size": info.st_size}
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            while block := stream.read(min(1024 * 1024, max_bytes - size + 1)):
                size += len(block)
                if size > max_bytes or time.monotonic() - started > max_seconds:
                    return {**result, "reason": "hash_budget", "bytes_read": size}
                digest.update(block)
            after = os.fstat(stream.fileno())
        if (stat_identity(before) != stat_identity(after)
                or stat_identity(after) != stat_identity(path.stat())
                or stat_identity(link_before) != stat_identity(path.lstat())):
            return {**result, "status": "unstable", "reason": "File changed during capture."}
        if time.monotonic() - started > max_seconds:
            return {**result, "reason": "hash_time_budget"}
        result.update(status="ok", sha256=digest.hexdigest(), size=size,
                      resolved_path=str(path.resolve()))
    except FileNotFoundError as exc:
        result.update(status="missing", reason=str(exc))
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(reason=str(exc))
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize before opening a file so an invalid value cannot replace a record.
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def normalize_manifest(manifest, cwd):
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    allowed = {"subjects", "dependencies", "outputs", "environment", "limits"}
    if set(manifest) - allowed:
        raise ValueError("Unknown manifest fields: " + ", ".join(sorted(set(manifest) - allowed)))
    result = {}
    for role in ("subjects", "dependencies", "outputs"):
        entries = manifest.get(role, [])
        if not isinstance(entries, list):
            raise ValueError(f"manifest.{role} must be a list")
        result[role] = []
        for entry in entries:
            entry = {"path": entry} if isinstance(entry, str) else entry
            if (not isinstance(entry, dict) or set(entry) - {"path", "label"}
                    or not isinstance(entry.get("path"), str) or not entry["path"]
                    or ("label" in entry and not isinstance(entry["label"], str))):
                raise ValueError(f"manifest.{role}: expected a path or {{path, label}}")
            result[role].append({**entry, "path": str(absolute(entry["path"], cwd))})
    names = manifest.get("environment", [])
    if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("manifest.environment must list explicit variable names")
    result["environment"] = list(dict.fromkeys(names))
    limits = manifest.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - {"max_bytes", "max_seconds"}:
        raise ValueError("manifest.limits accepts max_bytes and max_seconds")
    max_bytes, max_seconds = limits.get("max_bytes", MAX_HASH_BYTES), limits.get("max_seconds", 10)
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("max_seconds must be finite and positive")
    result["limits"] = {"max_bytes": max_bytes, "max_seconds": max_seconds}
    return result


def capture_manifest(manifest, helpers):
    result = []
    for role in ("subjects", "dependencies", "outputs", "helpers"):
        entries = helpers if role == "helpers" else manifest[role]
        for entry in entries:
            result.append({**fingerprint(entry["path"], **manifest["limits"]),
                           "role": role, **({"label": entry["label"]} if "label" in entry else {})})
    return result


def identity_relation(left, right):
    if left.get("status") != "ok" or right.get("status") != "ok":
        return "unknown"
    keys = ("sha256", "size", "symlink_target")
    return "same" if all(left.get(key) == right.get(key) for key in keys) else "different"


def command_launcher(command, cwd):
    if not command:
        return None
    if os.path.dirname(command[0]):
        return shutil.which(str(absolute(command[0], cwd)))
    # Relative PATH entries are resolved by the child after changing cwd.
    search_path = os.pathsep.join(str(absolute(item or ".", cwd)) for item in os.get_exec_path())
    return shutil.which(command[0], path=search_path)


def start_record(root, kind, manifest=None, command=None, scope=None, cwd=None, helper_paths=()):
    cwd = absolute(cwd or Path.cwd())
    manifest = normalize_manifest({} if manifest is None else manifest, cwd)
    root = absolute(root)
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    path = directory / "record.json"
    helpers = [{"path": str(absolute(item))} for item in dict.fromkeys([__file__, *helper_paths])]
    record = {"schema_version": 1, "record_id": directory.name, "kind": kind,
              "record_state": "started", "started_at": utc_now(),
              "scope": scope, "dependency_scope": "declared", "manifest": manifest,
              "command": {"argv": command, "cwd": str(cwd)},
              "runtime": {"helper_python": sys.executable, "helper_python_version": sys.version,
                          "command_launcher": command_launcher(command, cwd),
                          "environment_scope": "selected_variables_only"},
              "links": {key: os.environ[key] for key in (
                  "SKILLS_AGENT_PROCESS_SCOPE", "SKILLS_AGENT_PROCESS_CONTEXT", "SKILLS_AGENT_TOOL_CALL_ID")
                  if key in os.environ},
              "environment": {name: os.environ.get(name) for name in manifest["environment"]},
              "helpers": helpers, "execution": {"state": "unfinished"},
              "capture": {"mode": "boundary", "before": [], "after": [],
                          "completion_boundary": "entry_process_exit",
                          "descendant_quiescence": "unobserved", "relation": "unknown"}}
    atomic_json(path, record)
    record["capture"]["before"] = capture_manifest(manifest, helpers)
    atomic_json(path, record)
    return {"path": str(path), "record": record}


def finish_record(handle, execution, observations=None, consumed=None):
    record = handle["record"]
    capture = record["capture"]
    capture["after"] = capture_manifest(record["manifest"], record["helpers"])
    relevant = [(a, b) for a, b in zip(capture["before"], capture["after"])
                if a["role"] != "outputs"]
    relations = [identity_relation(a, b) for a, b in relevant]
    capture["relation"] = ("different" if "different" in relations else
                           "same" if relations and all(item == "same" for item in relations) else "unknown")
    capture["consumed"] = consumed or []
    if consumed:
        capture["mode"] = "consumed_bytes_with_dependency_boundaries"
    actual = dict(execution)
    if "state" not in actual:
        actual["state"] = ("start_failed" if actual.get("startup_error") else
                           "timed_out" if actual.get("timed_out") else
                           "exited" if actual.get("exit_code") is not None else "unknown")
    record.update(execution=actual, observations=observations or {"verdict": "not_reported"},
                  completed_at=utc_now(), record_state="finalized")
    atomic_json(handle["path"], record)
    return Path(handle["path"])


def status_record(path):
    path = absolute(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or not isinstance(record.get("record_state"), str)
            or record.get("record_state") not in {"started", "finalized"}
            or not isinstance(record.get("capture"), dict)
            or not isinstance(record.get("manifest"), dict)):
        raise ValueError("Not a supported verification record")
    capture = record["capture"]
    manifest = normalize_manifest(record["manifest"], path.parent)
    for key in ("before", "after", "consumed"):
        entries = capture.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(f"Invalid record capture.{key}")
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                    or not Path(entry["path"]).is_absolute()
                    or not isinstance(entry.get("status"), str)
                    or entry.get("status") not in {"ok", "missing", "unknown", "unstable"}
                    or (key != "consumed" and (not isinstance(entry.get("role"), str)
                        or entry.get("role") not in {"subjects", "dependencies", "outputs", "helpers"}))):
                raise ValueError(f"Invalid file identity in capture.{key}")
            if entry["status"] == "ok" and (
                    not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in entry["sha256"])
                    or type(entry.get("size")) is not int or entry["size"] < 0):
                raise ValueError(f"Invalid content digest in capture.{key}")
    environment = record.get("environment", {})
    if not isinstance(environment, dict) or any(
            not isinstance(name, str) or (value is not None and not isinstance(value, str))
            for name, value in environment.items()):
        raise ValueError("Invalid record environment")
    finalized = record.get("record_state") == "finalized"
    before_rows, after_rows = capture.get("before", []), capture.get("after", [])
    if finalized and (len(before_rows) != len(after_rows) or any(
            a["path"] != b["path"] or a["role"] != b["role"] for a, b in zip(before_rows, after_rows))):
        raise ValueError("Finalized record has inconsistent boundary captures")
    consumed = {item["path"]: item for item in capture.get("consumed", [])}
    rows = []
    for before, after in zip(capture.get("before", []), capture.get("after", [])):
        baseline = after if before["role"] == "outputs" else before
        if before["role"] in {"subjects", "dependencies"} and before["path"] in consumed:
            baseline = consumed[before["path"]]
        current = fingerprint(baseline["path"], **manifest["limits"])
        relation = identity_relation(baseline, current)
        rows.append({"path": baseline["path"], "role": before["role"],
                     "relation": "missing" if current["status"] == "missing" else relation,
                     "reference": baseline, "current": current})
    env = [{"name": name, "relation": "same" if os.environ.get(name) == value else "different"}
           for name, value in record.get("environment", {}).items()]
    relations = [row["relation"] for row in [*rows, *env]]
    relation = ("unknown" if not finalized or not rows else
                "different" if any(item in ("different", "missing") for item in relations) else
                "unknown" if "unknown" in relations else "same")
    return {"record": str(path), "record_state": record.get("record_state"),
            "historical_execution": record.get("execution"),
            "historical_observations": record.get("observations"),
            "capture_mode": capture.get("mode"),
            "boundary_relation": capture.get("relation", "unknown"),
            "current_relation": relation, "files": rows, "environment": env,
            "scope": record.get("scope"), "dependency_scope": "declared",
            "note": ("Current equality does not strengthen an uncertain historical binding: inspect capture_mode "
                     "and boundary_relation. Version relations do not decide correctness, coverage, or whether to rerun.")}
