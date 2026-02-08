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

## Key implementation learnings

### pyFrotz import path
Correct: `from pyfrotz import Frotz`, NOT `from pyfrotz.frotz import Frotz`.

### Database.save_puzzle returns int
`Database.save_puzzle(game_id, puzzle)` returns `puzzle_id` (int). Set `puzzle.puzzle_id` after.

### ItemManager.update_from_game_output extra param
Actual signature includes `current_turn: int = 0` as 4th optional parameter.

### Testing pattern for Orchestrator
Use `build_orchestrator_with_mocks()` that patches GameInterface, create_llm, MapManager, ItemManager. Real Database(":memory:") for DB assertions. Returns (orchestrator, mocks_dict).

### Files owned by core agent
main.py, orchestrator.py, game_interface.py, agents/{game,puzzle}_agent.py, hooks/{base,multimedia}.py, prompts/{game,puzzle}_agent.txt, tests/test_orchestrator.py
