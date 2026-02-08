# LLM Layer Builder - Memory

## CRITICAL: Corrections from authoritative spec (CLAUDE.md / GAME.md)

The agent definition file (`.claude/agents/llm-layer-builder.md`) contains several errors that conflict with the authoritative CLAUDE.md and GAME.md. Always follow the spec files over the agent definition when there is a conflict.

### Module structure is FLAT, not nested
CLAUDE.md specifies:
```
llm/
    __init__.py
    base.py
    openai_llm.py    # NOT providers/openai.py
    claude_llm.py    # NOT providers/anthropic.py
    gemini_llm.py    # NOT providers/google.py
    factory.py
```
Do NOT create a `providers/` subdirectory. Do NOT create `structured.py`, `exceptions.py`, `config.py`, or `cache.py` files.

### Method names and signatures (from GAME.md section 4)
```python
class BaseLLM(ABC):
    def complete(self, messages: list[dict], system_prompt: str,
                 temperature: float, max_tokens: int) -> LLMResponse: ...
    def complete_json(self, messages: list[dict], system_prompt: str,
                      schema: dict, temperature: float, max_tokens: int) -> dict: ...
    def count_tokens(self, text: str) -> int: ...
```
- The method is `complete_json`, NOT `complete_structured`
- Parameters are explicit (`messages`, `system_prompt`, `temperature`, `max_tokens`), NOT `(prompt, **kwargs)`
- `complete_json` takes `schema: dict` (not a Pydantic model) and returns `dict`

### LLMResponse fields (from GAME.md section 4)
```python
@dataclass
class LLMResponse:
    text: str              # NOT "content"
    input_tokens: int      # NOT "usage"
    output_tokens: int
    cached_tokens: int
    cost_estimate: float
    latency_ms: float
```
There is no `model`, `raw_response`, or `cached` field.

### Gemini SDK package name
- Correct: `google-genai` (per requirements.txt)
- Wrong: `google-generativeai`

### API key environment variable for Gemini
- Correct: `GEMINI_API_KEY` (per config.json spec in CLAUDE.md)
- Wrong: `GOOGLE_API_KEY`

### Caching strategy
The spec describes provider-level PROMPT caching, not response-level caching:
- **OpenAI**: Automatic prefix caching. Just structure prompts with static content first.
- **Anthropic**: Explicit `cache_control` breakpoints with `{"type": "ephemeral"}`. 25% write premium, 90% cheaper hits. 5-min TTL.
- **Gemini**: Explicit `caches.create()` API for named cached content. Configurable TTL (default 1hr).
- **Local/OpenAI-compatible**: Skip all caching logic.

Do NOT implement a `CachingLLMProvider` wrapper pattern for response caching unless specifically asked.

## Implementation Notes

### SDK Import Patterns
- **OpenAI**: `from openai import OpenAI, OpenAIError`
- **Anthropic**: `from anthropic import Anthropic, AnthropicError`
- **Gemini**: `from google import genai` and `from google.genai import types`

### Anthropic Cache Control
System prompt with caching:
```python
system=[{
    "type": "text",
    "text": system_prompt,
    "cache_control": {"type": "ephemeral"}
}]
```

### Anthropic Tool Use for JSON
Define tool with schema, force with `tool_choice`:
```python
tools=[{"name": "extract", "description": "...", "input_schema": schema}],
tool_choice={"type": "tool", "name": "extract"}
```
Extract from response: `block.input` where `block.type == "tool_use"`

### Gemini Content Format
Convert messages to Content objects:
```python
contents = [
    types.Content(
        role="user" if msg["role"] == "user" else "model",
        parts=[types.Part(text=msg["content"])]
    )
    for msg in messages
]
```

### Gemini JSON Mode
```python
config = types.GenerateContentConfig(
    system_instruction=system_prompt,
    response_mime_type="application/json",
    response_schema=schema,
    ...
)
```

### Cost Estimation Rates (2025 approximate)
- **GPT-4o**: $5/1M in, $15/1M out
- **GPT-4o-mini**: $0.15/1M in, $0.60/1M out
- **Claude Sonnet 4**: $3/1M in, $15/1M out (cache write +25%, cache read -90%)
- **Gemini 2.0 Flash**: $0.075/1M in (<128k), $0.30/1M out

### Error Handling Pattern
Always wrap provider exceptions and log them:
```python
try:
    # API call
except ProviderError as e:
    logger.error(f"Provider API error: {e}")
    raise RuntimeError(f"Provider completion failed: {e}") from e
```

### JSON Retry Pattern
Retry up to 3 times on JSON parse failure, adding error context to messages:
```python
messages.append({"role": "assistant", "content": text})
messages.append({
    "role": "user",
    "content": f"That was not valid JSON. Error: {e}. Please retry."
})
```

## Testing Strategy

All factory tests pass without requiring real API keys by:
- Using mock configurations
- Using monkeypatch for environment variables
- Testing provider instantiation without actual API calls
- Verifying correct attributes are set on provider instances

## Files Created

All files in `/home/ubuntu/workspace/autofrotz2/autofrotz/llm/`:
- `base.py` - Abstract base class
- `openai_llm.py` - OpenAI provider (344 lines)
- `claude_llm.py` - Anthropic provider (270 lines)
- `gemini_llm.py` - Gemini provider (225 lines)
- `factory.py` - Factory and config loader (137 lines)
- `__init__.py` - Module exports

Test file: `/home/ubuntu/workspace/autofrotz2/tests/test_llm_factory.py` (14 tests, all passing)
