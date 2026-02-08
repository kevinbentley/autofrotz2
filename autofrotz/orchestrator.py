"""
Orchestrator for AutoFrotz v2.

Central game loop that coordinates all agents, managers, and hooks.
Implements the 9-phase turn sequence, maze-solving mode, death recovery,
periodic saves, and crash-resumable state.
"""

import json
import logging
from datetime import datetime

from autofrotz.agents.game_agent import GameAgent
from autofrotz.agents.puzzle_agent import PuzzleAgent
from autofrotz.game_interface import GameInterface
from autofrotz.hooks.base import BaseHook
from autofrotz.llm.factory import create_llm
from autofrotz.managers.item_manager import ItemManager
from autofrotz.managers.map_manager import MapManager
from autofrotz.storage.database import Database
from autofrotz.storage.models import (
    LLMMetric,
    MazeGroup,
    Puzzle,
    PuzzleSuggestion,
    TurnRecord,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Central game loop coordinator.

    Manages the turn-by-turn game progression, agent invocations,
    maze-solving mode, save/restore, death recovery, and hook notifications.
    """

    # Number of rotating save slots
    SAVE_SLOTS = 3

    # Save every N turns
    SAVE_INTERVAL = 25

    # Puzzle agent evaluation frequency (every N turns unless triggered)
    PUZZLE_EVAL_INTERVAL = 1  # Run every turn; gpt-4o-mini is cheap and stale puzzles hurt

    def __init__(self, config: dict) -> None:
        """
        Initialize the orchestrator from configuration.

        Creates the database, game interface, LLM instances, managers,
        agents, and hooks.

        Args:
            config: Full configuration dictionary from config.json.
        """
        self.config = config
        self.max_turns = config.get("max_turns", 1000)
        self.save_on_death = config.get("save_on_death", True)

        # Database
        db_path = config.get("database_path", "autofrotz.db")
        self.database = Database(db_path)

        # Game interface
        game_file = config["game_file"]
        self.game_interface = GameInterface(game_file)
        self.game_file = game_file

        # LLM instances for each agent
        self.game_agent_llm = create_llm("game_agent", config)
        self.puzzle_agent_llm = create_llm("puzzle_agent", config)
        map_parser_llm = create_llm("map_parser", config)
        item_parser_llm = create_llm("item_parser", config)

        # Game session -- check for crash recovery first
        self.game_id: int = 0
        self.resuming = False
        self._try_resume(game_file)

        if not self.resuming:
            self.game_id = self.database.create_game(game_file)

        # Managers (load from DB if resuming)
        self.map_manager = MapManager(map_parser_llm, self.database, self.game_id)
        self.item_manager = ItemManager(item_parser_llm, self.database, self.game_id)

        # Agents
        self.game_agent = GameAgent(self.game_agent_llm)
        self.puzzle_agent = PuzzleAgent(
            self.puzzle_agent_llm, self.database, self.game_id
        )

        # Hooks
        self._hooks: list[BaseHook] = []

        # State tracking
        self._turn_number = 0
        self._recent_actions: list[tuple[str, str]] = []
        self._recent_rooms: list[str] = []
        self._special_instructions = ""
        self._last_save_slot = 0
        self._last_save_turn = 0
        self._previous_room_id: str | None = None
        self._previous_inventory_count = 0
        self._last_action_failed = False
        self._last_command = "look"  # Command that produced the current game_output

        # Maze-solving state
        self._maze_dfs_stack: list[tuple[str, list[str]]] = []
        self._maze_visited_rooms: set[str] = set()
        self._maze_marker_index = 0
        self._maze_phase: str = "idle"  # idle, exploring, backtracking, retrieving

        # If resuming, advance state from DB
        if self.resuming:
            self._restore_state_from_db()

        logger.info(
            f"Orchestrator initialized: game_id={self.game_id}, "
            f"max_turns={self.max_turns}, resuming={self.resuming}"
        )

    def _try_resume(self, game_file: str) -> None:
        """
        Check for an active game session to resume after a crash.

        Args:
            game_file: Current game file path for matching.
        """
        active = self.database.get_active_game()
        if active:
            active_id, active_file = active
            if active_file == game_file:
                self.game_id = active_id
                self.resuming = True
                logger.info(f"Resuming game session {active_id}")
            else:
                logger.info(
                    f"Found active game {active_id} for different file "
                    f"({active_file}), starting new session"
                )

    def _restore_state_from_db(self) -> None:
        """Restore orchestrator state from the database after a crash."""
        latest_turn = self.database.get_latest_turn(self.game_id)
        if latest_turn:
            self._turn_number = latest_turn.turn_number
            logger.info(f"Resuming from turn {self._turn_number}")

            # Rebuild recent actions from last 10 turns
            turns = self.database.get_turns(self.game_id)
            for turn in turns[-10:]:
                self._recent_actions.append(
                    (turn.command_sent, turn.game_output)
                )
                if turn.room_id:
                    self._recent_rooms.append(turn.room_id)

            # Attempt to restore game state from save file
            latest_slot = self._last_save_slot
            for slot in range(self.SAVE_SLOTS):
                filename = f"save_slot_{slot}.qzl"
                if self.game_interface.restore(filename):
                    logger.info(f"Restored game state from {filename}")
                    break

    def register_hook(self, hook: BaseHook) -> None:
        """
        Register a hook to receive game events.

        Args:
            hook: Hook instance to register.
        """
        self._hooks.append(hook)
        logger.info(f"Registered hook: {hook.__class__.__name__}")

    def run(self) -> None:
        """
        Run the main game loop.

        Processes the intro text, then loops through turns until
        the game ends (victory, death without recovery, or max turns).
        """
        logger.info(f"Starting game: {self.game_file}")

        # Fire game start hooks
        self._fire_hooks("on_game_start", game_id=self.game_id, game_file=self.game_file)

        # Get intro text as the first game output
        if not self.resuming:
            game_output = self.game_interface.get_intro()
            logger.info(f"Intro: {game_output[:200]}...")
        else:
            # When resuming, do a "look" to get current room state
            game_output = self.game_interface.do_command("look")
            self._turn_number += 1  # Skip ahead since we already have turns

        # Main turn loop
        try:
            while self._turn_number < self.max_turns:
                self._turn_number += 1

                # Check if we should be in maze mode
                if self.map_manager.is_maze_active():
                    game_output = self._maze_turn(self._turn_number, game_output)
                else:
                    game_output = self._normal_turn(self._turn_number, game_output)

                # Check for terminal state
                terminal = self.game_interface.detect_terminal_state(game_output)
                if terminal == "death":
                    if self.save_on_death:
                        game_output = self._handle_death(self._turn_number)
                        if game_output is None:
                            # Could not restore -- game over
                            self._end_game("lost")
                            return
                        continue
                    else:
                        self._end_game("lost")
                        return
                elif terminal == "victory":
                    self._end_game("won")
                    return

                # Periodic save (disabled -- pyFrotz save corrupts game state)
                # if (self._turn_number - self._last_save_turn) >= self.SAVE_INTERVAL:
                #     try:
                #         self._save_game(self._next_save_slot())
                #     except Exception as e:
                #         logger.warning(f"Periodic save failed (non-fatal): {e}")
                #         self._last_save_turn = self._turn_number

            # Max turns reached
            logger.warning(f"Max turns ({self.max_turns}) reached")
            self._end_game("abandoned")

        except KeyboardInterrupt:
            logger.info("Game interrupted by user")
            self._end_game("abandoned")
        except Exception as e:
            logger.error(f"Game loop error: {e}", exc_info=True)
            self._end_game("abandoned")

    def _normal_turn(self, turn_number: int, game_output: str) -> str:
        """
        Execute a single normal turn using the 9-phase sequence.

        Phase 1: Parse map from game output
        Phase 2: Parse items from game output
        Phase 3: Check maze condition
        Phase 4: Evaluate puzzle agent (throttled)
        Phase 5: Assemble context for game agent
        Phase 6: Game agent decides action
        Phase 7: Execute command
        Phase 8: Log turn to database
        Phase 9: Fire hooks

        Args:
            turn_number: Current turn number.
            game_output: Game output from the previous command.

        Returns:
            Game output from this turn's command execution.
        """
        current_room_id = self.map_manager.current_room_id or "unknown"

        # Fire turn start hooks
        self._fire_hooks("on_turn_start", turn_number=turn_number, room_id=current_room_id)

        # Phase 1: Parse map
        room_update = self.map_manager.update_from_game_output(game_output, self._last_command)
        self._collect_manager_metrics(turn_number, "map_parser", self.map_manager.get_last_metrics())

        # Track room changes for puzzle trigger detection
        new_room_entered = room_update.new_room
        if room_update.room_id:
            current_room_id = room_update.room_id
            self._recent_rooms.append(current_room_id)
            # Keep recent rooms bounded
            if len(self._recent_rooms) > 50:
                self._recent_rooms = self._recent_rooms[-50:]

        # Fire room entry hook
        if room_update.room_changed and room_update.room_id:
            self._fire_hooks(
                "on_room_enter",
                room_id=room_update.room_id,
                room_name=room_update.room_name or "",
                description=room_update.description or "",
                is_new=room_update.new_room,
            )

        # Phase 2: Parse items
        item_updates = self.item_manager.update_from_game_output(
            game_output, current_room_id, self._last_command, current_turn=turn_number
        )
        self._collect_manager_metrics(turn_number, "item_parser", self.item_manager.get_last_metrics())

        # Track inventory changes for puzzle trigger detection
        current_inventory = self.item_manager.get_inventory()
        inventory_changed = len(current_inventory) != self._previous_inventory_count
        self._previous_inventory_count = len(current_inventory)

        # Fire item hooks
        for update in item_updates:
            if update.change_type == "new":
                self._fire_hooks(
                    "on_item_found",
                    item_id=update.item_id,
                    item_name=update.name,
                    room_id=current_room_id,
                )
            elif update.change_type == "taken":
                self._fire_hooks(
                    "on_item_taken",
                    item_id=update.item_id,
                    item_name=update.name,
                )

        # Phase 3: Check maze condition
        if room_update.room_changed and room_update.description and room_update.room_id:
            maze_detected = self.map_manager.check_maze_condition(
                room_update.room_id, room_update.description
            )
            if maze_detected:
                maze = self.map_manager.get_active_maze()
                if maze:
                    self._fire_hooks(
                        "on_maze_detected",
                        maze_group_id=maze.group_id,
                        entry_room_id=maze.entry_room_id,
                        suspected_room_count=len(maze.room_ids),
                    )
                    # Initialize maze solver state
                    self._init_maze_solver(maze)
                    # The next turn will be handled by _maze_turn
                    # For now, fall through to let the game agent handle this turn

        # Phase 4: Puzzle evaluation (throttled)
        suggestions: list[PuzzleSuggestion] = []
        new_puzzles: list[Puzzle] = []

        should_evaluate = (
            turn_number % self.PUZZLE_EVAL_INTERVAL == 0
            or new_room_entered
            or inventory_changed
            or self._last_action_failed
        )

        if should_evaluate:
            current_room = self.map_manager.get_current_room()
            all_items = self.item_manager.get_all_items()
            map_summary = self.map_manager.get_map_summary()

            new_puzzles, suggestions, solved_ids = self.puzzle_agent.evaluate(
                game_output=game_output,
                current_room=current_room,
                inventory=current_inventory,
                all_items=all_items,
                map_summary=map_summary,
                recent_actions=self._recent_actions,
                current_turn=turn_number,
            )
            self._collect_manager_metrics(
                turn_number, "puzzle_agent",
                self.puzzle_agent.get_last_metrics(),
            )

            # Fire puzzle hooks
            for puzzle in new_puzzles:
                self._fire_hooks(
                    "on_puzzle_found",
                    puzzle_id=puzzle.puzzle_id or 0,
                    description=puzzle.description,
                )

            # Fire solved puzzle hooks
            for pid in solved_ids:
                # Look up description for the hook
                all_puzzles = self.database.get_puzzles(self.game_id)
                desc = next(
                    (p.description for p in all_puzzles if p.puzzle_id == pid),
                    f"Puzzle #{pid}",
                )
                self._fire_hooks(
                    "on_puzzle_solved",
                    puzzle_id=pid,
                    description=desc,
                )

        # Check for stuck behavior (every turn, no LLM call)
        stuck_suggestion = self.puzzle_agent.detect_stuck(
            self._recent_actions, self._recent_rooms
        )
        if stuck_suggestion:
            self._special_instructions += f"\n{stuck_suggestion}"

        # Phase 5: Assemble context
        context = self._assemble_context(game_output, suggestions)

        # Phase 6: Game agent decides
        command, reasoning = self.game_agent.decide_action(context)
        self._collect_manager_metrics(
            turn_number, "game_agent",
            self.game_agent.get_last_metrics(),
        )

        # Phase 7: Execute command
        new_output = self.game_interface.do_command(command)
        self._last_command = command

        # Track if the action appeared to fail (for puzzle eval triggers)
        self._last_action_failed = self._is_failure_output(new_output)

        # Update recent actions
        self._recent_actions.append((command, new_output))
        if len(self._recent_actions) > 20:
            self._recent_actions = self._recent_actions[-20:]

        # Clear special instructions after they have been delivered
        self._special_instructions = ""

        # Phase 8: Log turn
        self._log_turn(turn_number, command, new_output, current_room_id, reasoning)

        # Phase 9: Fire hooks
        self._fire_hooks(
            "on_turn_end",
            turn_number=turn_number,
            command=command,
            output=new_output,
            room_id=current_room_id,
        )

        logger.info(
            f"Turn {turn_number}: '{command}' in {current_room_id} "
            f"-> {new_output[:80]}..."
        )

        return new_output

    def _maze_turn(self, turn_number: int, game_output: str) -> str:
        """
        Execute a maze-solving turn using algorithmic DFS.

        Bypasses the game agent entirely. Issues commands to drop markers,
        explore exits, and map the maze systematically.

        Args:
            turn_number: Current turn number.
            game_output: Game output from the previous command.

        Returns:
            Game output from this turn's command execution.
        """
        maze = self.map_manager.get_active_maze()
        if not maze:
            logger.error("Maze turn called but no active maze")
            return game_output

        current_room_id = self.map_manager.current_room_id or "unknown"

        # Fire turn start hooks
        self._fire_hooks("on_turn_start", turn_number=turn_number, room_id=current_room_id)

        # Phase 1: Update map from last output
        room_update = self.map_manager.update_from_game_output(game_output, self._last_command)
        self._collect_manager_metrics(turn_number, "map_parser", self.map_manager.get_last_metrics())

        if room_update.room_id:
            current_room_id = room_update.room_id

        # Phase 2: Update items
        self.item_manager.update_from_game_output(
            game_output, current_room_id, self._last_command, current_turn=turn_number
        )
        self._collect_manager_metrics(turn_number, "item_parser", self.item_manager.get_last_metrics())

        # Determine next maze action
        command = self._next_maze_command(maze, current_room_id, game_output)

        # Execute
        new_output = self.game_interface.do_command(command)
        self._last_command = command

        # Update recent actions
        self._recent_actions.append((command, new_output))
        if len(self._recent_actions) > 20:
            self._recent_actions = self._recent_actions[-20:]

        # Log turn
        self._log_turn(
            turn_number, command, new_output, current_room_id,
            f"Maze mode ({self._maze_phase}): {command}"
        )

        # Fire hooks
        self._fire_hooks(
            "on_turn_end",
            turn_number=turn_number,
            command=command,
            output=new_output,
            room_id=current_room_id,
        )

        # Check if maze is complete
        if not self.map_manager.is_maze_active():
            logger.info(f"Maze {maze.group_id} solved!")
            self._fire_hooks(
                "on_maze_completed",
                maze_group_id=maze.group_id,
                total_rooms=len(maze.room_ids),
                total_exits=len(maze.exit_room_ids),
            )

        logger.info(
            f"Turn {turn_number} [MAZE]: '{command}' in {current_room_id}"
        )

        return new_output

    def _init_maze_solver(self, maze: MazeGroup) -> None:
        """
        Initialize the maze-solving DFS state.

        Args:
            maze: The detected maze group.
        """
        self._maze_phase = "exploring"
        self._maze_visited_rooms = set()
        self._maze_dfs_stack = []
        self._maze_marker_index = 0
        logger.info(f"Initialized maze solver for {maze.group_id}")

    def _next_maze_command(
        self, maze: MazeGroup, current_room_id: str, game_output: str
    ) -> str:
        """
        Determine the next command in the maze-solving DFS protocol.

        Implements the item-dropping marker strategy:
        1. If current room has no marker, drop one
        2. Try unexplored exits
        3. Backtrack when all exits explored
        4. Complete when fully mapped

        Args:
            maze: Active maze group.
            current_room_id: Current room ID.
            game_output: Latest game output.

        Returns:
            Next command to issue.
        """
        # Check if current room already has a marker
        current_room = self.map_manager.get_room(current_room_id)

        if self._maze_phase == "exploring":
            # Step 1: Mark current room if unmarked
            if current_room_id not in self._maze_visited_rooms:
                self._maze_visited_rooms.add(current_room_id)

                # Check if room already has a marker from a previous visit
                if current_room and not current_room.maze_marker_item:
                    # Get a droppable item
                    droppable = self.item_manager.get_droppable_items()
                    if droppable and self._maze_marker_index < len(droppable):
                        marker_item = droppable[self._maze_marker_index]
                        self._maze_marker_index += 1
                        # Record the marker assignment
                        self.map_manager.assign_maze_marker(
                            current_room_id, marker_item.item_id
                        )
                        self._fire_hooks(
                            "on_maze_room_marked",
                            maze_group_id=maze.group_id,
                            room_id=current_room_id,
                            marker_item_id=marker_item.item_id,
                        )
                        return f"drop {marker_item.name}"

                # Get unexplored exits for this room
                unexplored = self.map_manager.get_unexplored_exits(current_room_id)
                if unexplored:
                    # Push remaining exits onto DFS stack
                    exits_to_try = [direction for _, direction in unexplored]
                    self._maze_dfs_stack.append((current_room_id, exits_to_try))

            # Step 2: Try next unexplored exit
            while self._maze_dfs_stack:
                room_id, exits = self._maze_dfs_stack[-1]
                if exits:
                    next_exit = exits.pop(0)
                    if not exits:
                        self._maze_dfs_stack.pop()
                    return next_exit
                else:
                    self._maze_dfs_stack.pop()

            # Step 3: All exits explored -- check if maze is complete
            # Do a "look" to verify position
            all_explored = True
            for room_id in maze.room_ids:
                unexplored = self.map_manager.get_unexplored_exits(room_id)
                if unexplored:
                    all_explored = False
                    break

            if all_explored:
                self._maze_phase = "retrieving"
                self.map_manager.complete_maze(maze.group_id)
                return "look"
            else:
                # Navigate to a room with unexplored exits
                nearest = self.map_manager.get_nearest_unexplored(current_room_id)
                if nearest:
                    target_room, path = nearest
                    if path:
                        return path[0]
                # Fallback
                return "look"

        elif self._maze_phase == "retrieving":
            # Phase 4: Pick up markers
            # Check if there are items here to pick up
            room_items = self.item_manager.get_items_in_room(current_room_id)
            for item in room_items:
                if item.item_id in maze.markers.values():
                    return f"take {item.name}"

            # Navigate to next room with a marker
            for room_id, item_id in maze.markers.items():
                item = self.item_manager.get_item(item_id)
                if item and item.location == room_id:
                    path = self.map_manager.get_path(current_room_id, room_id)
                    if path:
                        return path[0]

            # All markers retrieved or unreachable
            self._maze_phase = "idle"
            return "look"

        # Fallback
        return "look"

    def _assemble_context(
        self, game_output: str, suggestions: list[PuzzleSuggestion]
    ) -> dict:
        """
        Build the context dictionary for the game agent.

        Args:
            game_output: Latest game output.
            suggestions: Puzzle suggestions for this turn.

        Returns:
            Context dictionary.
        """
        current_room = self.map_manager.get_current_room()
        inventory = self.item_manager.get_inventory()

        room_id = current_room.room_id if current_room else "unknown"
        room_items = self.item_manager.get_items_in_room(room_id)

        map_summary = self.map_manager.get_map_summary()

        open_puzzles = self.database.get_puzzles(self.game_id, status="open")
        in_progress = self.database.get_puzzles(self.game_id, status="in_progress")
        all_open = open_puzzles + in_progress

        # Compute navigation directions for puzzle locations
        navigation_hints = {}
        for puzzle in all_open:
            if puzzle.location and puzzle.location != room_id:
                try:
                    path = self.map_manager.get_path(room_id, puzzle.location)
                    if path:
                        navigation_hints[puzzle.location] = path
                except Exception:
                    pass  # No path found

        # Get nearest unexplored exit info
        nearest_unexplored = None
        try:
            nearest = self.map_manager.get_nearest_unexplored(room_id)
            if nearest:
                target_room, path = nearest
                nearest_unexplored = {
                    "target_room": target_room,
                    "path": path,
                }
        except Exception:
            pass

        return {
            "game_output": game_output,
            "room": current_room,
            "inventory": inventory,
            "room_items": room_items,
            "map_summary": map_summary,
            "open_puzzles": all_open,
            "puzzle_suggestions": suggestions,
            "recent_actions": self._recent_actions[-20:],
            "special_instructions": self._special_instructions,
            "navigation_hints": navigation_hints,
            "nearest_unexplored": nearest_unexplored,
        }

    def _fire_hooks(self, method_name: str, **kwargs) -> None:
        """
        Call a hook method on all registered hooks.

        Each call is wrapped in try/except so a broken hook never
        crashes the game.

        Args:
            method_name: Name of the hook method to call.
            **kwargs: Arguments to pass to the hook method.
        """
        for hook in self._hooks:
            try:
                method = getattr(hook, method_name, None)
                if method:
                    method(**kwargs)
            except Exception as e:
                logger.error(
                    f"Hook {hook.__class__.__name__}.{method_name} failed: {e}",
                    exc_info=True,
                )

    def _save_game(self, slot: int) -> None:
        """
        Save the game state to a numbered slot.

        Args:
            slot: Save slot number (0 to SAVE_SLOTS-1).
        """
        filename = f"save_slot_{slot}.qzl"
        if self.game_interface.save(filename):
            self._last_save_turn = self._turn_number
            self._last_save_slot = slot
            logger.info(f"Game saved to slot {slot} at turn {self._turn_number}")

    def _restore_game(self, slot: int) -> bool:
        """
        Restore game state from a numbered slot.

        Args:
            slot: Save slot number.

        Returns:
            True if restore succeeded.
        """
        filename = f"save_slot_{slot}.qzl"
        return self.game_interface.restore(filename)

    def _next_save_slot(self) -> int:
        """Get the next rotating save slot number."""
        return (self._last_save_slot + 1) % self.SAVE_SLOTS

    def _handle_death(self, turn_number: int) -> str | None:
        """
        Handle player death by restoring from the latest save.

        Args:
            turn_number: Turn where death occurred.

        Returns:
            Game output after restoration, or None if restore failed.
        """
        logger.warning(f"Player died at turn {turn_number}, attempting restore")

        # Try restore from most recent save slot, then try older ones
        for i in range(self.SAVE_SLOTS):
            slot = (self._last_save_slot - i) % self.SAVE_SLOTS
            if self._restore_game(slot):
                # Get the restored game state
                output = self.game_interface.do_command("look")

                # Determine how many turns we lost
                turns_lost = turn_number - self._last_save_turn

                # Set special instructions for the game agent
                last_cmd = self._recent_actions[-1][0] if self._recent_actions else "unknown action"
                self._special_instructions = (
                    f"WARNING: You died attempting '{last_cmd}'. "
                    f"Game restored to {turns_lost} turns ago. "
                    f"Do NOT repeat the same approach that led to death. "
                    f"Try a completely different strategy."
                )

                logger.info(f"Restored from slot {slot}, lost {turns_lost} turns")
                return output

        logger.error("All save slots failed, cannot recover from death")
        return None

    def _end_game(self, status: str) -> None:
        """
        End the game session.

        Args:
            status: Final status (won, lost, abandoned).
        """
        self.database.end_game(self.game_id, status, self._turn_number)
        self._fire_hooks(
            "on_game_end",
            game_id=self.game_id,
            status=status,
            total_turns=self._turn_number,
        )
        self.game_interface.quit()
        logger.info(f"Game {self.game_id} ended: {status} after {self._turn_number} turns")

    def _log_turn(
        self,
        turn_number: int,
        command: str,
        output: str,
        room_id: str,
        reasoning: str,
    ) -> None:
        """
        Save a turn record to the database.

        Args:
            turn_number: Turn number.
            command: Command sent.
            output: Game output received.
            room_id: Current room ID.
            reasoning: Agent reasoning for the action.
        """
        inventory = self.item_manager.get_inventory()
        inventory_ids = [item.item_id for item in inventory]

        turn_record = TurnRecord(
            game_id=self.game_id,
            turn_number=turn_number,
            timestamp=datetime.utcnow().isoformat(),
            command_sent=command,
            game_output=output,
            room_id=room_id,
            inventory_snapshot=inventory_ids,
            agent_reasoning=reasoning,
        )

        try:
            self.database.save_turn(turn_record)
        except Exception as e:
            logger.error(f"Failed to save turn {turn_number}: {e}")

    def _collect_manager_metrics(
        self, turn_number: int, agent_name: str, metric: LLMMetric | None
    ) -> None:
        """
        Save LLM metrics from a manager or agent call to the database.

        Args:
            turn_number: Current turn number.
            agent_name: Name of the agent/parser.
            metric: LLMMetric from the last call, or None.
        """
        if metric is None:
            return

        metric.game_id = self.game_id
        metric.turn_number = turn_number
        metric.agent_name = agent_name

        try:
            self.database.save_metric(metric)
        except Exception as e:
            logger.error(f"Failed to save metric for {agent_name}: {e}")

    def _is_failure_output(self, output: str) -> bool:
        """
        Check if game output indicates a failed action.

        Args:
            output: Game output text.

        Returns:
            True if the output looks like a failure response.
        """
        failure_indicators = [
            "you can't",
            "you cannot",
            "that's not something",
            "i don't understand",
            "i don't know",
            "nothing happens",
            "that doesn't work",
            "you don't see",
            "there is no",
            "you're not holding",
            "you can't see",
            "that's hardly",
            "you don't have",
            "i beg your pardon",
        ]

        output_lower = output.lower()
        return any(indicator in output_lower for indicator in failure_indicators)
