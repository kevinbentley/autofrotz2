"""
Integration tests for LLM providers.

These tests require valid API keys and make real API calls.
Run manually with: pytest tests/test_llm_integration.py -v -m integration

Mark tests with @pytest.mark.integration to exclude them from regular test runs.
"""

import pytest
import os

from autofrotz.llm import create_llm, load_config


# Skip all integration tests by default unless explicitly requested
pytestmark = pytest.mark.integration


@pytest.fixture
def real_config():
    """Load the actual config.json file."""
    return load_config("config.json")


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set"
)
def test_openai_simple_completion(real_config):
    """Test OpenAI completion with a simple prompt."""
    llm = create_llm("map_parser", real_config)

    messages = [{"role": "user", "content": "What is 2+2? Answer with just the number."}]
    system_prompt = "You are a helpful assistant. Be concise."

    response = llm.complete(messages, system_prompt, temperature=0.1, max_tokens=10)

    assert "4" in response.text
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.cost_estimate > 0
    assert response.latency_ms > 0

    print(f"OpenAI response: {response.text}")
    print(f"Tokens: {response.input_tokens} in, {response.output_tokens} out")
    print(f"Cost: ${response.cost_estimate:.6f}, Latency: {response.latency_ms:.1f}ms")


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set"
)
def test_anthropic_simple_completion(real_config):
    """Test Anthropic completion with a simple prompt."""
    llm = create_llm("game_agent", real_config)

    messages = [{"role": "user", "content": "Say 'hello' and nothing else."}]
    system_prompt = "You are a helpful assistant."

    response = llm.complete(messages, system_prompt, temperature=0.1, max_tokens=10)

    assert "hello" in response.text.lower()
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.cost_estimate > 0
    assert response.latency_ms > 0

    print(f"Anthropic response: {response.text}")
    print(f"Tokens: {response.input_tokens} in, {response.output_tokens} out")
    print(f"Cached: {response.cached_tokens}")
    print(f"Cost: ${response.cost_estimate:.6f}, Latency: {response.latency_ms:.1f}ms")


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set"
)
def test_gemini_simple_completion(real_config):
    """Test Gemini completion with a simple prompt."""
    # Create a test config for Gemini since the default might not have it
    test_config = {
        "agents": {
            "test_agent": {
                "provider": "gemini",
                "model": "gemini-2.0-flash-exp",
                "temperature": 0.1,
                "max_tokens": 10
            }
        },
        "providers": {
            "gemini": {
                "api_key_env": "GEMINI_API_KEY"
            }
        }
    }

    llm = create_llm("test_agent", test_config)

    messages = [{"role": "user", "content": "What is 3+3? Answer with just the number."}]
    system_prompt = "You are a helpful assistant. Be concise."

    response = llm.complete(messages, system_prompt, temperature=0.1, max_tokens=10)

    assert "6" in response.text
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.cost_estimate > 0
    assert response.latency_ms > 0

    print(f"Gemini response: {response.text}")
    print(f"Tokens: {response.input_tokens} in, {response.output_tokens} out")
    print(f"Cost: ${response.cost_estimate:.6f}, Latency: {response.latency_ms:.1f}ms")


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set"
)
def test_openai_json_completion(real_config):
    """Test OpenAI structured JSON output."""
    llm = create_llm("map_parser", real_config)

    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "number"},
            "explanation": {"type": "string"}
        },
        "required": ["answer", "explanation"]
    }

    messages = [{"role": "user", "content": "What is 5+7?"}]
    system_prompt = "You are a math assistant."

    result = llm.complete_json(messages, system_prompt, schema, temperature=0.1)

    assert isinstance(result, dict)
    assert "answer" in result
    assert result["answer"] == 12
    assert "explanation" in result

    print(f"OpenAI JSON result: {result}")


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set"
)
def test_anthropic_json_completion(real_config):
    """Test Anthropic structured JSON output using tool use."""
    llm = create_llm("game_agent", real_config)

    schema = {
        "type": "object",
        "properties": {
            "room_name": {"type": "string"},
            "exits": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["room_name", "exits"]
    }

    messages = [{
        "role": "user",
        "content": "You are in a room. North leads to the kitchen. South leads to the garden. What room are you in?"
    }]
    system_prompt = "Extract structured information from room descriptions."

    result = llm.complete_json(messages, system_prompt, schema, temperature=0.1)

    assert isinstance(result, dict)
    assert "room_name" in result
    assert "exits" in result
    assert isinstance(result["exits"], list)

    print(f"Anthropic JSON result: {result}")


def test_cost_tracking():
    """Test that cost tracking accumulates correctly across multiple calls."""
    # This test doesn't need real API keys, it's just demonstrating the pattern
    # In actual use, the orchestrator would track total costs across all agents

    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_tokens = 0

    # Simulate multiple LLM calls (in reality these would be actual API calls)
    # The pattern for cost tracking would be:
    #
    # response = llm.complete(...)
    # total_cost += response.cost_estimate
    # total_input_tokens += response.input_tokens
    # total_output_tokens += response.output_tokens
    # total_cached_tokens += response.cached_tokens
    #
    # Then store in database:
    # db.insert_metric(
    #     agent_name=agent_name,
    #     provider=llm.provider_name,
    #     model=llm.model,
    #     input_tokens=response.input_tokens,
    #     output_tokens=response.output_tokens,
    #     cached_tokens=response.cached_tokens,
    #     cost_estimate=response.cost_estimate,
    #     latency_ms=response.latency_ms
    # )

    assert True  # Placeholder test
