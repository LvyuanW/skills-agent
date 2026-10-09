"""Read and search UTF-8 files without loading whole files into memory."""

import argparse
import json
import re
from pathlib import Path


def read(path: Path, start: int, end: int) -> None:
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if number > end:
                break
            if number >= start:
                print(f"{number}: {line.rstrip(chr(10))}")


def history(path: Path, start: int, end: int) -> None:
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if number > end:
                break
            if number < start or not line.strip():
                continue
            event = json.loads(line)
            event.pop("_raw", None)
            if event.get("type") == "message":
                message = event["message"]
                message.pop("_raw", None)
                if message.get("role") == "tool" and message.get("name") == "subagent":
                    continue
                if message.get("role") == "assistant":
                    if "tool_calls" in message:
                        message["tool_calls"] = [
                            call for call in message["tool_calls"] if call["name"] != "subagent"
                        ]
                    if not message.get("content") and not message.get("tool_calls"):
                        continue
            print(f"{number}: {json.dumps(event, ensure_ascii=False)}")


def search(pattern: str, paths: list[Path], regex: bool, glob: str) -> None:
    expression = re.compile(pattern if regex else re.escape(pattern))
    for path in paths:
        files = sorted(path.rglob(glob)) if path.is_dir() else [path]
        for file in files:
            if not file.is_file():
                continue
            try:
                with file.open(encoding="utf-8") as source:
                    for number, line in enumerate(source, 1):
                        if expression.search(line):
                            print(f"{file}:{number}: {line.rstrip(chr(10))}")
            except UnicodeError:
                continue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("read", "history"):
        reader = commands.add_parser(command)
        reader.add_argument("path", type=Path)
        reader.add_argument("--start", type=int, default=1)
        reader.add_argument("--end", type=int)
    finder = commands.add_parser("search")
    finder.add_argument("pattern")
    finder.add_argument("paths", type=Path, nargs="+")
    finder.add_argument("--regex", action="store_true")
    finder.add_argument("--glob", default="*")
    args = parser.parse_args()
    try:
        if args.command in {"read", "history"}:
            end = args.end if args.end is not None else args.start + 199
            if args.start < 1 or end < args.start:
                parser.error("Line ranges require 1 <= start <= end")
            action = read if args.command == "read" else history
            action(args.path, args.start, end)
        else:
            search(args.pattern, args.paths, args.regex, args.glob)
    except (OSError, UnicodeError, re.error, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
