"""One optional TOML file, with command-line overrides and environment fallbacks."""

from argparse import Namespace
import os
from pathlib import Path
import tomllib

from .context import DEFAULT_CONTEXT_TOKENS
from .providers.base import DEFAULT_MAX_OUTPUT_TOKENS


DEFAULTS = {
    "provider": "openai", "model": None, "base_url": None,
    "context_tokens": DEFAULT_CONTEXT_TOKENS,
    "model_context_window": None,
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    "summary_output_tokens": 32768,
    "api_format": "auto", "token_counter": "auto",
    "tokenizer_path": None, "tokenizer_encoding": None,
    "skill_dirs": [], "sandbox_image": None,
}
CHOICES = {
    "provider": {"openai", "anthropic"},
    "api_format": {"auto", "responses", "chat"},
    "token_counter": {"auto", "official", "deepseek"},
    "tokenizer_encoding": {"v4", "v41"},
}


def _validate(values):
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown agent config option: {key}")
        if value is None:
            continue
        if key.endswith("_tokens") or key == "model_context_window":
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} must be a positive integer")
        elif key == "skill_dirs":
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError("skill_dirs must be an array of non-empty paths")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        if key in CHOICES and value not in CHOICES[key]:
            raise ValueError(f"{key} must be one of: {', '.join(sorted(CHOICES[key]))}")


def load_agent_config(args):
    """Precedence: explicit CLI > TOML > environment > built-in defaults."""
    explicit = getattr(args, "config", None)
    path = (explicit or args.workspace / "skills-agent.toml").expanduser().resolve()
    settings = {}
    if explicit is not None or path.exists():
        with path.open("rb") as stream:
            settings = tomllib.load(stream)
        _validate(settings)
        # File-relative paths stay usable when the config is selected from another cwd.
        for key in ("tokenizer_path", "skill_dirs"):
            if key not in settings:
                continue
            paths = settings[key] if key == "skill_dirs" else [settings[key]]
            resolved = []
            for item in paths:
                item = Path(item).expanduser()
                resolved.append(str((path.parent / item).resolve()))
            settings[key] = resolved if key == "skill_dirs" else resolved[0]
    supplied = {key: getattr(args, key) for key in DEFAULTS if getattr(args, key, None) is not None}
    provider = supplied.get("provider", settings.get("provider", DEFAULTS["provider"]))
    environment = {
        "model": os.environ.get(f"{provider.upper()}_MODEL"),
        "base_url": os.environ.get(f"{provider.upper()}_BASE_URL"),
        **{key: os.environ.get(f"SKILLS_AGENT_{key.upper()}") for key in (
            "api_format", "token_counter", "tokenizer_path", "tokenizer_encoding",
        )},
    }
    values = {**DEFAULTS, **{key: value for key, value in environment.items() if value},
              **settings, **supplied}
    _validate(values)
    return Namespace(**{**vars(args), **values})
