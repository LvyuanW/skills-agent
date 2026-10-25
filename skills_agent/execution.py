"""Bound foreground commands and clean up their owned process groups."""

from contextlib import contextmanager
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time


DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
TERM_GRACE_SECONDS = 1
CLEANUP_TIMEOUT_SECONDS = 5
TIMEOUT_OUTPUT_BYTES = 1024 * 1024
_POLL_SECONDS = 0.02
_PROBE_TIMEOUT_SECONDS = 0.5


class ToolCleanupError(RuntimeError):
    """A command may still be active; the agent must not continue normally."""


def validate_timeout(value):
    if (type(value) not in (int, float) or not 0 < value <= MAX_TIMEOUT_SECONDS
            or not math.isfinite(value)):
        raise ValueError(f"timeout_seconds must be a finite number greater than 0 and at most {MAX_TIMEOUT_SECONDS}")
    return value


def _signal_group(process, signum):
    # Only a live Popen object's unreaped child identifies the owned group.
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
        except BaseException:
            probe.kill()
            try:
                probe.wait(timeout=max(0.001, min(_PROBE_TIMEOUT_SECONDS, deadline - time.monotonic())))
            except subprocess.TimeoutExpired as exc:
                raise ToolCleanupError("Process inspection could not be stopped") from exc
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
        # Completion already confirmed the whole group before reaping.
        return False
    deadline = time.monotonic() + CLEANUP_TIMEOUT_SECONDS
    try:
        _signal_group(process, signal.SIGTERM)
        grace_deadline = min(deadline, time.monotonic() + TERM_GRACE_SECONDS)
        if _wait_for_completion(process, grace_deadline):
            return False
        _signal_group(process, signal.SIGKILL)
        if _wait_for_completion(process, deadline):
            return True
        raise ToolCleanupError("Command process group did not stop before the cleanup deadline")
    except BaseException as exc:
        # Even when observation failed, attempt to stop and reap our own child.
        if process.returncode is None:
            _signal_group(process, signal.SIGKILL)
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        if isinstance(exc, ToolCleanupError):
            raise
        raise ToolCleanupError("Command process group cleanup could not be confirmed") from exc


def _finish_main_exit(process):
    """Reap an exited entry process only after its owned group is empty.

    Scripts must wait for their own internal work. Work left in this process
    group after the entry process exits is stopped rather than awaited as a
    successful continuation. A process that changes groups is outside this
    lightweight ownership boundary.
    """
    members = _group_members(process.pid, time.monotonic() + CLEANUP_TIMEOUT_SECONDS)
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
    with _cleanup_signals():
        try:
            _stop_process(process)
        except BaseException as exc:
            failures.append(exc)
        if callback is not None:
            try:
                # The backend owns its cleanup deadline (e.g. Docker removal).
                callback()
            except BaseException as exc:
                failures.append(exc)
    if failures:
        details = "; ".join(str(exc) or type(exc).__name__ for exc in failures)
        raise ToolCleanupError("Command cleanup was not confirmed: " + details) from failures[0]


def _read_output(path, *, limit=None):
    with path.open("rb") as source:
        length = source.seek(0, os.SEEK_END)
        offset = max(0, length - limit) if limit is not None else 0
        source.seek(offset)
        content = source.read(limit)
    text = content.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return text, offset > 0


def run_command(command, *, cwd, shell=False, env=None,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS, log_dir=None, cleanup=None, include_log_paths=False):
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
        try:
            completed = _wait_for_main_exit(process, started + timeout_seconds)
            cleanup_result = _finish_main_exit(process) if completed else {}
        except BaseException:
            _cleanup(process, cleanup)
            raise
        if not completed:
            _cleanup(process, cleanup)
    limit = None if completed else TIMEOUT_OUTPUT_BYTES
    output, stdout_truncated = _read_output(stdout_path, limit=limit)
    errors, stderr_truncated = _read_output(stderr_path, limit=limit)
    result = {
        "exit_code": process.returncode if completed else 124,
        "stdout": output,
        "stderr": errors,
        **cleanup_result,
    }
    if include_log_paths:
        result.update(stdout_path=str(stdout_path), stderr_path=str(stderr_path),
                      stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
                      cleanup_confirmed=True,
                      duration_seconds=round(time.monotonic() - started, 3))
    if not completed:
        result.update(
            process_returncode=process.returncode, timed_out=True,
            timeout_seconds=timeout_seconds, duration_seconds=round(time.monotonic() - started, 3),
            stdout_path=str(stdout_path), stderr_path=str(stderr_path),
            stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
        )
    return result
