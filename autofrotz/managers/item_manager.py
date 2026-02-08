"""
Item Manager for AutoFrotz v2.

Maintains a registry of all known items as a dictionary keyed by normalized item ID.
Uses LLM parsing to extract item changes from game output.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from autofrotz.llm.base import BaseLLM
from autofrotz.storage.database import Database
from autofrotz.storage.models import Item, ItemUpdate, LLMMetric

logger = logging.getLogger(__name__)


class ItemManager:
    """
    Manages the item registry for a game session.

    Tracks all items, their locations, properties, and state changes.
    Uses LLM parsing to extract structured item updates from natural language game output.
    """

    def __init__(self, llm: BaseLLM, database: Database, game_id: int):
        """
        Initialize the item manager.

        Args:
            llm: LLM instance for parsing game output
            database: Database instance for persistence
            game_id: Game session ID
        """
        self.llm = llm
        self.database = database
        self.game_id = game_id

        # Item registry keyed by item_id
        self._items: dict[str, Item] = {}

        # Inventory capacity (discovered empirically)
        self._inventory_limit: int | None = None

        # Last LLM metrics for orchestrator access
        self._last_metrics: LLMMetric | None = None

        # Load prompt template
        prompt_path = Path(__file__).parent.parent / "prompts" / "item_update.txt"
        if prompt_path.exists():
            self._prompt_template = prompt_path.read_text()
        else:
            logger.warning(f"Item update prompt not found at {prompt_path}")
            self._prompt_template = self._get_default_prompt()

        # Load existing items from database
        self.load_from_db()

        logger.info(f"ItemManager initialized for game {game_id} with {len(self._items)} items")

    def _get_default_prompt(self) -> str:
        """Fallback prompt if file not found."""
        return """You are a text adventure item parser. Extract item changes from game output.

Return a JSON array of ItemUpdate objects with these fields:
- item_id: normalized item identifier (lowercase, underscores, no articles)
- name: display name as seen in game
- change_type: one of "new", "taken", "dropped", "state_change", "moved", "gone"
- location: room_id, "inventory", "unknown", or null
- properties: dict of item attributes (lit, open, locked, alive, edible, etc.) or null

Only report items explicitly mentioned. Return [] if no items found."""

    def _normalize_item_id(self, name: str) -> str:
        """
        Normalize an item name to a consistent item_id.

        Rules:
        - Lowercase
        - Spaces to underscores
        - Strip articles (the, a, an)
        - Remove non-alphanumeric except underscores

        Args:
            name: Display name of the item

        Returns:
            Normalized item_id
        """
        # Convert to lowercase
        normalized = name.lower()

        # Strip leading articles
        normalized = re.sub(r'^(the|a|an)\s+', '', normalized)

        # Replace spaces with underscores
        normalized = normalized.replace(' ', '_')

        # Remove non-alphanumeric except underscores
        normalized = re.sub(r'[^a-z0-9_]', '', normalized)

        # Collapse multiple underscores
        normalized = re.sub(r'_+', '_', normalized)

        # Strip leading/trailing underscores
        normalized = normalized.strip('_')

        return normalized

    def update_from_game_output(
        self,
        output_text: str,
        current_room: str,
        command_used: str,
        current_turn: int = 0
    ) -> list[ItemUpdate]:
        """
        Parse game output for item changes using LLM.

        Args:
            output_text: Raw game output text
            current_room: Current room_id
            command_used: Command that produced this output
            current_turn: Current turn number

        Returns:
            List of ItemUpdate objects describing changes
        """
        # Build the LLM prompt
        messages = [
            {
                "role": "user",
                "content": f"""Game output:
{output_text}

Current room: {current_room}
Command used: {command_used}

Extract all item changes from this output."""
            }
        ]

        # Define the JSON schema for structured output
        schema = {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id": {"type": "string"},
                            "name": {"type": "string"},
                            "change_type": {
                                "type": "string",
                                "enum": ["new", "taken", "dropped", "state_change", "moved", "gone"]
                            },
                            "location": {"type": ["string", "null"]},
                            "properties": {"type": ["object", "null"]}
                        },
                        "required": ["item_id", "name", "change_type"]
                    }
                }
            },
            "required": ["updates"]
        }

        try:
            # Call LLM for structured parsing
            result = self.llm.complete_json(
                messages=messages,
                system_prompt=self._prompt_template,
                schema=schema,
                temperature=0.1,
                max_tokens=512
            )

            # Store metrics for orchestrator access
            # Note: complete_json should populate this via a side channel
            # For now, we'll create a placeholder metric
            self._last_metrics = LLMMetric(
                game_id=self.game_id,
                turn_number=current_turn,
                agent_name="item_parser",
                provider=getattr(self.llm, 'provider_name', 'unknown'),
                model=self.llm.model,
                input_tokens=0,  # TODO: get from LLM response
                output_tokens=0,
                cached_tokens=0,
                cost_estimate=0.0,
                latency_ms=0.0
            )

            # Process the updates
            updates = []
            for update_data in result.get("updates", []):
                # Normalize the item_id
                normalized_id = self._normalize_item_id(update_data["item_id"])

                update = ItemUpdate(
                    item_id=normalized_id,
                    name=update_data["name"],
                    change_type=update_data["change_type"],
                    location=update_data.get("location"),
                    properties=update_data.get("properties")
                )

                # Apply the update to the registry
                self._apply_update(update, current_room, current_turn)
                updates.append(update)

            logger.debug(f"Parsed {len(updates)} item updates from game output")
            return updates

        except Exception as e:
            logger.error(f"Failed to parse item updates: {e}")
            return []

    def _apply_update(self, update: ItemUpdate, current_room: str, current_turn: int):
        """
        Apply an ItemUpdate to the registry.

        Args:
            update: ItemUpdate to apply
            current_room: Current room_id for context
            current_turn: Current turn number
        """
        item_id = update.item_id

        # Get existing item or create new one
        if item_id in self._items:
            item = self._items[item_id]
        else:
            item = Item(
                item_id=item_id,
                name=update.name,
                location="unknown",
                portable=None,
                properties={},
                first_seen_turn=current_turn,
                last_seen_turn=current_turn
            )
            self._items[item_id] = item
            logger.info(f"Registered new item: {update.name} ({item_id})")

        # Update last seen turn
        item.last_seen_turn = current_turn

        # Apply changes based on change_type
        if update.change_type == "new":
            # New item discovered
            if update.location:
                item.location = update.location
            elif item.location == "unknown":
                item.location = current_room

        elif update.change_type == "taken":
            # Item taken into inventory
            item.location = "inventory"
            item.portable = True  # Confirmed portable

        elif update.change_type == "dropped":
            # Item dropped in current room
            item.location = update.location or current_room

        elif update.change_type == "state_change":
            # Item properties changed
            if update.properties:
                item.properties.update(update.properties)

        elif update.change_type == "moved":
            # Item moved to a new location
            if update.location:
                item.location = update.location

        elif update.change_type == "gone":
            # Item disappeared (stolen, consumed, destroyed)
            item.location = "unknown"

        # Update properties if provided
        if update.properties:
            item.properties.update(update.properties)

        # Update name if it changed
        if update.name and update.name != item.name:
            item.name = update.name

        # Persist to database
        self.database.save_item(self.game_id, item)

    def take_item(self, item_id: str):
        """
        Move an item to inventory.

        Args:
            item_id: Item to take
        """
        if item_id in self._items:
            item = self._items[item_id]
            item.location = "inventory"
            item.portable = True
            self.database.save_item(self.game_id, item)
            logger.debug(f"Took item: {item.name}")
        else:
            logger.warning(f"Attempted to take unknown item: {item_id}")

    def drop_item(self, item_id: str, room_id: str):
        """
        Move an item from inventory to a room.

        Args:
            item_id: Item to drop
            room_id: Room to drop it in
        """
        if item_id in self._items:
            item = self._items[item_id]
            item.location = room_id
            self.database.save_item(self.game_id, item)
            logger.debug(f"Dropped item {item.name} in {room_id}")
        else:
            logger.warning(f"Attempted to drop unknown item: {item_id}")

    def get_item(self, item_id: str) -> Item | None:
        """
        Retrieve a specific item by ID.

        Args:
            item_id: Item to retrieve

        Returns:
            Item if found, None otherwise
        """
        return self._items.get(item_id)

    def get_inventory(self) -> list[Item]:
        """
        Get all items currently in inventory.

        Returns:
            List of items with location == "inventory"
        """
        return [item for item in self._items.values() if item.location == "inventory"]

    def get_items_in_room(self, room_id: str) -> list[Item]:
        """
        Get all items in a specific room.

        Args:
            room_id: Room to check

        Returns:
            List of items in that room
        """
        return [item for item in self._items.values() if item.location == room_id]

    def get_all_items(self) -> list[Item]:
        """
        Get all known items.

        Returns:
            List of all items in the registry
        """
        return list(self._items.values())

    def find_items_by_property(self, key: str, value: Any) -> list[Item]:
        """
        Find items with a specific property value.

        Args:
            key: Property key to search for
            value: Property value to match

        Returns:
            List of items where item.properties[key] == value
        """
        return [
            item for item in self._items.values()
            if item.properties.get(key) == value
        ]

    def get_droppable_items(self, puzzle_items: list[str] | None = None) -> list[Item]:
        """
        Get portable inventory items suitable for dropping (e.g., as maze markers).

        Items are sorted by safety:
        1. Items NOT in puzzle_items list (safe to drop)
        2. Items in puzzle_items list (potentially quest-critical)

        Args:
            puzzle_items: Optional list of item_ids to deprioritize (quest items)

        Returns:
            List of portable inventory items sorted by drop safety
        """
        puzzle_items = puzzle_items or []

        # Get portable items in inventory
        droppable = [
            item for item in self._items.values()
            if item.location == "inventory" and item.portable is True
        ]

        # Sort: non-puzzle items first, puzzle items last
        def sort_key(item: Item) -> tuple[int, str]:
            is_puzzle_item = 1 if item.item_id in puzzle_items else 0
            return (is_puzzle_item, item.item_id)

        droppable.sort(key=sort_key)

        return droppable

    def set_inventory_limit(self, limit: int):
        """
        Set the discovered inventory carry limit.

        Args:
            limit: Maximum number of items that can be carried
        """
        self._inventory_limit = limit
        logger.info(f"Inventory limit set to {limit}")

    def get_inventory_count(self) -> int:
        """
        Get the current number of items in inventory.

        Returns:
            Count of inventory items
        """
        return len(self.get_inventory())

    def is_inventory_full(self) -> bool:
        """
        Check if inventory is at capacity.

        Returns:
            True if at or above limit, False if under limit or limit unknown
        """
        if self._inventory_limit is None:
            return False
        return self.get_inventory_count() >= self._inventory_limit

    def load_from_db(self):
        """Load all items from the database for this game."""
        items = self.database.get_items(self.game_id)
        for item in items:
            self._items[item.item_id] = item
        logger.debug(f"Loaded {len(items)} items from database")

    def get_last_metrics(self) -> LLMMetric | None:
        """
        Get metrics from the last LLM call.

        Returns:
            LLMMetric if available, None otherwise
        """
        return self._last_metrics
