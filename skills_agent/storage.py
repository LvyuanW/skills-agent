"""Stable runtime storage, separated from the files an agent is working on."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4


class WorkspaceStorage:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()
        selected = Path(os.environ.get("SKILLS_AGENT_DATA_DIR") or Path.home() / ".skills-agent").expanduser()
        self.data_dir = (selected if selected.is_absolute() else self.workspace / selected).resolve()
        self.workspace_id = hashlib.sha256(os.fsencode(str(self.workspace))).hexdigest()
        self.root = self.data_dir / "workspaces" / self.workspace_id
        self.sessions = self.root / "sessions"
        self.images = self.root / "images"
        self.history = self.root / "history.git"

    def session_images(self, session_id: str):
        """Return this session's image output location without creating it."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id):
            raise ValueError("Invalid session name")
        return self.images / session_id

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

    def session_root(self, session_id: str):
        legacy = self.workspace / ".agent" / "sessions"
        if not (self.sessions / session_id).exists() and (legacy / session_id).exists():
            raise ValueError(
                f"发现旧位置的会话 {session_id}：{legacy / session_id}。"
                f"请停止使用该旧会话的进程后，离线迁移到 {self.sessions / session_id}。"
                "旧记录未移动；不要在旧进程仍运行时迁移。")
        return self.sessions

    def migrate_legacy_session_offline(self, session_id: str):
        """Maintenance only: stop every writer before calling; never used at startup.

        Keep old absolute references usable through a directory symlink. Raw
        history is moved unchanged, not rewritten or copied into a new session.
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id):
            raise ValueError("Invalid session name")
        source = self.workspace / ".agent" / "sessions" / session_id
        destination = self.sessions / session_id
        if source.is_symlink():
            if source.resolve() == destination and destination.is_dir():
                return destination
            raise ValueError(f"旧会话路径是其它符号链接：{source}")
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"目标会话已存在，未迁移：{destination}")
        if not source.is_dir():
            raise ValueError(f"旧会话不存在：{source}")
        self.ensure()
        shutil.move(str(source), str(destination))
        try:
            source.symlink_to(destination, target_is_directory=True)
        except OSError:
            shutil.move(str(destination), str(source))
            raise
        return destination
