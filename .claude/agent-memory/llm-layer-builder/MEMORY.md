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
