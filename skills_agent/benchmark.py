"""Run one task from a prompt file and exit, with an isolated session log."""

import argparse
from pathlib import Path
import signal
import sys

from .cli import ConsoleRenderer
from .runtime import add_agent_arguments, create_agent
from .session import Session


def parser():
    result = argparse.ArgumentParser(prog="python -m skills_agent.benchmark")
    result.add_argument("--prompt-file", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--log-dir", type=Path, required=True)
    result.add_argument("--file", type=Path, action="append", default=[])
    add_agent_arguments(result)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    agent = None
    try:
        prompt = args.prompt_file.expanduser().read_text(encoding="utf-8")
        if not prompt.strip():
            raise ValueError("任务文件不能为空")
        root = args.log_dir.expanduser().resolve()
        if (root / "session").exists():
            raise ValueError("日志目录已包含 session；每次 benchmark 运行需要独立日志目录")
        session = Session(root, "session")
        workspace = args.workspace.expanduser().resolve()
        paths = [path.expanduser() for path in args.file]
        paths = [path if path.is_absolute() else workspace / path for path in paths]
        user_message = session.make_user_message(prompt, paths)
        agent = create_agent(args, session, on_event=ConsoleRenderer())
        agent.run(user_message)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if agent is None:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def _terminate(signum, frame):
    # Let the active shell tool clean up its process group before exiting.
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    raise SystemExit(main())
