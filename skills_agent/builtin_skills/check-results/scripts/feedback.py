#!/usr/bin/env python3
"""Acquire bounded file, text, command, and image feedback through the existing shell."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import mimetypes
import os
from pathlib import Path
import shutil
import shlex
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


MAX_IMAGES = 10
MAX_IMAGE_BYTES = 1_000_000


class FeedbackError(Exception):
    """An acquisition failure with an actionable diagnostic."""


def absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))


def envelope(kind: str, source: str, observations: dict, attachments: list | None = None) -> dict:
    return {"skills_agent_feedback": 1, "kind": kind, "source": source,
            "observations": observations, "attachments": attachments or []}


def file_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def file_feedback(args: argparse.Namespace) -> dict:
    path = absolute(args.path)
    info = path.lstat()
    observations = {"path": str(path), "type": file_type(info.st_mode),
                    "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                    "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"}
    if stat.S_ISREG(info.st_mode):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        observations["sha256"] = digest.hexdigest()
    elif stat.S_ISDIR(info.st_mode):
        children = heapq.nsmallest(args.limit + 1, path.iterdir(), key=lambda child: child.name)
        observations["children"] = []
        for child in children[:args.limit]:
            try:
                child_info = child.lstat()
                entry = {"path": str(child), "name": child.name,
                         "type": file_type(child_info.st_mode), "size": child_info.st_size}
            except OSError as exc:
                entry = {"path": str(child), "name": child.name, "error": str(exc)}
            observations["children"].append(entry)
        observations["truncated"] = len(children) > args.limit
        observations["limit"] = args.limit
    elif stat.S_ISLNK(info.st_mode):
        observations["target"] = os.readlink(path)
    return envelope("file", str(path), observations)


def text_feedback(args: argparse.Namespace) -> dict:
    path = absolute(args.path)
    end = args.end if args.end is not None else args.start + 199
    if end < args.start:
        raise FeedbackError("text ranges require --end >= --start")
    if not path.is_file():
        raise FeedbackError(f"{path}: expected a readable text file")
    observations = {"path": str(path), "start": args.start, "end": end,
                    "column": args.column, "lines": [], "truncated": False,
                    "next_line": None, "next_column": None}
    budget = args.max_chars
    with path.open("r", encoding="utf-8", newline="") as source:
        for number, line in enumerate(source, 1):
            if "\0" in line:
                raise FeedbackError(f"{path}: NUL bytes found; use a suitable binary reader")
            if number < args.start:
                continue
            if number > end:
                observations.update(truncated=True, next_line=number, next_column=1)
                break
            column = args.column if number == args.start else 1
            if column > len(line) + 1:
                raise FeedbackError(f"line {number}: --column exceeds its {len(line)} characters")
            remaining = line[column - 1:]
            visible = remaining[:budget]
            if visible:
                observations["lines"].append({"line": number, "column": column, "text": visible})
                budget -= len(visible)
            if len(visible) < len(remaining):
                observations.update(truncated=True, next_line=number,
                                    next_column=column + len(visible))
                break
            if budget == 0:
                if source.read(1):
                    observations.update(truncated=True, next_line=number + 1, next_column=1)
                break
    return envelope("text", str(path), observations)


def tail(path: Path, max_chars: int = 6000) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - max_chars * 4))
        return stream.read().decode("utf-8", errors="replace")[-max_chars:]


def terminate_tree(process: subprocess.Popen) -> None:
    """Keep children in the enclosing shell group; clean up this command's descendants."""
    descendants: list[int] = []
    if os.name == "posix":
        try:
            listing = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True,
                                     text=True, timeout=2, check=True)
            children: dict[int, list[int]] = {}
            for line in listing.stdout.splitlines():
                pid, parent = (int(part) for part in line.split())
                children.setdefault(parent, []).append(pid)
            pending = list(children.get(process.pid, []))
            while pending:
                pid = pending.pop()
                descendants.append(pid)
                pending.extend(children.get(pid, []))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        for pid in reversed(descendants):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process.poll() is None:
        process.kill()
    process.wait()


def run_process(command: list[str], stdout_log: Path, stderr_log: Path, timeout: float) -> dict:
    started = time.monotonic()
    result = {"command": command, "exit_code": None, "timed_out": False,
              "stdout_log": str(stdout_log), "stderr_log": str(stderr_log)}
    with stdout_log.open("wb") as stdout, stderr_log.open("wb") as stderr:
        try:
            # No new session/group: interruption of the outer shell includes all children.
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        except OSError as exc:
            result["startup_error"] = str(exc)
            stderr.write((str(exc) + "\n").encode("utf-8"))
        else:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                terminate_tree(process)
            except BaseException:
                terminate_tree(process)
                raise
            result["exit_code"] = process.returncode
    result.update(stdout_tail=tail(stdout_log), stderr_tail=tail(stderr_log),
                  duration_seconds=round(time.monotonic() - started, 3))
    return result


def process_status(observations: dict) -> int:
    if observations["timed_out"]:
        return 124
    code = observations["exit_code"]
    if code is None:
        return 1
    return code if code >= 0 else 128 - code


def run_feedback(args: argparse.Namespace) -> tuple[dict, int]:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise FeedbackError("run requires a command after --")
    log_dir = absolute(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=log_dir))
    observations = run_process(command, run_dir / "stdout.log", run_dir / "stderr.log", args.timeout)
    return envelope("run", shlex.join(command), observations), process_status(observations)


def image_format(path: Path) -> str:
    if not path.is_file():
        raise FeedbackError(f"{path}: expected a readable image file")
    with path.open("rb") as source:
        header = source.read(64)
    if (header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24
            and header[12:16] == b"IHDR" and all(struct.unpack(">II", header[16:24]))):
        return "image/png"
    if len(header) >= 4 and header.startswith(b"\xff\xd8\xff") and header[3] not in (0, 255):
        return "image/jpeg"
    if (header[:6] in (b"GIF87a", b"GIF89a") and len(header) >= 10
            and all(struct.unpack("<HH", header[6:10]))):
        return "image/gif"
    if (len(header) >= 16 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
            and header[12:16] in (b"VP8 ", b"VP8L", b"VP8X")):
        return "image/webp"
    raise FeedbackError(f"{path}: unsupported or invalid image header; expected PNG, JPEG, GIF, or WebP")


def validate_source_url(source_url: str | None) -> None:
    if source_url is not None:
        try:
            parsed = urlsplit(source_url)
        except ValueError:
            raise FeedbackError("--source-url must be an absolute HTTP(S) or file URL") from None
        if parsed.scheme not in {"http", "https", "file"} or (
            parsed.scheme in {"http", "https"} and not parsed.netloc
        ) or (parsed.scheme == "file" and not parsed.path.startswith("/")):
            raise FeedbackError("--source-url must be an absolute HTTP(S) or file URL")


def attachment(path: Path, label: str, source_url: str | None = None) -> dict:
    mime = image_format(path)
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise FeedbackError(f"{path}: image has {size} bytes; maximum is {MAX_IMAGE_BYTES} bytes (1 MB). "
                            "Keep the original, then crop, resize or compress a separate copy and attach that copy.")
    validate_source_url(source_url)
    result = {"type": "image", "path": str(path), "mime_type": mime, "label": label,
              "url": path.as_uri()}
    if source_url is not None:
        result["source_url"] = source_url
    return result


def image_feedback(args: argparse.Namespace) -> dict:
    if len(args.paths) > MAX_IMAGES:
        raise FeedbackError(f"At most {MAX_IMAGES} images may be attached to one model request. "
                            "Select the images needed now, or use images/scripts/images.py compose "
                            "to combine needed sources into one bounded image; do not split tool calls to bypass this limit.")
    source_urls = args.source_url or [None] * len(args.paths)
    if len(source_urls) != len(args.paths):
        raise FeedbackError("Provide one --source-url per image path, in the same order, or omit all source URLs")
    paths = [absolute(value) for value in args.paths]
    attachments = [attachment(path, args.label or path.name, source_url)
                   for path, source_url in zip(paths, source_urls)]
    return envelope("image", str(paths[0]),
                    {"paths": [str(path) for path in paths], "header_validation_only": True}, attachments)


def find_browser(requested: str | None) -> str:
    if requested:
        candidate = shutil.which(requested) or str(absolute(requested))
        if not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
            raise FeedbackError(f"{requested}: browser executable is missing or not executable")
        return candidate
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if candidate := shutil.which(name):
            return candidate
    candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if value := os.environ.get(env_name):
            candidates.append(Path(value) / "Google/Chrome/Application/chrome.exe")
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise FeedbackError("Chrome/Chromium is unavailable; pass --browser PATH to an installed executable. "
                        "No browser or package is installed automatically.")


def render_feedback(args: argparse.Namespace) -> tuple[dict, int]:
    path, output = absolute(args.path), absolute(args.output)
    if not path.is_file() or path.suffix.lower() not in (".html", ".htm", ".svg"):
        raise FeedbackError(f"{path}: render requires a local HTML or SVG file, not a URL")
    if path == output or output.suffix.lower() != ".png":
        raise FeedbackError("--output must be a distinct .png path")
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise FeedbackError(f"{output}: output must be a regular file path, not a symlink or directory")
    browser = find_browser(args.browser)
    output.parent.mkdir(parents=True, exist_ok=True)
    observations = {"path": str(path), "browser": browser, "output": str(output),
                    "viewport": [args.width, args.height], "wait_ms": args.wait_ms,
                    "timing": "virtual-time wait budget; not an exact animation frame"}
    # Capture to a fresh file so failure cannot return a stale prior screenshot.
    with tempfile.TemporaryDirectory(prefix="feedback-render-", dir=output.parent) as directory:
        temporary = Path(directory)
        screenshot = temporary / "capture.png"
        command = [browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                   "--disable-extensions", "--disable-background-networking", "--disable-sync",
                   "--no-default-browser-check", f"--user-data-dir={temporary / 'profile'}",
                   f"--window-size={args.width},{args.height}",
                   f"--screenshot={screenshot}"]
        # A zero Chromium virtual-time budget can wait indefinitely; omit it for immediate capture.
        if args.wait_ms:
            command.append(f"--virtual-time-budget={args.wait_ms}")
        command.append(path.as_uri())
        process = run_process(command, temporary / "stdout.log", temporary / "stderr.log", args.timeout)
        # Temporary browser logs are represented by bounded content, not stale log references.
        process.pop("stdout_log")
        process.pop("stderr_log")
        observations.update(process)
        status = process_status(process)
        if status != 0:
            return envelope("render", str(path), observations), status
        if attachment(screenshot, path.name)["mime_type"] != "image/png":
            raise FeedbackError("browser did not produce a valid PNG screenshot header")
        os.replace(screenshot, output)
    return envelope("render", str(path), observations,
                    [attachment(output, f"{path.name}; viewport {args.width}x{args.height}; wait {args.wait_ms} ms")]), 0


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("expected an integer >= 1")
    return number


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("expected an integer >= 0")
    return number


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("expected a finite number of seconds > 0")
    return number


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__, epilog=(
        "Results are one JSON envelope on stdout. Errors also go to stderr. "
        "run/render retain nonzero command status; timeout returns 124, acquisition failure 1. "
        "No interactive input, dependency installation, or new agent tools are used."))
    commands = cli.add_subparsers(dest="kind", required=True)
    file = commands.add_parser("file", help="read actual file metadata or a bounded directory listing")
    file.add_argument("path")
    file.add_argument("--limit", type=positive_int, default=100)
    text = commands.add_parser("text", help="read numbered UTF-8 text; resume at returned line and column")
    text.add_argument("path")
    text.add_argument("--start", type=positive_int, default=1)
    text.add_argument("--end", type=positive_int)
    text.add_argument("--column", type=positive_int, default=1, help="one-based column on the first requested line")
    text.add_argument("--max-chars", type=positive_int, default=12000)
    run = commands.add_parser("run", help="run a command; save complete stdout/stderr and return bounded tails")
    run.add_argument("--log-dir", required=True)
    run.add_argument("--timeout", type=positive_seconds, default=120)
    run.add_argument("command", nargs=argparse.REMAINDER)
    image = commands.add_parser("image", help="legacy image feedback metadata; prefer shell.images")
    image.add_argument("paths", nargs="+")
    image.add_argument("--label")
    image.add_argument("--source-url", action="append",
                       help="original HTTP(S) or file URL; repeat in the same order as image paths")
    render = commands.add_parser("render", help="render local HTML/SVG through an installed Chrome/Chromium")
    render.add_argument("path")
    render.add_argument("--output", required=True)
    render.add_argument("--width", type=positive_int, default=1200)
    render.add_argument("--height", type=positive_int, default=800)
    render.add_argument("--wait-ms", type=nonnegative_int, default=1000)
    render.add_argument("--browser")
    render.add_argument("--timeout", type=positive_seconds, default=30)
    return cli


def main() -> int:
    args = parser().parse_args()
    status = 0
    try:
        if args.kind == "run":
            result, status = run_feedback(args)
        elif args.kind == "render":
            result, status = render_feedback(args)
        else:
            result = {"file": file_feedback, "text": text_feedback, "image": image_feedback}[args.kind](args)
    except (FeedbackError, OSError, UnicodeError) as exc:
        print(f"feedback.py: {exc}", file=sys.stderr)
        value = getattr(args, "path", None) or (args.paths[0] if hasattr(args, "paths") else args.command)
        source = str(absolute(value)) if isinstance(value, str) else shlex.join(value)
        result = envelope(args.kind, source, {"error": str(exc)})
        status = 1
    if status and "error" not in result["observations"]:
        reason = result["observations"].get("startup_error") or (
            "command timed out" if result["observations"].get("timed_out")
            else f"command exited with {result['observations'].get('exit_code')}")
        print(f"feedback.py: {reason}; inspect observations and saved logs", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
