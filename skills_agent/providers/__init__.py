from .anthropic import AnthropicProvider
from .base import Provider, ProviderError
from .openai import OpenAIProvider

__all__ = ["Provider", "ProviderError", "OpenAIProvider", "AnthropicProvider", "create_provider"]


def create_provider(provider: str, model: str, api_key: str,
                    base_url: str | None = None, max_output_tokens: int = 4096) -> Provider:
    providers = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}
    if provider not in providers:
        raise ValueError(f"Unknown provider: {provider}")
    return providers[provider](model, api_key, base_url, max_output_tokens)
