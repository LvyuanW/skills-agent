from .anthropic import AnthropicProvider
from .base import DEFAULT_MAX_OUTPUT_TOKENS, Provider, ProviderError, ProviderConnectionError, ProviderOutputLimitError
from .openai import OpenAIProvider

__all__ = ["Provider", "ProviderError", "ProviderConnectionError", "ProviderOutputLimitError",
           "OpenAIProvider", "AnthropicProvider", "create_provider"]


def create_provider(provider: str, model: str, api_key: str,
                    base_url: str | None = None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS, *,
                    api_format: str = "auto", token_counter: str = "auto",
                    model_context_window: int | None = None,
                    deepseek_effort: str | None = None) -> Provider:
    providers = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}
    if provider not in providers:
        raise ValueError(f"Unknown provider: {provider}")
    options = {"deepseek_effort": deepseek_effort} if provider == "openai" else {}
    return providers[provider](model, api_key, base_url, max_output_tokens,
                               api_format=api_format, token_counter=token_counter,
                               model_context_window=model_context_window, **options)
