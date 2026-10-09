from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class SkillsAgentConfig:
    name: ClassVar[str] = "skills_agent"

    package: str
    model: str
    provider: str = "openai"
    base_url: str | None = None
    api_key: str | None = None
    workspace: str | None = None
    context_chars: int = 48000
    max_output_tokens: int = 4096
    skill_dirs: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.package or not self.model:
            raise ValueError("package and model are required")
        if self.provider not in {"openai", "anthropic"}:
            raise ValueError("provider must be openai or anthropic")
        if self.context_chars <= 0 or self.max_output_tokens <= 0:
            raise ValueError("context_chars and max_output_tokens must be positive")
