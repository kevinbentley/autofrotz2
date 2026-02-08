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
