#!/usr/bin/env python3
"""Inspect runtime file checkpoints from any working directory."""

from pathlib import Path
import sys


# Import the runtime shipped with this helper, not a workspace lookalike.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills_agent.file_history import main


if __name__ == "__main__":
    raise SystemExit(main())
