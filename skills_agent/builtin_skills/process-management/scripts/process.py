#!/usr/bin/env python3
"""Manage owned, persistent POSIX processes across independent shell calls."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid


TERMINAL = {"exited", "failed", "stopped"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SCOPE_ENV = "SKILLS_AGENT_PROCESS_SCOPE"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def record_path(state_dir: str, name: str) -> Path:
    if not NAME.fullmatch(name):
        raise ValueError("Name must start with a letter or digit and contain at most 64 letters, digits, dots, underscores or hyphens")
    return Path(state_dir).expanduser().resolve() / f"{name}.json"


def load_record(path: Path) -> dict:
    record = load_json(path)
    if record.get("version") != 1 or record.get("name") != path.stem:
        raise ValueError(f"Invalid process record: {path}")
    token = record.get("token", "")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid process ownership token")
    expected = path.parent / f"{path.stem}.{token}"
    if record.get("artifacts") != str(expected):
        raise ValueError("Process artifact directory does not match its record")
    return record


@contextmanager
def scope_lock(value):
    """Serialize registration with closure, including starts from background jobs."""
    if not value:
        yield None
        return
    import fcntl
    path = Path(value).resolve(strict=True)
    if load_json(path / "scope.json").get("version") != 1:
        raise ValueError("Invalid agent process scope")
    with (path / "lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield path


def save_record(path, record):
    # A separate copy per ownership token survives reuse of the public name.
    if record.get("process_scope"):
        atomic_json(Path(record["process_scope"]) / "runs" / f"{record['token']}.json", record)
    atomic_json(path, record)


def pid_identity(pid: int) -> dict | None:
    """Creation time plus the exact private supervisor argument identifies ownership."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    result = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "lstart=", "-o", "command="],
        capture_output=True, text=True, timeout=3, env={**os.environ, "LC_ALL": "C"},
    )
    parts = result.stdout.strip().split(None, 5)
    if result.returncode != 0 or len(parts) != 6:
        return None
    return {"started": " ".join(parts[:5]), "command": parts[5]}


def supervisor_owned(record: dict) -> bool:
    identity = pid_identity(record.get("supervisor_pid"))
    scope = record.get("process_scope")
    return bool(
        identity
        and identity == record.get("supervisor_identity")
        and " _supervise " in f" {identity['command']} "
        and f" --token {record['token']} " in f" {identity['command']} "
        and (not scope or f" --scope-id {scope_id(scope)} " in f" {identity['command']} ")
    )


def scope_id(scope):
    return hashlib.sha256(str(Path(scope).resolve()).encode("utf-8")).hexdigest()


def observed_record(record: dict) -> dict:
    # The observer never replaces the supervisor's authoritative record.
    result = dict(record)
    if result.get("state") not in TERMINAL and not supervisor_owned(result):
        if result.get("supervisor_pid") is not None:
            result["state"] = "orphaned"
            result["error"] = "The recorded supervisor is absent or its identity changed; ownership cannot be confirmed"
    return result


def public(record: dict) -> dict:
    return {key: value for key, value in record.items() if key not in {"token", "supervisor_identity"}}


def emit(operation: str, record: dict | None = None, *, ok: bool = True, **observations) -> None:
    value = {"operation": operation, "ok": ok}
    if record is not None:
        value["process"] = public(record)
    if observations:
        value["observations"] = observations
    print(json.dumps(value, ensure_ascii=False))


def status_code(returncode: int | None) -> int:
    if returncode is None:
        return 1
    return returncode if returncode >= 0 else 128 - returncode


def require_platform() -> None:
    if os.name != "posix" or not all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "WEXITED")):
        raise ValueError("This helper requires a POSIX host with waitid/WNOWAIT; Windows is unsupported")


def start(args: argparse.Namespace) -> int:
    import fcntl

    # Ownership checks need ps. Fail before leaving an unconfirmable registration.
    if shutil.which("ps") is None:
        raise ValueError("process-management requires ps; install the procps package before starting a process")

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("Provide a command after --")
    path = record_path(args.state_dir, args.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / f"{args.name}.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            existing = observed_record(load_record(path))
            if existing.get("state") not in TERMINAL:
                emit("start", existing, ok=False, error="This name already has an active or unconfirmed process; inspect or stop it before reusing the name")
                return 1
        token = uuid.uuid4().hex
        artifacts = path.parent / f"{args.name}.{token}"
        artifacts.mkdir(mode=0o700)
        cwd = Path(args.cwd or os.getcwd()).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("--cwd must be a directory")
        record = {
            "version": 1, "name": args.name, "token": token,
            "record": str(path), "artifacts": str(artifacts),
            "state": "starting", "created_at": now(), "command": command, "cwd": str(cwd),
            "stdout": str(artifacts / "stdout.log"), "stderr": str(artifacts / "stderr.log"),
            "supervisor_log": str(artifacts / "supervisor.log"),
            "supervisor_pid": None, "child_pid": None, "returncode": None,
        }
        try:
            with scope_lock(os.environ.get(SCOPE_ENV)) as scope:
                if scope:
                    if (scope / "closed").exists():
                        raise ValueError("The agent turn's process scope is closed")
                    if args.persistent and not load_json(scope / "scope.json").get("allow_persistent"):
                        raise ValueError("Persistent processes are not allowed in this agent run")
                    record.update(process_scope=str(scope), persistent=args.persistent)
                save_record(path, record)
                with (artifacts / "supervisor.log").open("wb") as diagnostic:
                    supervisor_args = [sys.executable, str(Path(__file__).resolve()), "_supervise",
                                       "--record", str(path), "--token", token]
                    if scope:
                        supervisor_args.extend(["--scope-id", scope_id(scope)])
                    supervisor = subprocess.Popen(
                        supervisor_args,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=diagnostic,
                        close_fds=True, start_new_session=True,
                    )
        except OSError as error:
            record.update(state="failed", error=str(error), returncode=127, ended_at=now())
            save_record(path, record)
        deadline = time.monotonic() + args.startup_timeout
        while time.monotonic() < deadline:
            record = load_record(path)
            supervisor_exited = record["state"] == "starting" and supervisor.poll() is not None
            if supervisor_exited:
                # Identity and completion may have been published after our read.
                record = load_record(path)
            if record["state"] != "starting":
                emit("start", observed_record(record), ok=record["state"] not in {"failed", "orphaned"})
                return status_code(record["returncode"]) if record["state"] == "failed" else 0
            if supervisor_exited and record.get("supervisor_pid") is None and record.get("child_pid") is None:
                # The supervisor publishes its identity before it can spawn a
                # child. A confirmed earlier exit is consequently a failed start.
                record.update(state="failed", error="Supervisor exited before starting the managed command",
                              returncode=supervisor.returncode or 1, ended_at=now())
                save_record(path, record)
                emit("start", record, ok=False, error=record["error"])
                return status_code(record["returncode"])
            time.sleep(0.05)
        emit("start", observed_record(record), ok=False, error="Supervisor startup was not confirmed before the deadline; inspect this record before retrying")
        return 124


def group_members(pgid: int) -> list[int]:
    result = subprocess.run(["ps", "-e", "-o", "pid=", "-o", "pgid=", "-o", "stat="], capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise RuntimeError(f"Cannot inspect process group: {result.stderr.strip()}")
    members = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and int(parts[1]) == pgid and not parts[2].startswith("Z"):
            members.append(int(parts[0]))
    return members


def peek_exit(child: subprocess.Popen) -> bool:
    # WNOWAIT keeps the direct child's PID reserved until all group cleanup is done.
    # Its process-group ID consequently cannot be reused by an unrelated process.
    return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def signal_group(child: subprocess.Popen, signum: int) -> None:
    # The PID comes from our live Popen object, never from a mutable JSON record.
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        pass


def supervise(args: argparse.Namespace) -> int:
    path = Path(args.record).resolve(strict=True)
    record = load_record(path)
    if record["token"] != args.token or record["state"] != "starting":
        raise ValueError("Supervisor ownership token or initial state does not match")
    try:
        identity = pid_identity(os.getpid())
        if identity is None:
            raise RuntimeError("Supervisor identity could not be verified")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        # No child exists at this point. Record failure so close can confirm it.
        record.update(state="failed", error=str(error), returncode=127, ended_at=now())
        save_record(path, record)
        return 127
    record.update(supervisor_pid=os.getpid(), supervisor_identity=identity)
    scope = record.get("process_scope")
    if (scope_id(scope) if scope else None) != args.scope_id:
        raise ValueError("Supervisor process scope does not match its ownership arguments")
    if scope and (Path(scope) / "closed").exists():
        record.update(state="stopped", returncode=0, ended_at=now())
        save_record(path, record)
        return 0
    # Publish ownership before creating a child, making early-exit cleanup safe.
    save_record(path, record)
    artifacts = Path(record["artifacts"])
    request_path = artifacts / "stop.json"
    requested_signal = [False]
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: requested_signal.__setitem__(0, True))
    with Path(record["stdout"]).open("wb") as out, Path(record["stderr"]).open("wb") as err:
        try:
            child = subprocess.Popen(record["command"], cwd=record["cwd"], stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True, start_new_session=True)
        except OSError as error:
            record.update(state="failed", error=str(error), returncode=127, ended_at=now())
            save_record(path, record)
            return 127
        record.update(state="running", child_pid=child.pid, started_at=now())
        save_record(path, record)
        stopping = False
        shutdown_deadline: float | None = None
        forced = False
        try:
            while True:
                if not stopping and (requested_signal[0] or request_path.exists()):
                    request = load_json(request_path) if request_path.exists() else {"token": args.token, "grace": 3}
                    if request.get("token") == args.token:
                        grace = float(request.get("grace", 3))
                        if not 0 <= grace <= 60:
                            raise ValueError("Stop grace must be between 0 and 60 seconds")
                        stopping = True
                        shutdown_deadline = time.monotonic() + grace
                        record.update(state="stopping", stop_requested_at=now())
                        save_record(path, record)
                        signal_group(child, signal.SIGTERM)
                exited = peek_exit(child)
                if exited:
                    members = group_members(child.pid)
                    if not members:
                        returncode = child.wait()
                        record.update(state="stopped" if stopping else "exited", returncode=returncode, ended_at=now(), forced=forced)
                        save_record(path, record)
                        return 0
                    # A finished command does not leave its inherited children behind.
                    if shutdown_deadline is None:
                        shutdown_deadline = time.monotonic() + 1
                        signal_group(child, signal.SIGTERM)
                if shutdown_deadline is not None and time.monotonic() >= shutdown_deadline:
                    signal_group(child, signal.SIGKILL)
                    forced = True
                    shutdown_deadline = time.monotonic() + 1
                time.sleep(0.1)
        except BaseException as error:
            signal_group(child, signal.SIGKILL)
            child.wait()
            record.update(state="failed", error=str(error), returncode=1, ended_at=now())
            save_record(path, record)
            raise


def status(args: argparse.Namespace) -> int:
    emit("status", observed_record(load_record(record_path(args.state_dir, args.name))))
    return 0


def tail(path: Path, max_chars: int) -> dict:
    if not path.exists():
        return {"path": str(path), "available": False, "tail": "", "truncated": False}
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - max_chars * 4))
        data = stream.read()
    text = data.decode("utf-8", errors="replace")
    return {"path": str(path), "available": True, "bytes": size, "tail": text[-max_chars:], "truncated": size > len(data) or len(text) > max_chars}


def logs(args: argparse.Namespace) -> int:
    record = observed_record(load_record(record_path(args.state_dir, args.name)))
    streams = ("stdout", "stderr") if args.stream == "both" else (args.stream,)
    emit("logs", record, logs={name: tail(Path(record[name]), args.max_chars) for name in streams})
    return 0


class LogMatch:
    def __init__(self, record: dict, streams: list[str], text: str):
        self.paths = [Path(record[stream]) for stream in streams]
        self.match = text.encode("utf-8")
        self.positions = {path: 0 for path in self.paths}
        self.carry = {path: b"" for path in self.paths}

    def found(self) -> bool:
        for path in self.paths:
            if not path.exists():
                continue
            with path.open("rb") as stream:
                stream.seek(self.positions[path])
                while chunk := stream.read(65536):
                    data = self.carry[path] + chunk
                    self.positions[path] += len(chunk)
                    if self.match in data:
                        return True
                    self.carry[path] = data[-(len(self.match) - 1):] if len(self.match) > 1 else b""
        return False


def wait(args: argparse.Namespace) -> int:
    path = record_path(args.state_dir, args.name)
    record = load_record(path)
    token = record["token"]
    match = LogMatch(record, ["stdout", "stderr"] if args.stream == "both" else [args.stream], args.match) if args.match else None
    deadline = time.monotonic() + args.timeout
    condition = {"type": "log-substring", "text": args.match, "stream": args.stream} if match else {"type": "tcp", "host": args.tcp[0], "port": int(args.tcp[1])} if args.tcp else {"type": "exit"}
    while True:
        record = observed_record(load_record(path))
        if record["token"] != token:
            emit("wait", record, ok=False, error="The named process was replaced during this wait", condition=condition)
            return 1
        if record["state"] in TERMINAL:
            code = status_code(record.get("returncode"))
            if match or args.tcp:
                emit("wait", record, ok=False, ready=False, error="Process ended before readiness was confirmed", condition=condition)
                return code or 1
            emit("wait", record, ok=code == 0, condition=condition)
            return code
        if record["state"] == "orphaned":
            emit("wait", record, ok=False, ready=False, condition=condition, error=record["error"])
            return 1
        ready = False
        if record["state"] == "running":
            if match:
                ready = match.found()
            elif args.tcp:
                try:
                    with socket.create_connection((args.tcp[0], int(args.tcp[1])), timeout=min(0.2, max(0.01, deadline - time.monotonic()))):
                        ready = True
                except OSError:
                    pass
        if ready:
            emit("wait", record, ready=True, condition=condition)
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            emit("wait", record, ok=False, ready=False, timed_out=True, condition=condition, error="Wait deadline elapsed; the process was not stopped")
            return 124
        time.sleep(min(args.interval, remaining))


def stop(args: argparse.Namespace) -> int:
    path = record_path(args.state_dir, args.name)
    record = observed_record(load_record(path))
    if record["state"] in TERMINAL:
        emit("stop", record, already_ended=True)
        return 0
    if not supervisor_owned(record):
        emit("stop", record, ok=False, error="Cannot confirm the recorded supervisor identity; no process was signalled")
        return 1
    token = record["token"]
    atomic_json(Path(record["artifacts"]) / "stop.json", {"token": token, "grace": args.grace})
    deadline = time.monotonic() + args.timeout
    while True:
        record = observed_record(load_record(path))
        if record["token"] != token:
            emit("stop", record, ok=False, error="The named process was replaced while stopping; the replacement was not signalled")
            return 1
        if record["state"] in TERMINAL:
            emit("stop", record)
            return 0
        if record["state"] == "orphaned":
            emit("stop", record, ok=False, error=record["error"])
            return 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            emit("stop", record, ok=False, timed_out=True, error="Stop request was saved, but cleanup was not confirmed before the deadline")
            return 124
        time.sleep(min(0.1, remaining))


def cleanup(args: argparse.Namespace) -> int:
    """Stop only registered runs, using the same verified supervisor protocol as stop."""
    with scope_lock(args.scope) as scope:
        (scope / "closed").touch()
        paths = list((scope / "runs").glob("*.json"))
    deadline = time.monotonic() + 5
    pending = paths
    errors = []
    while pending:
        remaining = []
        for path in pending:
            try:
                record = load_json(path)
                token, name = record["token"], record["name"]
                if (record.get("process_scope") != str(scope) or token != path.stem
                        or not re.fullmatch(r"[0-9a-f]{32}", token) or not NAME.fullmatch(name)
                        or record["artifacts"] != str(Path(record["record"]).parent / f"{name}.{token}")):
                    raise ValueError("Ownership registration does not match this scope")
                if (args.preserve_persistent and record.get("persistent")
                        and load_json(scope / "scope.json").get("allow_persistent")):
                    continue
                owned = supervisor_owned(record)
                if record["state"] in TERMINAL:
                    if owned:
                        remaining.append(path)  # Confirm the supervisor itself has exited too.
                    continue
                if owned:
                    atomic_json(Path(record["artifacts"]) / "stop.json", {"token": token, "grace": 0.2})
                elif record.get("supervisor_pid") is not None:
                    raise ValueError("Supervisor identity is unavailable; cleanup cannot be confirmed")
                remaining.append(path)  # A registered start may still be launching its supervisor.
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                errors.append(f"{path.name}: {exc}")
        pending = remaining
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    errors.extend(f"{path.name}: cleanup deadline elapsed" for path in pending)
    emit("cleanup", ok=not errors, errors=errors)
    return int(bool(errors))


def positive(value: str) -> float:
    result = float(value)
    if not 0 < result <= 3600:
        raise argparse.ArgumentTypeError("Must be greater than 0 and at most 3600 seconds")
    return result


def grace_value(value: str) -> float:
    result = float(value)
    if not 0 <= result <= 60:
        raise argparse.ArgumentTypeError("Must be between 0 and 60 seconds")
    return result


def bounded_chars(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 100000:
        raise argparse.ArgumentTypeError("Must be between 1 and 100000 characters")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="operation", required=True)
    for operation in ("start", "status", "wait", "logs", "stop"):
        child = commands.add_parser(operation)
        child.add_argument("--state-dir", required=True, help="Persistent directory shared by independent shell calls")
        child.add_argument("--name", required=True, help="Local process name, not an arbitrary PID")
        if operation == "start":
            child.add_argument("--cwd", help="Working directory; defaults to this shell call's current directory")
            child.add_argument("--persistent", action="store_true", help="After its CLI turn succeeds, keep an ongoing service across session exit; forbidden in benchmark runs")
            child.add_argument("--startup-timeout", type=positive, default=5)
            child.add_argument("command", nargs=argparse.REMAINDER, help="Arguments after -- execute directly, without a shell")
        elif operation == "wait":
            child.add_argument("--timeout", type=positive, default=30)
            child.add_argument("--interval", type=positive, default=0.2)
            condition = child.add_mutually_exclusive_group()
            condition.add_argument("--match", help="Literal UTF-8 substring in fresh process logs; no regex")
            condition.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"), help="Wait until the managed process is running and this address accepts a connection")
            child.add_argument("--stream", choices=("stdout", "stderr", "both"), default="both")
        elif operation == "logs":
            child.add_argument("--stream", choices=("stdout", "stderr", "both"), default="both")
            child.add_argument("--max-chars", type=bounded_chars, default=6000)
        elif operation == "stop":
            child.add_argument("--grace", type=grace_value, default=3, help="Seconds before escalation from TERM to KILL")
            child.add_argument("--timeout", type=positive, default=10, help="How long this call waits for confirmed cleanup")
    supervisor = commands.add_parser("_supervise", help=argparse.SUPPRESS)
    supervisor.add_argument("--record", required=True)
    supervisor.add_argument("--token", required=True)
    supervisor.add_argument("--scope-id")
    cleaning = commands.add_parser("_cleanup", help=argparse.SUPPRESS)
    cleaning.add_argument("--scope", required=True)
    cleaning.add_argument("--preserve-persistent", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        require_platform()
        if args.operation == "wait" and args.match == "":
            raise ValueError("--match must not be empty")
        if args.operation == "wait" and args.tcp and not 1 <= int(args.tcp[1]) <= 65535:
            raise ValueError("TCP port must be between 1 and 65535")
        return {"start": start, "status": status, "wait": wait, "logs": logs, "stop": stop, "_supervise": supervise, "_cleanup": cleanup}[args.operation](args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        emit(args.operation, ok=False, error=str(error))
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
