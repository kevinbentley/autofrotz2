# Item Manager Builder - Memory

## CRITICAL: Corrections from authoritative spec (CLAUDE.md / GAME.md)

The agent definition file (`.claude/agents/item-manager-builder.md`) contains an error about item categories.

### Item categories: FOUR, not five
GAME.md section 6 defines exactly four item categories:
1. **Portable items** - can be picked up (sword, lantern, leaflet)
2. **Fixed items** - part of the environment, cannot be taken (house, mailbox, altar)
3. **NPCs and creatures** - modeled as items with `"alive"` property, may move on their own (troll, thief)
4. **Consumable or transformable items** - change state during play (lantern fuel, water, food)

"Containers" is NOT a separate category in the spec. If an item can contain other items, track that through the `properties` dict (e.g., `"container": True`), but do not treat it as a fifth classification category.

All classification is tracked via the `portable` field (True/False/None) and the freeform `properties` dict. There is no separate `type` or `category` field.

## Implementation Complete

Built the following files:
1. `/home/ubuntu/workspace/autofrotz2/autofrotz/managers/item_manager.py` - Full implementation
2. `/home/ubuntu/workspace/autofrotz2/autofrotz/prompts/item_update.txt` - LLM parsing prompt
3. `/home/ubuntu/workspace/autofrotz2/tests/test_item_manager.py` - 27 comprehensive tests

All tests pass (27/27).

## Key Design Decisions

### Item ID Normalization
Implemented in `_normalize_item_id()`:
- Lowercase conversion
- Strip leading articles (the, a, an) using regex `^(the|a|an)\s+`
- Spaces to underscores
- Remove non-alphanumeric except underscores
- Collapse multiple underscores
- Strip leading/trailing underscores

### Location Values
Always one of three:
- A room_id string
- "inventory" literal
- "unknown" literal
Never None after initial creation.

### Portable Tri-State
- `None` = unknown (default for new items)
- `True` = confirmed portable (set when successfully taken)
- `False` = confirmed non-portable (set when game says "hardly portable")

### Properties Dict
Completely freeform. Common keys include:
- `lit`, `open`, `locked`, `alive`, `edible`, `readable`, `wearable`, `fuel`
LLM parser determines these from game output context.

### Droppable Items Sorting
`get_droppable_items(puzzle_items)`:
1. Returns only portable=True items in inventory
2. Sorts by: non-puzzle items first, puzzle items last
3. Uses tuple sort key: `(is_puzzle_item, item_id)`

### LLM Integration
- Loads prompt from `autofrotz/prompts/item_update.txt`
- Calls `llm.complete_json()` with structured schema
- Schema enforces ItemUpdate structure with change_type enum
- Returns empty updates array when no items mentioned (never hallucinates)

### Database Integration
- Uses `Database.save_item()` for persistence after each update
- Uses `Database.get_items()` to load state on initialization
- Supports crash recovery via `load_from_db()`

## Testing Coverage

27 test cases organized into test classes covering:
- Item ID normalization (5 tests)
- Item registration and retrieval (2 tests)
- Inventory management (3 tests)
- Room-based queries (1 test)
- Property filtering (2 tests)
- Droppable items for maze markers (3 tests)
- Inventory capacity tracking (3 tests)
- LLM parsing integration (2 tests)
- Portable tri-state handling (3 tests)
- Location "unknown" for disappeared items (1 test)
- Database persistence and recovery (1 test)
- LLM metrics tracking (1 test)

All tests use MockLLM class that extends BaseLLM.

## Dependencies Confirmed

Works with existing storage layer:
- `autofrotz.storage.models` - Item, ItemUpdate, LLMResponse, LLMMetric all present
- `autofrotz.storage.database` - Database class fully functional
- `autofrotz.llm.base` - BaseLLM interface matches expectations

## Known Issues / TODOs

1. **LLM Metrics Incomplete**: The `_last_metrics` field is populated with placeholder values. The actual LLM response metadata (token counts, cost, latency) needs to be captured from `complete_json()`. This may require extending the `BaseLLM` interface or using a side-channel mechanism.

## Integration Notes

The managers/__init__.py file is shared with the map agent. It exists but is empty. No conflicts expected.
