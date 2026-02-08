# AutoFrotz v2 Architect - Memory

## CRITICAL: Corrections from authoritative spec (CLAUDE.md / GAME.md)

### MapManager interface: missing method
The agent definition's MapManager interface listing is missing a method that CLAUDE.md specifies:
```python
def get_maze_rooms(self, group_id: str) -> list[str]:
    """Returns all room IDs in a maze group."""
```
This method is needed during maze resolution (GAME.md section 13) and marker retrieval (Phase 4).

When calling MapManager, include `get_maze_rooms` in mocks and interface expectations.

### Minor CLAUDE.md vs GAME.md discrepancy
- CLAUDE.md describes `complete()` as returning `str`, but GAME.md section 4 defines it as returning `LLMResponse` (a dataclass with text, token counts, cost, latency). GAME.md's code definition is more authoritative for the interface.
- CLAUDE.md's `complete_json` signature omits `temperature` and `max_tokens` params. GAME.md includes them. Use the GAME.md version:
  ```python
  def complete_json(self, messages, system_prompt, schema, temperature, max_tokens) -> dict
  ```
