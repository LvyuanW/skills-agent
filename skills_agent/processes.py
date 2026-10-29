"""Ownership scopes for background helpers started during one agent turn."""

import json
import hashlib
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from .execution import ToolCleanupError
from .feedback import validate_images


SCOPE_ENV = "SKILLS_AGENT_PROCESS_SCOPE"
CONTEXT_ENV = "SKILLS_AGENT_PROCESS_CONTEXT"
CALL_ENV = "SKILLS_AGENT_TOOL_CALL_ID"
BACKGROUND_STARTUP_TIMEOUT_SECONDS = 10
HELPER = Path(__file__).parent / "builtin_skills/process-management/scripts/process.py"
_TERMINAL = {"exited", "failed", "stopped", "timed_out"}


class BackgroundJobError(ToolCleanupError):
    """A registered job's outcome or ownership cannot be safely established."""


def _regular_bytes(path, *, maximum, tail=False):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise BackgroundJobError(f"Expected a regular background-job file: {path}")
        if tail:
            os.lseek(fd, max(0, info.st_size - maximum), os.SEEK_SET)
        elif info.st_size > maximum:
            raise BackgroundJobError(f"Background-job metadata is too large: {path}")
        data = os.read(fd, maximum)
        return data, info.st_size
    finally:
        os.close(fd)


def _strict_registration(path):
    try:
        record = json.loads(_regular_bytes(path, maximum=1024 * 1024)[0])
        token, name = record.get("token"), record.get("name")
        if (record.get("version") != 1 or not isinstance(token, str)
                or not re.fullmatch(r"[0-9a-f]{32}", token) or token != path.stem
                or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name)
                or record.get("process_scope") != str(path.parent.parent)):
            raise ValueError("scope, name or ownership token mismatch")
        alias = Path(record["record"])
        artifacts = alias.parent / f"{name}.{token}"
        if (not alias.is_absolute() or alias.name != f"{name}.json"
                or record.get("artifacts") != str(artifacts)
                or artifacts.resolve() != artifacts
                or record.get("stdout") != str(artifacts / "stdout.log")
                or record.get("stderr") != str(artifacts / "stderr.log")):
            raise ValueError("artifact paths do not match the registered run")
        if record.get("state") not in _TERMINAL | {"starting", "running", "stopping"}:
            raise ValueError("job state or cleanup is unconfirmed")
        kind = record.get("kind", "job")
        if kind not in ("job", "service") or record.get("wait_on_exit", kind == "job") is not (kind == "job"):
            raise ValueError("invalid job kind or wait_on_exit flag")
        if record.get("background_lifecycle", "service" if kind == "service" else "task") != ("service" if kind == "service" else "task"):
            raise ValueError("invalid background_lifecycle")
        for field in ("owner_session_id", "origin_tool_call_id"):
            value = record.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"invalid {field}")
        for field in ("cleanup_required", "cleanup_confirmed", "cleanup_forced", "timed_out", "persistent"):
            if field in record and not isinstance(record[field], bool):
                raise ValueError(f"invalid {field}")
        if ("cleanup_process_count" in record and
                (not isinstance(record["cleanup_process_count"], int)
                 or isinstance(record["cleanup_process_count"], bool)
                 or record["cleanup_process_count"] < 0)):
            raise ValueError("invalid cleanup_process_count")
        if "display_name" in record and (not isinstance(record["display_name"], str)
                                          or not record["display_name"].strip()):
            raise ValueError("invalid display_name")
        if "images" in record:
            record["images"] = validate_images(record["images"])
        for field in ("job_timeout_seconds", "startup_timeout_seconds"):
            value = record.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value <= 0):
                raise ValueError(f"invalid {field}")
        for field in ("returncode", "exit_code"):
            code = record.get(field)
            if code is not None and (not isinstance(code, int) or isinstance(code, bool)):
                raise ValueError(f"invalid {field}")
        if record["state"] in _TERMINAL:
            if record.get("returncode") is None:
                raise ValueError("terminal return code is unavailable")
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
        raise BackgroundJobError(f"Cannot verify background job {path}: {exc}") from exc


def observe_background_jobs(scope_paths: list[Path], *, tolerate_unconfirmed=False) -> list[dict]:
    """Verify jobs; the Agent can retain uncertainty as observable pending work."""
    records, jobs, valid_scopes = [], [], []
    for original_scope in sorted({Path(path) for path in scope_paths}):
        try:
            scope = ProcessScope.attach(original_scope).path
            valid_scopes.append(scope)
            for path in sorted((scope / "runs").glob("*.json")):
                try:
                    records.append((path, _strict_registration(path)))
                except BackgroundJobError as exc:
                    if not tolerate_unconfirmed:
                        raise
                    jobs.append(_unconfirmed_job(path, scope, exc))
        except (BackgroundJobError, OSError) as exc:
            if not tolerate_unconfirmed:
                raise
            jobs.append(_unconfirmed_job(original_scope / "scope.json", original_scope, exc))
            # Even a damaged scope header must not hide its existing run IDs.
            # Do not inspect directories whose ownership is a symbolic link.
            if not any(path.is_symlink() for path in (
                    original_scope, original_scope.parent, original_scope / "runs")):
                jobs.extend(_unconfirmed_job(path, original_scope, exc)
                            for path in sorted((original_scope / "runs").glob("*.json")))
    supervisors = _supervisors([record for _, record in records])
    if supervisors is None and not tolerate_unconfirmed:
        raise BackgroundJobError("Background supervisor identity probe failed")
    for path, record in records:
        try:
            jobs.append(_observed_job(path, record, supervisors))
        except BackgroundJobError as exc:
            if not tolerate_unconfirmed:
                raise
            jobs.append(_unconfirmed_job(path, path.parent.parent, exc, record))
    if tolerate_unconfirmed:
        _observe_launch_expectations(valid_scopes, records, jobs)
        for scope in valid_scopes:
            # Resolve the header-level diagnostic only. Missing or unconfirmed
            # individual runs retain their own independent completion gates.
            identifier = hashlib.sha256(f"{scope}/scope".encode()).hexdigest()
            resolved = [identifier]
            directory = scope / "launches"
            if not directory.is_symlink() and (not directory.exists() or directory.is_dir()):
                resolved.append(hashlib.sha256(f"{scope}/launches".encode()).hexdigest())
            jobs.append({"id": identifier + "-verification", "state": "verified_scope",
                         "_verification_only": True, "_scope_path": str(scope),
                         "resolved_uncertainty_ids": resolved})
    return sorted(jobs, key=lambda job: (str(job.get("created_at") or ""), job["id"]))


def _observed_job(path, record, supervisors):
    state = record["state"]
    if state not in _TERMINAL:
        if supervisors is None:
            raise BackgroundJobError("Background supervisor identity probe failed")
        if not _owns_supervisor(record, supervisors):
            record = _strict_registration(path)
            state = record["state"]
            created = _timestamp(record.get("created_at"))
            startup = record.get("startup_timeout_seconds", BACKGROUND_STARTUP_TIMEOUT_SECONDS)
            starting = (state == "starting" and record.get("supervisor_pid") is None
                        and created is not None and isinstance(startup, (int, float))
                        and 0 <= datetime.now(timezone.utc).timestamp() - created <= startup + 2)
            if state not in _TERMINAL and not starting:
                raise BackgroundJobError(f"Background supervisor ownership cannot be confirmed: {record['name']}")
    if state in _TERMINAL and record.get("cleanup_confirmed") is not True:
        raise BackgroundJobError(f"Background cleanup was not confirmed: {record['name']}")
    code = record.get("returncode")
    job = {key: value for key, value in record.items()
           if key not in {"token", "supervisor_identity", "stdout", "stderr"}}
    job.update(id=hashlib.sha256(f"{record['process_scope']}/{record['token']}".encode()).hexdigest(),
               kind=record.get("kind", "job"), wait_on_exit=record.get("wait_on_exit", record.get("kind", "job") == "job"),
               background_lifecycle=record.get("background_lifecycle", "service" if record.get("kind") == "service" else "task"),
               owner_session_id=record.get("owner_session_id"), origin_tool_call_id=record.get("origin_tool_call_id"),
               timed_out=state == "timed_out", cleanup_confirmed=state in _TERMINAL,
               exit_code=124 if state == "timed_out" else record.get("exit_code", code if code is None or code >= 0 else 128 - code),
               stdout_path=record["stdout"], stderr_path=record["stderr"],
               _record_path=str(path), _artifacts_path=record["artifacts"], _scope_path=record["process_scope"])
    return job


def _unconfirmed_job(path, scope, error, record=None):
    """Uncertainty is pending evidence, never a fabricated exit status."""
    record = record or {}
    identifier = hashlib.sha256(f"{scope}/{path.stem}".encode()).hexdigest()
    job = {key: record[key] for key in (
        "name", "display_name", "owner_session_id", "origin_tool_call_id", "created_at",
        "kind", "background_lifecycle", "persistent", "job_timeout_seconds",
    ) if key in record}
    job.update(id=identifier, job_id=identifier, name=record.get("name", path.stem),
               state="unconfirmed", status="unconfirmed", completed=False,
               error=str(error), supervision_confirmed=False, cleanup_confirmed=False,
               wait_on_exit=record.get("wait_on_exit", True), exit_code=None,
               recorded_state=record.get("state"),
               process_record_path=record.get("record", str(path)),
               _record_path=str(path), _scope_path=str(scope))
    for stream in ("stdout", "stderr"):
        if stream in record:
            job[f"{stream}_path"] = record[stream]
    return job


def _launch_id(scope, name):
    return "launch-" + hashlib.sha256(f"{scope}/{name}".encode()).hexdigest()


def _observe_launch_expectations(scope_paths, records, jobs):
    """Adopt a recovered launch without forgetting an unresolved launch attempt."""
    by_path = {job.get("_record_path"): job for job in jobs}
    for scope in sorted({Path(path) for path in scope_paths}):
        directory = scope / "launches"
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            jobs.append(_unconfirmed_job(scope / "launches", scope,
                                         "Launch expectations require a real directory, not a symbolic link or file"))
            continue
        for path in sorted((scope / "launches").glob("*.json")):
            try:
                value = json.loads(_regular_bytes(path, maximum=1024 * 1024)[0])
                expected = value["expected"]
                if (value.get("version") != 1 or not isinstance(expected, dict)
                        or expected.get("name") != path.stem
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", path.stem)
                        or expected.get("record") != str(scope / "jobs" / f"{path.stem}.json")
                        or value.get("id") != _launch_id(scope, path.stem)):
                    raise ValueError("Launch expectation does not match its scope and name")
                _validate_launch_expectation(expected)
                candidates = [(registered, record) for registered, record in records
                              if registered.parent.parent == scope and record["name"] == path.stem]
                if len(candidates) == 1 and _matches_launch(candidates[0][1], expected):
                    job = by_path[str(candidates[0][0])]
                    job.setdefault("resolved_uncertainty_ids", []).append(value["id"])
                    job["launch_id"] = value["id"]
                    continue
                error = "Background launch has no matching verified registration; inspect before retrying"
                job = _unconfirmed_job(path, scope, error, expected)
                job.update(id=value["id"], job_id=value["id"], launch_id=value["id"],
                           _launch_path=str(path), launch_record_path=str(path), state_dir=str(scope / "jobs"))
                # This file records our own requested lifecycle. It does not
                # establish that a service is ready or that a payload exited.
                service = (expected.get("background_lifecycle") == "service"
                           and expected.get("kind") == "service" and expected.get("wait_on_exit") is False
                           and expected.get("persistent") is True)
                job["wait_on_exit"] = not service
                jobs.append(job)
            except (BackgroundJobError, OSError, ValueError, KeyError, TypeError, AttributeError,
                    OverflowError, RecursionError) as exc:
                job = _unconfirmed_job(path, scope, exc)
                job.update(id=_launch_id(scope, path.stem), job_id=_launch_id(scope, path.stem),
                           launch_id=_launch_id(scope, path.stem), _launch_path=str(path))
                jobs.append(job)


def _matches_launch(record, expected):
    return (all(record.get(key) == value for key, value in expected.items())
            and ("images" in record) == ("images" in expected))


def _validate_launch_expectation(expected):
    required = {"name", "display_name", "record", "command", "cwd", "owner_session_id", "origin_tool_call_id",
                "kind", "wait_on_exit", "background_lifecycle", "persistent",
                "job_timeout_seconds", "startup_timeout_seconds"}
    if not required <= set(expected) <= required | {"images"}:
        raise ValueError("Incomplete launch expectation")
    service = expected["background_lifecycle"] == "service"
    if (expected["background_lifecycle"] not in ("task", "service")
            or expected["kind"] != ("service" if service else "job")
            or expected["wait_on_exit"] is not (not service) or expected["persistent"] is not service):
        raise ValueError("Invalid launch lifecycle")
    command = expected["command"]
    if (not isinstance(command, list) or len(command) != 3 or command[:2] != ["/bin/sh", "-c"]
            or not isinstance(command[2], str) or not command[2].strip()
            or not isinstance(expected["display_name"], str) or not expected["display_name"].strip()
            or not Path(expected["cwd"]).is_absolute()):
        raise ValueError("Invalid launch command or working directory")
    for key in ("owner_session_id", "origin_tool_call_id"):
        if expected[key] is not None and (not isinstance(expected[key], str) or not expected[key].strip()):
            raise ValueError(f"Invalid launch {key}")
    for key in ("job_timeout_seconds", "startup_timeout_seconds"):
        timeout = expected[key]
        if timeout is None and service and key == "job_timeout_seconds":
            continue
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"Invalid launch {key}")
    if "images" in expected:
        validate_images(expected["images"])


def _unconfirmed_launch(scope, expected, error):
    path = scope / "launches" / f"{expected['name']}.json"
    identifier = _launch_id(scope, expected["name"])
    value = {"version": 1, "id": identifier, "expected": expected}
    job = _unconfirmed_job(path, scope, error, expected)
    job.update(id=identifier, job_id=identifier, launch_id=identifier,
               launch_status="unconfirmed",
               state_dir=str(scope / "jobs"), _launch_path=str(path), launch_record_path=str(path),
               message="Launch state is unconfirmed. Inspect the registered job and logs before retrying; "
                       "the runtime has not rerun this command.")
    temporary = path.parent / f".launch-{uuid4().hex}.json"
    try:
        if path.parent.is_symlink():
            raise OSError("Launch expectation directory must not be a symbolic link")
        path.parent.mkdir(mode=0o700, exist_ok=True)
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        # The tool result itself is also persisted in session history. Keep its
        # explicit pending identity even if the independent registry cannot save.
        job["registration_error"] = str(exc)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return job


def completion_result(job, max_bytes=16384) -> dict:
    """Read bounded terminal output while retaining the complete log files."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= 1024 * 1024:
        raise ValueError("max_bytes must be a positive integer no greater than 1048576")
    record = _strict_registration(Path(job["_record_path"]))
    expected = hashlib.sha256(f"{record['process_scope']}/{record['token']}".encode()).hexdigest()
    if expected != job.get("id") or record["state"] not in _TERMINAL or record.get("cleanup_confirmed") is not True:
        raise BackgroundJobError("A confirmed terminal background run is required")
    code = record.get("returncode")
    result = {key: record.get(key) for key in ("name", "display_name", "command", "cwd", "owner_session_id", "origin_tool_call_id", "started_at", "ended_at", "stop_reason", "job_timeout_seconds", "deadline_at", "forced", "cleanup_reason", "cleanup_process_count", "cleanup_message")}
    if "images" in record:
        result["images"] = record["images"]
    started, ended = _timestamp(record.get("started_at")), _timestamp(record.get("ended_at"))
    result.update(id=expected, job_id=expected, state=record["state"],
                  kind=record.get("kind", "job"), wait_on_exit=record.get("wait_on_exit", record.get("kind", "job") == "job"),
                  background_lifecycle=record.get("background_lifecycle", "service" if record.get("kind") == "service" else "task"),
                  persistent=record.get("persistent", False),
                  duration_seconds=max(0, ended - started) if started is not None and ended is not None else None,
                  exit_code=124 if record["state"] == "timed_out" else record.get("exit_code", code if code is None or code >= 0 else 128 - code),
                  process_returncode=code, timed_out=record["state"] == "timed_out", cleanup_confirmed=True,
                  cleanup_required=record.get("cleanup_required", False),
                  cleanup_forced=record.get("cleanup_forced", record.get("forced", False)))
    for stream in ("stdout", "stderr"):
        path = Path(record[stream])
        try:
            data, size = _regular_bytes(path, maximum=max_bytes, tail=True)
        except FileNotFoundError:
            if record.get("child_pid") is not None:
                raise BackgroundJobError(f"Completed background job log is missing: {path}")
            data, size = b"", 0  # A failed spawn never opened child logs.
        except OSError as exc:
            raise BackgroundJobError(f"Cannot read background job log {path}: {exc}") from exc
        result.update({stream: data.decode("utf-8", errors="replace"), f"{stream}_bytes": size,
                       f"{stream}_truncated": size > len(data), f"{stream}_path": str(path)})
    return result


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _supervisors(records):
    """Probe only registered supervisors, once per snapshot, without walking /proc."""
    pids = [record.get("supervisor_pid") for record in records
            if record.get("state") not in _TERMINAL]
    pids = sorted({pid for pid in pids if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1})
    if not pids:
        return {}
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", ",".join(map(str, pids)), "-o", "pid=", "-o", "lstart=", "-o", "command="],
            capture_output=True, text=True, timeout=1, env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode not in (0, 1):
            return None
        identities = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 6)
            if len(parts) == 7 and parts[0].isdigit():
                identities[int(parts[0])] = {"started": " ".join(parts[1:6]), "command": parts[6]}
        return identities
    except (OSError, subprocess.SubprocessError):
        return None


def _owns_supervisor(record, identities):
    pid = record.get("supervisor_pid")
    valid_pid = isinstance(pid, int) and not isinstance(pid, bool) and pid > 1
    identity = identities.get(pid) if valid_pid else None
    command = (identity or {}).get("command", "")
    scope_hash = hashlib.sha256(record["process_scope"].encode()).hexdigest()
    return (identity is not None and identity == record.get("supervisor_identity")
            and " _supervise " in f" {command} "
            and f" --token {record['token']} " in f" {command} "
            and f" --scope-id {scope_hash} " in f" {command} ")


def _confirmed_launch(scope, expected, deadline):
    """Recover this launch from the outbox, never by re-executing its command."""
    matches = []
    for path in sorted((scope / "runs").glob("*.json")):
        record = _strict_registration(path)
        if record["name"] == expected["name"]:
            matches.append((path, record))
    if not matches:
        # The helper writes runs/<token> before its public alias and before it
        # spawns a supervisor. An alias without a run is contradictory evidence.
        if Path(expected["record"]).exists():
            raise BackgroundJobError("Background launch alias has no authoritative registration")
        return None
    if len(matches) != 1:
        raise BackgroundJobError("Background launch has ambiguous registrations")
    path, record = matches[0]
    while True:
        if not _matches_launch(record, expected):
            raise BackgroundJobError("Background launch registration does not match this request")
        if record["state"] in _TERMINAL:
            if record.get("cleanup_confirmed") is not True:
                raise BackgroundJobError("Background launch cleanup was not confirmed")
            return record
        supervisors = _supervisors([record])
        if supervisors is None:
            raise BackgroundJobError("Background launch supervisor identity probe failed")
        if _owns_supervisor(record, supervisors) and record["state"] != "starting":
            return record
        # A supervisor can finish during ps, or be created just before the
        # launcher loses its receipt. Observe within the original startup limit;
        # never infer a failed spawn from temporarily absent PID fields.
        latest = _strict_registration(path)
        if latest != record:
            record = latest
            continue
        if latest["state"] != "starting" or time.monotonic() >= deadline:
            raise BackgroundJobError("Background launch supervisor ownership cannot be confirmed")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        record = _strict_registration(path)


def _registration(path):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("version") != 1
                or record.get("token") != path.stem
                or record.get("process_scope") != str(path.parent.parent)):
            return None
        if not isinstance(record.get("state"), str):
            record["state"] = "unknown"
        return record
    except (OSError, ValueError):
        return None


class BackgroundJobMonitor:
    """Transient UI observations of this session's registered background jobs."""

    def __init__(self, session_path, on_event, *, interval=0.5):
        self.path = Path(session_path) / "process-scopes"
        self.on_event, self.interval = on_event, interval
        self._stop = threading.Event()
        self._guard = threading.RLock()
        self._scan_lock = threading.RLock()
        self._thread = None
        self._turn_active = False
        self._pending = False
        self._last = None

    def _snapshot(self):
        records = []
        for path in sorted(self.path.glob("*/runs/*.json")):
            record = _registration(path)
            if record is not None:
                records.append((path, record))
        supervisors = _supervisors([record for _, record in records])
        now = datetime.now(timezone.utc)
        jobs = []
        pending = False
        for path, record in records:
            state = record.get("state")
            if state not in _TERMINAL | {"starting", "running", "stopping", "orphaned"}:
                state = "unknown"
            pid = record.get("supervisor_pid")
            valid_pid = isinstance(pid, int) and not isinstance(pid, bool) and pid > 1
            if state not in _TERMINAL and pid is not None and not valid_pid:
                state = "unknown"
            elif state not in _TERMINAL and valid_pid:
                if supervisors is None:
                    state = "unknown"
                else:
                    identity = supervisors.get(pid)
                    command = (identity or {}).get("command", "")
                    scope_id = hashlib.sha256(record["process_scope"].encode("utf-8")).hexdigest()
                    owned = (identity is not None and identity == record.get("supervisor_identity")
                             and " _supervise " in f" {command} "
                             and f" --token {record['token']} " in f" {command} "
                             and f" --scope-id {scope_id} " in f" {command} ")
                    if not owned:
                        # A supervisor saves its terminal state before exiting;
                        # it may have done so between our file read and ps.
                        latest = _registration(path)
                        if latest is not None and latest.get("state") in _TERMINAL:
                            record, state = latest, latest["state"]
                        else:
                            state = "orphaned"
            elif state == "running" and record.get("supervisor_pid") is None:
                state = "unknown"
            if state in _TERMINAL and record.get("cleanup_confirmed") is not True:
                state = "unconfirmed"
            started = _timestamp(record.get("started_at"))
            if started is None:
                started = _timestamp(record.get("created_at"))
            ended = _timestamp(record.get("ended_at"))
            end = ended if state in _TERMINAL | {"unknown", "orphaned", "unconfirmed"} else now.timestamp()
            elapsed = max(0.0, end - started) if started is not None and end is not None else None
            code = record.get("returncode")
            jobs.append({
                "id": hashlib.sha256(f"{record['process_scope']}/{record['token']}".encode()).hexdigest(),
                "name": record.get("name") if isinstance(record.get("name"), str) else "background job",
                "display_name": record.get("display_name", record.get("name", "background job")),
                "state": state, "created_at": record.get("created_at"),
                "started_at": record.get("started_at"), "ended_at": record.get("ended_at"),
                "returncode": code if isinstance(code, int) and not isinstance(code, bool) else None,
                "persistent": record.get("persistent") is True, "elapsed_seconds": elapsed,
                "kind": record.get("kind", "job"),
                "wait_on_exit": record.get("wait_on_exit", record.get("kind", "job") == "job"),
                "background_lifecycle": record.get("background_lifecycle", "service" if record.get("kind") == "service" else "task"),
                "cleanup_required": record.get("cleanup_required", False),
                "cleanup_confirmed": record.get("cleanup_confirmed", False),
                "cleanup_forced": record.get("cleanup_forced", record.get("forced", False)),
                "cleanup_reason": record.get("cleanup_reason"),
            })
            pending = pending or state not in _TERMINAL
        return jobs, pending, now.isoformat()

    def refresh(self, *, force=False):
        with self._scan_lock:
            try:
                jobs, pending, observed_at = self._snapshot()
            except Exception:
                # Files can disappear or be malformed while an external helper
                # updates them. Observation cannot fail or replace agent work.
                return
            # Durations advance in the UI; polling only publishes state changes.
            signature = [{key: value for key, value in job.items() if key != "elapsed_seconds"} for job in jobs]
            changed = signature != self._last
            self._pending = pending
            if (changed or force) and (jobs or self._last):
                try:
                    self.on_event({"type": "background_jobs", "jobs": jobs, "observed_at": observed_at})
                except Exception:
                    # A closed or failing display must never change execution.
                    pass
            self._last = signature

    def start_turn(self):
        with self._guard:
            self._turn_active = True
            self._stop.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._watch, name="skills-agent-background-jobs", daemon=True)
                try:
                    self._thread.start()
                except RuntimeError:
                    self._thread = None
        self.refresh(force=True)

    def end_turn(self):
        self.refresh()
        with self._guard:
            self._turn_active = False
        if not self._pending:
            self.close()

    def _watch(self):
        try:
            while not self._stop.wait(self.interval):
                self.refresh()
                with self._guard:
                    if not self._turn_active and not self._pending:
                        self._thread = None
                        return
        finally:
            with self._guard:
                if self._thread is threading.current_thread():
                    self._thread = None

    def close(self):
        self._stop.set()
        with self._guard:
            self._turn_active = False
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self.refresh()


class ProcessScope:
    def __init__(self, session_path, *, allow_persistent=False):
        self.successful = False
        self.path = Path(session_path) / "process-scopes" / uuid4().hex
        if self.path.parent.is_symlink():
            raise BackgroundJobError("The session's process-scopes directory must not be a symbolic link")
        (self.path / "runs").mkdir(parents=True, mode=0o700)
        (self.path / "scope.json").write_text(json.dumps({
            "version": 1, "allow_persistent": allow_persistent, "successful": False,
        }), encoding="utf-8")

    @classmethod
    def attach(cls, path):
        """Recover only a previously registered scope; do not create any files."""
        try:
            path = Path(path)
            if path.is_symlink() or path.parent.is_symlink() or (path / "runs").is_symlink():
                raise ValueError("Process scope, process-scopes and runs directories must not be symbolic links")
            path = path.resolve(strict=True)
            value = json.loads(_regular_bytes(path / "scope.json", maximum=65536)[0])
            if not isinstance(value, dict) or value.get("version") != 1 or not (path / "runs").is_dir():
                raise ValueError("Invalid process scope")
            scope = cls.__new__(cls)
            scope.path, scope.successful = path, value.get("successful") is True
            return scope
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise BackgroundJobError(f"Cannot recover process scope {path}: {exc}") from exc

    def mark_successful(self):
        path = self.path / "scope.json"
        value = json.loads(_regular_bytes(path, maximum=65536)[0])
        value["successful"] = True
        temporary = self.path / f".scope-{uuid4().hex}.json"
        try:
            temporary.write_text(json.dumps(value), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self.successful = True

    def start_background(self, command: str, name: str, cwd: Path, env: dict,
                         timeout_seconds: float | None = None, *, background_lifecycle: str = "task",
                         images: list[dict] | None = None) -> dict:
        """Register a supervised shell and return its launch receipt, not its result.

        The independently owned supervisor keeps the payload's process-group
        leader unreaped until group cleanup is confirmed.  This is cooperative
        POSIX process-group ownership; a deliberately detached process is outside
        the contract, rather than a containment guarantee.

        Image declarations are registered before launch; this path never opens
        their files or attaches them to the receipt.
        """
        try:
            scope = self.attach(self.path)
        except BackgroundJobError as exc:
            # No launch was attempted. The tolerant observer retains the
            # damaged existing scope as an independent diagnostic blocker.
            raise ValueError(f"Cannot start background work: {exc}") from exc
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Background command must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Background name must be a non-empty string")
        if background_lifecycle not in ("task", "service"):
            raise ValueError("background_lifecycle must be task or service")
        images = validate_images(images) if images is not None else None
        service = background_lifecycle == "service"
        scope_policy = json.loads(_regular_bytes(scope.path / "scope.json", maximum=65536)[0])
        if service and scope_policy.get("allow_persistent") is not True:
            raise ValueError("Persistent background services are not allowed in this agent run")
        timeout = (None if service else 3600.0) if timeout_seconds is None else timeout_seconds
        try:
            valid_timeout = timeout is None or (not isinstance(timeout, bool) and isinstance(timeout, (int, float))
                                                 and math.isfinite(timeout) and timeout > 0)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("Background timeout must be a positive finite number of seconds")
        cwd = Path(cwd).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("Background working directory must be a directory")
        if not isinstance(env, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                            for key, value in env.items()):
            raise ValueError("Background environment must contain string keys and values")
        if (scope.path / "closed").exists():
            raise ValueError("The agent turn's process scope is closed")
        # The display label is unrestricted by the helper's filesystem-name
        # contract.  A fresh suffix also makes repeated labels separate jobs.
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-_")[:32] or "job"
        internal_name = f"{stem}-{uuid4().hex[:16]}"
        state_dir = scope.path / "jobs"
        args = [sys.executable, str(HELPER), "start", "--state-dir", str(state_dir),
                "--name", internal_name, "--display-name", name, "--cwd", str(cwd),
                "--startup-timeout", str(BACKGROUND_STARTUP_TIMEOUT_SECONDS)]
        if timeout is not None:
            args.extend(["--job-timeout", str(timeout)])
        if service:
            args.extend(["--service", "--persistent"])
        if images is not None:
            args.extend(["--images-json", json.dumps(images, ensure_ascii=False)])
        args.extend(["--", "/bin/sh", "-c", command])
        environment = {**env, SCOPE_ENV: str(scope.path)}
        deadline = time.monotonic() + BACKGROUND_STARTUP_TIMEOUT_SECONDS
        launched, launch_error = None, None
        try:
            launched = subprocess.run(args, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, timeout=BACKGROUND_STARTUP_TIMEOUT_SECONDS + 2)
        except (OSError, subprocess.SubprocessError) as exc:
            launch_error = exc
        expected = {
            "name": internal_name, "display_name": name, "record": str(state_dir / f"{internal_name}.json"),
            "command": ["/bin/sh", "-c", command], "cwd": str(cwd),
            "owner_session_id": environment.get(CONTEXT_ENV), "origin_tool_call_id": environment.get(CALL_ENV),
            "kind": "service" if service else "job", "wait_on_exit": not service,
            "background_lifecycle": background_lifecycle, "persistent": service,
            "job_timeout_seconds": timeout, "startup_timeout_seconds": BACKGROUND_STARTUP_TIMEOUT_SECONDS,
        }
        if images is not None:
            expected["images"] = images
        try:
            registration = _confirmed_launch(scope.path, expected, deadline)
        except (BackgroundJobError, OSError) as exc:
            return _unconfirmed_launch(scope.path, expected, exc)
        if registration is None:
            # run() has reaped the launcher on return and on TimeoutExpired.
            # Since registration precedes supervisor creation, no registration
            # means the payload never started. Other subprocess errors do not
            # establish that boundary, so retain an unconfirmed launch instead.
            if launch_error is not None and not isinstance(launch_error, (OSError, subprocess.TimeoutExpired)):
                return _unconfirmed_launch(scope.path, expected, launch_error)
            detail = str(launch_error) if launch_error else (launched.stderr or launched.stdout).strip()
            raise ValueError(f"Background command did not start: no job was registered. {detail}")
        recovered = True
        try:
            payload = json.loads(launched.stdout)
            recovered = (launched.returncode != 0 or payload.get("ok") is not True
                         or payload["process"]["name"] != internal_name)
        except (ValueError, KeyError, TypeError, AttributeError):
            pass  # The independently verified registration is authoritative.
        failed_start = registration["state"] in _TERMINAL and registration.get("child_pid") is None
        job_id = hashlib.sha256(f"{scope.path}/{registration['token']}".encode()).hexdigest()
        receipt = {
            "status": "failed" if failed_start else "started",
            "launch_status": "failed" if failed_start else "started", "completed": False,
            "message": "Background job registered. This is a launch receipt, not a completion result. "
                       "The runtime will deliver its terminal result separately.",
            "id": job_id, "job_id": job_id, "name": internal_name, "display_name": name,
            "state": registration["state"], "kind": "service" if service else "job", "wait_on_exit": not service,
            "background_lifecycle": background_lifecycle, "persistent": service,
            "job_timeout_seconds": timeout, "startup_timeout_seconds": BACKGROUND_STARTUP_TIMEOUT_SECONDS,
            "state_dir": str(state_dir), "process_record_path": registration["record"],
            "stdout_path": registration["stdout"], "stderr_path": registration["stderr"],
        }
        if recovered:
            receipt["receipt_recovered"] = True
        if failed_start:
            receipt["error"] = registration.get("error") or "Supervisor failed before starting the managed command"
        return receipt

    def close_owner(self, owner_session_id: str):
        """Close and clean one agent's registrations without closing the scope.

        Failed children can share their parent's scope. Their services must not
        inherit the parent's later successful-exit preservation policy.
        """
        if not isinstance(owner_session_id, str) or not owner_session_id.strip():
            raise ValueError("Owner session ID must be a non-empty string")
        scope = self.attach(self.path)
        saved = {}
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                saved[signum] = signal.signal(signum, signal.SIG_IGN)
        try:
            args = [sys.executable, str(HELPER), "_cleanup", "--scope", str(scope.path),
                    f"--owner-session-id={owner_session_id}"]
            try:
                result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, timeout=7)
            except (OSError, subprocess.SubprocessError) as exc:
                raise BackgroundJobError(f"Owner background cleanup was not confirmed: {exc}") from exc
            if result.returncode:
                raise BackgroundJobError("Owner background cleanup was not confirmed: " +
                                         (result.stdout or result.stderr).strip())
        finally:
            for signum, handler in saved.items():
                signal.signal(signum, handler)

    def close(self, *, preserve_persistent=False):
        # Recheck before taking ownership: a saved scope may have been replaced
        # by an alias after creation or recovery.
        self.attach(self.path)
        saved = {}
        if threading.current_thread() is threading.main_thread():
            # A second Ctrl-C/TERM must not interrupt the cleanup already in flight.
            for signum in (signal.SIGINT, signal.SIGTERM):
                saved[signum] = signal.signal(signum, signal.SIG_IGN)
        try:
            with (self.path / "lock").open("a+b") as lock:
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX)
                (self.path / "closed").touch()
            # On non-POSIX hosts the helper cannot register processes at all.
            if not any((self.path / "runs").glob("*.json")):
                return
            args = [sys.executable, str(HELPER), "_cleanup", "--scope", str(self.path)]
            if preserve_persistent:
                args.append("--preserve-persistent")
            result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=7)
            if result.returncode:
                raise RuntimeError("Background process cleanup was not confirmed: " +
                                   (result.stdout or result.stderr).strip())
        finally:
            for signum, handler in saved.items():
                signal.signal(signum, handler)
