"""Local counting with DeepSeek's request encoder and matching tokenizer."""
from pathlib import Path

from .base import ProviderError


class DeepSeekCounter:
    def __init__(self, tokenizer_path: str | None, encoding: str | None):
        if not tokenizer_path or encoding not in {'v4', 'v41'}:
            raise ProviderError('DeepSeek counting requires tokenizer_path and tokenizer_encoding=v4 or v41 matching the model')
        path = Path(tokenizer_path).expanduser()
        if not path.is_file():
            raise ProviderError(f'DeepSeek tokenizer file does not exist: {path}')
        try:
            import deepseek_recipe as recipe
        except ImportError:
            raise ProviderError('Install the deepseek extra to use the official DeepSeek tokenizer') from None
        self.recipe = recipe
        self.version = encoding
        try:
            self.tokenizer = recipe.Tokenizer.from_file(str(path))
            encoding_type = recipe.DeepseekV4Encoding if encoding == 'v4' else recipe.DeepseekV41Encoding
            self.encoding = encoding_type().with_tokenizer(self.tokenizer)
        except (RuntimeError, ValueError, OSError):
            raise ProviderError('Could not load the matching DeepSeek tokenizer and encoder') from None

    def count_text(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    def count_request(self, payload: dict) -> int:
        try:
            converted = self.recipe.ChatCompletionRequest(payload).convert(self.recipe.ConversionOptions())
            count = len(self.encoding.encode(converted.conversation))
            images = self.encoding.render_conversation(converted.conversation).image_sources
            if images:
                if self.version != 'v41':
                    raise ProviderError('Official image token preprocessing requires the v41 encoder')
                resolver = self.recipe.ImageResolver(self.recipe.ReqwestImageFetcher(),
                                                      self.recipe.OpenCvImagePreprocessor())
                count += resolver.resolve(images, self.recipe.ImageQuota()).image_token_adjustment()
            return count
        except ProviderError:
            raise
        except Exception as exc:
            # Native bindings have provider-specific conversion/image exceptions.
            raise ProviderError(f'DeepSeek request cannot be counted by this encoder ({type(exc).__name__})') from None
