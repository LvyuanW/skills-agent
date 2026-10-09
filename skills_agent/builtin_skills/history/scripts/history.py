"""Reference script for source-linked history search and range reading."""

import argparse
import json
import sqlite3
from pathlib import Path

from skills_agent.context.history import HistoryIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--role")
    search.add_argument("--task")
    read = commands.add_parser("read")
    read.add_argument("event_id")
    read.add_argument("--start", type=int, default=1)
    read.add_argument("--end", type=int)
    neighbors = commands.add_parser("neighbors")
    neighbors.add_argument("event_id")
    neighbors.add_argument("--before", type=int, default=1)
    neighbors.add_argument("--after", type=int, default=1)
    listing = commands.add_parser("list")
    listing.add_argument("--after")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--role")
    args = parser.parse_args()
    index = HistoryIndex(args.session)
    try:
        if args.command == "search":
            result = index.search(args.query, args.limit, role=args.role, task=args.task)
        elif args.command == "read":
            result = index.read(args.event_id, args.start, args.end if args.end is not None else args.start + 79)
        elif args.command == "neighbors":
            result = index.neighbors(args.event_id, args.before, args.after)
        else:
            result = index.list(after=args.after, limit=args.limit, role=args.role)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
