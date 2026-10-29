"""Run one task from a prompt file and exit, with an isolated session log."""

import argparse
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from uuid import uuid4

from .cli import ConsoleRenderer
from .runtime import add_agent_arguments, create_agent
from .session import Session
from .storage import WorkspaceStorage


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
    session = None
    storage = None
    status = 1
    try:
        prompt = args.prompt_file.expanduser().read_text(encoding="utf-8")
        if not prompt.strip():
            raise ValueError("任务文件不能为空")
        root = args.log_dir.expanduser().resolve()
        if (root / "session").exists() or (root / "runtime-storage.json").exists():
            raise ValueError("日志目录已包含 session；每次 benchmark 运行需要独立日志目录")
        workspace = args.workspace.expanduser().resolve()
        storage = WorkspaceStorage(workspace, getattr(args, "data_dir", None)).ensure()
        root.mkdir(parents=True, exist_ok=True)
        session_id = f"benchmark-{uuid4().hex}"
        # The receipt is created before any model request and survives an abrupt exit.
        # UUIDs avoid collisions when separate ALE containers use the same log path.
        with (root / "runtime-storage.json").open("x", encoding="utf-8") as stream:
            json.dump({"workspace": str(workspace), "session": str(storage.sessions / session_id),
                       "history": str(storage.history)}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        session = Session(storage.sessions, session_id)
        paths = [path.expanduser() for path in args.file]
        paths = [path if path.is_absolute() else workspace / path for path in paths]
        user_message = session.make_user_message(prompt, paths)
        agent = create_agent(args, session, on_event=ConsoleRenderer())
        try:
            agent.run(user_message)
        finally:
            agent.close()
        status = 0
    except KeyboardInterrupt:
        status = 130
    except Exception as exc:
        if agent is None:
            print(f"error: {exc}", file=sys.stderr)
    finally:
        if session is not None:
            try:
                export_runtime_logs(root, session, storage)
            except Exception as exc:
                print(f"error: 无法导出运行记录：{exc}", file=sys.stderr)
                if status == 0:
                    status = 1
    return status


def export_runtime_logs(root, session, storage):
    """Keep ALE's archive contract while the live session resides in user data."""
    temporary = root / f".session-export-{uuid4().hex}"
    try:
        shutil.copytree(session.path, temporary)
        temporary.rename(root / "session")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    if storage.history.is_dir():
        exported_history = root / "history.git"
        subprocess.run(["git", "clone", "--mirror", "--no-hardlinks", "--quiet",
                        str(storage.history), str(exported_history)],
                       check=True, capture_output=True, text=True)
        metadata = storage.history / "workspace.json"
        if metadata.is_file():
            shutil.copyfile(metadata, exported_history / "workspace.json")
        # A copied checkpoint archive has no live observers, even if another
        # session is still monitoring the original shared workspace repository.
        for source in (storage.history / "runtime").glob("*.json"):
            record = json.loads(source.read_text(encoding="utf-8"))
            record.update(monitoring=False, archived=True, repository=str(exported_history))
            target = exported_history / "runtime" / source.name
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _terminate(signum, frame):
    # Let the active shell tool clean up its process group before exiting.
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    raise SystemExit(main())
