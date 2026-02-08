"""
Web monitoring hook for AutoFrotz v2.

Pushes game events to the web server's WebSocket connection manager
so connected clients receive live updates during gameplay.
"""

import asyncio
import logging
from typing import Optional

from autofrotz.hooks.base import BaseHook
from autofrotz.web.server import connection_manager

logger = logging.getLogger(__name__)


class WebMonitorHook(BaseHook):
    """
    Hook that broadcasts game events to WebSocket clients via the
    web server's ConnectionManager.

    Each event is sent as a JSON message with a "type" field identifying
    the event kind. The turn_end event follows the format specified in
    GAME.md Section 8.
    """

    def __init__(self) -> None:
        self._game_id: Optional[int] = None
        self._current_room: Optional[dict] = None
        self._inventory: list[str] = []
        self._new_items: list[dict] = []
        self._puzzles_updated: list[dict] = []
        self._metrics: dict = {}

    def _broadcast(self, message: dict) -> None:
        """Send a message to all WebSocket clients."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(connection_manager.broadcast(message))
            else:
                loop.run_until_complete(connection_manager.broadcast(message))
        except RuntimeError:
            # No event loop available -- create one and run
            loop = asyncio.new_event_loop()
            loop.run_until_complete(connection_manager.broadcast(message))
            loop.close()

    def on_game_start(self, game_id: int, game_file: str) -> None:
        self._game_id = game_id
        self._broadcast({
            "type": "game_start",
            "game_id": game_id,
            "game_file": game_file,
        })

    def on_turn_start(self, turn_number: int, room_id: str) -> None:
        # Reset per-turn accumulators
        self._new_items = []
        self._puzzles_updated = []
        self._current_room = {"id": room_id}

    def on_turn_end(self, turn_number: int, command: str, output: str, room_id: str) -> None:
        """Push the full turn event in the GAME.md Section 8 format."""
        self._broadcast({
            "type": "turn",
            "turn_number": turn_number,
            "command": command,
            "output": output,
            "room": self._current_room or {"id": room_id},
            "inventory": self._inventory,
            "new_items": self._new_items,
            "puzzles_updated": self._puzzles_updated,
            "agent_reasoning": "",
            "metrics": self._metrics,
        })

    def on_room_enter(self, room_id: str, room_name: str, description: str, is_new: bool) -> None:
        self._current_room = {
            "id": room_id,
            "name": room_name,
            "is_new": is_new,
        }
        self._broadcast({
            "type": "room_enter",
            "room_id": room_id,
            "room_name": room_name,
            "is_new": is_new,
        })

    def on_item_found(self, item_id: str, item_name: str, room_id: str) -> None:
        self._new_items.append({
            "id": item_id,
            "name": item_name,
            "location": room_id,
        })
        self._broadcast({
            "type": "item_found",
            "item_id": item_id,
            "item_name": item_name,
            "room_id": room_id,
        })

    def on_item_taken(self, item_id: str, item_name: str) -> None:
        if item_id not in self._inventory:
            self._inventory.append(item_name)
        self._broadcast({
            "type": "item_taken",
            "item_id": item_id,
            "item_name": item_name,
        })

    def on_puzzle_found(self, puzzle_id: int, description: str) -> None:
        self._puzzles_updated.append({
            "puzzle_id": puzzle_id,
            "description": description,
            "action": "found",
        })
        self._broadcast({
            "type": "puzzle_found",
            "puzzle_id": puzzle_id,
            "description": description,
        })

    def on_puzzle_solved(self, puzzle_id: int, description: str) -> None:
        self._puzzles_updated.append({
            "puzzle_id": puzzle_id,
            "description": description,
            "action": "solved",
        })
        self._broadcast({
            "type": "puzzle_solved",
            "puzzle_id": puzzle_id,
            "description": description,
        })

    def on_maze_detected(self, maze_group_id: str, entry_room_id: str, suspected_room_count: int) -> None:
        self._broadcast({
            "type": "maze_detected",
            "maze_group_id": maze_group_id,
            "entry_room_id": entry_room_id,
            "suspected_room_count": suspected_room_count,
        })

    def on_maze_room_marked(self, maze_group_id: str, room_id: str, marker_item_id: str) -> None:
        self._broadcast({
            "type": "maze_room_marked",
            "maze_group_id": maze_group_id,
            "room_id": room_id,
            "marker_item_id": marker_item_id,
        })

    def on_maze_completed(self, maze_group_id: str, total_rooms: int, total_exits: int) -> None:
        self._broadcast({
            "type": "maze_completed",
            "maze_group_id": maze_group_id,
            "total_rooms": total_rooms,
            "total_exits": total_exits,
        })

    def on_game_end(self, game_id: int, status: str, total_turns: int) -> None:
        self._broadcast({
            "type": "game_end",
            "game_id": game_id,
            "status": status,
            "total_turns": total_turns,
        })
