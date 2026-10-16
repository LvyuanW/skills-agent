#!/usr/bin/env python3
"""Foreground example: synchronously run one command and preserve its status."""

from __future__ import annotations

import argparse
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("supply a foreground command after --")
    result = subprocess.run(command, check=False)
    print(f"child_returncode={result.returncode}", flush=True)
    # Python uses negative return codes for signals; expose the usual shell form.
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
