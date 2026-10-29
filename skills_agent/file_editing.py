"""Exact file edits with safe paths and recoverable preimages."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from uuid import uuid4


class EditError(RuntimeError):
    """An actionable edit failure, optionally carrying recovery information."""

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = {"applied": False, "changed": False, **(details or {})}


def text_from_bytes(data: bytes, source: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditError(f"{source}: expected UTF-8 text ({exc.reason})") from exc
    if "\x00" in text:
        raise EditError(f"{source}: NUL bytes are not supported in text operations")
    return text


def supplied_text(value, name):
    if not isinstance(value, str):
        raise EditError(f"{name} must be a string")
    try:
        return text_from_bytes(value.encode("utf-8"), name)
    except UnicodeEncodeError as exc:
        raise EditError(f"{name}: expected valid UTF-8 text") from exc


def unique_offset(text: str, needle: str, option: str) -> int:
    if not needle:
        raise EditError(f"{option} must be nonempty")
    start = text.find(needle)
    if start < 0:
        raise EditError(f"{option} matched no text; inspect the current file and retry")
    second = text.find(needle, start + 1)
    if second >= 0:
        raise EditError(
            f"{option} matched more than once (first matches on lines "
            f"{location(text, start)['line']} and {location(text, second)['line']}); "
            "include enough surrounding text to identify one occurrence"
        )
    return start


def location(text: str, offset: int) -> dict[str, int]:
    line, last_end = 1, 0
    for match in re.finditer(r"\r\n|\r|\n", text[:offset]):
        line += 1
        last_end = match.end()
    return {"line": line, "column": offset - last_end + 1}


def excerpt(text: str, offset: int) -> dict[str, object]:
    """Reuse the old helper's bounded, numbered view around the edit."""
    lines = re.split(r"\r\n|\r|\n", text) if text else []
    point = location(text, offset)
    focus = point["line"]
    first, budget = max(0, focus - 3), 1600
    end = min(len(lines), first + 12)
    numbered, clipped = [], False
    for index in range(first, end):
        line_text = lines[index]
        limit = min(budget, 1000 if index + 1 == focus else 200)
        column = 0
        if len(line_text) > limit:
            if index + 1 == focus:
                column = max(0, min(point["column"] - 1 - limit // 3, len(line_text) - limit))
            clipped = True
        visible = line_text[column:column + limit]
        numbered.append({"line": index + 1, "column": column + 1, "text": visible})
        budget -= len(visible)
        if budget == 0 and index + 1 < end:
            clipped = True
            break
    return {"lines": numbered, "truncated": clipped or first > 0 or end < len(lines)}


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class FileState:
    data: bytes
    mode: int
    fingerprint: tuple


class SafeFile:
    """Pin a nonsymlink parent, and validate its path again before mutations."""

    def __init__(self, workspace, value, *, protected_names, excluded_roots):
        if not isinstance(value, (str, os.PathLike)) or not os.fspath(value):
            raise EditError("path must be nonempty")
        self.workspace = Path(workspace)
        if ".." in Path(value).parts:
            raise EditError("File paths must not contain '..'")
        self.path = Path(os.path.abspath(self.workspace / value))
        if not self.path.is_relative_to(self.workspace) or self.path == self.workspace:
            raise EditError("File edits must stay inside the workspace")
        self.relative = self.path.relative_to(self.workspace)
        protected = {part.casefold() for part in protected_names}
        path_parts = tuple(part.casefold() for part in self.path.parts)
        excluded = [tuple(part.casefold() for part in root.parts) for root in excluded_roots]
        if (any(part.casefold() in protected for part in self.relative.parts[:-1])
                or self.path.name.casefold() in {".git", ".agent", ".skills-agent"}
                or any(path_parts[:len(parts)] == parts for parts in excluded)):
            raise EditError(f"Protected file path: {self.path}")
        self.parent = None

    def _open_parent(self):
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise EditError("Safe file editing requires directory descriptors and O_NOFOLLOW on this platform")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.workspace, flags)
        try:
            for component in self.relative.parts[:-1]:
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def __enter__(self):
        try:
            self.parent = self._open_parent()
        except OSError as exc:
            raise EditError(f"{self.path}: parent directories must exist and must not be symlinks ({exc})") from exc
        return self

    def __exit__(self, *args):
        if self.parent is not None:
            os.close(self.parent)

    def verify_parent(self):
        try:
            current = self._open_parent()
            try:
                old, new = os.fstat(self.parent), os.fstat(current)
                if (old.st_dev, old.st_ino) != (new.st_dev, new.st_ino):
                    raise EditError(f"{self.path}: parent directory changed during the edit")
            finally:
                os.close(current)
        except OSError as exc:
            raise EditError(f"{self.path}: parent directory changed during the edit") from exc

    def read(self):
        self.verify_parent()
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(self.path.name, flags, dir_fd=self.parent)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise EditError(f"{self.path}: cannot open a regular nonsymlink file ({exc})") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise EditError(f"{self.path}: expected a regular file; symlinks and directories are not supported")
            stream = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            data = stream.read()
            after = os.fstat(stream.fileno())
        if fingerprint(before) != fingerprint(after):
            raise EditError(f"{self.path}: file changed while being read; inspect it and retry")
        return FileState(data, stat.S_IMODE(after.st_mode), fingerprint(after))

    def verify(self, expected):
        current = self.read()
        if current != expected:
            raise EditError(f"{self.path}: file changed since the edit was prepared; nothing was overwritten")

    def write_atomically(self, data, mode, expected, on_publish, on_attempt):
        temporary = ".file-editing-" + uuid4().hex + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666, dir_fd=self.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                if mode is not None:
                    os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            self.verify(expected)
            on_attempt()
            if expected is None:
                os.link(temporary, self.path.name, src_dir_fd=self.parent, dst_dir_fd=self.parent,
                        follow_symlinks=False)
            else:
                os.replace(temporary, self.path.name, src_dir_fd=self.parent, dst_dir_fd=self.parent)
            on_publish()
        finally:
            try:
                os.unlink(temporary, dir_fd=self.parent)
            except FileNotFoundError:
                pass  # os.replace already consumed the temporary name.

    def delete(self, expected, on_publish, on_attempt):
        self.verify(expected)
        on_attempt()
        os.unlink(self.path.name, dir_fd=self.parent)
        on_publish()


class PreparedEdit:
    def __init__(self, operation, targets, before, expected, review):
        self.operation, self.targets, self.before = operation, targets, before
        self.expected, self.review = expected, review
        self.applied = False
        self.applied_paths = []
        self.attempted_paths = []
        self.changed = any((None if value is None else (value.data, value.mode)) != expected[path]
                           for path, value in before.items())

    def apply(self):
        for path, target in self.targets.items():
            target.verify(self.before[path])
        source = next(iter(self.targets))
        def published(path):
            self.applied = True
            self.applied_paths.append(path)
        def attempted(path):
            self.attempted_paths.append(path)
        if self.operation == "move":
            destination = next(path for path in self.targets if path != source)
            data, mode = self.expected[destination]
            self.targets[destination].write_atomically(data, mode, None, lambda: published(destination),
                                                       lambda: attempted(destination))
            self.targets[source].delete(self.before[source], lambda: published(source), lambda: attempted(source))
        elif self.operation == "delete":
            self.targets[source].delete(self.before[source], lambda: published(source), lambda: attempted(source))
        else:
            data, mode = self.expected[source]
            self.targets[source].write_atomically(data, mode, self.before[source], lambda: published(source),
                                                  lambda: attempted(source))
        return self.verify_result()

    def verify_result(self):
        states = {}
        for path, target in self.targets.items():
            state = target.read()
            expected = self.expected[path]
            if ((expected is None and state is not None)
                    or (expected is not None and (state is None or state.data != expected[0]
                        or expected[1] is not None and state.mode != expected[1]))):
                raise EditError(f"{target.path}: result changed during the edit; inspect the saved preimage")
            states[path] = state
        return states


@contextmanager
def prepare_edit(workspace, operation, path, *, content=None, old_text=None, new_text=None,
                 destination=None, protected_names=(), excluded_roots=()):
    """Validate and read only this operation's paths, retaining their directory fds."""
    if operation not in {"create", "replace", "delete", "move"}:
        raise EditError("operation must be create, replace, delete, or move")
    with ExitStack() as stack:
        def open_target(value):
            return stack.enter_context(SafeFile(workspace, value, protected_names=protected_names,
                                               excluded_roots=excluded_roots))
        target = open_target(path)
        relative = target.relative.as_posix()
        original = target.read()
        targets, before = {relative: target}, {relative: original}
        review = {"operation": operation, "path": str(target.path), "relative_path": relative}
        if operation == "create":
            if original is not None:
                raise EditError(f"{target.path}: already exists; create never overwrites")
            text = supplied_text(content, "content")
            expected = {relative: (text.encode("utf-8"), None)}
            review.update(location=location(text, 0), excerpt=excerpt(text, 0))
        else:
            if original is None:
                raise EditError(f"{target.path}: file does not exist")
            if operation == "replace":
                old = supplied_text(old_text, "old_text")
                new = supplied_text(new_text, "new_text")
                text = text_from_bytes(original.data, str(target.path))
                start = 0 if not text and not old else unique_offset(text, old, "old_text")
                result = text[:start] + new + text[start + len(old):]
                expected = {relative: (result.encode("utf-8"), original.mode)}
                review.update(location=location(result, start), excerpt=excerpt(result, start),
                              characters_removed=len(old), characters_inserted=len(new))
            elif operation == "delete":
                expected = {relative: None}
                review["removed_bytes"] = len(original.data)
            else:
                if destination is None:
                    raise EditError("move requires destination")
                other = open_target(destination)
                name = other.relative.as_posix()
                if name == relative:
                    raise EditError("move destination must differ from path")
                if other.read() is not None:
                    raise EditError(f"{other.path}: already exists; move never overwrites")
                targets[name], before[name] = other, None
                expected = {relative: None, name: (original.data, original.mode)}
                review.update(destination=str(other.path), destination_relative_path=name,
                              moved_bytes=len(original.data))
        yield PreparedEdit(operation, targets, before, expected, review)
