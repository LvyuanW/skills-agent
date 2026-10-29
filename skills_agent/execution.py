"""Bound foreground commands and clean up their owned process groups."""

from contextlib import contextmanager
from copy import deepcopy
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import traceback


DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
TERM_GRACE_SECONDS = 1
CLEANUP_TIMEOUT_SECONDS = 5
TIMEOUT_OUTPUT_BYTES = 1024 * 1024
_POLL_SECONDS = 0.02
_PROBE_TIMEOUT_SECONDS = 0.5


class ToolCleanupError(RuntimeError):
    """A command may still be active; the agent must not continue normally."""

    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


def _error_details(error, phase=None):
    details = {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": "".join(traceback.format_exception(error)),
    }
    if phase is not None:
        details["phase"] = phase
    if getattr(error, "errno", None) is not None:
        details["errno"] = error.errno
    if getattr(error, "diagnostics", None) is not None:
        details["diagnostics"] = deepcopy(error.diagnostics)
    return details


def validate_timeout(value):
    if (type(value) not in (int, float) or not 0 < value <= MAX_TIMEOUT_SECONDS
            or not math.isfinite(value)):
        raise ValueError(f"timeout_seconds must be a finite number greater than 0 and at most {MAX_TIMEOUT_SECONDS}")
    return value


def _signal_group(process, signum):
    # Only a live Popen object's unreaped child identifies the owned group.
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _peek_exit(process):
    # Keep the leader's PID reserved until group cleanup is confirmed.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _group_members(pgid, deadline):
    """Observe live members without making ps itself an unbounded wait."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("process group inspection", 0)
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        probe = subprocess.Popen(
            ["ps", "-e", "-o", "pid=", "-o", "pgid=", "-o", "stat="],
            stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
        )
        try:
            probe.wait(timeout=min(_PROBE_TIMEOUT_SECONDS, remaining))
        except BaseException as original:
            errors = []
            try:
                probe.kill()
            except BaseException as exc:
                errors.append(_error_details(exc, "inspection_kill"))
            try:
                probe.wait(timeout=max(0.001, min(_PROBE_TIMEOUT_SECONDS, deadline - time.monotonic())))
            except BaseException as exc:
                errors.append(_error_details(exc, "inspection_reap"))
                raise ToolCleanupError("Process inspection could not be stopped", diagnostics={
                    "original_error": _error_details(original, "inspection_wait"),
                    "cleanup_errors": errors, "cleanup_confirmed": False,
                    "inspection_pid": probe.pid,
                }) from original
            raise
        if probe.returncode:
            raise ToolCleanupError("Cannot inspect the command's process group")
        output.seek(0)
        members = []
        for line in output.read().decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) != 3:
                raise ToolCleanupError("Unexpected process group inspection output")
            if int(parts[1]) == pgid and not parts[2].startswith("Z"):
                members.append(int(parts[0]))
        return members


def _inspect_group(pgid, deadline):
    """Retry a reaped, timed-out probe within the same observation deadline."""
    while True:
        try:
            return _group_members(pgid, deadline)
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(_POLL_SECONDS, max(0, deadline - time.monotonic())))


def _finished(process, deadline):
    return _peek_exit(process) and not _group_members(process.pid, deadline)


def _wait_for_completion(process, deadline):
    while time.monotonic() < deadline:
        try:
            if _finished(process, deadline):
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
                return True
        except subprocess.TimeoutExpired:
            # A slow inspection consumes this phase's existing deadline.
            pass
        time.sleep(min(_POLL_SECONDS, max(0, deadline - time.monotonic())))
    return False


def _wait_for_main_exit(process, deadline):
    """Wait for the entry process without reaping its process-group leader."""
    while time.monotonic() < deadline:
        if _peek_exit(process):
            return True
        time.sleep(min(_POLL_SECONDS, max(0, deadline - time.monotonic())))
    return False


def _stop_process(process):
    if process.returncode is not None:
        # Never signal a group after releasing its leader's PID.
        return False
    deadline = time.monotonic() + CLEANUP_TIMEOUT_SECONDS
    failures = []
    fatal = False
    for signum in (signal.SIGTERM, signal.SIGKILL):
        phase = signal.Signals(signum).name
        try:
            _signal_group(process, signum)
        except BaseException as exc:
            failures.append((exc, _error_details(exc, phase)))
            # macOS may reject signals to a zombie-only group. Only a later
            # successful exit AND live-member check can resolve that error.
            fatal = fatal or not isinstance(exc, PermissionError)
        phase_deadline = (min(deadline, time.monotonic() + TERM_GRACE_SECONDS)
                          if signum == signal.SIGTERM else deadline)
        try:
            if _wait_for_completion(process, phase_deadline):
                if not fatal:
                    return signum == signal.SIGKILL
                break
        except BaseException as exc:
            failures.append((exc, _error_details(exc, phase + "_confirmation")))
            # In particular, a probe that could not itself be stopped must
            # remain a fatal error even if a later group check succeeds.
            fatal = True
        if process.returncode is not None:
            break
    # Both signal attempts are guarded. A second error cannot skip reaping.
    # Reaping our direct child does not establish that the group was cleaned;
    # no group signal is permitted after this last, bounded wait.
    if process.returncode is None:
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except BaseException as exc:
            failures.append((exc, _error_details(exc, "reap")))
    message = "Command process group cleanup could not be confirmed"
    if time.monotonic() >= deadline:
        message += " before the cleanup deadline"
    if failures:
        message += ": " + "; ".join(
            f"{details['phase']}: {details['type']}: {details['message']}"
            for _, details in failures
        )
    failure = ToolCleanupError(message, diagnostics={
        "cleanup_confirmed": False,
        "cleanup_errors": [details for _, details in failures],
    })
    if failures:
        raise failure from failures[0][0]
    raise failure


def _finish_main_exit(process):
    """Reap an exited entry process only after its owned group is empty.

    Scripts must wait for their own internal work. Work left in this process
    group after the entry process exits is stopped rather than awaited as a
    successful continuation. A process that changes groups is outside this
    lightweight ownership boundary.
    """
    members = _inspect_group(process.pid, time.monotonic() + CLEANUP_TIMEOUT_SECONDS)
    if not members:
        process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
        return {}
    with _cleanup_signals():
        forced = _stop_process(process)
    return {
        "cleanup_required": True,
        "cleanup_confirmed": True,
        "cleanup_reason": "main_process_exited",
        "cleanup_forced": forced,
        "cleanup_initial_process_count": len(members),
        "cleanup_message": (
            "The entry process exited while other processes in its process group "
            "were still running. Those remaining processes were stopped; their "
            "work did not finish naturally. The exit code belongs to the entry "
            "process only."
        ),
    }


@contextmanager
def _cleanup_signals():
    saved = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            saved[signum] = signal.signal(signum, signal.SIG_IGN)
    try:
        yield
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)


def _cleanup(process, callback):
    failures = []
    errors = []
    with _cleanup_signals():
        try:
            _stop_process(process)
        except BaseException as exc:
            failures.append(exc)
            diagnostics = getattr(exc, "diagnostics", None) or {}
            errors.extend(diagnostics.get("cleanup_errors") or [_error_details(exc, "process_group")])
        if callback is not None:
            try:
                # The backend owns its cleanup deadline (e.g. Docker removal).
                callback()
            except BaseException as exc:
                failures.append(exc)
                errors.append(_error_details(exc, "backend_cleanup"))
    if failures:
        details = "; ".join(str(exc) or type(exc).__name__ for exc in failures)
        raise ToolCleanupError("Command cleanup was not confirmed: " + details, diagnostics={
            "cleanup_confirmed": False, "cleanup_errors": errors,
        }) from failures[0]


def _record_command_failure(error, original, process, phase, stdout_path, stderr_path,
                            timeout_seconds, started):
    # Agent.run persists exception diagnostics in the session's error event.
    # Keep the files as evidence without presenting an unconfirmed command as
    # an ordinary successful tool result or loading unbounded output here.
    details = dict(error.diagnostics or {})
    if original is not error:
        original_details = getattr(original, "diagnostics", None) or {}
        details["cleanup_errors"] = [
            *original_details.get("cleanup_errors", []),
            *details.get("cleanup_errors", []),
        ]
    details.update(
        original_error=_error_details(original, phase),
        cleanup_confirmed=False, process_pid=process.pid,
        process_returncode=process.returncode,
        stdout_path=str(stdout_path), stderr_path=str(stderr_path),
        timeout_seconds=timeout_seconds,
        duration_seconds=round(time.monotonic() - started, 3),
    )
    error.diagnostics = details


def _read_output(path, *, limit=None):
    with path.open("rb") as source:
        length = source.seek(0, os.SEEK_END)
        offset = max(0, length - limit) if limit is not None else 0
        source.seek(offset)
        content = source.read(limit)
    text = content.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return text, offset > 0


def run_command(command, *, cwd, shell=False, env=None,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS, log_dir=None, cleanup=None):
    """Wait for the entry process, then stop any same-group residual work.

    Timeout is a tool result, cancellation still propagates. Scripts must wait
    for their own children before exiting. Cleanup of residual same-group
    processes is reported separately from the entry process's exit code.

    ``cleanup`` must perform bounded backend cleanup and raise if it cannot
    confirm completion. It runs after a started command times out or raises.
    """
    validate_timeout(timeout_seconds)
    if os.name != "posix" or not hasattr(os, "WNOWAIT"):
        raise RuntimeError("Bounded foreground commands require POSIX waitid/WNOWAIT")
    if log_dir is not None:
        log_dir = Path(log_dir).expanduser().resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="command-", dir=log_dir)).resolve()
    stdout_path, stderr_path = directory / "stdout.log", directory / "stderr.log"
    started = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        # File logs also avoid waiting for EOF held by an inherited grandchild.
        process = subprocess.Popen(
            command, shell=shell, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=True,
        )
        phase = "execution"
        try:
            completed = _wait_for_main_exit(process, started + timeout_seconds)
            phase = "exit_confirmation"
            cleanup_result = _finish_main_exit(process) if completed else {}
        except BaseException as original:
            try:
                _cleanup(process, cleanup)
            except ToolCleanupError as error:
                _record_command_failure(error, original, process, phase, stdout_path, stderr_path,
                                        timeout_seconds, started)
                raise
            if isinstance(original, ToolCleanupError):
                _record_command_failure(original, original, process, phase, stdout_path, stderr_path,
                                        timeout_seconds, started)
            raise
        if not completed:
            try:
                _cleanup(process, cleanup)
            except ToolCleanupError as error:
                _record_command_failure(
                    error, subprocess.TimeoutExpired(command, timeout_seconds), process,
                    "execution_timeout", stdout_path, stderr_path, timeout_seconds, started,
                )
                raise
    limit = None if completed else TIMEOUT_OUTPUT_BYTES
    output, stdout_truncated = _read_output(stdout_path, limit=limit)
    errors, stderr_truncated = _read_output(stderr_path, limit=limit)
    result = {
        "exit_code": process.returncode if completed else 124,
        "stdout": output,
        "stderr": errors,
        **cleanup_result,
    }
    if not completed:
        result.update(
            process_returncode=process.returncode, timed_out=True,
            timeout_seconds=timeout_seconds, duration_seconds=round(time.monotonic() - started, 3),
            stdout_path=str(stdout_path), stderr_path=str(stderr_path),
            stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
        )
    return result
