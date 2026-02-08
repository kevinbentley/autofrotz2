"""
Tests for the LLM factory and provider instantiation.

These tests verify that the factory correctly instantiates providers from
configuration without making actual API calls.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from autofrotz.llm.factory import create_llm, load_config
from autofrotz.llm.openai_llm import OpenAILLM
from autofrotz.llm.claude_llm import ClaudeLLM
from autofrotz.llm.gemini_llm import GeminiLLM


@pytest.fixture
def mock_config():
    """Mock configuration dictionary matching config.json structure."""
    return {
        "agents": {
            "game_agent": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "temperature": 0.7,
                "max_tokens": 1024
            },
            "puzzle_agent": {
                "provider": "openai",
                "model": "gpt-4o",
                "temperature": 0.5,
                "max_tokens": 1024
            },
            "map_parser": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "temperature": 0.1,
                "max_tokens": 512
            },
            "item_parser": {
                "provider": "gemini",
                "model": "gemini-2.0-flash-exp",
                "temperature": 0.1,
                "max_tokens": 512
            },
            "local_agent": {
                "provider": "local",
                "model": "llama-3-70b",
                "temperature": 0.7,
                "max_tokens": 1024
            }
        },
        "providers": {
            "openai": {
                "api_key_env": "OPENAI_API_KEY",
                "base_url": None
            },
            "anthropic": {
                "api_key_env": "ANTHROPIC_API_KEY"
            },
            "gemini": {
                "api_key_env": "GEMINI_API_KEY"
            },
            "local": {
                "base_url": "http://localhost:1234/v1",
                "api_key": "not-needed",
                "provider_type": "openai"
            }
        }
    }


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set mock environment variables for API keys."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")


def test_factory_creates_openai_provider(mock_config, mock_env_vars):
    """Test that factory creates OpenAILLM for openai provider."""
    llm = create_llm("puzzle_agent", mock_config)
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "gpt-4o"
    assert llm.api_key == "test-openai-key"
    assert llm.provider_name == "openai"


def test_factory_creates_anthropic_provider(mock_config, mock_env_vars):
    """Test that factory creates ClaudeLLM for anthropic provider."""
    llm = create_llm("game_agent", mock_config)
    assert isinstance(llm, ClaudeLLM)
    assert llm.model == "claude-sonnet-4-20250514"
    assert llm.api_key == "test-anthropic-key"
    assert llm.provider_name == "anthropic"


def test_factory_creates_gemini_provider(mock_config, mock_env_vars):
    """Test that factory creates GeminiLLM for gemini provider."""
    llm = create_llm("item_parser", mock_config)
    assert isinstance(llm, GeminiLLM)
    assert llm.model == "gemini-2.0-flash-exp"
    assert llm.api_key == "test-gemini-key"
    assert llm.provider_name == "gemini"


def test_factory_creates_local_openai_provider(mock_config, mock_env_vars):
    """Test that factory creates OpenAILLM with base_url for local provider."""
    llm = create_llm("local_agent", mock_config)
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "llama-3-70b"
    assert llm.api_key == "not-needed"
    assert llm.base_url == "http://localhost:1234/v1"


def test_factory_raises_on_unknown_provider(mock_config, mock_env_vars):
    """Test that factory raises ValueError for unknown provider."""
    mock_config["agents"]["bad_agent"] = {
        "provider": "unknown_provider",
        "model": "some-model"
    }
    mock_config["providers"]["unknown_provider"] = {
        "api_key_env": "SOME_KEY"
    }

    with pytest.raises(ValueError, match="Unknown provider"):
        create_llm("bad_agent", mock_config)


def test_factory_raises_on_missing_agent(mock_config, mock_env_vars):
    """Test that factory raises ValueError for missing agent configuration."""
    with pytest.raises(ValueError, match="Invalid configuration"):
        create_llm("nonexistent_agent", mock_config)


def test_factory_reads_api_keys_from_env(mock_config, monkeypatch):
    """Test that factory correctly reads API keys from environment variables."""
    monkeypatch.setenv("OPENAI_API_KEY", "custom-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "custom-anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "custom-gemini-key")

    openai_llm = create_llm("puzzle_agent", mock_config)
    assert openai_llm.api_key == "custom-openai-key"

    anthropic_llm = create_llm("game_agent", mock_config)
    assert anthropic_llm.api_key == "custom-anthropic-key"

    gemini_llm = create_llm("item_parser", mock_config)
    assert gemini_llm.api_key == "custom-gemini-key"


def test_factory_handles_missing_api_key_env_var(mock_config, monkeypatch):
    """Test that factory handles missing API key environment variable gracefully."""
    # Ensure the env var is not set
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Should create the LLM but with empty api_key
    llm = create_llm("puzzle_agent", mock_config)
    assert isinstance(llm, OpenAILLM)
    assert llm.api_key == ""


def test_load_config_success(tmp_path):
    """Test that load_config successfully loads a valid JSON file."""
    config_file = tmp_path / "test_config.json"
    config_data = {
        "agents": {"test": {"provider": "openai"}},
        "providers": {"openai": {"api_key_env": "TEST_KEY"}}
    }

    import json
    with open(config_file, 'w') as f:
        json.dump(config_data, f)

    config = load_config(str(config_file))
    assert config == config_data


def test_load_config_file_not_found():
    """Test that load_config raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_config.json")


def test_load_config_invalid_json(tmp_path):
    """Test that load_config raises JSONDecodeError for invalid JSON."""
    config_file = tmp_path / "bad_config.json"
    with open(config_file, 'w') as f:
        f.write("{ invalid json }")

    import json
    with pytest.raises(json.JSONDecodeError):
        load_config(str(config_file))


def test_openai_provider_attributes(mock_config, mock_env_vars):
    """Test that OpenAI provider is initialized with correct attributes."""
    llm = create_llm("map_parser", mock_config)
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "gpt-4o-mini"
    assert llm.config.get("temperature") == 0.1
    assert llm.config.get("max_tokens") == 512


def test_anthropic_provider_attributes(mock_config, mock_env_vars):
    """Test that Anthropic provider is initialized with correct attributes."""
    llm = create_llm("game_agent", mock_config)
    assert isinstance(llm, ClaudeLLM)
    assert llm.model == "claude-sonnet-4-20250514"
    assert llm.config.get("temperature") == 0.7
    assert llm.config.get("max_tokens") == 1024


def test_gemini_provider_attributes(mock_config, mock_env_vars):
    """Test that Gemini provider is initialized with correct attributes."""
    llm = create_llm("item_parser", mock_config)
    assert isinstance(llm, GeminiLLM)
    assert llm.model == "gemini-2.0-flash-exp"
    assert llm.config.get("temperature") == 0.1
    assert llm.config.get("max_tokens") == 512
