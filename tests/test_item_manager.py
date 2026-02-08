"""
Unit tests for ItemManager.

Tests item registration, location tracking, property updates, and LLM parsing.
"""

import json
from unittest.mock import MagicMock

import pytest

from autofrotz.llm.base import BaseLLM
from autofrotz.managers.item_manager import ItemManager
from autofrotz.storage.database import Database
from autofrotz.storage.models import Item, LLMResponse


class MockLLM(BaseLLM):
    """Mock LLM for testing."""

    def __init__(self, model: str = "mock-model", api_key: str = "mock-key", **kwargs):
        super().__init__(model, api_key, **kwargs)
        self.provider_name = "mock"
        self.next_json_response = {"updates": []}

    def complete(self, messages, system_prompt, temperature=0.7, max_tokens=1024):
        """Mock completion."""
        return LLMResponse(
            text="mock response",
            input_tokens=10,
            output_tokens=10,
            cached_tokens=0,
            cost_estimate=0.001,
            latency_ms=100.0
        )

    def complete_json(self, messages, system_prompt, schema, temperature=0.1, max_tokens=512):
        """Mock JSON completion."""
        return self.next_json_response

    def count_tokens(self, text):
        """Mock token counting."""
        return len(text.split())


@pytest.fixture
def db():
    """In-memory database for testing."""
    database = Database(":memory:")
    game_id = database.create_game("test_game.z5")
    database.game_id = game_id
    yield database
    database.close()


@pytest.fixture
def mock_llm():
    """Mock LLM instance."""
    return MockLLM()


@pytest.fixture
def item_manager(mock_llm, db):
    """ItemManager instance for testing."""
    return ItemManager(mock_llm, db, db.game_id)


class TestItemIDNormalization:
    """Test item ID normalization."""

    def test_lowercase(self, item_manager):
        """Item IDs should be lowercase."""
        assert item_manager._normalize_item_id("Brass Lantern") == "brass_lantern"

    def test_strip_articles(self, item_manager):
        """Leading articles should be stripped."""
        assert item_manager._normalize_item_id("The brass lantern") == "brass_lantern"
        assert item_manager._normalize_item_id("A wooden door") == "wooden_door"
        assert item_manager._normalize_item_id("An old key") == "old_key"

    def test_spaces_to_underscores(self, item_manager):
        """Spaces should become underscores."""
        assert item_manager._normalize_item_id("white house") == "white_house"

    def test_remove_punctuation(self, item_manager):
        """Non-alphanumeric characters (except underscores) should be removed."""
        assert item_manager._normalize_item_id("key (rusty)") == "key_rusty"
        assert item_manager._normalize_item_id("sword!") == "sword"

    def test_collapse_underscores(self, item_manager):
        """Multiple underscores should be collapsed."""
        assert item_manager._normalize_item_id("the    brass    lantern") == "brass_lantern"


class TestItemRegistration:
    """Test item registration and retrieval."""

    def test_add_item_and_retrieve_by_id(self, item_manager, db):
        """Items should be retrievable by ID."""
        item = Item(
            item_id="brass_lantern",
            name="brass lantern",
            location="west_of_house",
            portable=True
        )
        item_manager._items["brass_lantern"] = item
        db.save_item(db.game_id, item)

        retrieved = item_manager.get_item("brass_lantern")
        assert retrieved is not None
        assert retrieved.name == "brass lantern"
        assert retrieved.location == "west_of_house"

    def test_get_all_items(self, item_manager, db):
        """get_all_items should return all registered items."""
        item1 = Item(item_id="lamp", name="lamp", location="inventory")
        item2 = Item(item_id="sword", name="sword", location="armory")

        item_manager._items["lamp"] = item1
        item_manager._items["sword"] = item2

        all_items = item_manager.get_all_items()
        assert len(all_items) == 2
        assert any(item.item_id == "lamp" for item in all_items)
        assert any(item.item_id == "sword" for item in all_items)


class TestInventoryManagement:
    """Test inventory operations."""

    def test_take_item_moves_to_inventory(self, item_manager, db):
        """take_item should move an item to inventory."""
        item = Item(item_id="lamp", name="lamp", location="table")
        item_manager._items["lamp"] = item

        item_manager.take_item("lamp")

        assert item.location == "inventory"
        assert item.portable is True

    def test_drop_item_moves_to_room(self, item_manager, db):
        """drop_item should move an item from inventory to a room."""
        item = Item(item_id="lamp", name="lamp", location="inventory", portable=True)
        item_manager._items["lamp"] = item

        item_manager.drop_item("lamp", "west_of_house")

        assert item.location == "west_of_house"

    def test_get_inventory_returns_only_inventory_items(self, item_manager, db):
        """get_inventory should return only items in inventory."""
        item1 = Item(item_id="lamp", name="lamp", location="inventory")
        item2 = Item(item_id="sword", name="sword", location="armory")
        item3 = Item(item_id="key", name="key", location="inventory")

        item_manager._items["lamp"] = item1
        item_manager._items["sword"] = item2
        item_manager._items["key"] = item3

        inventory = item_manager.get_inventory()
        assert len(inventory) == 2
        assert all(item.location == "inventory" for item in inventory)


class TestRoomItems:
    """Test room-based item queries."""

    def test_get_items_in_room_returns_correct_subset(self, item_manager, db):
        """get_items_in_room should return items in the specified room."""
        item1 = Item(item_id="lamp", name="lamp", location="west_of_house")
        item2 = Item(item_id="sword", name="sword", location="armory")
        item3 = Item(item_id="mailbox", name="mailbox", location="west_of_house")

        item_manager._items["lamp"] = item1
        item_manager._items["sword"] = item2
        item_manager._items["mailbox"] = item3

        room_items = item_manager.get_items_in_room("west_of_house")
        assert len(room_items) == 2
        assert all(item.location == "west_of_house" for item in room_items)


class TestPropertySearch:
    """Test property-based item filtering."""

    def test_find_items_by_property_filtering(self, item_manager, db):
        """find_items_by_property should filter correctly."""
        item1 = Item(item_id="lamp", name="lamp", properties={"lit": True})
        item2 = Item(item_id="door", name="door", properties={"locked": True})
        item3 = Item(item_id="chest", name="chest", properties={"locked": True, "open": False})

        item_manager._items["lamp"] = item1
        item_manager._items["door"] = item2
        item_manager._items["chest"] = item3

        locked_items = item_manager.find_items_by_property("locked", True)
        assert len(locked_items) == 2
        assert all(item.properties.get("locked") is True for item in locked_items)

    def test_find_items_by_nonexistent_property(self, item_manager, db):
        """Finding items by nonexistent property should return empty list."""
        item = Item(item_id="lamp", name="lamp", properties={"lit": True})
        item_manager._items["lamp"] = item

        result = item_manager.find_items_by_property("magical", True)
        assert len(result) == 0


class TestDroppableItems:
    """Test droppable item selection for maze markers."""

    def test_get_droppable_items_excludes_non_portable(self, item_manager, db):
        """get_droppable_items should only return portable inventory items."""
        item1 = Item(item_id="lamp", name="lamp", location="inventory", portable=True)
        item2 = Item(item_id="house", name="house", location="west_of_house", portable=False)
        item3 = Item(item_id="key", name="key", location="inventory", portable=None)

        item_manager._items["lamp"] = item1
        item_manager._items["house"] = item2
        item_manager._items["key"] = item3

        droppable = item_manager.get_droppable_items()
        assert len(droppable) == 1
        assert droppable[0].item_id == "lamp"

    def test_get_droppable_items_sorts_puzzle_items_last(self, item_manager, db):
        """Puzzle-related items should be sorted last."""
        item1 = Item(item_id="leaflet", name="leaflet", location="inventory", portable=True)
        item2 = Item(item_id="sword", name="sword", location="inventory", portable=True)
        item3 = Item(item_id="key", name="key", location="inventory", portable=True)

        item_manager._items["leaflet"] = item1
        item_manager._items["sword"] = item2
        item_manager._items["key"] = item3

        # Mark sword and key as puzzle-related
        droppable = item_manager.get_droppable_items(puzzle_items=["sword", "key"])

        assert len(droppable) == 3
        # leaflet should come first (not in puzzle list)
        assert droppable[0].item_id == "leaflet"
        # sword and key should come after
        puzzle_item_ids = {droppable[1].item_id, droppable[2].item_id}
        assert puzzle_item_ids == {"sword", "key"}

    def test_get_droppable_items_with_exclusion_list(self, item_manager, db):
        """Exclusion list should deprioritize specific items."""
        item1 = Item(item_id="garlic", name="garlic", location="inventory", portable=True)
        item2 = Item(item_id="sword", name="sword", location="inventory", portable=True)

        item_manager._items["garlic"] = item1
        item_manager._items["sword"] = item2

        droppable = item_manager.get_droppable_items(puzzle_items=["sword"])

        # garlic should come before sword
        assert droppable[0].item_id == "garlic"
        assert droppable[1].item_id == "sword"


class TestInventoryLimits:
    """Test inventory capacity tracking."""

    def test_inventory_limit_detection(self, item_manager, db):
        """Inventory limit should be settable and queryable."""
        item_manager.set_inventory_limit(5)

        assert item_manager._inventory_limit == 5

    def test_get_inventory_count(self, item_manager, db):
        """get_inventory_count should return correct count."""
        item1 = Item(item_id="lamp", name="lamp", location="inventory")
        item2 = Item(item_id="sword", name="sword", location="inventory")
        item3 = Item(item_id="key", name="key", location="armory")

        item_manager._items["lamp"] = item1
        item_manager._items["sword"] = item2
        item_manager._items["key"] = item3

        assert item_manager.get_inventory_count() == 2

    def test_is_inventory_full(self, item_manager, db):
        """is_inventory_full should work correctly."""
        item1 = Item(item_id="lamp", name="lamp", location="inventory")
        item2 = Item(item_id="sword", name="sword", location="inventory")

        item_manager._items["lamp"] = item1
        item_manager._items["sword"] = item2

        # No limit set
        assert item_manager.is_inventory_full() is False

        # Set limit to 2
        item_manager.set_inventory_limit(2)
        assert item_manager.is_inventory_full() is True

        # Set limit to 3
        item_manager.set_inventory_limit(3)
        assert item_manager.is_inventory_full() is False


class TestLLMParsing:
    """Test LLM-based game output parsing."""

    def test_update_from_game_output_with_mock_llm(self, item_manager, mock_llm, db):
        """update_from_game_output should parse with LLM."""
        # Configure mock LLM response
        mock_llm.next_json_response = {
            "updates": [
                {
                    "item_id": "brass_lantern",
                    "name": "brass lantern",
                    "change_type": "new",
                    "location": "west_of_house",
                    "properties": None
                }
            ]
        }

        updates = item_manager.update_from_game_output(
            output_text="You see a brass lantern here.",
            current_room="west_of_house",
            command_used="look",
            current_turn=1
        )

        assert len(updates) == 1
        assert updates[0].item_id == "brass_lantern"
        assert updates[0].change_type == "new"

        # Item should be registered
        item = item_manager.get_item("brass_lantern")
        assert item is not None
        assert item.location == "west_of_house"

    def test_parsing_taken_item(self, item_manager, mock_llm, db):
        """Parsing a 'taken' update should set portable=True."""
        mock_llm.next_json_response = {
            "updates": [
                {
                    "item_id": "lamp",
                    "name": "lamp",
                    "change_type": "taken",
                    "location": "inventory",
                    "properties": None
                }
            ]
        }

        # Pre-register the item
        item = Item(item_id="lamp", name="lamp", location="table", portable=None)
        item_manager._items["lamp"] = item

        updates = item_manager.update_from_game_output(
            output_text="Taken.",
            current_room="kitchen",
            command_used="take lamp",
            current_turn=5
        )

        assert len(updates) == 1
        assert item.location == "inventory"
        assert item.portable is True


class TestPortableTriState:
    """Test portable field tri-state handling."""

    def test_portable_starts_none(self, item_manager, db):
        """New items should have portable=None until determined."""
        item = Item(item_id="lamp", name="lamp", location="table")
        item_manager._items["lamp"] = item

        assert item.portable is None

    def test_portable_set_true_on_take(self, item_manager, db):
        """Taking an item should set portable=True."""
        item = Item(item_id="lamp", name="lamp", location="table", portable=None)
        item_manager._items["lamp"] = item

        item_manager.take_item("lamp")

        assert item.portable is True

    def test_portable_can_be_false(self, item_manager, db):
        """Items can be explicitly non-portable."""
        item = Item(item_id="house", name="house", location="west_of_house", portable=False)
        item_manager._items["house"] = item

        assert item.portable is False


class TestLocationUnknown:
    """Test location set to 'unknown' for disappeared items."""

    def test_disappeared_item_location_unknown(self, item_manager, mock_llm, db):
        """Items marked as 'gone' should have location='unknown'."""
        # Pre-register item
        item = Item(item_id="garlic", name="garlic", location="inventory")
        item_manager._items["garlic"] = item

        # Mock LLM response for consumed item
        mock_llm.next_json_response = {
            "updates": [
                {
                    "item_id": "garlic",
                    "name": "garlic",
                    "change_type": "gone",
                    "location": "unknown",
                    "properties": None
                }
            ]
        }

        updates = item_manager.update_from_game_output(
            output_text="You eat the garlic. It's gone.",
            current_room="kitchen",
            command_used="eat garlic",
            current_turn=10
        )

        assert item.location == "unknown"


class TestDatabasePersistence:
    """Test database loading and persistence."""

    def test_load_from_db_restores_state(self, mock_llm, db):
        """load_from_db should restore all items."""
        # Save items directly to DB
        item1 = Item(item_id="lamp", name="lamp", location="inventory", portable=True)
        item2 = Item(item_id="sword", name="sword", location="armory", portable=False)

        db.save_item(db.game_id, item1)
        db.save_item(db.game_id, item2)

        # Create new ItemManager instance (should load from DB)
        manager = ItemManager(mock_llm, db, db.game_id)

        assert len(manager._items) == 2
        assert manager.get_item("lamp") is not None
        assert manager.get_item("sword") is not None
        assert manager.get_item("lamp").location == "inventory"


class TestMetrics:
    """Test LLM metrics tracking."""

    def test_get_last_metrics(self, item_manager, mock_llm, db):
        """get_last_metrics should return metrics from last LLM call."""
        mock_llm.next_json_response = {"updates": []}

        item_manager.update_from_game_output(
            output_text="Nothing here.",
            current_room="empty_room",
            command_used="look",
            current_turn=1
        )

        metrics = item_manager.get_last_metrics()
        assert metrics is not None
        assert metrics.agent_name == "item_parser"
