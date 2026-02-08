"""
Hook base class for AutoFrotz v2.

Provides an observer pattern interface with no-op default implementations.
Subclasses override only the methods they need. The orchestrator wraps
every hook call in try/except so a broken hook never crashes the game.
"""

import logging

logger = logging.getLogger(__name__)


class BaseHook:
    """
    Base class for all hooks in the AutoFrotz event system.

    All methods are no-ops by default. Subclasses override selectively
    to handle specific events. Methods should not raise exceptions;
    the orchestrator wraps calls defensively, but hooks should still
    handle their own errors gracefully.
    """

    def on_game_start(self, game_id: int, game_file: str) -> None:
        """Called when a new game session begins."""
        pass

    def on_turn_start(self, turn_number: int, room_id: str) -> None:
        """Called at the beginning of each turn, before any processing."""
        pass

    def on_turn_end(self, turn_number: int, command: str, output: str, room_id: str) -> None:
        """Called at the end of each turn, after all processing and logging."""
        pass

    def on_room_enter(self, room_id: str, room_name: str, description: str, is_new: bool) -> None:
        """Called when the player enters a room. is_new is True for first visits."""
        pass

    def on_item_found(self, item_id: str, item_name: str, room_id: str) -> None:
        """Called when a new item is discovered for the first time."""
        pass

    def on_item_taken(self, item_id: str, item_name: str) -> None:
        """Called when an item is picked up into inventory."""
        pass

    def on_puzzle_found(self, puzzle_id: int, description: str) -> None:
        """Called when the puzzle agent detects a new puzzle."""
        pass

    def on_puzzle_solved(self, puzzle_id: int, description: str) -> None:
        """Called when a puzzle is marked as solved."""
        pass

    def on_maze_detected(self, maze_group_id: str, entry_room_id: str, suspected_room_count: int) -> None:
        """Called when the map manager detects a maze condition."""
        pass

    def on_maze_room_marked(self, maze_group_id: str, room_id: str, marker_item_id: str) -> None:
        """Called when a marker item is dropped in a maze room."""
        pass

    def on_maze_completed(self, maze_group_id: str, total_rooms: int, total_exits: int) -> None:
        """Called when a maze has been fully mapped."""
        pass

    def on_game_end(self, game_id: int, status: str, total_turns: int) -> None:
        """Called when the game session ends (won, lost, or abandoned)."""
        pass
