"""Best-effort local metadata captured once when an Agent is created."""

import os
from pathlib import Path
import platform
import subprocess
import sys


def _value(read):
    try:
        value = read()
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\r", "\\r").replace("\n", "\\n")
    except Exception:
        # Metadata must not prevent a session from starting. Cancellation still
        # propagates because KeyboardInterrupt/SystemExit are BaseExceptions.
        pass
    return "unknown"


def _git_repository(workspace):
    if workspace is None:
        return "unknown"
    # Discover from this directory, even when the launching process inherited
    # Git's repository overrides. No remote operation or directory walk is used.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("GIT_")}
    environment.update(LC_ALL="C", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--git-dir"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=environment,
            timeout=1, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode == 0 and result.stdout.strip():
        return "true"
    if result.returncode == 128 and "not a git repository" in result.stderr:
        return "false"
    return "unknown"


def _shell():
    # execution.run_command uses Popen(shell=True) without an executable
    # override. POSIX subprocess chooses this path, independently of $SHELL.
    # The bounded command executor currently does not support non-POSIX hosts.
    if os.name != "posix":
        return "unknown"
    return "/system/bin/sh" if hasattr(sys, "getandroidapilevel") else "/bin/sh"


def environment_info(workspace):
    """Return stable prompt text; callers retain this snapshot for the Agent.

    Collection performs one bounded, local Git probe. It does not cache globally,
    so another Agent can correctly describe a different working directory.
    """
    try:
        directory = Path(workspace).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        directory = None
    system = _value(platform.system)
    release = _value(platform.release)
    os_version = f"{system} {release}" if "unknown" not in (system, release) else "unknown"
    return (
        "# Environment\n\n"
        "You have been invoked in the following environment:\n\n"
        f"- Primary working directory: {_value(lambda: str(directory) if directory is not None else None)}\n"
        f"- Is a git repository: {_git_repository(directory)}\n"
        f"- Platform: {_value(lambda: sys.platform)}\n"
        f"- Shell: {_shell()}\n"
        f"- OS Version: {os_version}"
    )
