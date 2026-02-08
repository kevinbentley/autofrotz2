"""
Unit tests for MapManager.

Tests map building, pathfinding, exploration tracking, blocked paths,
maze detection, and serialization with a mock LLM.
"""

import pytest
from unittest.mock import MagicMock

from autofrotz.llm.base import BaseLLM
from autofrotz.managers.map_manager import MapManager
from autofrotz.storage.database import Database
from autofrotz.storage.models import Room, LLMResponse


class MockLLM(BaseLLM):
    """Mock LLM for testing without real API calls."""

    provider_name = "mock"

    def __init__(self, model: str = "mock-model", api_key: str = "mock-key", **kwargs):
        super().__init__(model, api_key, **kwargs)
        self.responses = {}  # command -> json response mapping

    def complete(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> LLMResponse:
        """Mock completion."""
        return LLMResponse(
            text="Mock response",
            input_tokens=10,
            output_tokens=20,
            cached_tokens=0,
            cost_estimate=0.001,
            latency_ms=100.0,
        )

    def complete_json(
        self,
        messages: list[dict],
        system_prompt: str,
        schema: dict,
        temperature: float = 0.1,
        max_tokens: int = 512
    ) -> dict:
        """Mock JSON completion with predefined responses."""
        # Extract command from messages
        user_message = messages[0]['content']

        # Check if we have a preset response for this command
        for key, response in self.responses.items():
            if key in user_message:
                return response

        # Default: no room change
        return {
            "room_changed": False,
            "room_name": None,
            "description": None,
            "exits": [],
            "is_dark": False,
            "items_seen": [],
        }

    def count_tokens(self, text: str) -> int:
        """Mock token counting."""
        return len(text.split())


class MockDatabase(Database):
    """Mock database for testing."""

    def __init__(self, db_path: str = ":memory:"):
        # Don't call super().__init__() to avoid creating real database
        self.db_path = db_path


@pytest.fixture
def mock_llm():
    """Create a mock LLM instance."""
    return MockLLM()


@pytest.fixture
def mock_db():
    """Create a mock database instance."""
    return MockDatabase()


@pytest.fixture
def map_manager(mock_llm, mock_db):
    """Create a MapManager instance with mocks."""
    return MapManager(llm=mock_llm, database=mock_db, game_id=1)


def test_add_room(map_manager):
    """Test adding a room to the graph."""
    room = Room(
        room_id="test_room",
        name="Test Room",
        description="A test room",
        visited=True,
        visit_count=1,
    )

    map_manager._add_room(room)

    assert "test_room" in map_manager.graph.nodes
    assert map_manager.graph.nodes["test_room"]["name"] == "Test Room"
    assert map_manager.graph.nodes["test_room"]["description"] == "A test room"


def test_add_bidirectional_connection(map_manager):
    """Test adding a bidirectional connection."""
    # Add two rooms
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))

    # Add bidirectional connection
    map_manager._add_connection("room_a", "room_b", "north", bidirectional=True)

    # Check forward edge
    assert map_manager.graph.has_edge("room_a", "room_b")
    assert map_manager.graph.edges["room_a", "room_b"]["direction"] == "north"

    # Check reverse edge
    assert map_manager.graph.has_edge("room_b", "room_a")
    assert map_manager.graph.edges["room_b", "room_a"]["direction"] == "south"


def test_add_unidirectional_connection(map_manager):
    """Test adding a unidirectional connection."""
    # Add two rooms
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))

    # Add unidirectional connection
    map_manager._add_connection("room_a", "room_b", "down", bidirectional=False)

    # Check forward edge exists
    assert map_manager.graph.has_edge("room_a", "room_b")
    assert map_manager.graph.edges["room_a", "room_b"]["direction"] == "down"

    # Check reverse edge does not exist
    assert not map_manager.graph.has_edge("room_b", "room_a")


def test_pathfinding_connected_rooms(map_manager):
    """Test pathfinding between connected rooms."""
    # Create a simple 3-room path: A -> B -> C
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))
    map_manager._add_room(Room(room_id="room_c", name="Room C"))

    map_manager._add_connection("room_a", "room_b", "east", bidirectional=True)
    map_manager._add_connection("room_b", "room_c", "north", bidirectional=True)

    # Test path from A to C
    path = map_manager.get_path("room_a", "room_c")
    assert path == ["east", "north"]

    # Test path from C to A
    path = map_manager.get_path("room_c", "room_a")
    assert path == ["south", "west"]


def test_pathfinding_no_path(map_manager):
    """Test pathfinding when no path exists."""
    # Create two disconnected rooms
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))

    # No connection between them
    path = map_manager.get_path("room_a", "room_b")
    assert path == []


def test_unexplored_exits_tracking(map_manager):
    """Test tracking of unexplored exits."""
    # Add room with exits
    room = Room(
        room_id="test_room",
        name="Test Room",
        exits={"north": None, "south": None, "east": "room_b"}
    )
    map_manager._add_room(room)

    # Get unexplored exits for this room
    unexplored = map_manager.get_unexplored_exits("test_room")

    # Should have two unexplored (north, south) but not east
    assert len(unexplored) == 2
    assert ("test_room", "north") in unexplored
    assert ("test_room", "south") in unexplored


def test_get_all_unexplored_exits(map_manager):
    """Test getting all unexplored exits across the map."""
    # Add multiple rooms with unexplored exits
    room_a = Room(room_id="room_a", name="Room A", exits={"north": None})
    room_b = Room(room_id="room_b", name="Room B", exits={"south": None, "east": None})

    map_manager._add_room(room_a)
    map_manager._add_room(room_b)

    # Get all unexplored
    unexplored = map_manager.get_unexplored_exits()

    assert len(unexplored) == 3
    assert ("room_a", "north") in unexplored
    assert ("room_b", "south") in unexplored
    assert ("room_b", "east") in unexplored


def test_nearest_unexplored_bfs(map_manager):
    """Test finding nearest room with unexplored exits."""
    # Create path: A -> B -> C (where C has unexplored exits)
    map_manager._add_room(Room(room_id="room_a", name="Room A", exits={}))
    map_manager._add_room(Room(room_id="room_b", name="Room B", exits={}))
    map_manager._add_room(Room(room_id="room_c", name="Room C", exits={"north": None}))

    map_manager._add_connection("room_a", "room_b", "east", bidirectional=True)
    map_manager._add_connection("room_b", "room_c", "north", bidirectional=True)

    # Find nearest unexplored from room_a
    result = map_manager.get_nearest_unexplored("room_a")

    assert result is not None
    room_id, path = result
    assert room_id == "room_c"
    assert path == ["east", "north"]


def test_blocked_paths_affect_pathfinding(map_manager):
    """Test that blocked paths are excluded from pathfinding."""
    # Create: A <-> B <-> C
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))
    map_manager._add_room(Room(room_id="room_c", name="Room C"))

    map_manager._add_connection("room_a", "room_b", "east", bidirectional=True)
    map_manager._add_connection("room_b", "room_c", "north", bidirectional=True)

    # Path should exist initially
    path = map_manager.get_path("room_a", "room_c")
    assert path == ["east", "north"]

    # Block the connection from A to B
    map_manager.mark_blocked("room_a", "east", "locked door")

    # Path should now be empty
    path = map_manager.get_path("room_a", "room_c")
    assert path == []

    # Unblock and path should work again
    map_manager.unblock("room_a", "east")
    path = map_manager.get_path("room_a", "room_c")
    assert path == ["east", "north"]


def test_maze_detection_on_similar_descriptions(map_manager):
    """Test maze detection triggers on 3+ identical descriptions."""
    # Add rooms with similar descriptions
    desc = "You are in a maze of twisty little passages, all alike."

    for i in range(5):
        room_id = f"room_{i}"
        map_manager._add_room(Room(
            room_id=room_id,
            name=f"Room {i}",
            description=desc,
        ))
        map_manager._recent_descriptions.append((room_id, desc))

    # Check maze condition on the latest room
    detected = map_manager.check_maze_condition("room_4", desc)

    assert detected is True
    assert map_manager.is_maze_active() is True
    assert map_manager.get_active_maze() is not None


def test_maze_room_marker_assignment(map_manager):
    """Test assigning and looking up maze markers."""
    # Manually create an active maze
    from autofrotz.storage.models import MazeGroup

    maze = MazeGroup(
        group_id="test_maze",
        entry_room_id="entry",
        room_ids=["maze_1", "maze_2"],
    )
    map_manager._active_maze = maze
    map_manager.maze_active = True

    # Add maze rooms
    map_manager._add_room(Room(room_id="maze_1", name="Maze 1"))
    map_manager._add_room(Room(room_id="maze_2", name="Maze 2"))

    # Assign markers
    map_manager.assign_maze_marker("maze_1", "leaflet")
    map_manager.assign_maze_marker("maze_2", "sword")

    # Look up by marker
    room_id = map_manager.identify_maze_room_by_marker("leaflet")
    assert room_id == "maze_1"

    room_id = map_manager.identify_maze_room_by_marker("sword")
    assert room_id == "maze_2"

    room_id = map_manager.identify_maze_room_by_marker("unknown")
    assert room_id is None


def test_complete_maze_clears_active_state(map_manager):
    """Test completing a maze clears the active state."""
    from autofrotz.storage.models import MazeGroup

    maze = MazeGroup(
        group_id="test_maze",
        entry_room_id="entry",
        room_ids=["maze_1", "maze_2"],
    )
    map_manager._maze_groups["test_maze"] = maze
    map_manager._active_maze = maze
    map_manager.maze_active = True

    # Complete the maze
    map_manager.complete_maze("test_maze")

    # Active state should be cleared
    assert map_manager.is_maze_active() is False
    assert map_manager.get_active_maze() is None

    # Maze group should be marked complete
    assert map_manager._maze_groups["test_maze"].fully_mapped is True


def test_room_id_normalization(map_manager):
    """Test room ID normalization."""
    # Test basic normalization
    assert map_manager._normalize_room_id("The Great Hall") == "great_hall"
    assert map_manager._normalize_room_id("A Dark Room") == "dark_room"
    assert map_manager._normalize_room_id("West of House") == "west_of_house"

    # Test punctuation removal
    assert map_manager._normalize_room_id("Bob's Room!") == "bobs_room"

    # Test multiple spaces
    assert map_manager._normalize_room_id("The   Big    Room") == "big_room"


def test_to_dict_serialization(map_manager):
    """Test serialization to dictionary."""
    # Add some rooms and connections
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))
    map_manager._add_connection("room_a", "room_b", "north", bidirectional=True)

    map_manager.current_room_id = "room_a"

    # Serialize
    data = map_manager.to_dict()

    assert data["current_room_id"] == "room_a"
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 2  # Bidirectional = 2 edges


def test_get_map_summary(map_manager):
    """Test map summary returns correct counts."""
    # Add rooms with varied visit status
    map_manager._add_room(Room(room_id="room_a", name="Room A", visited=True))
    map_manager._add_room(Room(room_id="room_b", name="Room B", visited=True))
    map_manager._add_room(Room(room_id="room_c", name="Room C", visited=False))

    # Add unexplored exits
    map_manager.graph.nodes["room_a"]["exits"] = {"north": None, "south": "room_b"}

    map_manager.current_room_id = "room_a"

    # Get summary
    summary = map_manager.get_map_summary()

    assert summary["rooms_visited"] == 2
    assert summary["rooms_total"] == 3
    assert summary["unexplored_exits_count"] == 1
    assert summary["current_room"] == "room_a"


def test_update_from_game_output_with_mock(mock_llm, mock_db):
    """Test update_from_game_output with mocked LLM response."""
    # Configure mock LLM to return a room change
    mock_llm.responses = {
        "north": {
            "room_changed": True,
            "room_name": "Kitchen",
            "description": "A large kitchen with a table.",
            "exits": ["south", "east"],
            "is_dark": False,
            "items_seen": ["table", "knife"],
        }
    }

    map_manager = MapManager(llm=mock_llm, database=mock_db, game_id=1)

    # Update from game output
    update = map_manager.update_from_game_output("You enter the kitchen.", "north")

    assert update.room_changed is True
    assert update.room_name == "Kitchen"
    assert update.description == "A large kitchen with a table."
    assert update.exits == ["south", "east"]
    assert update.items_seen == ["table", "knife"]

    # Room should be added to graph
    assert "kitchen" in map_manager.graph.nodes


def test_get_next_step(map_manager):
    """Test getting just the next step in a path."""
    # Create A -> B -> C
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))
    map_manager._add_room(Room(room_id="room_c", name="Room C"))

    map_manager._add_connection("room_a", "room_b", "east", bidirectional=True)
    map_manager._add_connection("room_b", "room_c", "north", bidirectional=True)

    # Get next step from A to C
    next_step = map_manager.get_next_step("room_a", "room_c")
    assert next_step == "east"

    # Get next step when already there
    next_step = map_manager.get_next_step("room_a", "room_a")
    assert next_step is None


def test_get_room_and_current_room(map_manager):
    """Test getting room by ID and current room."""
    room = Room(room_id="test_room", name="Test Room", description="A test")
    map_manager._add_room(room)
    map_manager.current_room_id = "test_room"

    # Get by ID
    retrieved = map_manager.get_room("test_room")
    assert retrieved is not None
    assert retrieved.room_id == "test_room"
    assert retrieved.name == "Test Room"

    # Get current room
    current = map_manager.get_current_room()
    assert current is not None
    assert current.room_id == "test_room"

    # Get non-existent room
    none_room = map_manager.get_room("nonexistent")
    assert none_room is None


def test_get_all_rooms(map_manager):
    """Test getting all rooms."""
    map_manager._add_room(Room(room_id="room_a", name="Room A"))
    map_manager._add_room(Room(room_id="room_b", name="Room B"))
    map_manager._add_room(Room(room_id="room_c", name="Room C"))

    all_rooms = map_manager.get_all_rooms()
    assert len(all_rooms) == 3

    room_ids = {room.room_id for room in all_rooms}
    assert room_ids == {"room_a", "room_b", "room_c"}


def test_get_maze_rooms(map_manager):
    """Test getting rooms in a maze group."""
    from autofrotz.storage.models import MazeGroup

    maze = MazeGroup(
        group_id="maze_1",
        entry_room_id="entry",
        room_ids=["maze_1", "maze_2", "maze_3"],
    )
    map_manager._maze_groups["maze_1"] = maze

    rooms = map_manager.get_maze_rooms("maze_1")
    assert len(rooms) == 3
    assert "maze_1" in rooms
    assert "maze_2" in rooms
    assert "maze_3" in rooms

    # Non-existent maze
    rooms = map_manager.get_maze_rooms("nonexistent")
    assert rooms == []


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
