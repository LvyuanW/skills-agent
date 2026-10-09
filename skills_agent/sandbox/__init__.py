"""Execute commands inside an explicitly configured Docker container."""

import os
import signal
import subprocess
import uuid
from pathlib import Path


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

    def run(self, command: str) -> dict:
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
        with subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", start_new_session=True,
        ) as process:
            try:
                stdout, stderr = process.communicate()
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                try:
                    subprocess.run(
                        ["docker", "rm", "-f", container_name],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                raise
        return {
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
