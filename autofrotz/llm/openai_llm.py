"""
OpenAI LLM provider implementation.

Supports OpenAI models (GPT-4o, GPT-4, GPT-3.5-turbo) and OpenAI-compatible
local servers.
"""

import json
import logging
import time
from typing import Optional

from openai import OpenAI, OpenAIError

from autofrotz.llm.base import BaseLLM
from autofrotz.storage.models import LLMResponse

logger = logging.getLogger(__name__)


class OpenAILLM(BaseLLM):
    """OpenAI and OpenAI-compatible LLM provider."""

    provider_name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize OpenAI provider.

        Args:
            model: Model name (e.g., "gpt-4o", "gpt-4o-mini")
            api_key: OpenAI API key
            base_url: Optional base URL for OpenAI-compatible servers
            **kwargs: Additional configuration
        """
        super().__init__(model, api_key, **kwargs)
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.base_url = base_url
        logger.info(
            f"Initialized OpenAI provider with model={model}, "
            f"base_url={base_url or 'default'}"
        )

    def complete(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> LLMResponse:
        """Generate a text completion using OpenAI's chat API."""
        start_time = time.monotonic()

        # Build messages array with system prompt first (for prompt caching)
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        try:
            logger.debug(
                f"OpenAI completion request: model={self.model}, "
                f"temperature={temperature}, max_tokens={max_tokens}"
            )

            response = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens
            )

            latency_ms = (time.monotonic() - start_time) * 1000

            text = response.choices[0].message.content or ""
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            # OpenAI doesn't explicitly report cached tokens in the standard response
            # but cached prompts are automatically handled
            cached_tokens = 0

            # Rough cost estimate (in USD, approximate rates as of 2025)
            # GPT-4o: $5/1M input, $15/1M output
            # GPT-4o-mini: $0.15/1M input, $0.60/1M output
            if "gpt-4o-mini" in self.model.lower():
                cost_estimate = (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000
            elif "gpt-4o" in self.model.lower():
                cost_estimate = (input_tokens * 5.0 + output_tokens * 15.0) / 1_000_000
            elif "gpt-4" in self.model.lower():
                cost_estimate = (input_tokens * 30.0 + output_tokens * 60.0) / 1_000_000
            elif "gpt-3.5" in self.model.lower():
                cost_estimate = (input_tokens * 0.5 + output_tokens * 1.5) / 1_000_000
            else:
                cost_estimate = 0.0

            logger.debug(
                f"OpenAI completion response: {input_tokens} input tokens, "
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

        except OpenAIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise RuntimeError(f"OpenAI completion failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error in OpenAI completion: {e}")
            raise RuntimeError(f"OpenAI completion failed: {e}") from e

    def complete_json(
        self,
        messages: list[dict],
        system_prompt: str,
        schema: dict,
        temperature: float = 0.1,
        max_tokens: int = 512
    ) -> dict:
        """
        Generate structured JSON output using OpenAI's JSON mode.

        Retries up to 2 times on JSON parse failure.
        """
        # Append JSON instruction to system prompt
        json_system_prompt = (
            f"{system_prompt}\n\n"
            f"You must respond with valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}"
        )

        for attempt in range(3):
            try:
                start_time = time.monotonic()

                # Build messages array with system prompt first
                full_messages = [{"role": "system", "content": json_system_prompt}]
                full_messages.extend(messages)

                logger.debug(
                    f"OpenAI JSON completion request (attempt {attempt + 1}): "
                    f"model={self.model}, temperature={temperature}"
                )

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=full_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"}
                )

                latency_ms = (time.monotonic() - start_time) * 1000

                text = response.choices[0].message.content or "{}"

                logger.debug(
                    f"OpenAI JSON response received in {latency_ms:.1f}ms, "
                    f"parsing JSON..."
                )

                result = json.loads(text)
                logger.debug("JSON parsing successful")
                return result

            except json.JSONDecodeError as e:
                logger.warning(
                    f"JSON parse error on attempt {attempt + 1}: {e}. "
                    f"Response text: {text[:200]}"
                )
                if attempt == 2:
                    raise RuntimeError(
                        f"Failed to parse JSON after 3 attempts: {e}"
                    ) from e
                # Add error context for retry
                messages.append({
                    "role": "assistant",
                    "content": text
                })
                messages.append({
                    "role": "user",
                    "content": (
                        f"That response was not valid JSON. Error: {e}. "
                        f"Please provide a valid JSON response matching the schema."
                    )
                })

            except OpenAIError as e:
                logger.error(f"OpenAI API error in JSON completion: {e}")
                raise RuntimeError(f"OpenAI JSON completion failed: {e}") from e
            except Exception as e:
                logger.error(f"Unexpected error in OpenAI JSON completion: {e}")
                raise RuntimeError(f"OpenAI JSON completion failed: {e}") from e

        # Should never reach here
        raise RuntimeError("JSON completion failed after retries")

    def count_tokens(self, text: str) -> int:
        """
        Estimate token count using simple heuristic.

        OpenAI's tiktoken library could be used for exact counts,
        but for cost estimation purposes, the 1 token ≈ 4 characters
        heuristic is sufficient.
        """
        return len(text) // 4
