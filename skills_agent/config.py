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
    "summary_output_tokens": 8192,
    "api_format": "auto", "token_counter": "auto",
    "skill_dirs": [], "sandbox_image": None,
}
CHOICES = {
    "provider": {"openai", "anthropic"},
    "api_format": {"auto", "responses", "chat"},
    "token_counter": {"auto", "official", "usage"},
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


def normalize_paths(args):
    """Resolve task paths once against the selected working directory."""
    values = dict(vars(args))
    workspace = Path(values.get("workspace") or Path.cwd()).expanduser().resolve()
    values["workspace"] = workspace

    def resolve(value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else workspace / path).resolve()

    for key in ("config", "prompt_file", "log_dir"):
        if values.get(key) is not None:
            values[key] = resolve(values[key])
    if values.get("file") is not None:
        values["file"] = [resolve(path) for path in values["file"]]
    if values.get("skill_dirs") is not None:
        values["skill_dirs"] = [str(resolve(path)) for path in values["skill_dirs"]]
    return Namespace(**values)


def load_agent_config(args):
    """Precedence: explicit CLI > TOML > environment > built-in defaults."""
    args = normalize_paths(args)
    explicit = getattr(args, "config", None)
    path = (explicit or args.workspace / "skills-agent.toml").expanduser().resolve()
    settings = {}
    if explicit is not None or path.exists():
        with path.open("rb") as stream:
            settings = tomllib.load(stream)
        _validate(settings)
        # File-relative paths stay usable when the config is selected from another cwd.
        if "skill_dirs" in settings:
            resolved = []
            for item in settings["skill_dirs"]:
                item = Path(item).expanduser()
                resolved.append(str((path.parent / item).resolve()))
            settings["skill_dirs"] = resolved
    supplied = {key: getattr(args, key) for key in DEFAULTS if getattr(args, key, None) is not None}
    provider = supplied.get("provider", settings.get("provider", DEFAULTS["provider"]))
    environment = {
        "model": os.environ.get(f"{provider.upper()}_MODEL"),
        "base_url": os.environ.get(f"{provider.upper()}_BASE_URL"),
        **{key: os.environ.get(f"SKILLS_AGENT_{key.upper()}") for key in (
            "api_format", "token_counter",
        )},
    }
    values = {**DEFAULTS, **{key: value for key, value in environment.items() if value},
              **settings, **supplied}
    _validate(values)
    return Namespace(**{**vars(args), **values})
