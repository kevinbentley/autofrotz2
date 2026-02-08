# Map Manager Builder - Memory

## Implementation Complete (2026-02-08)

Built map manager for AutoFrotz v2 with full test coverage. All 20 tests passing.

## Key Decisions

### Pathfinding with Blocked Edges
NetworkX's `shortest_path` weight function approach didn't work for filtering blocked edges. Used `subgraph_view` with `filter_edge` instead. This creates a filtered view that completely excludes blocked edges from pathfinding.

### Room ID Normalization
Collapse multiple spaces before converting to underscores. Sequence: lowercase -> remove articles -> collapse spaces -> spaces to underscores -> strip non-alphanumeric.

### Maze Detection
Used `difflib.SequenceMatcher` with 95% similarity threshold. Triggers when 3+ rooms have similar descriptions. Recent descriptions stored as list of tuples.

### Mock LLM for Testing
`MockLLM` class with `responses` dict for command->JSON mapping. Default response is "no room change" for unmatched commands.

## TODO for Storage Agent
- `_add_room()` needs room save method
- `_add_connection()` needs connection save method
- `load_from_db()` needs graph population from DB

## Spec Alignment
Agent definition aligned with CLAUDE.md and GAME.md. GAME.md defines `complete_json` with explicit temp/max_tokens params and `complete()` returns `LLMResponse`.
