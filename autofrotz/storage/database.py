"""
SQLite database layer for AutoFrotz v2.

Manages persistent storage of game sessions, turns, world state, puzzles,
and LLM metrics using stdlib sqlite3.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from autofrotz.storage.models import (
    Connection,
    GameSession,
    Item,
    LLMMetric,
    MazeGroup,
    Puzzle,
    Room,
    TurnRecord,
)

logger = logging.getLogger(__name__)


class Database:
    """SQLite database manager for game state persistence."""

    def __init__(self, db_path: str) -> None:
        """
        Initialize database connection and create schema if needed.

        Args:
            db_path: Path to SQLite database file (or ':memory:' for in-memory)
        """
        self.db_path = db_path

        # Create parent directory if needed
        if db_path != ':memory:':
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # Enable WAL mode for better concurrency
        self.conn.execute("PRAGMA journal_mode=WAL")

        self._create_schema()
        logger.info(f"Database initialized at {db_path}")

    def _create_schema(self) -> None:
        """Create all tables and indexes if they don't exist."""
        cursor = self.conn.cursor()

        # Games table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS games (
                game_id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_file TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT NOT NULL DEFAULT 'playing',
                total_turns INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Turns table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS turns (
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                turn_number INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                command_sent TEXT NOT NULL,
                game_output TEXT NOT NULL,
                room_id TEXT NOT NULL,
                inventory_snapshot TEXT NOT NULL,
                agent_reasoning TEXT,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, turn_number)
            )
        """)

        # Rooms table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                room_id TEXT NOT NULL,
                game_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                visited INTEGER NOT NULL DEFAULT 0,
                visit_count INTEGER NOT NULL DEFAULT 0,
                items_here TEXT NOT NULL DEFAULT '[]',
                maze_group TEXT,
                maze_marker_item TEXT,
                is_dark INTEGER NOT NULL DEFAULT 0,
                first_visited_turn INTEGER,
                last_visited_turn INTEGER,
                exits TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (game_id, room_id),
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        # Connections table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS connections (
                connection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                from_room_id TEXT NOT NULL,
                to_room_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                bidirectional INTEGER NOT NULL DEFAULT 1,
                blocked INTEGER NOT NULL DEFAULT 0,
                block_reason TEXT,
                teleport INTEGER NOT NULL DEFAULT 0,
                random INTEGER NOT NULL DEFAULT 0,
                observed_destinations TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, from_room_id, direction)
            )
        """)

        # Items table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                item_id TEXT NOT NULL,
                game_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                location TEXT NOT NULL DEFAULT 'unknown',
                portable INTEGER,
                properties TEXT NOT NULL DEFAULT '{}',
                first_seen_turn INTEGER NOT NULL DEFAULT 0,
                last_seen_turn INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (game_id, item_id),
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        # Puzzles table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS puzzles (
                puzzle_id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                location TEXT NOT NULL,
                related_items TEXT NOT NULL DEFAULT '[]',
                attempts TEXT NOT NULL DEFAULT '[]',
                created_turn INTEGER NOT NULL,
                solved_turn INTEGER,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        # Maze groups table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS maze_groups (
                group_id TEXT NOT NULL,
                game_id INTEGER NOT NULL,
                entry_room_id TEXT NOT NULL,
                room_ids TEXT NOT NULL DEFAULT '[]',
                exit_room_ids TEXT NOT NULL DEFAULT '[]',
                markers TEXT NOT NULL DEFAULT '{}',
                fully_mapped INTEGER NOT NULL DEFAULT 0,
                created_turn INTEGER NOT NULL,
                completed_turn INTEGER,
                PRIMARY KEY (game_id, group_id),
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        # Metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                turn_number INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER NOT NULL DEFAULT 0,
                cost_estimate REAL NOT NULL DEFAULT 0.0,
                latency_ms REAL NOT NULL DEFAULT 0.0,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        # Create indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_turns_game_turn ON turns(game_id, turn_number)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rooms_game ON rooms(game_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_game ON items(game_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_puzzles_game ON puzzles(game_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_game ON metrics(game_id)")

        self.conn.commit()
        logger.debug("Database schema created/verified")

    def create_game(self, game_file: str) -> int:
        """
        Create a new game session.

        Args:
            game_file: Path to the game file being played

        Returns:
            The game_id of the newly created session
        """
        cursor = self.conn.cursor()
        start_time = datetime.utcnow().isoformat()

        cursor.execute(
            "INSERT INTO games (game_file, start_time, status, total_turns) VALUES (?, ?, 'playing', 0)",
            (game_file, start_time)
        )
        self.conn.commit()

        game_id = cursor.lastrowid
        logger.info(f"Created game session {game_id} for {game_file}")
        return game_id

    def end_game(self, game_id: int, status: str, total_turns: int) -> None:
        """
        Mark a game session as ended.

        Args:
            game_id: Game session ID
            status: Final status ('won', 'lost', 'abandoned')
            total_turns: Total number of turns played
        """
        end_time = datetime.utcnow().isoformat()

        self.conn.execute(
            "UPDATE games SET end_time = ?, status = ?, total_turns = ? WHERE game_id = ?",
            (end_time, status, total_turns, game_id)
        )
        self.conn.commit()
        logger.info(f"Game {game_id} ended with status '{status}' after {total_turns} turns")

    def save_turn(self, turn: TurnRecord) -> None:
        """
        Save a turn record to the database.

        Args:
            turn: TurnRecord instance to persist
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO turns (
                game_id, turn_number, timestamp, command_sent, game_output,
                room_id, inventory_snapshot, agent_reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            turn.game_id,
            turn.turn_number,
            turn.timestamp,
            turn.command_sent,
            turn.game_output,
            turn.room_id,
            json.dumps(turn.inventory_snapshot),
            turn.agent_reasoning
        ))
        self.conn.commit()
        logger.debug(f"Saved turn {turn.turn_number} for game {turn.game_id}")

    def get_turns(self, game_id: int) -> list[TurnRecord]:
        """
        Retrieve all turns for a game session.

        Args:
            game_id: Game session ID

        Returns:
            List of TurnRecord objects ordered by turn number
        """
        cursor = self.conn.execute(
            "SELECT * FROM turns WHERE game_id = ? ORDER BY turn_number",
            (game_id,)
        )

        turns = []
        for row in cursor.fetchall():
            turns.append(TurnRecord(
                turn_id=row['turn_id'],
                game_id=row['game_id'],
                turn_number=row['turn_number'],
                timestamp=row['timestamp'],
                command_sent=row['command_sent'],
                game_output=row['game_output'],
                room_id=row['room_id'],
                inventory_snapshot=json.loads(row['inventory_snapshot']),
                agent_reasoning=row['agent_reasoning'] or ""
            ))

        return turns

    def get_turn(self, game_id: int, turn_number: int) -> TurnRecord | None:
        """
        Retrieve a specific turn.

        Args:
            game_id: Game session ID
            turn_number: Turn number to retrieve

        Returns:
            TurnRecord if found, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT * FROM turns WHERE game_id = ? AND turn_number = ?",
            (game_id, turn_number)
        )

        row = cursor.fetchone()
        if not row:
            return None

        return TurnRecord(
            turn_id=row['turn_id'],
            game_id=row['game_id'],
            turn_number=row['turn_number'],
            timestamp=row['timestamp'],
            command_sent=row['command_sent'],
            game_output=row['game_output'],
            room_id=row['room_id'],
            inventory_snapshot=json.loads(row['inventory_snapshot']),
            agent_reasoning=row['agent_reasoning'] or ""
        )

    def get_latest_turn(self, game_id: int) -> TurnRecord | None:
        """
        Retrieve the most recent turn for a game.

        Args:
            game_id: Game session ID

        Returns:
            TurnRecord if any turns exist, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT * FROM turns WHERE game_id = ? ORDER BY turn_number DESC LIMIT 1",
            (game_id,)
        )

        row = cursor.fetchone()
        if not row:
            return None

        return TurnRecord(
            turn_id=row['turn_id'],
            game_id=row['game_id'],
            turn_number=row['turn_number'],
            timestamp=row['timestamp'],
            command_sent=row['command_sent'],
            game_output=row['game_output'],
            room_id=row['room_id'],
            inventory_snapshot=json.loads(row['inventory_snapshot']),
            agent_reasoning=row['agent_reasoning'] or ""
        )

    def save_room(self, game_id: int, room: Room) -> None:
        """
        Save or update a room.

        Args:
            game_id: Game session ID
            room: Room instance to persist
        """
        self.conn.execute("""
            INSERT INTO rooms (
                game_id, room_id, name, description, visited, visit_count,
                items_here, maze_group, maze_marker_item, is_dark,
                first_visited_turn, last_visited_turn, exits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, room_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                visited = excluded.visited,
                visit_count = excluded.visit_count,
                items_here = excluded.items_here,
                maze_group = excluded.maze_group,
                maze_marker_item = excluded.maze_marker_item,
                is_dark = excluded.is_dark,
                last_visited_turn = excluded.last_visited_turn,
                exits = excluded.exits
        """, (
            game_id,
            room.room_id,
            room.name,
            room.description,
            1 if room.visited else 0,
            room.visit_count,
            json.dumps(room.items_here),
            room.maze_group,
            room.maze_marker_item,
            1 if room.is_dark else 0,
            room.first_visited_turn,
            room.last_visited_turn,
            json.dumps(room.exits)
        ))
        self.conn.commit()
        logger.debug(f"Saved room {room.room_id} for game {game_id}")

    def get_rooms(self, game_id: int) -> list[Room]:
        """
        Retrieve all rooms for a game session.

        Args:
            game_id: Game session ID

        Returns:
            List of Room objects
        """
        cursor = self.conn.execute(
            "SELECT * FROM rooms WHERE game_id = ?",
            (game_id,)
        )

        rooms = []
        for row in cursor.fetchall():
            rooms.append(Room(
                room_id=row['room_id'],
                name=row['name'],
                description=row['description'] or "",
                visited=bool(row['visited']),
                visit_count=row['visit_count'],
                items_here=json.loads(row['items_here']),
                maze_group=row['maze_group'],
                maze_marker_item=row['maze_marker_item'],
                is_dark=bool(row['is_dark']),
                first_visited_turn=row['first_visited_turn'],
                last_visited_turn=row['last_visited_turn'],
                exits=json.loads(row['exits'])
            ))

        return rooms

    def save_connection(self, game_id: int, conn: Connection) -> None:
        """
        Save or update a connection between rooms.

        Args:
            game_id: Game session ID
            conn: Connection instance to persist
        """
        self.conn.execute("""
            INSERT INTO connections (
                game_id, from_room_id, to_room_id, direction, bidirectional,
                blocked, block_reason, teleport, random, observed_destinations
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, from_room_id, direction) DO UPDATE SET
                to_room_id = excluded.to_room_id,
                bidirectional = excluded.bidirectional,
                blocked = excluded.blocked,
                block_reason = excluded.block_reason,
                teleport = excluded.teleport,
                random = excluded.random,
                observed_destinations = excluded.observed_destinations
        """, (
            game_id,
            conn.from_room_id,
            conn.to_room_id,
            conn.direction,
            1 if conn.bidirectional else 0,
            1 if conn.blocked else 0,
            conn.block_reason,
            1 if conn.teleport else 0,
            1 if conn.random else 0,
            json.dumps(conn.observed_destinations)
        ))
        self.conn.commit()
        logger.debug(f"Saved connection {conn.from_room_id} --{conn.direction}--> {conn.to_room_id}")

    def get_connections(self, game_id: int) -> list[Connection]:
        """
        Retrieve all connections for a game session.

        Args:
            game_id: Game session ID

        Returns:
            List of Connection objects
        """
        cursor = self.conn.execute(
            "SELECT * FROM connections WHERE game_id = ?",
            (game_id,)
        )

        connections = []
        for row in cursor.fetchall():
            connections.append(Connection(
                from_room_id=row['from_room_id'],
                to_room_id=row['to_room_id'],
                direction=row['direction'],
                bidirectional=bool(row['bidirectional']),
                blocked=bool(row['blocked']),
                block_reason=row['block_reason'],
                teleport=bool(row['teleport']),
                random=bool(row['random']),
                observed_destinations=json.loads(row['observed_destinations'])
            ))

        return connections

    def save_item(self, game_id: int, item: Item) -> None:
        """
        Save or update an item.

        Args:
            game_id: Game session ID
            item: Item instance to persist
        """
        self.conn.execute("""
            INSERT INTO items (
                game_id, item_id, name, description, location, portable,
                properties, first_seen_turn, last_seen_turn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, item_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                location = excluded.location,
                portable = excluded.portable,
                properties = excluded.properties,
                last_seen_turn = excluded.last_seen_turn
        """, (
            game_id,
            item.item_id,
            item.name,
            item.description,
            item.location,
            1 if item.portable is True else (0 if item.portable is False else None),
            json.dumps(item.properties),
            item.first_seen_turn,
            item.last_seen_turn
        ))
        self.conn.commit()
        logger.debug(f"Saved item {item.item_id} for game {game_id}")

    def get_items(self, game_id: int) -> list[Item]:
        """
        Retrieve all items for a game session.

        Args:
            game_id: Game session ID

        Returns:
            List of Item objects
        """
        cursor = self.conn.execute(
            "SELECT * FROM items WHERE game_id = ?",
            (game_id,)
        )

        items = []
        for row in cursor.fetchall():
            portable = None
            if row['portable'] is not None:
                portable = bool(row['portable'])

            items.append(Item(
                item_id=row['item_id'],
                name=row['name'],
                description=row['description'],
                location=row['location'],
                portable=portable,
                properties=json.loads(row['properties']),
                first_seen_turn=row['first_seen_turn'],
                last_seen_turn=row['last_seen_turn']
            ))

        return items

    def save_puzzle(self, game_id: int, puzzle: Puzzle) -> int:
        """
        Save or update a puzzle.

        Args:
            game_id: Game session ID
            puzzle: Puzzle instance to persist

        Returns:
            The puzzle_id (newly created or existing)
        """
        cursor = self.conn.cursor()

        if puzzle.puzzle_id is None:
            # Insert new puzzle
            cursor.execute("""
                INSERT INTO puzzles (
                    game_id, description, status, location, related_items,
                    attempts, created_turn, solved_turn
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                puzzle.description,
                puzzle.status,
                puzzle.location,
                json.dumps(puzzle.related_items),
                json.dumps(puzzle.attempts),
                puzzle.created_turn,
                puzzle.solved_turn
            ))
            self.conn.commit()
            puzzle_id = cursor.lastrowid
            logger.debug(f"Created puzzle {puzzle_id} for game {game_id}")
            return puzzle_id
        else:
            # Update existing puzzle
            self.update_puzzle(puzzle)
            return puzzle.puzzle_id

    def update_puzzle(self, puzzle: Puzzle) -> None:
        """
        Update an existing puzzle.

        Args:
            puzzle: Puzzle instance with puzzle_id set
        """
        if puzzle.puzzle_id is None:
            raise ValueError("Cannot update puzzle without puzzle_id")

        self.conn.execute("""
            UPDATE puzzles SET
                description = ?,
                status = ?,
                location = ?,
                related_items = ?,
                attempts = ?,
                solved_turn = ?
            WHERE puzzle_id = ?
        """, (
            puzzle.description,
            puzzle.status,
            puzzle.location,
            json.dumps(puzzle.related_items),
            json.dumps(puzzle.attempts),
            puzzle.solved_turn,
            puzzle.puzzle_id
        ))
        self.conn.commit()
        logger.debug(f"Updated puzzle {puzzle.puzzle_id}")

    def get_puzzles(self, game_id: int, status: str | None = None) -> list[Puzzle]:
        """
        Retrieve puzzles for a game session.

        Args:
            game_id: Game session ID
            status: Optional filter by status ('open', 'solved', etc.)

        Returns:
            List of Puzzle objects
        """
        if status:
            cursor = self.conn.execute(
                "SELECT * FROM puzzles WHERE game_id = ? AND status = ?",
                (game_id, status)
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM puzzles WHERE game_id = ?",
                (game_id,)
            )

        puzzles = []
        for row in cursor.fetchall():
            puzzles.append(Puzzle(
                puzzle_id=row['puzzle_id'],
                description=row['description'],
                status=row['status'],
                location=row['location'],
                related_items=json.loads(row['related_items']),
                attempts=json.loads(row['attempts']),
                created_turn=row['created_turn'],
                solved_turn=row['solved_turn']
            ))

        return puzzles

    def save_maze_group(self, game_id: int, maze: MazeGroup) -> None:
        """
        Save or update a maze group.

        Args:
            game_id: Game session ID
            maze: MazeGroup instance to persist
        """
        self.conn.execute("""
            INSERT INTO maze_groups (
                game_id, group_id, entry_room_id, room_ids, exit_room_ids,
                markers, fully_mapped, created_turn, completed_turn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, group_id) DO UPDATE SET
                room_ids = excluded.room_ids,
                exit_room_ids = excluded.exit_room_ids,
                markers = excluded.markers,
                fully_mapped = excluded.fully_mapped,
                completed_turn = excluded.completed_turn
        """, (
            game_id,
            maze.group_id,
            maze.entry_room_id,
            json.dumps(maze.room_ids),
            json.dumps(maze.exit_room_ids),
            json.dumps(maze.markers),
            1 if maze.fully_mapped else 0,
            maze.created_turn,
            maze.completed_turn
        ))
        self.conn.commit()
        logger.debug(f"Saved maze group {maze.group_id} for game {game_id}")

    def update_maze_group(self, maze: MazeGroup) -> None:
        """
        Update an existing maze group (alias for save_maze_group with game_id lookup).

        This method exists for API consistency but requires the game_id to be known.
        In practice, callers should use save_maze_group directly.

        Args:
            maze: MazeGroup instance to update
        """
        # Note: This method signature matches the spec but is awkward without game_id.
        # In practice, the orchestrator/map manager will call save_maze_group with game_id.
        # We'll document that this is a legacy/convenience method.
        logger.warning("update_maze_group called without game_id context - use save_maze_group instead")

    def get_maze_groups(self, game_id: int) -> list[MazeGroup]:
        """
        Retrieve all maze groups for a game session.

        Args:
            game_id: Game session ID

        Returns:
            List of MazeGroup objects
        """
        cursor = self.conn.execute(
            "SELECT * FROM maze_groups WHERE game_id = ?",
            (game_id,)
        )

        maze_groups = []
        for row in cursor.fetchall():
            maze_groups.append(MazeGroup(
                group_id=row['group_id'],
                entry_room_id=row['entry_room_id'],
                room_ids=json.loads(row['room_ids']),
                exit_room_ids=json.loads(row['exit_room_ids']),
                markers=json.loads(row['markers']),
                fully_mapped=bool(row['fully_mapped']),
                created_turn=row['created_turn'],
                completed_turn=row['completed_turn']
            ))

        return maze_groups

    def save_metric(self, metric: LLMMetric) -> None:
        """
        Save an LLM usage metric.

        Args:
            metric: LLMMetric instance to persist
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            INSERT INTO metrics (
                game_id, turn_number, agent_name, provider, model,
                input_tokens, output_tokens, cached_tokens,
                cost_estimate, latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metric.game_id,
            metric.turn_number,
            metric.agent_name,
            metric.provider,
            metric.model,
            metric.input_tokens,
            metric.output_tokens,
            metric.cached_tokens,
            metric.cost_estimate,
            metric.latency_ms
        ))
        self.conn.commit()
        logger.debug(f"Saved metric for {metric.agent_name} on turn {metric.turn_number}")

    def get_metrics(self, game_id: int) -> list[LLMMetric]:
        """
        Retrieve all LLM metrics for a game session.

        Args:
            game_id: Game session ID

        Returns:
            List of LLMMetric objects
        """
        cursor = self.conn.execute(
            "SELECT * FROM metrics WHERE game_id = ? ORDER BY turn_number",
            (game_id,)
        )

        metrics = []
        for row in cursor.fetchall():
            metrics.append(LLMMetric(
                metric_id=row['metric_id'],
                game_id=row['game_id'],
                turn_number=row['turn_number'],
                agent_name=row['agent_name'],
                provider=row['provider'],
                model=row['model'],
                input_tokens=row['input_tokens'],
                output_tokens=row['output_tokens'],
                cached_tokens=row['cached_tokens'],
                cost_estimate=row['cost_estimate'],
                latency_ms=row['latency_ms']
            ))

        return metrics

    def get_active_game(self) -> tuple[int, str] | None:
        """
        Find the most recent game with status 'playing' for crash recovery.

        Returns:
            Tuple of (game_id, game_file) if found, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT game_id, game_file FROM games WHERE status = 'playing' ORDER BY start_time DESC LIMIT 1"
        )

        row = cursor.fetchone()
        if not row:
            return None

        return (row['game_id'], row['game_file'])

    def get_game(self, game_id: int) -> GameSession | None:
        """
        Retrieve metadata for a specific game session.

        Args:
            game_id: Game session ID

        Returns:
            GameSession if found, None otherwise
        """
        cursor = self.conn.execute(
            "SELECT * FROM games WHERE game_id = ?",
            (game_id,)
        )

        row = cursor.fetchone()
        if not row:
            return None

        return GameSession(
            game_id=row['game_id'],
            game_file=row['game_file'],
            start_time=row['start_time'],
            end_time=row['end_time'],
            status=row['status'],
            total_turns=row['total_turns']
        )

    def get_all_games(self) -> list[GameSession]:
        """
        Retrieve all game sessions.

        Returns:
            List of GameSession objects ordered by start time (newest first)
        """
        cursor = self.conn.execute(
            "SELECT * FROM games ORDER BY start_time DESC"
        )

        games = []
        for row in cursor.fetchall():
            games.append(GameSession(
                game_id=row['game_id'],
                game_file=row['game_file'],
                start_time=row['start_time'],
                end_time=row['end_time'],
                status=row['status'],
                total_turns=row['total_turns']
            ))

        return games

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
        logger.info("Database connection closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
