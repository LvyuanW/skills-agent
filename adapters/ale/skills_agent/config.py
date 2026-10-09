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
    context_tokens: int = 272000
    api_format: str = "auto"
    token_counter: str = "auto"
    tokenizer_path: str | None = None
    tokenizer_encoding: str | None = None
    model_context_window: int | None = None
    max_output_tokens: int = 4096
    summary_output_tokens: int = 32768
    skill_dirs: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.package or not self.model:
            raise ValueError("package and model are required")
        if self.provider not in {"openai", "anthropic"}:
            raise ValueError("provider must be openai or anthropic")
        if self.context_tokens <= 0 or self.max_output_tokens <= 0 or self.summary_output_tokens <= 0:
            raise ValueError("context_tokens, max_output_tokens and summary_output_tokens must be positive")
        if self.model_context_window is not None and self.model_context_window <= 0:
            raise ValueError("model_context_window must be positive")
        if self.api_format not in {"auto", "responses", "chat"}:
            raise ValueError("api_format must be auto, responses, or chat")
        if self.token_counter not in {"auto", "official", "deepseek"}:
            raise ValueError("token_counter must be auto, official, or deepseek")
