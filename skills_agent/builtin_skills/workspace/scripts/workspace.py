"""Read and search UTF-8 files without loading whole files into memory."""

import argparse
import re
from pathlib import Path


def read(path: Path, start: int, end: int) -> None:
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if number > end:
                break
            if number >= start:
                print(f"{number}: {line.rstrip(chr(10))}")


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
    reader = commands.add_parser("read")
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
        if args.command == "read":
            end = args.end if args.end is not None else args.start + 199
            if args.start < 1 or end < args.start:
                parser.error("Line ranges require 1 <= start <= end")
            read(args.path, args.start, end)
        else:
            search(args.pattern, args.paths, args.regex, args.glob)
    except (OSError, UnicodeError, re.error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
