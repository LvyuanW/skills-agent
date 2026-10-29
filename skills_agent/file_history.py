"""External Git history for files changed through the editing tool."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import threading
from uuid import uuid4


EXCLUDED_NAMES = frozenset({
    ".git", ".agent", ".skills-agent", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})
HISTORY_REF = "refs/heads/history"
def _environment():
    # Runtime history must not inherit the user's index, filters or Git config.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="skills-agent runtime", GIT_AUTHOR_EMAIL="runtime@skills-agent.local",
               GIT_COMMITTER_NAME="skills-agent runtime", GIT_COMMITTER_EMAIL="runtime@skills-agent.local")
    return env


def _git(repository, *args, input=None, stdin=None, env=None, check=True, pass_fds=()):
    result = subprocess.run(
        ["git", "--literal-pathspecs", "--git-dir", str(repository), "-c", "core.hooksPath=" + os.devnull,
         "-c", "core.autocrlf=false", *map(str, args)],
        input=input, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env or _environment(), timeout=120, pass_fds=pass_fds,
    )
    if check and result.returncode:
        raise RuntimeError("File history Git operation failed: " +
                           result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout if check else result


@contextmanager
def _repository_lock(repository):
    lock = Path(repository).parent / (Path(repository).name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _atomic_json(path, value, *, ensure_ascii=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=ensure_ascii, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class WorkspaceHistory:
    """Record only edits performed through the dedicated file-editing tool."""

    def __init__(self, workspace, repository, *, session_id, excluded_roots=()):
        self.workspace = Path(workspace).expanduser().resolve()
        self.repository = Path(repository).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"History workspace does not exist: {self.workspace}")
        workspace_parts = tuple(part.casefold() for part in self.workspace.parts)
        repository_parts = tuple(part.casefold() for part in self.repository.parts)
        if workspace_parts[:len(repository_parts)] == repository_parts:
            raise ValueError("The history repository cannot contain the workspace")
        self.session_id = str(session_id)
        self.excluded_roots = tuple({self.repository,
                                    self.repository.parent / (self.repository.name + ".lock"), *(
            Path(path).expanduser().resolve() for path in excluded_roots
        )})
        self.last_error = None
        self.pending_error = None
        self.last_checkpoint = None
        self._mutex = threading.RLock()
        self._closed = False
        self._owns_repository = False
        self._instance_id = uuid4().hex

    def start(self):
        """Compatibility entry point; never scan, watch, or initialize storage."""
        if self._closed:
            raise RuntimeError("File editing history is closed")
        return self

    def close(self):
        with self._mutex:
            self._closed = True

    def checkpoint(self, reason="manual checkpoint"):
        """Compatibility only: programs and shell writes are not recorded."""
        return self.last_checkpoint

    def status(self):
        return {"workspace": str(self.workspace), "repository": str(self.repository),
                "session_id": self.session_id, "pid": os.getpid(), "mode": "editing-only",
                "monitoring": False, "initialized": (self.repository / "HEAD").is_file(),
                "checkpoint": self.last_checkpoint, "last_error": self.last_error,
                "pending_error": None, "closed": self._closed,
                "excluded_names": sorted(EXCLUDED_NAMES),
                "excluded_roots": [str(path) for path in sorted(self.excluded_roots)],
                "recorded_at": datetime.now(timezone.utc).isoformat()}

    def _initialize(self):
        metadata = self.repository / "workspace.json"
        self._owns_repository = False
        if metadata.exists():
            saved = json.loads(metadata.read_text(encoding="utf-8"))
            if saved.get("format") not in {1, 2} or saved.get("workspace") != str(self.workspace):
                raise ValueError("File history repository belongs to a different workspace")
        else:
            if (self.repository / "HEAD").exists():
                raise ValueError("Refuse to take over an existing Git repository without runtime ownership metadata")
            saved = {"workspace": str(self.workspace), "excluded_names": sorted(EXCLUDED_NAMES)}
        updated = {**saved, "format": 2, "mode": "editing-only"}
        if updated != saved:
            _atomic_json(metadata, updated, ensure_ascii=True)
        self._owns_repository = True
        if not (self.repository / "HEAD").exists():
            _git(self.repository, "init", "--bare", "--initial-branch=history", self.repository)

    def _record_status(self):
        if self._owns_repository:
            _atomic_json(self.repository / "runtime" / (self._instance_id + ".json"),
                         self.status(), ensure_ascii=True)

    def _head(self):
        result = _git(self.repository, "rev-parse", "--verify", HISTORY_REF, check=False)
        return result.stdout.strip().decode("ascii") if result.returncode == 0 else None

    def _commit_paths(self, states, metadata, phase):
        """Change only the operation's paths in the existing history tree."""
        parent = self._head()
        index = self.repository / ("runtime-index-" + uuid4().hex)
        env = {**_environment(), "GIT_INDEX_FILE": str(index)}
        try:
            if parent:
                _git(self.repository, "read-tree", parent, env=env)
            else:
                _git(self.repository, "read-tree", "--empty", env=env)
            updates = []
            if parent:
                names = _git(self.repository, "ls-tree", "-r", "-z", "--name-only", parent).split(b"\0")
                for raw_name in filter(None, names):
                    name = os.fsdecode(raw_name)
                    if any(name == path or name.startswith(path + "/") or path.startswith(name + "/")
                           for path in states):
                        updates.append(b"0 " + b"0" * 40 + b"\t" + raw_name + b"\0")
            for name, state in states.items():
                if state is None:
                    continue
                oid = _git(self.repository, "hash-object", "-w", "--no-filters", "--stdin",
                           input=state.data).strip()
                mode = b"100755" if state.mode & 0o111 else b"100644"
                updates.append(mode + b" " + oid + b"\t" + os.fsencode(name) + b"\0")
            _git(self.repository, "update-index", "-z", "--index-info", input=b"".join(updates), env=env)
            tree = _git(self.repository, "write-tree", env=env).strip().decode("ascii")
        finally:
            index.unlink(missing_ok=True)
            index.with_name(index.name + ".lock").unlink(missing_ok=True)
        old_tree = (_git(self.repository, "rev-parse", parent + "^{tree}").strip().decode("ascii")
                    if parent else None)
        if tree == old_tree:
            self.last_checkpoint = parent
            return parent
        details = {key: metadata.get(key) for key in ("operation_id", "session_id", "tool_call_id",
                   "operation", "path", "destination")}
        details["phase"] = phase
        message = "File edit " + phase + "\n\n" + json.dumps(details, ensure_ascii=True, sort_keys=True) + "\n"
        commit = _git(self.repository, "commit-tree", tree, *(["-p", parent] if parent else []),
                      input=message.encode("utf-8")).strip().decode("ascii")
        _git(self.repository, "update-ref", HISTORY_REF, commit, parent or "0" * 40)
        self.last_checkpoint = commit
        return commit

    @staticmethod
    def _summaries(states):
        return {path: None if value is None else {"bytes": len(value.data), "mode": value.mode,
                "sha256": hashlib.sha256(value.data).hexdigest()} for path, value in states.items()}

    def edit(self, operation, path, *, content=None, old_text=None, new_text=None,
             destination=None, tool_call_id=None, session_id=None):
        from .file_editing import EditError, prepare_edit
        with self._mutex:
            if self._closed:
                raise EditError("File editing history is closed", details={"applied": False, "changed": False})
            with _repository_lock(self.repository):
                with prepare_edit(self.workspace, operation, path, content=content, old_text=old_text,
                                  new_text=new_text, destination=destination,
                                  protected_names=EXCLUDED_NAMES, excluded_roots=self.excluded_roots) as plan:
                    if not plan.changed:
                        for name, target in plan.targets.items():
                            target.verify(plan.before[name])
                        return {**plan.review, "changed": False, "applied": False, "operation_id": None,
                                "before_revision": None, "after_revision": None}
                    identifier = uuid4().hex
                    record = {**plan.review, "operation_id": identifier,
                              "session_id": self.session_id if session_id is None else str(session_id),
                              "tool_call_id": tool_call_id, "status": "prepared", "applied": False,
                              "before_revision": None, "after_revision": None,
                              "before": self._summaries(plan.before),
                              "created_at": datetime.now(timezone.utc).isoformat()}
                    record_path = self.repository / "operations" / (identifier + ".json")
                    try:
                        self._initialize()
                        _atomic_json(record_path, record, ensure_ascii=True)
                        record["before_revision"] = self._commit_paths(plan.before, record, "before")
                        _atomic_json(record_path, record, ensure_ascii=True)
                        after = plan.apply()
                        record.update(applied=True, applied_paths=list(plan.applied_paths),
                                      after=self._summaries(after))
                        record["after_revision"] = self._commit_paths(after, record, "after")
                        record.update(status="succeeded", completed_at=datetime.now(timezone.utc).isoformat())
                        _atomic_json(record_path, record, ensure_ascii=True)
                        self.last_error = None
                        self._record_status()
                        return {**plan.review, "changed": True, "applied": True, "operation_id": identifier,
                                "before_revision": record["before_revision"],
                                "after_revision": record["after_revision"], "history_repository": str(self.repository)}
                    except BaseException as exc:
                        self.last_error = str(exc) or type(exc).__name__
                        applied, changed = plan.applied, plan.applied
                        observed, observed_changed = None, []
                        if plan.attempted_paths:
                            try:
                                observed = {name: target.read() for name, target in plan.targets.items()}
                                observed_changed = [name for name, value in observed.items()
                                    if self._summaries({name: value}) != self._summaries({name: plan.before[name]})]
                                if not applied and observed_changed:
                                    applied, changed = None, True
                            except BaseException as observation_error:
                                record["recovery_error"] = str(observation_error) or type(observation_error).__name__
                                if not applied:
                                    applied, changed = None, None
                        record.update(status="failed" if isinstance(exc, Exception) else "interrupted",
                                      error=self.last_error, applied=applied, changed=changed,
                                      application_state="uncertain" if applied is None else
                                                        "confirmed" if applied else "not-applied",
                                      applied_paths=list(plan.applied_paths),
                                      attempted_paths=list(plan.attempted_paths),
                                      observed_changed_paths=observed_changed,
                                      completed_at=datetime.now(timezone.utc).isoformat())
                        if observed is not None and not record["after_revision"]:
                            try:
                                record["after"] = self._summaries(observed)
                                record["after_revision"] = self._commit_paths(observed, record, "failed result")
                            except BaseException as recovery_error:
                                record["recovery_error"] = str(recovery_error) or type(recovery_error).__name__
                        if self._owns_repository:
                            try:
                                _atomic_json(record_path, record, ensure_ascii=True)
                                self._record_status()
                            except BaseException as record_error:
                                record["recording_error"] = str(record_error) or type(record_error).__name__
                        if not isinstance(exc, Exception):
                            raise
                        details = {"operation_id": identifier, "applied": applied,
                                   "changed": changed, "application_state": record["application_state"],
                                   "applied_paths": list(plan.applied_paths),
                                   "attempted_paths": list(plan.attempted_paths),
                                   "observed_changed_paths": observed_changed,
                                   "before_revision": record["before_revision"],
                                   "after_revision": record["after_revision"],
                                   "history_repository": str(self.repository)}
                        for key in ("recovery_error", "recording_error"):
                            if key in record:
                                details[key] = record[key]
                        message = f"File edit failed: {exc}"
                        if record["before_revision"]:
                            message += f"; original content is saved at {record['before_revision']}"
                        if plan.applied:
                            message += "; workspace changes were applied; inspect the files before retrying"
                        elif applied is None:
                            message += "; publication was attempted and its outcome is uncertain; inspect the files before retrying"
                        raise EditError(message, details=details) from exc


def _revision(repository, revision):
    return _git(repository, "rev-parse", "--verify", "--end-of-options",
                str(revision) + "^{commit}").strip().decode("ascii")


def _existing_head(repository):
    if not (repository / "HEAD").is_file():
        return None
    result = _git(repository, "rev-parse", "--verify", HISTORY_REF, check=False)
    return result.stdout.strip().decode("ascii") if result.returncode == 0 else None


def _path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Use a workspace-relative file path without '..'")
    return path.as_posix()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect runtime file checkpoints without modifying the workspace")
    parser.add_argument("--repository", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    log = commands.add_parser("log")
    log.add_argument("--path", type=_path)
    log.add_argument("--limit", type=int, default=20)
    diff = commands.add_parser("diff")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--path", type=_path)
    show = commands.add_parser("show")
    show.add_argument("revision")
    show.add_argument("path", type=_path)
    args = parser.parse_args(argv)
    repository = args.repository.expanduser().resolve()
    try:
        if args.command == "log" and not 1 <= args.limit <= 1000:
            raise ValueError("--limit must be between 1 and 1000")
        if not (repository / "workspace.json").is_file():
            if args.command in {"status", "log"} and not (repository / "HEAD").exists():
                value = ({"repository": str(repository), "mode": "editing-only", "initialized": False,
                          "checkpoint": None, "monitoring": False, "last_error": None, "observers": []}
                         if args.command == "status" else [])
                print(json.dumps(value, indent=2))
                return 0
            raise ValueError(f"No runtime file history found at {repository}")
        if args.command == "status":
            metadata = json.loads((repository / "workspace.json").read_text())
            observers = []
            for path in sorted((repository / "runtime").glob("*.json")):
                item = json.loads(path.read_text())
                item["monitoring"] = False
                observers.append(item)
            observers.sort(key=lambda item: item.get("recorded_at", ""))
            head = _existing_head(repository)
            print(json.dumps({**metadata, "repository": str(repository), "checkpoint": head,
                              "mode": metadata.get("mode", "legacy-snapshots"),
                              "initialized": (repository / "HEAD").is_file(), "monitoring": False,
                              "last_error": next((item["last_error"] for item in reversed(observers)
                                                  if item.get("last_error")), None),
                              "observers": observers}, ensure_ascii=False, indent=2))
        elif args.command == "log":
            if _existing_head(repository) is None:
                print("[]")
                return 0
            paths = ["--", args.path] if args.path else []
            raw = _git(repository, "log", "-z", "--format=%H%x00%aI%x00%B",
                       f"--max-count={args.limit}", HISTORY_REF, *paths)
            fields = raw.decode("utf-8", errors="replace").rstrip("\0").split("\0") if raw else []
            records = [{"checkpoint": fields[i], "time": fields[i + 1], "message": fields[i + 2].strip()}
                       for i in range(0, len(fields), 3)]
            print(json.dumps(records, ensure_ascii=False, indent=2))
        elif args.command == "diff":
            paths = ["--", args.path] if args.path else ["--"]
            output = _git(repository, "diff", "--no-ext-diff", "--no-textconv",
                          _revision(repository, args.before), _revision(repository, args.after), *paths)
            sys.stdout.buffer.write(output)
        else:
            commit = _revision(repository, args.revision)
            sys.stdout.buffer.write(_git(repository, "cat-file", "blob", commit + ":" + args.path))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
