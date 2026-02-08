"""
LLM abstraction layer for AutoFrotz v2.

Provides a unified interface for multiple LLM providers (OpenAI, Anthropic, Google)
with support for standard completions, structured JSON output, and prompt caching.
"""

from autofrotz.llm.base import BaseLLM
from autofrotz.llm.factory import create_llm, load_config
from autofrotz.llm.openai_llm import OpenAILLM
from autofrotz.llm.claude_llm import ClaudeLLM
from autofrotz.llm.gemini_llm import GeminiLLM
from autofrotz.storage.models import LLMResponse

__all__ = [
    "BaseLLM",
    "OpenAILLM",
    "ClaudeLLM",
    "GeminiLLM",
    "LLMResponse",
    "create_llm",
    "load_config",
]
