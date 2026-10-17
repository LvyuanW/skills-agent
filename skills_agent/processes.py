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
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise BackgroundJobError(f"Cannot verify background job {path}: {exc}") from exc


def observe_background_jobs(scope_paths: list[Path]) -> list[dict]:
    """Strict execution observations; unlike the UI, uncertainty is an error."""
    records = []
    for original_scope in sorted({Path(path) for path in scope_paths}):
        scope = ProcessScope.attach(original_scope).path
        for path in sorted((scope / "runs").glob("*.json")):
            records.append((path, _strict_registration(path)))
    supervisors = _supervisors([record for _, record in records])
    if supervisors is None:
        raise BackgroundJobError("Background supervisor identity probe failed")
    jobs = []
    for path, record in records:
        state = record["state"]
        if state not in _TERMINAL:
            pid = record.get("supervisor_pid")
            valid_pid = isinstance(pid, int) and not isinstance(pid, bool) and pid > 1
            identity = supervisors.get(pid) if valid_pid else None
            command = (identity or {}).get("command", "")
            scope_hash = hashlib.sha256(record["process_scope"].encode()).hexdigest()
            owned = (identity is not None and identity == record.get("supervisor_identity")
                     and " _supervise " in f" {command} "
                     and f" --token {record['token']} " in f" {command} "
                     and f" --scope-id {scope_hash} " in f" {command} ")
            if not owned:
                record = _strict_registration(path)
                state = record["state"]
                created = _timestamp(record.get("created_at"))
                startup = record.get("startup_timeout_seconds", BACKGROUND_STARTUP_TIMEOUT_SECONDS)
                starting = (state == "starting" and record.get("supervisor_pid") is None
                            and created is not None and isinstance(startup, (int, float))
                            and 0 <= datetime.now(timezone.utc).timestamp() - created <= startup + 2)
                if state not in _TERMINAL and not starting:
                    raise BackgroundJobError(f"Background supervisor ownership cannot be confirmed: {record['name']}")
        if state in _TERMINAL and record.get("cleanup_confirmed") is False:
            raise BackgroundJobError(f"Background cleanup was not confirmed: {record['name']}")
        code = record.get("returncode")
        if state in _TERMINAL and (not isinstance(code, int) or isinstance(code, bool)):
            raise BackgroundJobError(f"Background return code is unavailable: {record['name']}")
        job = {key: value for key, value in record.items()
               if key not in {"token", "supervisor_identity", "stdout", "stderr"}}
        job.update(id=hashlib.sha256(f"{record['process_scope']}/{record['token']}".encode()).hexdigest(),
                   kind=record.get("kind", "job"), wait_on_exit=record.get("wait_on_exit", record.get("kind", "job") == "job"),
                   background_lifecycle=record.get("background_lifecycle", "service" if record.get("kind") == "service" else "task"),
                   owner_session_id=record.get("owner_session_id"),
                   origin_tool_call_id=record.get("origin_tool_call_id"),
                   timed_out=state == "timed_out", cleanup_confirmed=state in _TERMINAL,
                   exit_code=124 if state == "timed_out" else record.get("exit_code", code if code is None or code >= 0 else 128 - code),
                   stdout_path=record["stdout"], stderr_path=record["stderr"],
                   _record_path=str(path), _artifacts_path=record["artifacts"], _scope_path=record["process_scope"])
        jobs.append(job)
    return sorted(jobs, key=lambda job: (job.get("created_at", ""), job["id"]))


def completion_result(job, max_bytes=16384) -> dict:
    """Read bounded terminal output while retaining the complete log files."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= 1024 * 1024:
        raise ValueError("max_bytes must be a positive integer no greater than 1048576")
    record = _strict_registration(Path(job["_record_path"]))
    expected = hashlib.sha256(f"{record['process_scope']}/{record['token']}".encode()).hexdigest()
    if expected != job.get("id") or record["state"] not in _TERMINAL or record.get("cleanup_confirmed") is False:
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
            started = _timestamp(record.get("started_at"))
            if started is None:
                started = _timestamp(record.get("created_at"))
            ended = _timestamp(record.get("ended_at"))
            end = ended if state in _TERMINAL | {"unknown", "orphaned"} else now.timestamp()
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
            pending = pending or record.get("state") not in _TERMINAL
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
        except (OSError, ValueError, TypeError) as exc:
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
        scope = self.attach(self.path)
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
            raise BackgroundJobError("The agent turn's process scope is closed")
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
        try:
            launched = subprocess.run(args, env=environment, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, timeout=BACKGROUND_STARTUP_TIMEOUT_SECONDS + 2)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackgroundJobError(f"Background launch could not be confirmed: {exc}") from exc
        try:
            payload = json.loads(launched.stdout)
            record = payload["process"]
            # Public helper output omits the ownership token.  Recover it only
            # from the freshly created, uniquely named authoritative alias.
            alias = state_dir / f"{internal_name}.json"
            saved = json.loads(_regular_bytes(alias, maximum=1024 * 1024)[0])
            registration = _strict_registration(scope.path / "runs" / f"{saved['token']}.json")
            if (registration["name"] != internal_name or registration.get("kind") != ("service" if service else "job")
                    or registration.get("wait_on_exit") is not (not service)
                    or registration.get("persistent") is not service
                    or ("images" in registration) != (images is not None)
                    or registration.get("images") != images):
                raise ValueError("Background launch registration does not match this request")
            # A very short job can already have failed or timed out by the time
            # the helper observes startup.  It still launched: preserve its
            # separate terminal event instead of treating its exit as a launch
            # failure or marking that event as already delivered.
            early_completion = (registration["state"] in _TERMINAL
                                and isinstance(registration.get("child_pid"), int))
            if (launched.returncode or not payload.get("ok")) and not early_completion:
                raise ValueError(payload.get("observations", {}).get("error") or registration.get("error")
                                 or f"Supervisor launch failed with status {launched.returncode}")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            detail = (launched.stderr or launched.stdout).strip()
            raise BackgroundJobError(f"Background launch was not confirmed: {exc}; {detail}") from exc
        job_id = hashlib.sha256(f"{scope.path}/{registration['token']}".encode()).hexdigest()
        return {
            "status": "started", "launch_status": "started", "completed": False,
            "message": "Background job registered. This is a launch receipt, not a completion result. "
                       "The runtime will deliver its terminal result separately.",
            "id": job_id, "job_id": job_id, "name": internal_name, "display_name": name,
            "state": registration["state"], "kind": "service" if service else "job", "wait_on_exit": not service,
            "background_lifecycle": background_lifecycle, "persistent": service,
            "job_timeout_seconds": timeout, "startup_timeout_seconds": BACKGROUND_STARTUP_TIMEOUT_SECONDS,
            "state_dir": str(state_dir), "process_record_path": registration["record"],
            "stdout_path": registration["stdout"], "stderr_path": registration["stderr"],
        }

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
