"""
Abstract base class for LLM providers.

Defines the interface that all LLM providers must implement for AutoFrotz v2.
"""

from abc import ABC, abstractmethod
from autofrotz.storage.models import LLMResponse


class BaseLLM(ABC):
    """
    Abstract base class for all LLM providers.

    Each provider implementation must support:
    - Standard text completion
    - Structured JSON output
    - Token counting for cost estimation
    """

    provider_name: str = "base"

    def __init__(self, model: str, api_key: str, **kwargs):
        """
        Initialize the LLM provider.

        Args:
            model: Model identifier (e.g., "gpt-4o", "claude-sonnet-4-20250514")
            api_key: API key for authentication
            **kwargs: Provider-specific configuration
        """
        self.model = model
        self.api_key = api_key
        self.config = kwargs

    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> LLMResponse:
        """
        Generate a text completion.

        Args:
            messages: List of message dicts with "role" and "content" keys
            system_prompt: System-level instructions
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse with text, token counts, cost estimate, and latency
        """
        pass

    @abstractmethod
    def complete_json(
        self,
        messages: list[dict],
        system_prompt: str,
        schema: dict,
        temperature: float = 0.1,
        max_tokens: int = 512
    ) -> dict:
        """
        Generate structured JSON output matching the provided schema.

        Args:
            messages: List of message dicts with "role" and "content" keys
            system_prompt: System-level instructions
            schema: JSON schema defining the expected output structure
            temperature: Sampling temperature (typically low for structured output)
            max_tokens: Maximum tokens to generate

        Returns:
            Parsed JSON dict matching the schema
        """
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """
        Estimate token count for a text string.

        Args:
            text: Text to count tokens for

        Returns:
            Approximate token count
        """
        pass
