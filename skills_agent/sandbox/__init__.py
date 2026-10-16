"""Execute commands inside an explicitly configured Docker container."""

import time
import uuid
from pathlib import Path

from ..execution import (
    CLEANUP_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS, ToolCleanupError,
    _cleanup_signals, run_command, validate_timeout,
)


class DockerSandbox:
    def __init__(
        self,
        image: str,
        workspace: Path,
        skill_roots: list[Path] | None = None,
        read_roots: list[Path] | None = None,
    ):
        self.image = image
        self.workspace = Path(workspace).expanduser().resolve()
        self.read_roots = [
            Path(root).expanduser().resolve()
            for root in [*(skill_roots or []), *(read_roots or [])]
        ]

    def _remove_container(self, name: str, log_dir) -> None:
        deadline = time.monotonic() + CLEANUP_TIMEOUT_SECONDS

        def docker(*arguments):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolCleanupError(f"Sandbox cleanup deadline exceeded: {name}")
            return run_command(["docker", *arguments], cwd=self.workspace,
                               timeout_seconds=remaining, log_dir=log_dir)

        try:
            removed = docker("rm", "-f", name)
            if removed["exit_code"] == 0:
                return
            if not removed.get("timed_out"):
                # A failed rm may just mean --rm already removed the container.
                # A successful inventory distinguishes absence from daemon failure.
                containers = docker("container", "ls", "--all", "--format", "{{.Names}}")
                if containers["exit_code"] == 0 and name not in containers["stdout"].splitlines():
                    return
        except (OSError, ToolCleanupError) as exc:
            raise ToolCleanupError(f"Could not confirm sandbox container cleanup: {name}") from exc
        raise ToolCleanupError(f"Could not confirm sandbox container cleanup: {name}")

    def run(self, command: str, *, timeout_seconds=DEFAULT_TIMEOUT_SECONDS, log_dir=None) -> dict:
        timeout_seconds = validate_timeout(timeout_seconds)
        mounts: dict[Path, str] = {self.workspace: "rw"}
        for root in self.read_roots:
            # Paths in the workspace are already accessible and writable.
            if not root.is_relative_to(self.workspace):
                mounts[root] = "ro"
        container_name = f"skills-agent-{uuid.uuid4().hex}"
        args = ["docker", "run", "--rm", "--network", "none", "--name", container_name]
        # Parent mounts precede children so the workspace keeps its writable mount.
        for root, mode in sorted(mounts.items(), key=lambda item: len(item[0].parts)):
            args.extend(["-v", f"{root}:{root}:{mode}"])
        args.extend(["-w", str(self.workspace), self.image, "sh", "-lc", command])
        cleaned = False

        def cleanup():
            nonlocal cleaned
            if not cleaned:
                self._remove_container(container_name, log_dir)
                cleaned = True

        result = run_command(args, cwd=self.workspace, timeout_seconds=timeout_seconds,
                             log_dir=log_dir, cleanup=cleanup)
        if result["exit_code"] != 0:
            with _cleanup_signals():
                cleanup()
        return result
