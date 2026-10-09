from .anthropic import AnthropicProvider
from .base import Provider, ProviderError, ProviderConnectionError, ProviderOutputLimitError
from .openai import OpenAIProvider

__all__ = ["Provider", "ProviderError", "ProviderConnectionError", "ProviderOutputLimitError",
           "OpenAIProvider", "AnthropicProvider", "create_provider"]


def create_provider(provider: str, model: str, api_key: str,
                    base_url: str | None = None, max_output_tokens: int = 4096, *,
                    api_format: str = "auto", token_counter: str = "auto",
                    tokenizer_path: str | None = None, tokenizer_encoding: str | None = None,
                    model_context_window: int | None = None) -> Provider:
    providers = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}
    if provider not in providers:
        raise ValueError(f"Unknown provider: {provider}")
    return providers[provider](model, api_key, base_url, max_output_tokens,
                               api_format=api_format, token_counter=token_counter,
                               tokenizer_path=tokenizer_path, tokenizer_encoding=tokenizer_encoding,
                               model_context_window=model_context_window)
