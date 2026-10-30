#!/usr/bin/env python3
"""Foreground example: no children, flushed progress, and an explicit result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    if args.steps < 1 or not 0 <= args.delay <= 60 or not 0 <= args.exit_code <= 125:
        parser.error("steps must be positive, delay 0..60, and exit-code 0..125")

    total = 0
    for step in range(1, args.steps + 1):
        total += step * step
        print(f"completed {step}/{args.steps}", flush=True)
        time.sleep(args.delay)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"steps": args.steps, "sum_of_squares": total, "exit_code": args.exit_code})
        + "\n",
        encoding="utf-8",
    )
    print(f"saved {args.output}", flush=True)
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
