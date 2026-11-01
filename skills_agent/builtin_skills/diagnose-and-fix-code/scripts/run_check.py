"""Run a command, save its full output, and display a bounded tail and exit status."""

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required after the log path")

    log = args.log.expanduser().resolve()
    with log.open("wb") as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT)

    with log.open("rb") as output:
        output.seek(0, 2)
        output.seek(max(0, output.tell() - 8192))
        tail = output.read().decode("utf-8", errors="replace").splitlines()[-30:]
    if tail:
        print("Output tail:")
        print("\n".join(tail))
    print(f"Exit code: {result.returncode}\nLog: {log}")
    # subprocess uses negative signal numbers; shells use 128 + signal number.
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
