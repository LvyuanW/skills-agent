"""Automatic, external Git checkpoints of files observed in a workspace.

The watcher observes states, not every write syscall. Checkpoints can contain
changes from any process sharing the workspace; an observer is not an author.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import threading
import time
from uuid import uuid4


EXCLUDED_NAMES = frozenset({
    ".git", ".agent", ".skills-agent", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})
HISTORY_REF = "refs/heads/history"
BLOB_BATCH_SIZE = 64
BLOB_BATCH_BYTES = 32 * 1024 * 1024


class TransientScanError(RuntimeError):
    """A concurrent writer changed a file while it was being captured."""


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


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _cache_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode("ascii")).hexdigest()


class WorkspaceHistory:
    """Watch one workspace and save recoverable file contents outside it."""

    def __init__(self, workspace, repository, *, session_id, excluded_roots=()):
        self.workspace = Path(workspace).expanduser().resolve()
        self.repository = Path(repository).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"History workspace does not exist: {self.workspace}")
        if self.repository == self.workspace or self.workspace.is_relative_to(self.repository):
            raise ValueError("The history repository cannot contain the workspace")
        self.session_id = str(session_id)
        self.excluded_roots = tuple({self.repository,
                                    self.repository.parent / (self.repository.name + ".lock"), *(
            Path(path).expanduser().resolve() for path in excluded_roots
        )})
        self.last_error = None
        self.pending_error = None
        self.last_checkpoint = None
        self._cache = {}
        self._cache_loaded = False
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._mutex = threading.RLock()
        self._lifecycle = threading.RLock()
        self._thread = None
        self._watching = False
        self._closed = False
        self._owns_repository = False
        self._instance_id = uuid4().hex

    def _excluded(self, path):
        relative = path.relative_to(self.workspace)
        return (any(part in EXCLUDED_NAMES for part in relative.parts[:-1])
                or path.name in {".git", ".agent", ".skills-agent"}
                or (path.name in EXCLUDED_NAMES and path.is_dir())
                or any(path == root or path.is_relative_to(root) for root in self.excluded_roots))

    def _initialize(self):
        metadata = self.repository / "workspace.json"
        self._owns_repository = False
        if metadata.exists():
            saved = json.loads(metadata.read_text(encoding="utf-8"))
            if saved.get("format") != 1 or saved.get("workspace") != str(self.workspace):
                raise ValueError("File history repository belongs to a different workspace")
        else:
            if (self.repository / "HEAD").exists():
                raise ValueError("Refuse to take over an existing Git repository without runtime ownership metadata")
            # Establish ownership before Git initialization; interrupted initialization
            # can then resume without mistaking the partial repository for a user's.
            _atomic_json(metadata, {"workspace": str(self.workspace),
                                    "excluded_names": sorted(EXCLUDED_NAMES),
                                    "format": 1})
        self._owns_repository = True
        if not (self.repository / "HEAD").exists():
            _git(self.repository, "init", "--bare", "--initial-branch=history", self.repository)
        if not self._cache_loaded:
            self._cache_loaded = True
            self._load_blob_cache()

    def _load_blob_cache(self):
        """Reuse unchanged file hashes across starts; validate the referenced blobs."""
        try:
            saved = json.loads((self.repository / "file-cache.json").read_text(encoding="utf-8"))
            payload = saved["payload"]
            if (payload["version"] != 1 or payload["workspace"] != str(self.workspace)
                    or saved["sha256"] != _cache_digest(payload)):
                return
            entries = {}
            for name, value in payload["entries"].items():
                if (not isinstance(name, str) or not name or PurePosixPath(name).is_absolute()
                        or ".." in PurePosixPath(name).parts
                        or not isinstance(value, list) or len(value) != 2):
                    return
                fingerprint, oid = value
                if (not isinstance(fingerprint, list) or len(fingerprint) != 6
                        or not all(type(part) is int for part in fingerprint)
                        or not isinstance(oid, str) or len(oid) != 40
                        or any(char not in "0123456789abcdef" for char in oid)):
                    return
                entries[name] = (tuple(fingerprint), oid)
            if not entries:
                return
            identifiers = sorted({value[1] for value in entries.values()})
            result = _git(self.repository, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                          input=("\n".join(identifiers) + "\n").encode("ascii"), check=False)
            if result.returncode:
                return
            verified = {line.split()[0].decode("ascii") for line in result.stdout.splitlines()
                        if len(line.split()) == 2 and line.split()[1] == b"blob"}
            self._cache.update({name: value for name, value in entries.items() if value[1] in verified})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
            # The cache is optional; a miss takes the normal safe file-read path.
            return

    def _save_blob_cache(self):
        payload = {"version": 1, "workspace": str(self.workspace), "entries": self._cache}
        try:
            _atomic_json(self.repository / "file-cache.json",
                         {"payload": payload, "sha256": _cache_digest(payload)}, ensure_ascii=True)
        except OSError:
            pass  # The committed snapshot remains valid without this optimization.

    def _record_status(self):
        if not self._owns_repository:
            return
        _atomic_json(self.repository / "runtime" / (self._instance_id + ".json"), self.status())

    def status(self):
        return {"workspace": str(self.workspace), "repository": str(self.repository),
                "session_id": self.session_id, "pid": os.getpid(),
                "monitoring": bool(self._watching and not self._closed),
                "checkpoint": self.last_checkpoint, "last_error": self.last_error,
                "pending_error": self.pending_error,
                "excluded_names": sorted(EXCLUDED_NAMES),
                "excluded_roots": [str(path) for path in sorted(self.excluded_roots)],
                "recorded_at": datetime.now(timezone.utc).isoformat()}

    @contextmanager
    def _parent_directory(self, path):
        """Keep each parent open without following a raced-in directory symlink."""
        relative = path.relative_to(self.workspace)
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(self.workspace, flags)
            try:
                for component in relative.parts[:-1]:
                    child = os.open(component, flags, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                yield descriptor, relative.name
            finally:
                os.close(descriptor)
        else:
            if not path.parent.resolve().is_relative_to(self.workspace):
                raise ValueError("History path escaped its workspace")
            yield None, str(path)

    def _open_regular(self, path):
        # Keep the safe descriptor open until hashing and the final fstat finish.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with self._parent_directory(path) as (parent, name):
            descriptor = os.open(name, flags, dir_fd=parent)
        return os.fdopen(descriptor, "rb")

    def _regular_blob(self, path, info):
        """Single-file fallback for platforms without inherited /dev/fd paths."""
        relative = path.relative_to(self.workspace).as_posix()
        cached = self._cache.get(relative)
        fingerprint = _fingerprint(info)
        if cached and cached[0] == fingerprint:
            return cached[1]
        for _ in range(3):
            with self._open_regular(path) as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise TransientScanError(f"File changed type during history scan: {relative}")
                oid = _git(self.repository, "hash-object", "-w", "--stdin", stdin=stream).strip().decode("ascii")
                after = os.fstat(stream.fileno())
            if _fingerprint(before) == _fingerprint(after):
                self._cache[relative] = (_fingerprint(after), oid)
                return oid
        raise TransientScanError(f"File kept changing during history scan: {relative}")

    def _regular_blobs(self, requests):
        """Hash bounded batches of safely opened files with one Git process each."""
        results = {}
        try:
            if os.name != "posix" or not Path("/dev/fd").is_dir():
                for path, info in requests:
                    results[path.relative_to(self.workspace).as_posix()] = self._regular_blob(path, info)
                return results
            offset = 0
            while offset < len(requests):
                end, size = offset, 0
                while end < len(requests) and end - offset < BLOB_BATCH_SIZE:
                    next_size = requests[end][1].st_size
                    if end > offset and size + next_size > BLOB_BATCH_BYTES:
                        break
                    size += next_size
                    end += 1
                batch = requests[offset:end]
                offset = end
                with ExitStack() as stack:
                    opened = []
                    for path, info in batch:
                        stream = stack.enter_context(self._open_regular(path))
                        before = os.fstat(stream.fileno())
                        if not stat.S_ISREG(before.st_mode) or _fingerprint(before) != _fingerprint(info):
                            raise TransientScanError(f"File changed before history read: {path}")
                        opened.append((path.relative_to(self.workspace).as_posix(), stream, before))
                    descriptors = tuple(stream.fileno() for _, stream, _ in opened)
                    paths = b"".join(f"/dev/fd/{descriptor}\n".encode("ascii") for descriptor in descriptors)
                    raw = _git(self.repository, "hash-object", "-w", "--no-filters", "--stdin-paths",
                               input=paths, pass_fds=descriptors)
                    identifiers = raw.decode("ascii").splitlines()
                    if len(identifiers) != len(opened):
                        raise RuntimeError("Git did not return one object for each file history input")
                    for relative, stream, before in opened:
                        if _fingerprint(before) != _fingerprint(os.fstat(stream.fileno())):
                            raise TransientScanError(f"File changed during history read: {relative}")
                    for (relative, _, before), oid in zip(opened, identifiers):
                        self._cache[relative] = (_fingerprint(before), oid)
                        results[relative] = oid
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EINVAL):
                raise TransientScanError(f"File changed during history scan: {exc.filename}") from exc
            raise
        return results

    def _entries(self):
        entries = []
        requests = []
        present = set()
        def scan_error(error):
            if error.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                raise TransientScanError(f"Directory changed during history scan: {error.filename}") from error
            raise error
        for directory, folders, files in os.walk(self.workspace, followlinks=False, onerror=scan_error):
            base = Path(directory)
            kept = []
            for name in folders:
                path = base / name
                if self._excluded(path):
                    continue
                if path.is_symlink():
                    files.append(name)
                else:
                    kept.append(name)
            folders[:] = kept
            for name in files:
                path = base / name
                if self._excluded(path):
                    continue
                relative = path.relative_to(self.workspace).as_posix()
                try:
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode):
                        with self._parent_directory(path) as (parent, name):
                            target = os.readlink(name, dir_fd=parent)
                        oid = _git(self.repository, "hash-object", "-w", "--stdin",
                                   input=os.fsencode(target)).strip().decode("ascii")
                        mode = "120000"
                    elif stat.S_ISREG(info.st_mode):
                        cached = self._cache.get(relative)
                        oid = cached[1] if cached and cached[0] == _fingerprint(info) else None
                        if oid is None:
                            requests.append((path, info))
                        mode = "100755" if info.st_mode & 0o111 else "100644"
                    else:
                        continue
                except OSError as exc:
                    if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EINVAL):
                        raise TransientScanError(f"File changed during history scan: {relative}") from exc
                    raise
                present.add(relative)
                entries.append((mode, relative, oid))
        captured = self._regular_blobs(requests)
        self._cache = {key: value for key, value in self._cache.items() if key in present}
        return b"".join(mode.encode() + b" " + (oid or captured[relative]).encode() + b"\t" +
                        os.fsencode(relative) + b"\0" for mode, relative, oid in entries)

    def checkpoint(self, reason="manual checkpoint"):
        """Save an observed file state. Return its commit, also for a no-op."""
        with self._mutex, _repository_lock(self.repository):
            try:
                self._initialize()
                entries = self._entries()
                index = self.repository / ("runtime-index-" + uuid4().hex)
                env = {**_environment(), "GIT_INDEX_FILE": str(index)}
                try:
                    _git(self.repository, "read-tree", "--empty", env=env)
                    _git(self.repository, "update-index", "-z", "--index-info", input=entries, env=env)
                    tree = _git(self.repository, "write-tree", env=env).strip().decode("ascii")
                finally:
                    index.unlink(missing_ok=True)
                    index.with_name(index.name + ".lock").unlink(missing_ok=True)
                previous = _git(self.repository, "rev-parse", "--verify", HISTORY_REF, check=False)
                parent = previous.stdout.strip().decode("ascii") if previous.returncode == 0 else None
                old_tree = (_git(self.repository, "rev-parse", parent + "^{tree}").strip().decode("ascii")
                            if parent else None)
                if tree != old_tree:
                    description = ("Workspace checkpoint\n\nObserver session: " + self.session_id +
                                   "\nReason: " + str(reason) +
                                   "\nIncludes observed workspace changes from any writer.\n")
                    parent_args = ["-p", parent] if parent else []
                    commit = _git(self.repository, "commit-tree", tree, *parent_args,
                                  input=description.encode("utf-8")).strip().decode("ascii")
                    _git(self.repository, "update-ref", HISTORY_REF, commit, parent or "0" * 40)
                    self.last_checkpoint = commit
                else:
                    self.last_checkpoint = parent
                self.pending_error = None
                self._save_blob_cache()
                self._record_status()
                return self.last_checkpoint
            except TransientScanError as exc:
                self.pending_error = str(exc)
                self._record_status()
                raise
            except Exception as exc:
                self.last_error = str(exc)
                if (self.repository / "HEAD").exists():
                    self._record_status()
                raise

    def _settled_checkpoint(self, reason, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.checkpoint(reason)
            except TransientScanError as exc:
                if time.monotonic() >= deadline:
                    self.last_error = "Unable to capture a stable final file state: " + str(exc)
                    self._record_status()
                    raise RuntimeError(self.last_error) from exc
                time.sleep(.05)

    def start(self):
        # Lifecycle state has its own lock: an already-running monitor can be
        # returned immediately even while its checkpoint holds the scan mutex.
        with self._lifecycle:
            if self._closed:
                raise RuntimeError("File history monitor is closed")
            if self._thread is not None:
                if self.last_error:
                    raise RuntimeError(self.last_error)
                if self._ready.is_set() and self._thread.is_alive():
                    return self
                raise RuntimeError("File history monitor is not running")
            # Import before saving, so missing dependencies fail before reporting monitoring.
            from watchfiles import watch
            self._settled_checkpoint("initial state")

            def monitor():
                last_scan = time.monotonic()
                self._watching = True
                try:
                    for changes in watch(self.workspace, watch_filter=lambda change, path: not self._excluded(Path(path)),
                                         stop_event=self._stop, debounce=300, step=50,
                                         rust_timeout=200, yield_on_timeout=True):
                        if self._stop.is_set():
                            break
                        now = time.monotonic()
                        if changes or self.pending_error or not self._ready.is_set() or now - last_scan >= 10:
                            try:
                                self.checkpoint("filesystem changes" if changes else "monitor reconciliation")
                                last_scan = now
                            except TransientScanError:
                                pass  # Retry a complete scan once the concurrent write settles.
                        self._ready.set()
                except Exception as exc:
                    self.last_error = str(exc)
                finally:
                    self._watching = False
                    self._ready.set()
                    try:
                        self._record_status()
                    except OSError:
                        pass  # last_error remains available to close() and the runtime.

            self._thread = threading.Thread(target=monitor, name="workspace-file-history", daemon=True)
            self._thread.start()
            if not self._ready.wait(10):
                self._stop.set()
                raise RuntimeError("File history monitor did not become ready")
            if self.last_error:
                self._stop.set()
                raise RuntimeError("File history monitor failed: " + self.last_error)
            if not self._thread.is_alive():
                raise RuntimeError("File history monitor stopped during startup")
            return self

    def close(self):
        with self._lifecycle:
            self._close()

    def _close(self):
        if self._closed:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=130)
            if self._thread.is_alive():
                self.last_error = "File history monitor did not stop"
                raise RuntimeError(self.last_error)
        try:
            if self._thread is not None:
                self._settled_checkpoint("runtime closing")
        finally:
            self._closed = True
            if (self.repository / "HEAD").exists():
                self._record_status()
        if self.last_error:
            raise RuntimeError("File history recording encountered an error: " + self.last_error)


def _revision(repository, revision):
    return _git(repository, "rev-parse", "--verify", "--end-of-options",
                str(revision) + "^{commit}").strip().decode("ascii")


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
        if not (repository / "workspace.json").is_file():
            raise ValueError(f"No runtime file history found at {repository}")
        if args.command == "status":
            metadata = json.loads((repository / "workspace.json").read_text())
            observers = []
            for path in sorted((repository / "runtime").glob("*.json")):
                item = json.loads(path.read_text())
                if item.get("monitoring"):
                    try:
                        os.kill(item["pid"], 0)
                    except ProcessLookupError:
                        item["monitoring"] = False
                observers.append(item)
            observers.sort(key=lambda item: item.get("recorded_at", ""))
            head = _git(repository, "rev-parse", "--verify", HISTORY_REF).strip().decode("ascii")
            print(json.dumps({**metadata, "repository": str(repository), "checkpoint": head,
                              "monitoring": any(item["monitoring"] for item in observers),
                              "last_error": next((item["last_error"] for item in reversed(observers)
                                                  if item.get("last_error")), None),
                              "observers": observers}, ensure_ascii=False, indent=2))
        elif args.command == "log":
            if args.limit < 1 or args.limit > 1000:
                raise ValueError("--limit must be between 1 and 1000")
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
