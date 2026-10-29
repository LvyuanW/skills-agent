"""External Git history for files changed through the editing tool."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from uuid import uuid4


EXCLUDED_NAMES = frozenset()
HISTORY_REF = "refs/heads/history"
PATH_ENCODING = "absolute-hex-v1"


def _path_key(path):
    return "files/" + os.fsencode(os.path.abspath(path)).hex()


def _encoded_revision(repository, revision):
    manifest = repository / "path-map.json"
    if not manifest.exists():
        return False
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    if saved.get("format") != 1:
        raise ValueError("Unsupported file-history path manifest")
    return saved.get("revisions", {}).get(revision) == PATH_ENCODING


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
            if saved.get("format") not in {1, 2, 3} or saved.get("workspace") != str(self.workspace):
                raise ValueError("File history repository belongs to a different workspace")
        else:
            if (self.repository / "HEAD").exists():
                raise ValueError("Refuse to take over an existing Git repository without runtime ownership metadata")
            saved = {"workspace": str(self.workspace), "excluded_names": sorted(EXCLUDED_NAMES)}
        updated = {**saved, "format": 3, "mode": "editing-only", "excluded_names": [],
                   "path_encoding": PATH_ENCODING}
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

    def _register_revision(self, revision, paths=()):
        manifest_path = self.repository / "path-map.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("format") != 1:
                raise ValueError("Unsupported file-history path manifest")
        else:
            manifest = {"format": 1, "paths": {}, "revisions": {}}
        manifest["revisions"][revision] = PATH_ENCODING
        manifest["paths"].update({_path_key(path): str(path) for path in paths})
        _atomic_json(manifest_path, manifest, ensure_ascii=True)

    def _write_tree(self, states):
        """Build an absolute-path tree without reading any unrelated live files."""
        parent = self._head()
        index = self.repository / ("runtime-index-" + uuid4().hex)
        env = {**_environment(), "GIT_INDEX_FILE": str(index)}
        paths = set(states)
        try:
            updates = []
            if parent and _encoded_revision(self.repository, parent):
                _git(self.repository, "read-tree", parent, env=env)
            else:
                _git(self.repository, "read-tree", "--empty", env=env)
                if parent:
                    # Re-encode only the blobs already in the old history tree.
                    # Its commits remain unchanged and continue to use legacy paths.
                    entries = _git(self.repository, "ls-tree", "-r", "-z", parent).split(b"\0")
                    for entry in filter(None, entries):
                        description, name = entry.split(b"\t", 1)
                        mode, _, oid = description.split(b" ", 2)
                        absolute = str(self.workspace / os.fsdecode(name))
                        paths.add(absolute)
                        updates.append(mode + b" " + oid + b"\t" + _path_key(absolute).encode() + b"\0")
            for path, state in states.items():
                name = _path_key(path).encode("ascii")
                updates.append(b"0 " + b"0" * 40 + b"\t" + name + b"\0")
                if state is None:
                    continue
                oid = _git(self.repository, "hash-object", "-w", "--no-filters", "--stdin",
                           input=state.data).strip()
                mode = (b"120000" if getattr(state, "kind", "file") == "symlink" else
                        b"100755" if state.mode & 0o111 else b"100644")
                updates.append(mode + b" " + oid + b"\t" + name + b"\0")
            _git(self.repository, "update-index", "-z", "--index-info", input=b"".join(updates), env=env)
            tree = _git(self.repository, "write-tree", env=env).strip().decode("ascii")
        finally:
            index.unlink(missing_ok=True)
            index.with_name(index.name + ".lock").unlink(missing_ok=True)
        self._register_revision(tree, paths)
        return parent, tree

    def _commit_paths(self, states, metadata, phase):
        parent, tree = self._write_tree(states)
        if phase == "before":
            reference = "refs/edit-preimages/" + metadata["operation_id"]
            _git(self.repository, "update-ref", reference, tree)
            metadata["before_ref"] = reference
            return tree
        old_tree = (_git(self.repository, "rev-parse", parent + "^{tree}").strip().decode("ascii")
                    if parent else None)
        # A successful changed batch gets its own commit even when an external
        # writer changed the live file and this edit restores the previous tree.
        # Genuine no-ops already return before preparing any history objects.
        if phase != "after" and tree == old_tree:
            self.last_checkpoint = parent
            return parent
        details = {key: metadata.get(key) for key in ("operation_id", "session_id", "tool_call_id",
                   "operation", "path", "destination", "operations", "before_revision", "before_ref")}
        details.update(phase=phase, paths=list(states), path_encoding=PATH_ENCODING)
        message = "File edit batch " + phase + "\n\n" + json.dumps(details, ensure_ascii=True, sort_keys=True) + "\n"
        commit = _git(self.repository, "commit-tree", tree, *(["-p", parent] if parent else []),
                      input=message.encode("utf-8")).strip().decode("ascii")
        self._register_revision(commit, states)
        _git(self.repository, "update-ref", HISTORY_REF, commit, parent or "0" * 40)
        self.last_checkpoint = commit
        return commit

    @staticmethod
    def _summaries(states):
        return {path: None if value is None else {"bytes": len(value.data), "mode": value.mode,
                "kind": getattr(value, "kind", "file"),
                "sha256": hashlib.sha256(value.data).hexdigest()} for path, value in states.items()}

    def edit(self, operation, path, *, content=None, old_text=None, new_text=None,
             destination=None, tool_call_id=None, session_id=None, **options):
        edit = {"operation": operation, "path": path, **options}
        for name, value in (("content", content), ("old_text", old_text),
                            ("new_text", new_text), ("destination", destination)):
            if value is not None:
                edit[name] = value
        result = self.edit_batch([edit], tool_call_id=tool_call_id, session_id=session_id)
        return {**result, **result["edits"][0]}

    def edit_batch(self, edits, *, tool_call_id=None, session_id=None):
        from .file_editing import EditError, prepare_batch
        with self._mutex:
            if self._closed:
                raise EditError("File editing history is closed", details={"applied": False, "changed": False})
            with _repository_lock(self.repository):
                with prepare_batch(self.workspace, edits, excluded_roots=self.excluded_roots) as plan:
                    changed_paths = list(plan.changed_paths)
                    before = {name: plan.before[name] for name in changed_paths}
                    review = {**plan.review, "operation": "batch", "edit_count": len(edits),
                              "edits": plan.review.get("operations", [plan.review]),
                              "changed_paths": changed_paths}
                    if not plan.changed:
                        for name, target in plan.targets.items():
                            target.verify(plan.before[name])
                        return {**review, "changed": False, "applied": False, "operation_id": None,
                                "before_revision": None, "after_revision": None}
                    identifier = uuid4().hex
                    record = {**review, "operation_id": identifier,
                              "session_id": self.session_id if session_id is None else str(session_id),
                              "tool_call_id": tool_call_id, "status": "prepared", "applied": False,
                              "before_revision": None, "after_revision": None,
                              "path_encoding": PATH_ENCODING,
                              "path_mapping": {name: _path_key(name) for name in changed_paths},
                              "before": self._summaries(before),
                              "created_at": datetime.now(timezone.utc).isoformat()}
                    record_path = self.repository / "operations" / (identifier + ".json")
                    try:
                        self._initialize()
                        _atomic_json(record_path, record, ensure_ascii=True)
                        record["before_revision"] = self._commit_paths(before, record, "before")
                        _atomic_json(record_path, record, ensure_ascii=True)
                        observed_after = plan.apply()
                        after = {name: observed_after[name] for name in changed_paths}
                        record.update(applied=True, applied_paths=list(plan.applied_paths),
                                      after=self._summaries(after))
                        record["after_revision"] = self._commit_paths(after, record, "after")
                        record.update(status="succeeded", completed_at=datetime.now(timezone.utc).isoformat())
                        _atomic_json(record_path, record, ensure_ascii=True)
                        self.last_error = None
                        self._record_status()
                        return {**review, "changed": True, "applied": True, "operation_id": identifier,
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
                                result_states = {name: observed[name] for name in changed_paths}
                                record["after"] = self._summaries(result_states)
                                record["after_revision"] = self._commit_paths(result_states, record, "failed result")
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
    resolved = _git(repository, "rev-parse", "--verify", "--end-of-options",
                    str(revision) + "^{}").strip().decode("ascii")
    if _git(repository, "cat-file", "-t", resolved).strip() not in {b"commit", b"tree"}:
        raise ValueError("History revision must identify a commit or preimage tree")
    return resolved


def _existing_head(repository):
    if not (repository / "HEAD").is_file():
        return None
    result = _git(repository, "rev-parse", "--verify", HISTORY_REF, check=False)
    return result.stdout.strip().decode("ascii") if result.returncode == 0 else None


def _path(value):
    if not value or "\0" in value:
        raise ValueError("Use a nonempty file path without NUL bytes")
    return value


def _query_paths(repository, value, revision=None):
    metadata = json.loads((repository / "workspace.json").read_text(encoding="utf-8"))
    workspace = Path(metadata["workspace"])
    absolute = Path(os.path.abspath(workspace / Path(value).expanduser()))
    absolute = absolute.parent.resolve() / absolute.name
    encoded = _path_key(absolute)
    legacy = absolute.relative_to(workspace).as_posix() if absolute.is_relative_to(workspace) else None
    if revision is not None:
        if _encoded_revision(repository, revision):
            return str(absolute), [encoded]
        if legacy is None:
            raise ValueError("This legacy revision has no history for paths outside its original workspace")
        return str(absolute), [legacy]
    return str(absolute), list(dict.fromkeys([encoded, *([legacy] if legacy else [])]))


def _display_diff(repository, output, before, after):
    manifest = repository / "path-map.json"
    if not manifest.exists():
        return output
    names = json.loads(manifest.read_text(encoding="utf-8"))["paths"]
    encoded = {b"a": _encoded_revision(repository, before), b"b": _encoded_revision(repository, after)}
    token = re.compile(rb"(?:(?P<side>[ab])/)?(?P<key>files/[0-9a-f]+)(?=\s|$)")
    result = []
    for line in output.splitlines(keepends=True):
        side = (b"a" if line.startswith((b"--- ", b"rename from ", b"copy from ")) else
                b"b" if line.startswith((b"+++ ", b"rename to ", b"copy to ")) else None)
        if side is not None or line.startswith((b"diff --git ", b"Binary files ")):
            def replace(match):
                path = names.get(match["key"].decode("ascii"))
                if path is None or not encoded.get(match["side"] or side):
                    return match[0]
                return json.dumps(path, ensure_ascii=False).encode("utf-8", errors="backslashreplace")
            line = token.sub(replace, line)
        result.append(line)
    return b"".join(result)


def _log_records(repository, limit, path=None):
    head = _existing_head(repository)
    absolute, candidates = _query_paths(repository, path) if path else (None, [])
    legacy_matches = None
    if head and path:
        legacy_matches = set(_git(repository, "log", "--format=%H", HISTORY_REF, "--", *candidates)
                             .decode("ascii").splitlines())
    raw = (_git(repository, "log", "-z", "--format=%H%x00%aI%x00%B", HISTORY_REF) if head else b"")
    fields = raw.decode("utf-8", errors="replace").rstrip("\0").split("\0") if raw else []
    records, by_operation = [], {}
    for index in range(0, len(fields), 3):
        revision, timestamp, message = fields[index:index + 3]
        record = {"checkpoint": revision, "time": timestamp, "message": message.strip()}
        if _encoded_revision(repository, revision):
            details = json.loads(record["message"].split("\n\n", 1)[1])
            record.update(operation_id=details["operation_id"], before_revision=details.get("before_revision"),
                          after_revision=revision, paths=details.get("paths", []), status="recorded")
            by_operation[details["operation_id"]] = record
        elif legacy_matches is not None and revision not in legacy_matches:
            continue
        records.append(record)
    # Failed or interrupted batches can have a recoverable preimage and no
    # after-commit. Include them once, and expose both revisions without requiring
    # callers to inspect runtime-internal JSON files.
    for record_path in (repository / "operations").glob("*.json"):
        operation = json.loads(record_path.read_text(encoding="utf-8"))
        if operation.get("path_encoding") != PATH_ENCODING:
            continue
        identifier = operation["operation_id"]
        record = by_operation.get(identifier)
        if record is None:
            record = {"checkpoint": operation.get("after_revision"), "operation_id": identifier,
                      "message": "File edit batch " + operation["status"]}
            records.append(record)
        record.update(before_revision=operation.get("before_revision"),
                      after_revision=record["checkpoint"] or operation.get("after_revision"),
                      status=operation["status"], paths=operation.get("changed_paths", []),
                      time=operation.get("completed_at", operation["created_at"]))
    if absolute is not None:
        records = [record for record in records if "paths" not in record or absolute in record["paths"]]
    records.sort(key=lambda record: record["time"], reverse=True)
    return records[:limit]


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
            print(json.dumps(_log_records(repository, args.limit, args.path), ensure_ascii=False, indent=2))
        elif args.command == "diff":
            paths = ["--", *_query_paths(repository, args.path)[1]] if args.path else ["--"]
            before, after = _revision(repository, args.before), _revision(repository, args.after)
            output = _git(repository, "diff", "--no-ext-diff", "--no-textconv",
                          before, after, *paths)
            sys.stdout.buffer.write(_display_diff(repository, output, before, after))
        else:
            revision = _revision(repository, args.revision)
            path = _query_paths(repository, args.path, revision)[1][0]
            sys.stdout.buffer.write(_git(repository, "cat-file", "blob", revision + ":" + path))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
