"""Load this experiment's built-in skill as persistent system instructions."""

from pathlib import Path

import yaml


SYSTEM_SKILL_NAME = None


def load_system_skill():
    if SYSTEM_SKILL_NAME is None:
        return ""
    path = Path(__file__).resolve().parent / "builtin_skills" / SYSTEM_SKILL_NAME / "SKILL.md"
    with path.open(encoding="utf-8") as source:
        if source.readline().strip() != "---":
            raise ValueError(f"Missing system skill frontmatter: {path}")
        header = []
        for line in source:
            if line.strip() == "---":
                break
            header.append(line)
        else:
            raise ValueError(f"Unclosed system skill frontmatter: {path}")
        body = source.read().strip()
    try:
        metadata = yaml.safe_load("".join(header))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid system skill frontmatter: {path}: {exc}") from exc
    if (not isinstance(metadata, dict) or metadata.get("name") != SYSTEM_SKILL_NAME
            or not isinstance(metadata.get("description"), str)
            or not metadata["description"].strip()):
        raise ValueError(f"Invalid system skill metadata: {path}")
    if not body:
        raise ValueError(f"Empty system skill body: {path}")
    return (
        f"Preloaded skill instructions: {SYSTEM_SKILL_NAME}\n"
        f"Source: {path}\n"
        "The skill below is already loaded as part of the system instructions. "
        "Apply it when its stated conditions match the task. "
        "Do not load it again just to read the same instructions. "
        f"Resolve relative references from {path.parent}.\n\n"
        f"{body}\n\n"
    )
