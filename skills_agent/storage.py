"""Stable runtime storage, separated from the files an agent is working on."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


class WorkspaceStorage:
    def __init__(self, workspace: Path, data_dir: Path | str | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        selected = data_dir or os.environ.get("SKILLS_AGENT_DATA_DIR") or Path.home() / ".skills-agent"
        self.data_dir = Path(selected).expanduser().resolve()
        self.workspace_id = hashlib.sha256(os.fsencode(str(self.workspace))).hexdigest()
        self.root = self.data_dir / "workspaces" / self.workspace_id
        self.sessions = self.root / "sessions"
        self.history = self.root / "history.git"

    def ensure(self):
        if not self.workspace.is_dir():
            raise ValueError(f"工作目录不存在：{self.workspace}")
        self.sessions.mkdir(parents=True, exist_ok=True)
        metadata = self.root / "workspace.json"
        record = {"workspace_id": self.workspace_id, "workspace": str(self.workspace),
                  "created_at": datetime.now(timezone.utc).isoformat()}
        temporary = self.root / f".workspace-{uuid4().hex}.json"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            try:
                os.link(temporary, metadata)
            except FileExistsError:
                saved = json.loads(metadata.read_text(encoding="utf-8"))
                if saved.get("workspace") != str(self.workspace):
                    raise ValueError(f"工作区存储记录不匹配：{metadata}")
        finally:
            temporary.unlink(missing_ok=True)
        return self

    def session_root(self, session_id: str, explicit: Path | None = None):
        if explicit is not None:
            return Path(explicit).expanduser().resolve()
        legacy = self.workspace / ".agent" / "sessions"
        if not (self.sessions / session_id).exists() and (legacy / session_id).exists():
            raise ValueError(
                f"发现旧位置的会话 {session_id}：{legacy / session_id}。"
                f"请使用 --sessions-dir '{legacy}' 继续或回放该会话；旧记录未移动。")
        return self.sessions
