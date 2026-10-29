"""Keep CLI test sessions under each temporary task's runtime directory."""

import os
from unittest.mock import patch

from skills_agent.storage import WorkspaceStorage


def isolated_runtime():
    return patch.dict(os.environ, {"SKILLS_AGENT_DATA_DIR": ".test-runtime"})


def runtime_sessions(workspace):
    return WorkspaceStorage(workspace).sessions


def terminal_storage(workspace):
    """Match the PTY child's explicit runtime root, regardless of the test module."""
    with isolated_runtime():
        return WorkspaceStorage(workspace)
