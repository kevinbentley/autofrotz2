#!/usr/bin/env python3
"""
Example script demonstrating how to use the LLM abstraction layer.

This script shows the basic patterns for using the LLM providers.
It won't run without valid API keys, but illustrates the usage patterns.
"""

from autofrotz.llm import create_llm, load_config


def example_basic_completion():
    """Example: Basic text completion."""
    print("=" * 60)
    print("Example: Basic Text Completion")
    print("=" * 60)

    # Load configuration
    config = load_config("config.json")

    # Create an LLM instance for the game agent
    llm = create_llm("game_agent", config)

    # Build a conversation
    messages = [
        {"role": "user", "content": "You see a brass lantern here. What should you do?"}
    ]

    system_prompt = (
        "You are an expert text adventure game player. "
        "Analyze the situation and suggest a single command."
    )

    # Make the API call
    response = llm.complete(
        messages=messages,
        system_prompt=system_prompt,
        temperature=0.7,
        max_tokens=100
    )

    # Use the response
    print(f"Agent's response: {response.text}")
    print(f"Tokens used: {response.input_tokens} in, {response.output_tokens} out")
    print(f"Cached tokens: {response.cached_tokens}")
    print(f"Cost: ${response.cost_estimate:.6f}")
    print(f"Latency: {response.latency_ms:.1f}ms")
    print()


def example_structured_json():
    """Example: Structured JSON output for parsing game text."""
    print("=" * 60)
    print("Example: Structured JSON Output")
    print("=" * 60)

    config = load_config("config.json")

    # Use a smaller model for parsing tasks
    llm = create_llm("map_parser", config)

    # Define the expected structure
    schema = {
        "type": "object",
        "properties": {
            "room_changed": {"type": "boolean"},
            "room_name": {"type": "string"},
            "exits": {
                "type": "array",
                "items": {"type": "string"}
            },
            "items_seen": {
                "type": "array",
                "items": {"type": "string"}
            },
            "is_dark": {"type": "boolean"}
        },
        "required": ["room_changed", "room_name", "exits", "items_seen", "is_dark"]
    }

    game_output = """
    West of House
    You are standing in an open field west of a white house, with a boarded
    front door. There is a small mailbox here.
    Obvious exits: north, south, east
    """

    messages = [
        {"role": "user", "content": f"Parse this game output:\n\n{game_output}"}
    ]

    system_prompt = "Extract structured information from text adventure game output."

    # Make the API call with JSON output
    result = llm.complete_json(
        messages=messages,
        system_prompt=system_prompt,
        schema=schema,
        temperature=0.1,
        max_tokens=200
    )

    # Use the structured result
    print(f"Parsed data: {result}")
    print(f"Room name: {result['room_name']}")
    print(f"Exits: {', '.join(result['exits'])}")
    print(f"Items: {', '.join(result['items_seen']) if result['items_seen'] else 'none'}")
    print()


def example_multi_turn_conversation():
    """Example: Multi-turn conversation with context."""
    print("=" * 60)
    print("Example: Multi-turn Conversation")
    print("=" * 60)

    config = load_config("config.json")
    llm = create_llm("puzzle_agent", config)

    # Build conversation history
    messages = [
        {
            "role": "user",
            "content": "I found a locked door that says 'The key is rusty and old'."
        },
        {
            "role": "assistant",
            "content": "This is a locked door puzzle. Look for a rusty old key nearby."
        },
        {
            "role": "user",
            "content": "I found a brass key in the mailbox. Is this the right key?"
        }
    ]

    system_prompt = "You are a puzzle solving assistant for text adventure games."

    response = llm.complete(
        messages=messages,
        system_prompt=system_prompt,
        temperature=0.5,
        max_tokens=150
    )

    print(f"Puzzle agent's response: {response.text}")
    print()


def example_switching_providers():
    """Example: Using different providers for different tasks."""
    print("=" * 60)
    print("Example: Switching Between Providers")
    print("=" * 60)

    config = load_config("config.json")

    # Use Claude for main gameplay (better reasoning)
    game_llm = create_llm("game_agent", config)
    print(f"Game agent: {game_llm.provider_name} ({game_llm.model})")

    # Use GPT-4o-mini for parsing (cheaper, faster)
    map_llm = create_llm("map_parser", config)
    print(f"Map parser: {map_llm.provider_name} ({map_llm.model})")

    # Use GPT-4o-mini for item tracking (cheaper, faster)
    item_llm = create_llm("item_parser", config)
    print(f"Item parser: {item_llm.provider_name} ({item_llm.model})")

    # Use GPT-4o for puzzle solving (good balance)
    puzzle_llm = create_llm("puzzle_agent", config)
    print(f"Puzzle agent: {puzzle_llm.provider_name} ({puzzle_llm.model})")

    print("\nThis allows you to optimize for cost and performance per task.")
    print()


def example_error_handling():
    """Example: Proper error handling."""
    print("=" * 60)
    print("Example: Error Handling")
    print("=" * 60)

    config = load_config("config.json")
    llm = create_llm("game_agent", config)

    messages = [{"role": "user", "content": "Test message"}]
    system_prompt = "Test system prompt"

    try:
        response = llm.complete(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=100
        )
        print(f"Success: {response.text[:50]}...")

    except RuntimeError as e:
        # All provider exceptions are wrapped in RuntimeError
        print(f"LLM call failed: {e}")
        # Log the error, retry with backoff, or fall back to a different provider

    except Exception as e:
        print(f"Unexpected error: {e}")

    print()


def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("LLM Abstraction Layer Usage Examples")
    print("=" * 60 + "\n")

    print("NOTE: These examples require valid API keys to actually run.")
    print("They are provided to illustrate the usage patterns.\n")

    # Uncomment to run examples (requires API keys):
    # example_basic_completion()
    # example_structured_json()
    # example_multi_turn_conversation()
    example_switching_providers()
    # example_error_handling()

    print("\nExamples complete!")


if __name__ == "__main__":
    main()
