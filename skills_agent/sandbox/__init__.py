"""Execute commands inside an explicitly configured Docker container."""

import subprocess
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
        args = ["docker", "run", "--rm", "--network", "none"]
        # Parent mounts precede children so the workspace keeps its writable mount.
        for root, mode in sorted(mounts.items(), key=lambda item: len(item[0].parts)):
            args.extend(["-v", f"{root}:{root}:{mode}"])
        args.extend(["-w", str(self.workspace), self.image, "sh", "-lc", command])
        result = subprocess.run(args, capture_output=True, text=True, errors="replace")
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
