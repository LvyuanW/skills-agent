"""Ownership scopes for background helpers started during one agent turn."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from uuid import uuid4


SCOPE_ENV = "SKILLS_AGENT_PROCESS_SCOPE"
HELPER = Path(__file__).parent / "builtin_skills/process-management/scripts/process.py"


class ProcessScope:
    def __init__(self, session_path, *, allow_persistent=False):
        self.successful = False
        self.path = Path(session_path) / "process-scopes" / uuid4().hex
        (self.path / "runs").mkdir(parents=True, mode=0o700)
        (self.path / "scope.json").write_text(json.dumps({
            "version": 1, "allow_persistent": allow_persistent,
        }), encoding="utf-8")

    def close(self, *, preserve_persistent=False):
        saved = {}
        if threading.current_thread() is threading.main_thread():
            # A second Ctrl-C/TERM must not interrupt the cleanup already in flight.
            for signum in (signal.SIGINT, signal.SIGTERM):
                saved[signum] = signal.signal(signum, signal.SIG_IGN)
        try:
            with (self.path / "lock").open("a+b") as lock:
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX)
                (self.path / "closed").touch()
            # On non-POSIX hosts the helper cannot register processes at all.
            if not any((self.path / "runs").glob("*.json")):
                return
            args = [sys.executable, str(HELPER), "_cleanup", "--scope", str(self.path)]
            if preserve_persistent:
                args.append("--preserve-persistent")
            result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=7)
            if result.returncode:
                raise RuntimeError("Background process cleanup was not confirmed: " +
                                   (result.stdout or result.stderr).strip())
        finally:
            for signum, handler in saved.items():
                signal.signal(signum, handler)
