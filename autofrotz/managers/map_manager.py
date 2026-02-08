"""
Map manager for AutoFrotz v2.

Maintains a directed graph of rooms and connections, provides pathfinding,
tracks unexplored exits, and handles maze detection and resolution.
"""

import logging
import re
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import networkx as nx

from autofrotz.llm.base import BaseLLM
from autofrotz.storage.database import Database
from autofrotz.storage.models import (
    Room,
    Connection,
    MazeGroup,
    RoomUpdate,
    LLMMetric,
    LLMResponse,
)

logger = logging.getLogger(__name__)


class MapManager:
    """
    Graph-based map manager using NetworkX DiGraph.

    Handles room and connection tracking, pathfinding, exploration state,
    blocked paths, and maze detection/resolution using item markers.
    """

    def __init__(self, llm: BaseLLM, database: Database, game_id: int):
        """
        Initialize the map manager.

        Args:
            llm: LLM instance for parsing game output
            database: Database instance for persistence
            game_id: Current game session ID
        """
        self.llm = llm
        self.db = database
        self.game_id = game_id

        # Core graph structure
        self.graph = nx.DiGraph()
        self.current_room_id: Optional[str] = None

        # Maze detection and resolution state
        self.maze_active: bool = False
        self._active_maze: Optional[MazeGroup] = None
        self._maze_groups: dict[str, MazeGroup] = {}
        self._similarity_threshold: float = 0.95
        self._recent_descriptions: list[tuple[str, str]] = []  # (room_id, description)
        self._maze_sequence_counter: dict[str, int] = {}  # group_id -> next_seq

        # Metrics tracking
        self._last_metrics: Optional[LLMMetric] = None

        # Load existing state from database
        self.load_from_db()

        logger.info(f"MapManager initialized for game {game_id}")

    def _normalize_room_id(self, name: str) -> str:
        """
        Normalize a room name into a room ID.

        Converts to lowercase, spaces to underscores, strips articles and
        non-alphanumeric characters except underscores.

        Args:
            name: Room name from the game

        Returns:
            Normalized room ID string
        """
        # Lowercase
        normalized = name.lower()

        # Remove leading articles
        normalized = re.sub(r'^(the|a|an)\s+', '', normalized)

        # Collapse multiple spaces to single space
        normalized = ' '.join(normalized.split())

        # Replace spaces with underscores
        normalized = normalized.replace(' ', '_')

        # Strip non-alphanumeric except underscores
        normalized = re.sub(r'[^a-z0-9_]', '', normalized)

        return normalized

    def _add_room(self, room: Room) -> None:
        """
        Add a room to the graph and database.

        Args:
            room: Room object to add
        """
        # Add node to graph with all room attributes
        self.graph.add_node(
            room.room_id,
            name=room.name,
            description=room.description,
            visited=room.visited,
            visit_count=room.visit_count,
            items_here=room.items_here,
            maze_group=room.maze_group,
            maze_marker_item=room.maze_marker_item,
            is_dark=room.is_dark,
            first_visited_turn=room.first_visited_turn,
            last_visited_turn=room.last_visited_turn,
            exits=room.exits,
        )

        # Save to database
        # TODO: request from storage agent - specific room save method
        logger.debug(f"Added room: {room.room_id} ({room.name})")

    def _add_connection(
        self,
        from_room: str,
        to_room: str,
        direction: str,
        bidirectional: bool = True,
        blocked: bool = False,
        block_reason: Optional[str] = None,
        teleport: bool = False,
        random: bool = False,
    ) -> None:
        """
        Add a directed connection between rooms.

        Args:
            from_room: Source room ID
            to_room: Destination room ID
            direction: Direction command (e.g., "north", "up")
            bidirectional: If True, create reverse connection
            blocked: Whether the path is currently blocked
            block_reason: Optional reason for blocking
            teleport: Whether this is a one-way teleport
            random: Whether this connection is randomized
        """
        # Add edge with attributes
        self.graph.add_edge(
            from_room,
            to_room,
            direction=direction,
            bidirectional=bidirectional,
            blocked=blocked,
            block_reason=block_reason,
            teleport=teleport,
            random=random,
            observed_destinations=[to_room] if random else [],
        )

        # Add reverse edge if bidirectional
        if bidirectional:
            # Determine reverse direction
            reverse_dir = self._reverse_direction(direction)
            self.graph.add_edge(
                to_room,
                from_room,
                direction=reverse_dir,
                bidirectional=True,
                blocked=blocked,
                block_reason=block_reason,
                teleport=False,
                random=False,
                observed_destinations=[],
            )

        # Update room exits
        if from_room in self.graph.nodes:
            exits = self.graph.nodes[from_room].get('exits', {})
            exits[direction] = to_room
            self.graph.nodes[from_room]['exits'] = exits

        # TODO: request from storage agent - specific connection save method
        logger.debug(f"Added connection: {from_room} --{direction}--> {to_room}")

    def _reverse_direction(self, direction: str) -> str:
        """
        Get the reverse direction for a given direction.

        Args:
            direction: Direction command

        Returns:
            Reverse direction string
        """
        reverse_map = {
            'north': 'south',
            'south': 'north',
            'east': 'west',
            'west': 'east',
            'northeast': 'southwest',
            'northwest': 'southeast',
            'southeast': 'northwest',
            'southwest': 'northeast',
            'up': 'down',
            'down': 'up',
            'in': 'out',
            'out': 'in',
        }
        return reverse_map.get(direction, f'back_from_{direction}')

    def get_room(self, room_id: str) -> Optional[Room]:
        """
        Get a room by ID.

        Args:
            room_id: Room identifier

        Returns:
            Room object or None if not found
        """
        if room_id not in self.graph.nodes:
            return None

        data = self.graph.nodes[room_id]
        return Room(
            room_id=room_id,
            name=data.get('name', ''),
            description=data.get('description', ''),
            visited=data.get('visited', False),
            visit_count=data.get('visit_count', 0),
            items_here=data.get('items_here', []),
            maze_group=data.get('maze_group'),
            maze_marker_item=data.get('maze_marker_item'),
            is_dark=data.get('is_dark', False),
            first_visited_turn=data.get('first_visited_turn'),
            last_visited_turn=data.get('last_visited_turn'),
            exits=data.get('exits', {}),
        )

    def get_current_room(self) -> Optional[Room]:
        """
        Get the current room.

        Returns:
            Current Room object or None
        """
        if self.current_room_id is None:
            return None
        return self.get_room(self.current_room_id)

    def get_all_rooms(self) -> list[Room]:
        """
        Get all rooms in the map.

        Returns:
            List of all Room objects
        """
        rooms = []
        for room_id in self.graph.nodes:
            room = self.get_room(room_id)
            if room:
                rooms.append(room)
        return rooms

    def update_from_game_output(
        self, output_text: str, command_used: str
    ) -> RoomUpdate:
        """
        Parse game output and update map state using LLM.

        Args:
            output_text: Raw game output text
            command_used: Command that produced this output

        Returns:
            RoomUpdate object with parsed information
        """
        # Load prompt template
        prompt_path = Path(__file__).parent.parent / 'prompts' / 'map_update.txt'
        try:
            with open(prompt_path, 'r') as f:
                system_prompt = f.read()
        except FileNotFoundError:
            logger.error(f"Map update prompt not found at {prompt_path}")
            system_prompt = "You are a parser for text adventure game output. Extract room information."

        # Define JSON schema for structured output
        schema = {
            "type": "object",
            "properties": {
                "room_changed": {"type": "boolean"},
                "room_name": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "exits": {"type": "array", "items": {"type": "string"}},
                "is_dark": {"type": "boolean"},
                "items_seen": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["room_changed", "room_name", "description", "exits", "is_dark"],
        }

        # Build message
        messages = [
            {
                "role": "user",
                "content": f"Command: {command_used}\nOutput:\n{output_text}"
            }
        ]

        # Call LLM for structured parsing
        import time
        start_time = time.time()

        try:
            parsed = self.llm.complete_json(
                messages=messages,
                system_prompt=system_prompt,
                schema=schema,
                temperature=0.1,
                max_tokens=512,
            )
        except Exception as e:
            logger.error(f"LLM parsing failed: {e}")
            # Return minimal update on failure
            return RoomUpdate(
                room_changed=False,
                room_id=self.current_room_id,
                room_name=None,
                description=None,
                exits=[],
                is_dark=False,
                new_room=False,
            )

        latency_ms = (time.time() - start_time) * 1000

        # Store metrics
        # TODO: Get actual token counts from LLM response
        self._last_metrics = LLMMetric(
            game_id=self.game_id,
            turn_number=0,  # Will be set by orchestrator
            agent_name="map_parser",
            provider=self.llm.provider_name,
            model=self.llm.model,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_estimate=0.0,
            latency_ms=latency_ms,
        )

        # Process parsed data
        room_changed = parsed.get('room_changed', False)
        room_name = parsed.get('room_name')
        description = parsed.get('description')
        exits = parsed.get('exits', [])
        is_dark = parsed.get('is_dark', False)
        items_seen = parsed.get('items_seen', [])

        new_room = False
        room_id = None

        if room_changed and room_name:
            # Generate room ID
            room_id = self._normalize_room_id(room_name)

            # Check if this is a new room
            new_room = room_id not in self.graph.nodes

            if new_room:
                # Create new room
                room = Room(
                    room_id=room_id,
                    name=room_name,
                    description=description or "",
                    visited=True,
                    visit_count=1,
                    items_here=items_seen,
                    is_dark=is_dark,
                    exits={exit_dir: None for exit_dir in exits},
                )
                self._add_room(room)
            else:
                # Update existing room
                self.graph.nodes[room_id]['description'] = description or self.graph.nodes[room_id].get('description', '')
                self.graph.nodes[room_id]['visited'] = True
                self.graph.nodes[room_id]['visit_count'] = self.graph.nodes[room_id].get('visit_count', 0) + 1
                self.graph.nodes[room_id]['is_dark'] = is_dark

                # Update exits
                existing_exits = self.graph.nodes[room_id].get('exits', {})
                for exit_dir in exits:
                    if exit_dir not in existing_exits:
                        existing_exits[exit_dir] = None
                self.graph.nodes[room_id]['exits'] = existing_exits

            # If we came from another room, create/update connection
            if self.current_room_id and self.current_room_id != room_id:
                # Extract direction from command
                direction = self._extract_direction(command_used)
                if direction:
                    # Check if connection already exists
                    if not self.graph.has_edge(self.current_room_id, room_id):
                        self._add_connection(
                            self.current_room_id,
                            room_id,
                            direction,
                            bidirectional=True,
                        )
                    else:
                        # Update existing edge direction if needed
                        edge_data = self.graph.edges[self.current_room_id, room_id]
                        if edge_data.get('direction') != direction:
                            edge_data['direction'] = direction

            # Update current room
            self.current_room_id = room_id

            # Track description for maze detection
            if description:
                self._recent_descriptions.append((room_id, description))
                # Keep only last 20 descriptions
                if len(self._recent_descriptions) > 20:
                    self._recent_descriptions.pop(0)

        return RoomUpdate(
            room_changed=room_changed,
            room_id=room_id,
            room_name=room_name,
            description=description,
            exits=exits,
            is_dark=is_dark,
            new_room=new_room,
            items_seen=items_seen,
        )

    def _extract_direction(self, command: str) -> Optional[str]:
        """
        Extract direction from a movement command.

        Args:
            command: Game command string

        Returns:
            Direction string or None
        """
        # Common movement commands
        directions = [
            'north', 'south', 'east', 'west',
            'northeast', 'northwest', 'southeast', 'southwest',
            'up', 'down', 'in', 'out',
            'n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw', 'u', 'd',
        ]

        command_lower = command.lower().strip()

        # Expand abbreviations
        abbrev_map = {
            'n': 'north', 's': 'south', 'e': 'east', 'w': 'west',
            'ne': 'northeast', 'nw': 'northwest',
            'se': 'southeast', 'sw': 'southwest',
            'u': 'up', 'd': 'down',
        }

        # Check if command is just a direction
        if command_lower in directions:
            return abbrev_map.get(command_lower, command_lower)

        # Check for "go <direction>"
        if command_lower.startswith('go '):
            direction = command_lower[3:].strip()
            return abbrev_map.get(direction, direction) if direction in directions else direction

        # Check for direction anywhere in command
        for direction in directions:
            if direction in command_lower.split():
                return abbrev_map.get(direction, direction)

        return None

    def get_path(self, from_room: str, to_room: str) -> list[str]:
        """
        Find shortest path between rooms using Dijkstra's algorithm.

        Args:
            from_room: Source room ID
            to_room: Destination room ID

        Returns:
            List of direction commands, empty if no path exists
        """
        if from_room not in self.graph.nodes or to_room not in self.graph.nodes:
            return []

        # Create a filtered view of the graph without blocked edges
        def edge_filter(u, v):
            edge_data = self.graph.edges[u, v]
            return not edge_data.get('blocked', False)

        # Create subgraph without blocked edges
        filtered_graph = nx.subgraph_view(self.graph, filter_edge=edge_filter)

        try:
            # Find shortest path
            path = nx.shortest_path(
                filtered_graph,
                source=from_room,
                target=to_room,
            )

            # Convert room path to direction commands
            directions = []
            for i in range(len(path) - 1):
                edge_data = self.graph.edges[path[i], path[i + 1]]
                directions.append(edge_data['direction'])

            return directions
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def get_next_step(self, from_room: str, to_room: str) -> Optional[str]:
        """
        Get the next direction command to move toward a destination.

        Args:
            from_room: Source room ID
            to_room: Destination room ID

        Returns:
            Direction command or None if no path
        """
        path = self.get_path(from_room, to_room)
        return path[0] if path else None

    def get_unexplored_exits(
        self, room_id: Optional[str] = None
    ) -> list[tuple[str, str]]:
        """
        Get exits that have never been traversed.

        Args:
            room_id: Specific room to check, or None for all rooms

        Returns:
            List of (room_id, direction) tuples for unexplored exits
        """
        unexplored = []

        rooms_to_check = [room_id] if room_id else list(self.graph.nodes)

        for rid in rooms_to_check:
            if rid not in self.graph.nodes:
                continue

            exits = self.graph.nodes[rid].get('exits', {})
            for direction, destination in exits.items():
                if destination is None:  # Exit mentioned but never traversed
                    unexplored.append((rid, direction))

        return unexplored

    def get_nearest_unexplored(
        self, from_room: str
    ) -> Optional[tuple[str, list[str]]]:
        """
        Find the nearest room with unexplored exits using BFS.

        Args:
            from_room: Starting room ID

        Returns:
            Tuple of (room_id, path_directions) or None if none found
        """
        if from_room not in self.graph.nodes:
            return None

        # BFS to find nearest room with unexplored exits
        visited = {from_room}
        queue = deque([(from_room, [])])

        while queue:
            current, path = queue.popleft()

            # Check if this room has unexplored exits
            unexplored = self.get_unexplored_exits(current)
            if unexplored:
                return (current, path)

            # Explore neighbors
            for neighbor in self.graph.neighbors(current):
                edge_data = self.graph.edges[current, neighbor]
                if not edge_data.get('blocked', False) and neighbor not in visited:
                    visited.add(neighbor)
                    new_path = path + [edge_data['direction']]
                    queue.append((neighbor, new_path))

        return None

    def mark_blocked(
        self, from_room: str, direction: str, reason: str
    ) -> None:
        """
        Mark a path as blocked.

        Args:
            from_room: Source room ID
            direction: Direction command
            reason: Reason for blocking (e.g., "locked door")
        """
        # Find the edge with this direction
        for neighbor in self.graph.neighbors(from_room):
            edge_data = self.graph.edges[from_room, neighbor]
            if edge_data.get('direction') == direction:
                edge_data['blocked'] = True
                edge_data['block_reason'] = reason
                logger.info(f"Blocked path: {from_room} --{direction}--> {neighbor} ({reason})")
                break

    def unblock(self, from_room: str, direction: str) -> None:
        """
        Unblock a previously blocked path.

        Args:
            from_room: Source room ID
            direction: Direction command
        """
        # Find the edge with this direction
        for neighbor in self.graph.neighbors(from_room):
            edge_data = self.graph.edges[from_room, neighbor]
            if edge_data.get('direction') == direction:
                edge_data['blocked'] = False
                edge_data['block_reason'] = None
                logger.info(f"Unblocked path: {from_room} --{direction}--> {neighbor}")
                break

    def check_maze_condition(self, room_id: str, description: str) -> bool:
        """
        Check if a maze condition is detected using string similarity.

        Compares description against recent room descriptions. Triggers maze
        detection if 3+ rooms have 95%+ similar descriptions.

        Args:
            room_id: Current room ID
            description: Current room description

        Returns:
            True if maze detected and activated
        """
        if self.maze_active:
            return False  # Already in maze mode

        # Normalize description for comparison
        normalized = self._normalize_description(description)

        # Compare against recent descriptions
        similar_count = 0
        similar_rooms = []

        for other_id, other_desc in self._recent_descriptions:
            if other_id == room_id:
                continue

            other_normalized = self._normalize_description(other_desc)
            similarity = SequenceMatcher(None, normalized, other_normalized).ratio()

            if similarity >= self._similarity_threshold:
                similar_count += 1
                similar_rooms.append(other_id)

        # Trigger if we have 3+ similar rooms
        if similar_count >= 2:  # 2 others + current = 3 total
            logger.warning(f"Maze condition detected: {similar_count + 1} similar rooms")

            # Create maze group
            group_id = f"maze_{len(self._maze_groups) + 1}"
            self._maze_sequence_counter[group_id] = 0

            # Find entry room (last non-similar room)
            entry_room = None
            for rid, desc in reversed(self._recent_descriptions):
                norm_desc = self._normalize_description(desc)
                sim = SequenceMatcher(None, normalized, norm_desc).ratio()
                if sim < self._similarity_threshold:
                    entry_room = rid
                    break

            maze_group = MazeGroup(
                group_id=group_id,
                entry_room_id=entry_room or "unknown",
                room_ids=similar_rooms + [room_id],
                exit_room_ids=[],
                markers={},
                fully_mapped=False,
                created_turn=0,  # Will be set by orchestrator
            )

            self._maze_groups[group_id] = maze_group
            self._active_maze = maze_group
            self.maze_active = True

            # Update maze_group attribute on rooms
            for rid in maze_group.room_ids:
                if rid in self.graph.nodes:
                    self.graph.nodes[rid]['maze_group'] = group_id

            return True

        return False

    def _normalize_description(self, description: str) -> str:
        """
        Normalize a description for similarity comparison.

        Args:
            description: Room description text

        Returns:
            Normalized text
        """
        # Lowercase
        normalized = description.lower()

        # Collapse whitespace
        normalized = ' '.join(normalized.split())

        # Remove punctuation
        normalized = re.sub(r'[^\w\s]', '', normalized)

        return normalized

    def is_maze_active(self) -> bool:
        """
        Check if maze-solving mode is currently active.

        Returns:
            True if in maze mode
        """
        return self.maze_active

    def get_active_maze(self) -> Optional[MazeGroup]:
        """
        Get the currently active maze group.

        Returns:
            MazeGroup object or None
        """
        return self._active_maze

    def assign_maze_marker(self, room_id: str, item_id: str) -> None:
        """
        Record which marker item was dropped in a maze room.

        Args:
            room_id: Maze room ID
            item_id: Item used as marker
        """
        if not self._active_maze:
            logger.warning("Cannot assign marker: no active maze")
            return

        self._active_maze.markers[room_id] = item_id

        # Update room node
        if room_id in self.graph.nodes:
            self.graph.nodes[room_id]['maze_marker_item'] = item_id

        logger.debug(f"Assigned marker {item_id} to maze room {room_id}")

    def identify_maze_room_by_marker(self, item_id: str) -> Optional[str]:
        """
        Find which maze room contains a marker item.

        Args:
            item_id: Marker item ID

        Returns:
            Room ID or None
        """
        if not self._active_maze:
            return None

        for room_id, marker in self._active_maze.markers.items():
            if marker == item_id:
                return room_id

        return None

    def get_maze_rooms(self, group_id: str) -> list[str]:
        """
        Get all room IDs in a maze group.

        Args:
            group_id: Maze group identifier

        Returns:
            List of room IDs
        """
        if group_id not in self._maze_groups:
            return []
        return self._maze_groups[group_id].room_ids.copy()

    def complete_maze(self, group_id: str) -> None:
        """
        Mark a maze as fully mapped and deactivate maze mode.

        Args:
            group_id: Maze group identifier
        """
        if group_id not in self._maze_groups:
            logger.warning(f"Cannot complete unknown maze: {group_id}")
            return

        maze_group = self._maze_groups[group_id]
        maze_group.fully_mapped = True
        maze_group.completed_turn = 0  # Will be set by orchestrator

        # Clear active maze if this is the active one
        if self._active_maze and self._active_maze.group_id == group_id:
            self.maze_active = False
            self._active_maze = None

        logger.info(f"Maze {group_id} marked as complete")

    def get_map_summary(self) -> dict:
        """
        Get a compact map summary for agent context.

        Returns:
            Dict with rooms_visited, rooms_total, unexplored_exits_count, current_room
        """
        visited_count = sum(
            1 for node_id in self.graph.nodes
            if self.graph.nodes[node_id].get('visited', False)
        )

        total_count = self.graph.number_of_nodes()
        unexplored = len(self.get_unexplored_exits())

        current_room = self.current_room_id or "unknown"

        return {
            "rooms_visited": visited_count,
            "rooms_total": total_count,
            "unexplored_exits_count": unexplored,
            "current_room": current_room,
        }

    def get_last_metrics(self) -> Optional[LLMMetric]:
        """
        Get metrics from the last LLM call.

        Returns:
            LLMMetric object or None
        """
        return self._last_metrics

    def to_dict(self) -> dict:
        """
        Serialize the entire map state to a dictionary.

        Returns:
            Dict representation of the map
        """
        return {
            "current_room_id": self.current_room_id,
            "maze_active": self.maze_active,
            "nodes": [
                {
                    "room_id": node_id,
                    **self.graph.nodes[node_id],
                }
                for node_id in self.graph.nodes
            ],
            "edges": [
                {
                    "from": u,
                    "to": v,
                    **data,
                }
                for u, v, data in self.graph.edges(data=True)
            ],
            "maze_groups": {
                group_id: {
                    "group_id": group.group_id,
                    "entry_room_id": group.entry_room_id,
                    "room_ids": group.room_ids,
                    "exit_room_ids": group.exit_room_ids,
                    "markers": group.markers,
                    "fully_mapped": group.fully_mapped,
                    "created_turn": group.created_turn,
                    "completed_turn": group.completed_turn,
                }
                for group_id, group in self._maze_groups.items()
            },
        }

    def load_from_db(self) -> None:
        """
        Load map state from the database.

        Reconstructs the graph from stored rooms, connections, and maze groups.
        """
        # TODO: request from storage agent - specific load methods
        # For now, this is a placeholder
        logger.info(f"Loading map state from database for game {self.game_id}")
        # The database methods should be called here to populate:
        # - self.graph nodes and edges
        # - self.current_room_id
        # - self._maze_groups
        # - self._active_maze if a maze is in progress
