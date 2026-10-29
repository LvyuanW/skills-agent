"""Validated batches of file edits, with exact contents and recoverable preimages."""

from __future__ import annotations

import base64
import binascii
import codecs
from collections import deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from uuid import uuid4


class EditError(RuntimeError):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = {"applied": False, "changed": False, **(details or {})}


def supplied_text(value, name):
    if not isinstance(value, str):
        raise EditError(f"{name} must be a string")
    return value


def encode_text(text, encoding, name):
    try:
        return supplied_text(text, name).encode(encoding)
    except (LookupError, UnicodeError) as exc:
        raise EditError(f"{name}: cannot encode text as {encoding}: {exc}") from exc


def text_from_bytes(data, source, encoding="utf-8"):
    try:
        return data.decode(encoding)
    except (LookupError, UnicodeError) as exc:
        raise EditError(f"{source}: cannot decode text as {encoding}: {exc}") from exc


def encode_edited_text(text, original, encoding):
    """Preserve the original BOM and byte order when an encoding infers them."""
    name = codecs.lookup(encoding).name
    choices = {"utf-16": ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")),
               "utf-32": ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"))}
    for marker, precise in choices.get(name, ()):
        if original.startswith(marker):
            return marker + encode_text(text, precise, "result")
    if name == "utf-8-sig" and not original.startswith(codecs.BOM_UTF8):
        encoding = "utf-8"
    return encode_text(text, encoding, "result")


def unique_offset(text, needle, option):
    if not needle:
        raise EditError(f"{option} must be nonempty")
    start = text.find(needle)
    if start < 0:
        raise EditError(f"{option} matched no text; inspect the current file and retry")
    second = text.find(needle, start + 1)
    if second >= 0:
        raise EditError(f"{option} matched more than once (first matches on lines "
                        f"{location(text, start)['line']} and {location(text, second)['line']}); "
                        "provide a unique match; for replacing all occurrences, use replace_all=true with expected_count")
    return start


def location(text, offset):
    line, last_end = 1, 0
    for match in re.finditer(r"\r\n|\r|\n", text[:offset]):
        line += 1
        last_end = match.end()
    return {"line": line, "column": offset - last_end + 1}


def excerpt(text, offset):
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
    mode: int | None
    fingerprint: tuple
    kind: str = "file"


def same_contents(actual, desired):
    if actual is None or desired is None:
        return actual is desired
    return (actual.data == desired.data and actual.kind == desired.kind
            and (desired.mode is None or actual.mode == desired.mode))


def _case_insensitive_directory(path):
    """Read-only probe using an existing directory's differently cased name."""
    path = Path(path)
    while path != path.parent:
        try:
            original = path.lstat()
        except FileNotFoundError:
            path = path.parent
            continue
        if not stat.S_ISDIR(original.st_mode):
            return False
        if path.name != path.name.swapcase():
            try:
                variant = path.with_name(path.name.swapcase()).lstat()
            except FileNotFoundError:
                return False
            return (stat.S_ISDIR(variant.st_mode)
                    and (original.st_dev, original.st_ino) == (variant.st_dev, variant.st_ino))
        path = path.parent
    return False


def _same_case_alias(left, right):
    """Distinguish case aliases from separate names on case-sensitive volumes."""
    if left.casefold() != right.casefold():
        return False
    left, right = Path(left), Path(right)
    try:
        first, second = left.lstat(), right.lstat()
    except FileNotFoundError:
        if os.path.lexists(left) or os.path.lexists(right):
            return False
        return _case_insensitive_directory(left.parent)
    if (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino):
        return False
    # Two case-sensitive directory entries may be hard links to the same inode.
    names = {entry.name for entry in os.scandir(left.parent)}
    return not (left.name != right.name and left.name in names and right.name in names)


def _directory(path):
    """Open an already resolved absolute directory without following new links."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class SafeFile:
    """Pin the nearest existing parent; create missing directories only on apply."""

    def __init__(self, workspace, value, *, protected_names=(), excluded_roots=()):
        self.workspace = Path(workspace)
        self.path = Path(os.path.abspath(self.workspace / value))
        if self.path == self.path.parent:
            raise EditError("path must name a file, not a filesystem root")
        self.relative = Path(os.path.relpath(self.path, self.workspace))
        self.anchor_fd = None
        self.anchor_path = None
        self.missing = ()

    def check_writable(self, excluded_roots):
        parts = tuple(part.casefold() for part in self.path.parts)
        for root in excluded_roots:
            excluded = tuple(part.casefold() for part in Path(root).resolve().parts)
            if parts[:len(excluded)] == excluded:
                raise EditError(f"Cannot edit the active runtime's own data: {self.path}")

    def __enter__(self):
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise EditError("Safe file editing requires directory descriptors and O_NOFOLLOW")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.path.anchor, flags)
        ancestor = Path(self.path.anchor)
        components = self.path.parent.parts[1:]
        try:
            for index, component in enumerate(components):
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    self.missing = components[index:]
                    break
                os.close(descriptor)
                descriptor = child
                ancestor /= component
            self.anchor_fd, self.anchor_path = descriptor, ancestor
            return self
        except BaseException as exc:
            os.close(descriptor)
            if isinstance(exc, OSError):
                raise EditError(f"{self.path}: parent is not a stable directory ({exc})") from exc
            raise

    def __exit__(self, *args):
        if self.anchor_fd is not None:
            os.close(self.anchor_fd)
            self.anchor_fd = None

    def _verify_anchor(self):
        try:
            current = _directory(self.anchor_path)
            try:
                old, new = os.fstat(self.anchor_fd), os.fstat(current)
                if (old.st_dev, old.st_ino) != (new.st_dev, new.st_ino):
                    raise EditError(f"{self.path}: parent directory changed during the edit")
            finally:
                os.close(current)
        except OSError as exc:
            raise EditError(f"{self.path}: parent directory changed during the edit") from exc

    def _parent(self, *, create=False):
        self._verify_anchor()
        descriptor = os.dup(self.anchor_fd)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            for component in self.missing:
                if create:
                    try:
                        os.mkdir(component, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    os.close(descriptor)
                    return None
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _verify_parent(self, descriptor):
        current = self._parent()
        try:
            if current is None:
                raise EditError(f"{self.path}: parent directory changed during the edit")
            old, new = os.fstat(descriptor), os.fstat(current)
            if (old.st_dev, old.st_ino) != (new.st_dev, new.st_ino):
                raise EditError(f"{self.path}: parent directory changed during the edit")
        finally:
            if current is not None:
                os.close(current)

    def read(self):
        parent = self._parent()
        if parent is None:
            return None
        try:
            try:
                before = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(before.st_mode):
                data = os.fsencode(os.readlink(self.path.name, dir_fd=parent))
                after = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
                kind = "symlink"
            elif stat.S_ISREG(before.st_mode):
                descriptor = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                                     dir_fd=parent)
                try:
                    opened = os.fstat(descriptor)
                    if not stat.S_ISREG(opened.st_mode) or fingerprint(opened) != fingerprint(before):
                        raise EditError(f"{self.path}: file changed while being opened")
                    with os.fdopen(descriptor, "rb") as stream:
                        descriptor = None
                        data = stream.read()
                        after = os.fstat(stream.fileno())
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                kind = "file"
            else:
                raise EditError(f"{self.path}: expected a regular file or symbolic link; directories and devices are unsupported")
            if fingerprint(before) != fingerprint(after):
                raise EditError(f"{self.path}: file changed while being read; inspect it and retry")
            return FileState(data, stat.S_IMODE(after.st_mode), fingerprint(after), kind)
        finally:
            os.close(parent)

    def verify(self, expected):
        if self.read() != expected:
            raise EditError(f"{self.path}: file changed since the batch was prepared; nothing was overwritten")

    def write_atomically(self, state, expected, on_publish, on_attempt):
        parent = self._parent(create=True)
        temporary = ".file-editing-" + uuid4().hex + ".tmp"
        created = False
        try:
            if state.kind == "symlink":
                os.symlink(os.fsdecode(state.data), temporary, dir_fd=parent)
                created = True
            else:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666, dir_fd=parent)
                created = True
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(state.data)
                    stream.flush()
                    if state.mode is not None:
                        os.fchmod(stream.fileno(), state.mode)
                    os.fsync(stream.fileno())
            self.verify(expected)
            self._verify_parent(parent)
            on_attempt()
            if expected is None:
                os.link(temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            else:
                os.replace(temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent)
            on_publish()
        finally:
            try:
                if created:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
            finally:
                os.close(parent)

    def delete(self, expected, on_publish, on_attempt):
        parent = self._parent()
        try:
            self.verify(expected)
            self._verify_parent(parent)
            on_attempt()
            os.unlink(self.path.name, dir_fd=parent)
            on_publish()
        finally:
            if parent is not None:
                os.close(parent)


class PreparedBatch:
    def __init__(self, targets, before, expected, review, link_dependencies):
        self.targets, self.before, self.expected = targets, before, expected
        self.review, self.link_dependencies = review, link_dependencies
        self.operation = review["operation"]
        self.applied, self.applied_paths, self.attempted_paths = False, [], []
        self.changed_paths = [path for path in before if not same_contents(before[path], expected[path])]
        self.changed = bool(self.changed_paths)

    def apply(self):
        for target, state in self.link_dependencies:
            target.verify(state)
        for path, target in self.targets.items():
            target.verify(self.before[path])
        # Publish destinations before removing sources, so a partial move keeps
        # at least one copy of the original content.
        changed = sorted(self.changed_paths, key=lambda path: self.expected[path] is None)
        def published(path):
            self.applied = True
            self.applied_paths.append(path)
        for path in changed:
            target, expected = self.targets[path], self.expected[path]
            if expected is None:
                target.delete(self.before[path], lambda p=path: published(p),
                              lambda p=path: self.attempted_paths.append(p))
            else:
                target.write_atomically(expected, self.before[path], lambda p=path: published(p),
                                        lambda p=path: self.attempted_paths.append(p))
        return self.verify_result()

    def verify_result(self):
        states = {}
        for path, target in self.targets.items():
            state = target.read()
            if not same_contents(state, self.expected[path]):
                raise EditError(f"{target.path}: result changed during the edit; inspect the saved preimage")
            states[path] = state
        return states


# Preserve the former single-edit injection point for integrations.
PreparedEdit = PreparedBatch


def _validate_edit(edit):
    if not isinstance(edit, dict):
        raise EditError("Each edit must be an object")
    required = {
        "create": {"operation", "path"}, "write": {"operation", "path"},
        "replace": {"operation", "path", "old_text", "new_text"},
        "insert": {"operation", "path", "anchor", "position", "content"},
        "delete": {"operation", "path"},
        "move": {"operation", "path", "destination"}, "copy": {"operation", "path", "destination"},
    }
    optional = {"create": {"content", "content_base64", "encoding"},
                "write": {"content", "content_base64", "encoding"},
                "replace": {"encoding", "replace_all", "expected_count"},
                "insert": {"encoding"}, "delete": set(), "move": {"overwrite"}, "copy": {"overwrite"}}
    operation = edit.get("operation")
    if not isinstance(operation, str) or operation not in required:
        raise EditError("operation must be create, write, replace, insert, delete, move or copy")
    if not required[operation] <= set(edit) <= required[operation] | optional[operation]:
        raise EditError(f"Invalid fields for {operation}")
    for field in ("path", "destination"):
        if field in edit and (not isinstance(edit[field], (str, os.PathLike)) or not os.fspath(edit[field])):
            raise EditError(f"{field} must be a nonempty path")
    if "encoding" in edit and (not isinstance(edit["encoding"], str) or not edit["encoding"]):
        raise EditError("encoding must be a nonempty string")
    for field in ("overwrite", "replace_all"):
        if field in edit and type(edit[field]) is not bool:
            raise EditError(f"{field} must be a boolean")
    if "expected_count" in edit and (type(edit["expected_count"]) is not int or edit["expected_count"] < 1):
        raise EditError("expected_count must be a positive integer")
    if operation in {"create", "write"}:
        if ("content" in edit) == ("content_base64" in edit):
            raise EditError(f"{operation} requires exactly one of content or content_base64")
        if "content_base64" in edit and "encoding" in edit:
            raise EditError("encoding applies only to text content, not content_base64")
    if operation == "insert" and edit["position"] not in ("before", "after"):
        raise EditError("position must be before or after")


@contextmanager
def prepare_batch(workspace, edits, *, excluded_roots=()):
    """Simulate every edit before exposing a plan that can mutate the filesystem."""
    if not isinstance(edits, list) or not edits:
        raise EditError("edits must be a nonempty array")
    for edit in edits:
        _validate_edit(edit)
    workspace = Path(workspace).expanduser().resolve()
    with ExitStack() as stack:
        targets, before, states, dependencies, summaries = {}, {}, {}, {}, []
        aliases = {}

        def remember(path):
            group = aliases.setdefault(path.casefold(), [])
            if path not in group:
                group.append(path)

        def open_target(path):
            return stack.enter_context(SafeFile(workspace, path, excluded_roots=excluded_roots))

        def resolve(value, *, follow_leaf):
            raw = Path(value).expanduser()
            raw = raw if raw.is_absolute() else workspace / raw
            pending, resolved, links = deque(raw.parts[1:]), [], 0
            while pending:
                component = pending.popleft()
                if component in {"", "."}:
                    continue
                if component == "..":
                    if resolved:
                        resolved.pop()
                    continue
                candidate = str(Path(raw.anchor, *resolved, component))
                candidate = next((known for known in aliases.get(candidate.casefold(), ())
                                  if known == candidate or _same_case_alias(candidate, known)), candidate)
                state = states.get(candidate)
                if candidate not in states:
                    try:
                        info = os.lstat(candidate)
                    except FileNotFoundError:
                        info = None
                    except OSError as exc:
                        raise EditError(f"{candidate}: cannot resolve path ({exc})") from exc
                    if info is not None and stat.S_ISLNK(info.st_mode):
                        if candidate not in dependencies:
                            target = open_target(candidate)
                            dependencies[candidate] = (target, target.read())
                            remember(candidate)
                        state = dependencies[candidate][1]
                if state is not None and state.kind == "symlink" and (pending or follow_leaf):
                    links += 1
                    if links > 40:
                        raise EditError(f"{value}: too many symbolic links")
                    link = Path(os.fsdecode(state.data))
                    if link.is_absolute():
                        resolved = []
                        link_parts = link.parts[1:]
                    else:
                        link_parts = link.parts
                    pending.extendleft(reversed(link_parts))
                else:
                    resolved = list(Path(candidate).parts[1:])
            return str(Path(raw.anchor, *resolved))

        def current(path):
            if path not in states:
                target = open_target(path)
                targets[path] = target
                before[path] = states[path] = target.read()
                remember(path)
            return states[path]

        for edit in edits:
            operation = edit["operation"]
            path = resolve(edit["path"], follow_leaf=operation in {"write", "replace", "insert"})
            original = current(path)
            summary = {"operation": operation, "path": path, "requested_path": os.fspath(edit["path"]),
                       "relative_path": os.path.relpath(path, workspace)}
            encoding = edit.get("encoding", "utf-8")
            if operation in {"create", "write"}:
                if operation == "create" and original is not None:
                    raise EditError(f"{path}: already exists; create never overwrites")
                if "content_base64" in edit:
                    value = supplied_text(edit["content_base64"], "content_base64")
                    try:
                        data = base64.b64decode(value, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise EditError("content_base64 must be valid base64") from exc
                    summary["bytes_written"] = len(data)
                else:
                    text = supplied_text(edit["content"], "content")
                    data = encode_text(text, encoding, "content")
                    summary.update(location=location(text, 0), excerpt=excerpt(text, 0))
                states[path] = FileState(data, original.mode if original else None, ())
            elif operation in {"replace", "insert"}:
                if original is None:
                    raise EditError(f"{path}: file does not exist")
                if original.kind != "file":
                    raise EditError(f"{path}: expected a regular file")
                text = text_from_bytes(original.data, path, encoding)
                if operation == "replace":
                    old = supplied_text(edit["old_text"], "old_text")
                    new = supplied_text(edit["new_text"], "new_text")
                    if not old:
                        if text:
                            raise EditError("old_text may be empty only when the current file is empty")
                        count, start, result = 1, 0, new
                    elif edit.get("replace_all"):
                        count, start = text.count(old), text.find(old)
                        if not count:
                            raise EditError("old_text matched no text; inspect the current file and retry")
                        result = text.replace(old, new)
                    else:
                        start, count = unique_offset(text, old, "old_text"), 1
                        result = text[:start] + new + text[start + len(old):]
                    if "expected_count" in edit and count != edit["expected_count"]:
                        raise EditError(f"old_text matched {count} times; expected {edit['expected_count']}")
                    summary.update(replacements=count, characters_removed=len(old) * count,
                                   characters_inserted=len(new) * count)
                else:
                    anchor = supplied_text(edit["anchor"], "anchor")
                    insertion = supplied_text(edit["content"], "content")
                    start = unique_offset(text, anchor, "anchor")
                    if edit["position"] == "after":
                        start += len(anchor)
                    result = text[:start] + insertion + text[start:]
                    summary["characters_inserted"] = len(insertion)
                states[path] = FileState(encode_edited_text(result, original.data, encoding), original.mode, ())
                summary.update(location=location(result, start), excerpt=excerpt(result, start))
            else:
                if original is None:
                    raise EditError(f"{path}: file does not exist")
                if operation == "delete":
                    states[path] = None
                    summary["removed_bytes"] = len(original.data)
                else:
                    destination = resolve(edit["destination"], follow_leaf=False)
                    if destination == path:
                        raise EditError(f"{operation} destination must differ from path")
                    existing = current(destination)
                    if existing is not None and not edit.get("overwrite", False):
                        raise EditError(f"{destination}: already exists; set overwrite=true to replace it")
                    states[destination] = FileState(original.data, original.mode, (), original.kind)
                    if operation == "move":
                        states[path] = None
                    summary.update(destination=destination, requested_destination=os.fspath(edit["destination"]),
                                   destination_relative_path=os.path.relpath(destination, workspace),
                                   bytes_copied=len(original.data), kind=original.kind)
            summary["changed"] = (not same_contents(original, states[path])
                                  or operation == "copy" and not same_contents(existing, states[destination]))
            summaries.append(summary)
        review = ({**summaries[0]} if len(summaries) == 1 else {"operation": "batch"})
        review.update(operations=summaries, edit_count=len(summaries), paths=list(states))
        # Links that are themselves modified are already checked against before;
        # dependencies retain only the aliases followed to resolve other targets.
        plan = PreparedBatch(targets, before, states, review,
                             [value for path, value in dependencies.items() if path not in targets])
        for path in plan.changed_paths:
            targets[path].check_writable(excluded_roots)
        for path, state in states.items():
            if state is not None and any(str(parent) in states and states[str(parent)] is not None
                                         for parent in Path(path).parents):
                raise EditError(f"{path}: another file in this batch occupies a required parent directory")
        yield plan


@contextmanager
def prepare_edit(workspace, operation, path, *, protected_names=(), excluded_roots=(), **kwargs):
    edit = {"operation": operation, "path": path, **{key: value for key, value in kwargs.items() if value is not None}}
    with prepare_batch(workspace, [edit], excluded_roots=excluded_roots) as plan:
        yield plan
