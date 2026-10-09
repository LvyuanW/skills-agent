"""Discover skill metadata and paths for the model's prompt."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path


class SkillRegistry:
    def __init__(self, roots: list[Path]):
        self.roots = [Path(root).expanduser().resolve() for root in roots]
        self._skills: dict[str, Skill] = {}
        for root in self.roots:
            for path in sorted(root.rglob("SKILL.md")):
                with path.open(encoding="utf-8") as source:
                    if source.readline().strip() != "---":
                        raise ValueError(f"Missing YAML frontmatter: {path}")
                    header: list[str] = []
                    for line in source:
                        if line.strip() == "---":
                            break
                        header.append(line)
                    else:
                        raise ValueError(f"Unclosed YAML frontmatter: {path}")
                try:
                    metadata = yaml.safe_load("".join(header))
                except yaml.YAMLError as exc:
                    raise ValueError(f"Invalid YAML frontmatter: {path}: {exc}") from exc
                if not isinstance(metadata, dict):
                    raise ValueError(f"Invalid skill metadata: {path}")
                name = metadata.get("name")
                description = metadata.get("description")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f"Skill name must be a nonempty string: {path}")
                if not isinstance(description, str) or not description.strip():
                    raise ValueError(f"Skill description must be a nonempty string: {path}")
                if name in self._skills:
                    if self._skills[name].path == path:
                        continue
                    raise ValueError(f"Duplicate skill name: {name}")
                self._skills[name] = Skill(name, description, path)

    def catalog(self) -> str:
        return "\n".join(
            f"- {skill.name}: {skill.description.strip()} ({skill.path})"
            for skill in self._skills.values()
        )
