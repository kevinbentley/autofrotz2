"""
Storage layer for AutoFrotz v2.

Provides dataclasses for all game state and a SQLite database manager.
"""

from autofrotz.storage.database import Database
from autofrotz.storage.models import (
    Connection,
    GameSession,
    Item,
    ItemUpdate,
    LLMMetric,
    LLMResponse,
    MazeGroup,
    Puzzle,
    PuzzleSuggestion,
    Room,
    RoomUpdate,
    TurnRecord,
)

__all__ = [
    "Database",
    "Connection",
    "GameSession",
    "Item",
    "ItemUpdate",
    "LLMMetric",
    "LLMResponse",
    "MazeGroup",
    "Puzzle",
    "PuzzleSuggestion",
    "Room",
    "RoomUpdate",
    "TurnRecord",
]
