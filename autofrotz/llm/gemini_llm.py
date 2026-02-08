"""
Google Gemini LLM provider implementation.

Supports Gemini models with explicit prompt caching.
"""

import json
import logging
import time

from google import genai
from google.genai import types

from autofrotz.llm.base import BaseLLM
from autofrotz.storage.models import LLMResponse

logger = logging.getLogger(__name__)


class GeminiLLM(BaseLLM):
    """Google Gemini LLM provider with prompt caching support."""

    provider_name = "gemini"

    def __init__(self, model: str, api_key: str, **kwargs):
        """
        Initialize Gemini provider.

        Args:
            model: Model name (e.g., "gemini-2.0-flash-exp")
            api_key: Google API key
            **kwargs: Additional configuration
        """
        super().__init__(model, api_key, **kwargs)
        self.client = genai.Client(api_key=api_key)
        logger.info(f"Initialized Gemini provider with model={model}")

    def complete(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> LLMResponse:
        """Generate a text completion using Gemini's generate_content API."""
        start_time = time.monotonic()

        try:
            logger.debug(
                f"Gemini completion request: model={self.model}, "
                f"temperature={temperature}, max_tokens={max_tokens}"
            )

            # Convert messages to Gemini Content format
            contents = []
            for msg in messages:
                role = "user" if msg["role"] == "user" else "model"
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part(text=msg["content"])]
                    )
                )

            # Create config with system instruction
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                max_output_tokens=max_tokens
            )

            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )

            latency_ms = (time.monotonic() - start_time) * 1000

            # Extract text from response
            text = ""
            if response.text:
                text = response.text

            # Extract usage metadata
            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0

            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                input_tokens = getattr(usage, 'prompt_token_count', 0)
                output_tokens = getattr(usage, 'candidates_token_count', 0)
                cached_tokens = getattr(usage, 'cached_content_token_count', 0)

            # Cost estimate (approximate rates as of 2025):
            # Gemini 2.0 Flash: $0.075/1M input (prompt < 128k), $0.30/1M output
            # Gemini 2.0 Flash: $0.15/1M input (prompt ≥ 128k), $0.60/1M output
            # Gemini Pro: $1.25/1M input (< 128k), $5.00/1M output
            # Cache storage: $1.00/1M tokens per hour
            if "flash" in self.model.lower():
                # Assume < 128k for now (most game turns will be)
                input_rate = 0.075
                output_rate = 0.30
            elif "pro" in self.model.lower():
                input_rate = 1.25
                output_rate = 5.00
            else:
                input_rate = 0.075
                output_rate = 0.30

            # Cached tokens have 75% discount (roughly)
            cache_rate = input_rate * 0.25
            regular_input_tokens = input_tokens - cached_tokens

            cost_estimate = (
                (regular_input_tokens * input_rate +
                 cached_tokens * cache_rate +
                 output_tokens * output_rate) / 1_000_000
            )

            logger.debug(
                f"Gemini completion response: {input_tokens} input tokens "
                f"({cached_tokens} cached), {output_tokens} output tokens, "
                f"{latency_ms:.1f}ms"
            )

            return LLMResponse(
                text=text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                cost_estimate=cost_estimate,
                latency_ms=latency_ms
            )

        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise RuntimeError(f"Gemini completion failed: {e}") from e

    def complete_json(
        self,
        messages: list[dict],
        system_prompt: str,
        schema: dict,
        temperature: float = 0.1,
        max_tokens: int = 512
    ) -> dict:
        """
        Generate structured JSON output using Gemini's response schema.

        Retries up to 2 times on JSON parse failure.
        """
        for attempt in range(3):
            try:
                start_time = time.monotonic()

                logger.debug(
                    f"Gemini JSON completion request (attempt {attempt + 1}): "
                    f"model={self.model}, temperature={temperature}"
                )

                # Convert messages to Gemini Content format
                contents = []
                for msg in messages:
                    role = "user" if msg["role"] == "user" else "model"
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[types.Part(text=msg["content"])]
                        )
                    )

                # Create config with JSON mode and response schema
                config = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                    response_schema=schema
                )

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config
                )

                latency_ms = (time.monotonic() - start_time) * 1000

                text = response.text if response.text else "{}"

                logger.debug(
                    f"Gemini JSON response received in {latency_ms:.1f}ms, "
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

            except Exception as e:
                logger.error(f"Gemini API error in JSON completion: {e}")
                if attempt == 2:
                    raise RuntimeError(
                        f"Gemini JSON completion failed: {e}"
                    ) from e

        # Should never reach here
        raise RuntimeError("JSON completion failed after retries")

    def count_tokens(self, text: str) -> int:
        """
        Estimate token count using simple heuristic.

        For more accurate counts, could use the count_tokens API endpoint,
        but for cost estimation purposes, the 1 token ≈ 4 characters
        heuristic is sufficient.
        """
        return len(text) // 4
