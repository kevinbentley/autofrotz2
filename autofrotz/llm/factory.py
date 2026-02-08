"""
LLM factory for creating provider instances from configuration.

Reads config.json and instantiates the appropriate LLM provider for each agent.
"""

import json
import logging
import os
from typing import Any

from autofrotz.llm.base import BaseLLM
from autofrotz.llm.openai_llm import OpenAILLM
from autofrotz.llm.claude_llm import ClaudeLLM
from autofrotz.llm.gemini_llm import GeminiLLM

logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.json") -> dict:
    """
    Load configuration from JSON file.

    Args:
        config_path: Path to config.json

    Returns:
        Parsed configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
    """
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        logger.info(f"Loaded configuration from {config_path}")
        return config
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        raise


def create_llm(agent_name: str, config: dict) -> BaseLLM:
    """
    Create an LLM provider instance for a specific agent.

    Args:
        agent_name: Name of the agent (e.g., "game_agent", "map_parser")
        config: Full configuration dictionary

    Returns:
        Initialized LLM provider instance

    Raises:
        ValueError: If provider is unknown or configuration is invalid
        KeyError: If required configuration keys are missing
    """
    try:
        # Get agent configuration
        agent_config = config["agents"][agent_name]
        provider_name = agent_config["provider"]
        model = agent_config["model"]
        temperature = agent_config.get("temperature", 0.7)
        max_tokens = agent_config.get("max_tokens", 1024)

        # Get provider configuration
        provider_config = config["providers"][provider_name]

        logger.info(
            f"Creating LLM for agent '{agent_name}': "
            f"provider={provider_name}, model={model}"
        )

        # Resolve API key from environment variable
        if "api_key_env" in provider_config:
            api_key_env = provider_config["api_key_env"]
            api_key = os.environ.get(api_key_env)
            if not api_key:
                logger.warning(
                    f"API key environment variable '{api_key_env}' not set "
                    f"for provider '{provider_name}'"
                )
                api_key = ""
        elif "api_key" in provider_config:
            # Direct API key (for local servers)
            api_key = provider_config["api_key"]
        else:
            raise ValueError(
                f"No API key configuration found for provider '{provider_name}'"
            )

        # Instantiate the appropriate provider
        if provider_name == "openai":
            base_url = provider_config.get("base_url")
            return OpenAILLM(
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif provider_name == "anthropic":
            return ClaudeLLM(
                model=model,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif provider_name == "gemini":
            return GeminiLLM(
                model=model,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens
            )

        elif provider_name == "local":
            # Local servers typically use OpenAI-compatible API
            provider_type = provider_config.get("provider_type", "openai")
            base_url = provider_config.get("base_url")

            if provider_type == "openai":
                return OpenAILLM(
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
            else:
                raise ValueError(
                    f"Unsupported provider_type '{provider_type}' for local provider"
                )

        else:
            raise ValueError(f"Unknown provider: {provider_name}")

    except KeyError as e:
        logger.error(f"Missing configuration key for agent '{agent_name}': {e}")
        raise ValueError(
            f"Invalid configuration for agent '{agent_name}': missing key {e}"
        ) from e
    except Exception as e:
        logger.error(f"Failed to create LLM for agent '{agent_name}': {e}")
        raise
