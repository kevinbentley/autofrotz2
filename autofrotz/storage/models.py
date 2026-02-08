"""
Storage models for AutoFrotz v2.

All dataclasses used across the project for game state, LLM interactions,
and database persistence.
"""

from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    """Response from an LLM call with usage metrics."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_estimate: float = 0.0
    latency_ms: float = 0.0


@dataclass
class Room:
    """A location in the game world."""
    room_id: str
    name: str
    description: str = ""
    visited: bool = False
    visit_count: int = 0
    items_here: list[str] = field(default_factory=list)
    maze_group: str | None = None
    maze_marker_item: str | None = None
    is_dark: bool = False
    first_visited_turn: int | None = None
    last_visited_turn: int | None = None
    exits: dict[str, str | None] = field(default_factory=dict)  # direction -> room_id or None


@dataclass
class Connection:
    """A directional connection between two rooms."""
    from_room_id: str
    to_room_id: str
    direction: str
    bidirectional: bool = True
    blocked: bool = False
    block_reason: str | None = None
    teleport: bool = False
    random: bool = False
    observed_destinations: list[str] = field(default_factory=list)


@dataclass
class Item:
    """An object in the game world."""
    item_id: str
    name: str
    description: str | None = None
    location: str = "unknown"
    portable: bool | None = None
    properties: dict = field(default_factory=dict)
    first_seen_turn: int = 0
    last_seen_turn: int = 0


@dataclass
class Puzzle:
    """A puzzle or obstacle in the game."""
    puzzle_id: int | None = None
    description: str = ""
    status: str = "open"
    location: str = ""
    related_items: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    created_turn: int = 0
    solved_turn: int | None = None


@dataclass
class MazeGroup:
    """A detected maze region with tracking state."""
    group_id: str
    entry_room_id: str
    room_ids: list[str] = field(default_factory=list)
    exit_room_ids: list[str] = field(default_factory=list)
    markers: dict[str, str] = field(default_factory=dict)  # room_id -> item_id
    fully_mapped: bool = False
    created_turn: int = 0
    completed_turn: int | None = None


@dataclass
class TurnRecord:
    """A single game turn with full state snapshot."""
    turn_id: int | None = None
    game_id: int = 0
    turn_number: int = 0
    timestamp: str = ""
    command_sent: str = ""
    game_output: str = ""
    room_id: str = ""
    inventory_snapshot: list[str] = field(default_factory=list)
    agent_reasoning: str = ""


@dataclass
class GameSession:
    """Metadata for a complete game session."""
    game_id: int | None = None
    game_file: str = ""
    start_time: str = ""
    end_time: str | None = None
    status: str = "playing"
    total_turns: int = 0


@dataclass
class LLMMetric:
    """Per-turn LLM usage metrics."""
    metric_id: int | None = None
    game_id: int = 0
    turn_number: int = 0
    agent_name: str = ""
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_estimate: float = 0.0
    latency_ms: float = 0.0


@dataclass
class RoomUpdate:
    """Parsed room state change from game output."""
    room_changed: bool
    room_id: str | None = None
    room_name: str | None = None
    description: str | None = None
    exits: list[str] = field(default_factory=list)
    is_dark: bool = False
    new_room: bool = False
    items_seen: list[str] = field(default_factory=list)


@dataclass
class ItemUpdate:
    """Parsed item state change from game output."""
    item_id: str
    name: str
    change_type: str  # "new", "taken", "dropped", "state_change", "moved", "gone"
    location: str | None = None
    properties: dict | None = None


@dataclass
class PuzzleSuggestion:
    """Puzzle agent suggestion for the game agent."""
    puzzle_id: int
    description: str
    proposed_action: str
    items_to_use: list[str] = field(default_factory=list)
    confidence: str = "medium"  # "high", "medium", "low"
