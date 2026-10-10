#!/usr/bin/env python3
"""Make small, exact text-file edits through an ordinary shell command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys


class EditError(Exception):
    """An actionable input or file error that should not print a traceback."""


def text_from_bytes(data: bytes, source: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EditError(f"{source}: expected UTF-8 text ({exc.reason})") from exc
    if "\x00" in text:
        raise EditError(f"{source}: NUL bytes are not supported in text operations")
    return text


def supplied_text(args: argparse.Namespace, name: str) -> str:
    inline = getattr(args, name)
    filename = getattr(args, f"{name}_file")
    if inline is not None:
        return text_from_bytes(inline.encode("utf-8"), f"--{name}")
    if filename is not None:
        return text_from_bytes(Path(filename).read_bytes(), filename)
    if sys.stdin.isatty():
        raise EditError(f"supply --{name}, --{name}-file, or UTF-8 input on stdin")
    return text_from_bytes(sys.stdin.buffer.read(), "stdin")


def regular_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise EditError(f"{path}: target symlinks are not supported")
    if not stat.S_ISREG(info.st_mode):
        raise EditError(f"{path}: expected a regular file")
    return info


def unique_offset(text: str, needle: str, option: str) -> int:
    if not needle:
        raise EditError(f"{option} must be nonempty")
    start = text.find(needle)
    if start < 0:
        raise EditError(f"{option} matched no text; inspect the current file and retry")
    second = text.find(needle, start + 1)
    if second >= 0:
        first_line = location(text, start)["line"]
        second_line = location(text, second)["line"]
        raise EditError(
            f"{option} matched more than once (first matches on lines "
            f"{first_line} and {second_line}); include enough surrounding text "
            "to identify one occurrence"
        )
    return start


def location(text: str, offset: int) -> dict[str, int]:
    line = 1
    last_end = 0
    for match in re.finditer(r"\r\n|\r|\n", text[:offset]):
        line += 1
        last_end = match.end()
    return {"line": line, "column": offset - last_end + 1}


def excerpt(text: str, offset: int) -> dict[str, object]:
    """Return nearby numbered lines, bounded even when one line is enormous."""
    lines = re.split(r"\r\n|\r|\n", text) if text else []
    point = location(text, offset)
    focus = point["line"]
    first = max(0, focus - 3)
    end = min(len(lines), first + 12)
    budget = 1600
    numbered = []
    clipped = False
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
    return {
        "lines": numbered,
        "truncated": clipped or first > 0 or end < len(lines),
    }


def write_atomically(path: Path, data: bytes, mode: int | None, create: bool) -> None:
    """Use a same-directory temporary file; never overwrite during creation."""
    temp_path = path.parent / f".file-editing-{secrets.token_hex(8)}.tmp"
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        if create:
            # A hard link publishes the complete file and fails if PATH exists.
            os.link(temp_path, path)
        else:
            os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def execute(args: argparse.Namespace) -> dict[str, object]:
    path = Path(os.path.abspath(args.path))
    result: dict[str, object] = {
        "operation": args.operation,
        "path": str(path),
        "changed": True,
    }
    if args.operation == "delete-file":
        regular_file(path)
        path.unlink()
        return result

    if args.operation == "create":
        if path.is_symlink() or path.exists():
            raise EditError(f"{path}: already exists; create never overwrites")
        new_text = supplied_text(args, "text")
        write_atomically(path, new_text.encode("utf-8"), mode=None, create=True)
        result["location"] = location(new_text, 0)
        result["excerpt"] = excerpt(new_text, 0)
        return result

    info = regular_file(path)
    old_text = text_from_bytes(path.read_bytes(), str(path))
    if args.operation == "insert":
        anchor = supplied_text(args, "anchor")
        anchor_start = unique_offset(old_text, anchor, "--anchor")
        start = anchor_start + (len(anchor) if args.position == "after" else 0)
        removed = ""
        added = supplied_text(args, "text")
    else:
        removed = supplied_text(args, "old")
        start = unique_offset(old_text, removed, "--old")
        added = supplied_text(args, "new") if args.operation == "replace" else ""
    new_text = old_text[:start] + added + old_text[start + len(removed) :]
    changed = new_text != old_text
    if changed:
        write_atomically(
            path, new_text.encode("utf-8"), stat.S_IMODE(info.st_mode), create=False
        )
    result["changed"] = changed
    result["location"] = location(new_text, start)
    result["characters_removed"] = len(removed)
    result["characters_inserted"] = len(added)
    result["excerpt"] = excerpt(new_text, start)
    return result


def add_text_input(parser: argparse.ArgumentParser, name: str, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument(f"--{name}", metavar="TEXT", help="literal text (no newline rewriting)")
    group.add_argument(
        f"--{name}-file", metavar="FILE", help="read exact UTF-8 text from FILE"
    )
    if not required:
        parser.epilog = (
            f"If neither --{name} nor --{name}-file is supplied, read UTF-8 text "
            "from stdin until EOF. No interactive questions are asked."
        )


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        description=(
            "Create or edit UTF-8 files using exact text matches; run through the existing "
            "shell. Success is one JSON object; errors go to stderr with a nonzero exit."
        ),
        epilog=(
            "Text edits reject NUL bytes, directories, and target symlinks. Exact matches "
            "must be nonempty and unique, including overlapping matches. Bytes outside "
            "the edited region and existing file modes are preserved. Parent directories "
            "must already exist. Excerpts are limited to 12 lines and 1600 text characters."
        ),
    )
    commands = cli.add_subparsers(dest="operation", required=True)

    create = commands.add_parser("create", help="create a new file; refuse to overwrite")
    create.add_argument("path")
    add_text_input(create, "text", required=False)

    replace = commands.add_parser("replace", help="replace one unique exact text occurrence")
    replace.add_argument("path")
    add_text_input(replace, "old", required=True)
    add_text_input(replace, "new", required=False)

    insert = commands.add_parser("insert", help="insert text before or after one unique anchor")
    insert.add_argument("path")
    add_text_input(insert, "anchor", required=True)
    insert.add_argument("--position", choices=("before", "after"), required=True)
    add_text_input(insert, "text", required=False)

    delete_content = commands.add_parser(
        "delete-content", help="delete one unique exact text occurrence"
    )
    delete_content.add_argument("path")
    add_text_input(delete_content, "old", required=True)

    delete_file = commands.add_parser(
        "delete-file", help="remove one regular file, including a binary file; never recurse"
    )
    delete_file.add_argument("path")
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        result = execute(args)
    except (EditError, OSError, UnicodeError) as exc:
        print(f"edit.py: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
