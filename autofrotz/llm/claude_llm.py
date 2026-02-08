"""
Anthropic Claude LLM provider implementation.

Supports Claude models with explicit prompt caching using cache_control.
"""

import json
import logging
import time

from anthropic import Anthropic, AnthropicError

from autofrotz.llm.base import BaseLLM
from autofrotz.storage.models import LLMResponse

logger = logging.getLogger(__name__)


class ClaudeLLM(BaseLLM):
    """Anthropic Claude LLM provider with prompt caching support."""

    provider_name = "anthropic"

    def __init__(self, model: str, api_key: str, **kwargs):
        """
        Initialize Anthropic provider.

        Args:
            model: Model name (e.g., "claude-sonnet-4-20250514")
            api_key: Anthropic API key
            **kwargs: Additional configuration
        """
        super().__init__(model, api_key, **kwargs)
        self.client = Anthropic(api_key=api_key)
        logger.info(f"Initialized Anthropic provider with model={model}")

    def complete(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> LLMResponse:
        """
        Generate a text completion using Anthropic's messages API.

        Uses explicit cache_control on the system prompt for prompt caching.
        """
        start_time = time.monotonic()

        try:
            logger.debug(
                f"Anthropic completion request: model={self.model}, "
                f"temperature={temperature}, max_tokens={max_tokens}"
            )

            # System prompt with cache_control for ephemeral caching
            # Cache TTL is 5 minutes, refreshed on each use
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"}
                    }
                ],
                messages=messages
            )

            latency_ms = (time.monotonic() - start_time) * 1000

            text = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    text += block.text

            # Extract usage metrics
            usage = response.usage
            input_tokens = usage.input_tokens if hasattr(usage, 'input_tokens') else 0
            output_tokens = usage.output_tokens if hasattr(usage, 'output_tokens') else 0

            # Anthropic reports cache read and cache creation tokens separately
            cache_read_tokens = 0
            cache_creation_tokens = 0
            if hasattr(usage, 'cache_read_input_tokens'):
                cache_read_tokens = usage.cache_read_input_tokens or 0
            if hasattr(usage, 'cache_creation_input_tokens'):
                cache_creation_tokens = usage.cache_creation_input_tokens or 0

            cached_tokens = cache_read_tokens

            # Cost estimate (approximate rates as of 2025):
            # Claude Sonnet 4: $3/1M input, $15/1M output
            # Cache writes: 25% premium (3.75/1M)
            # Cache reads: 90% discount (0.30/1M)
            if "claude-sonnet-4" in self.model.lower():
                base_input_rate = 3.0
                output_rate = 15.0
            elif "claude-opus-4" in self.model.lower():
                base_input_rate = 15.0
                output_rate = 75.0
            elif "claude-haiku" in self.model.lower():
                base_input_rate = 0.25
                output_rate = 1.25
            else:
                base_input_rate = 3.0
                output_rate = 15.0

            cache_write_rate = base_input_rate * 1.25
            cache_read_rate = base_input_rate * 0.10

            regular_input_tokens = input_tokens - cache_creation_tokens
            cost_estimate = (
                (regular_input_tokens * base_input_rate +
                 cache_creation_tokens * cache_write_rate +
                 cache_read_tokens * cache_read_rate +
                 output_tokens * output_rate) / 1_000_000
            )

            logger.debug(
                f"Anthropic completion response: {input_tokens} input tokens "
                f"({cache_read_tokens} cached, {cache_creation_tokens} cache writes), "
                f"{output_tokens} output tokens, {latency_ms:.1f}ms"
            )

            return LLMResponse(
                text=text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                cost_estimate=cost_estimate,
                latency_ms=latency_ms
            )

        except AnthropicError as e:
            logger.error(f"Anthropic API error: {e}")
            raise RuntimeError(f"Anthropic completion failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error in Anthropic completion: {e}")
            raise RuntimeError(f"Anthropic completion failed: {e}") from e

    def complete_json(
        self,
        messages: list[dict],
        system_prompt: str,
        schema: dict,
        temperature: float = 0.1,
        max_tokens: int = 512
    ) -> dict:
        """
        Generate structured JSON output using Anthropic's tool use pattern.

        Defines a tool called "extract" with the provided schema and forces
        its use via tool_choice.
        """
        # Create a tool definition from the schema
        tool_definition = {
            "name": "extract",
            "description": "Extract structured information from the input",
            "input_schema": schema
        }

        for attempt in range(3):
            try:
                start_time = time.monotonic()

                logger.debug(
                    f"Anthropic JSON completion request (attempt {attempt + 1}): "
                    f"model={self.model}, temperature={temperature}"
                )

                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"}
                        }
                    ],
                    messages=messages,
                    tools=[tool_definition],
                    tool_choice={"type": "tool", "name": "extract"}
                )

                latency_ms = (time.monotonic() - start_time) * 1000

                # Extract tool use from response
                for block in response.content:
                    if block.type == "tool_use" and block.name == "extract":
                        result = block.input
                        logger.debug(
                            f"Anthropic JSON response received in {latency_ms:.1f}ms, "
                            f"extracted tool input"
                        )
                        return result

                # If no tool use found, this is an error
                raise RuntimeError(
                    "No tool use found in Anthropic response. "
                    "Response may not have followed tool_choice directive."
                )

            except AnthropicError as e:
                logger.error(f"Anthropic API error in JSON completion: {e}")
                if attempt == 2:
                    raise RuntimeError(
                        f"Anthropic JSON completion failed: {e}"
                    ) from e
                # Retry with error context
                messages.append({
                    "role": "assistant",
                    "content": str(e)
                })
                messages.append({
                    "role": "user",
                    "content": (
                        f"Previous request failed with error: {e}. "
                        f"Please use the extract tool to provide structured output."
                    )
                })

            except Exception as e:
                logger.error(f"Unexpected error in Anthropic JSON completion: {e}")
                if attempt == 2:
                    raise RuntimeError(
                        f"Anthropic JSON completion failed: {e}"
                    ) from e

        # Should never reach here
        raise RuntimeError("JSON completion failed after retries")

    def count_tokens(self, text: str) -> int:
        """
        Estimate token count using simple heuristic.

        Anthropic's tokenizer could be used for exact counts, but for
        cost estimation purposes, the 1 token ≈ 4 characters heuristic
        is sufficient.
        """
        return len(text) // 4
