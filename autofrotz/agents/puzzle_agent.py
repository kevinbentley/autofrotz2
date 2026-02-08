"""
Puzzle agent for AutoFrotz v2.

Secondary strategic agent that detects puzzles in game output, cross-references
them with available items to suggest solutions, and monitors for stuck behavior.
"""

import json
import logging
from collections import Counter
from pathlib import Path

from autofrotz.llm.base import BaseLLM
from autofrotz.storage.database import Database
from autofrotz.storage.models import (
    Item,
    LLMMetric,
    LLMResponse,
    Puzzle,
    PuzzleSuggestion,
    Room,
)

logger = logging.getLogger(__name__)


class PuzzleAgent:
    """
    Strategic puzzle detection and suggestion agent.

    Detects new puzzles from game output, cross-references open puzzles
    with inventory/items to generate actionable suggestions, and
    algorithmically detects stuck behavior.
    """

    def __init__(
        self,
        llm: BaseLLM,
        database: Database,
        game_id: int,
        prompt_path: str = "autofrotz/prompts/puzzle_agent.txt",
    ) -> None:
        """
        Initialize the puzzle agent.

        Args:
            llm: LLM instance for puzzle reasoning.
            database: Database instance for puzzle persistence.
            game_id: Current game session ID.
            prompt_path: Path to the system prompt file.
        """
        self.llm = llm
        self.database = database
        self.game_id = game_id
        self._last_response: LLMResponse | None = None

        # Load system prompt from file
        try:
            self._system_prompt = Path(prompt_path).read_text()
            logger.info(f"Puzzle agent system prompt loaded from {prompt_path}")
        except FileNotFoundError:
            logger.error(f"Puzzle agent prompt not found at {prompt_path}")
            self._system_prompt = (
                "You are a puzzle analyst for a text adventure game. "
                "Detect new puzzles and suggest solutions. "
                "Return JSON with new_puzzles and suggestions arrays."
            )

    def evaluate(
        self,
        game_output: str,
        current_room: Room | None,
        inventory: list[Item],
        all_items: list[Item],
        map_summary: dict,
        recent_actions: list[tuple[str, str]],
        current_turn: int,
    ) -> tuple[list[Puzzle], list[PuzzleSuggestion]]:
        """
        Detect new puzzles and generate solution suggestions.

        Performs both puzzle detection and cross-referencing in a single
        LLM call for efficiency.

        Args:
            game_output: Latest game output text.
            current_room: Current room object (may be None early in game).
            inventory: Items currently in inventory.
            all_items: All known items in the game.
            map_summary: Map exploration statistics.
            recent_actions: Recent (command, output) pairs.
            current_turn: Current turn number.

        Returns:
            Tuple of (new_puzzles, suggestions).
        """
        # Build context message for the puzzle agent
        user_message = self._build_evaluation_message(
            game_output, current_room, inventory, all_items,
            map_summary, recent_actions,
        )

        messages = [{"role": "user", "content": user_message}]

        # JSON schema for structured output
        schema = {
            "type": "object",
            "properties": {
                "new_puzzles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "location": {"type": "string"},
                            "related_items": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["description", "location"],
                    },
                },
                "suggestions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "puzzle_id": {"type": "integer"},
                            "description": {"type": "string"},
                            "proposed_action": {"type": "string"},
                            "items_to_use": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": ["puzzle_id", "description", "proposed_action", "confidence"],
                    },
                },
            },
            "required": ["new_puzzles", "suggestions"],
        }

        try:
            result = self.llm.complete_json(
                messages=messages,
                system_prompt=self._system_prompt,
                schema=schema,
                temperature=0.5,
                max_tokens=1024,
            )

            # Track metrics via a follow-up complete call's response data
            # Since complete_json returns dict, we create a metric placeholder
            self._last_response = LLMResponse(
                text=json.dumps(result),
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                cost_estimate=0.0,
                latency_ms=0.0,
            )

            # Process new puzzles
            new_puzzles = []
            for puzzle_data in result.get("new_puzzles", []):
                room_id = current_room.room_id if current_room else "unknown"
                puzzle = Puzzle(
                    description=puzzle_data["description"],
                    status="open",
                    location=puzzle_data.get("location", room_id),
                    related_items=puzzle_data.get("related_items", []),
                    attempts=[],
                    created_turn=current_turn,
                )
                # Save to database and get the assigned puzzle_id
                puzzle_id = self.database.save_puzzle(self.game_id, puzzle)
                puzzle.puzzle_id = puzzle_id
                new_puzzles.append(puzzle)
                logger.info(f"New puzzle detected: {puzzle.description} (id={puzzle_id})")

            # Process suggestions
            suggestions = []
            for sugg_data in result.get("suggestions", []):
                suggestion = PuzzleSuggestion(
                    puzzle_id=sugg_data["puzzle_id"],
                    description=sugg_data["description"],
                    proposed_action=sugg_data["proposed_action"],
                    items_to_use=sugg_data.get("items_to_use", []),
                    confidence=sugg_data.get("confidence", "medium"),
                )
                suggestions.append(suggestion)
                logger.debug(
                    f"Puzzle suggestion [{suggestion.confidence}]: "
                    f"{suggestion.proposed_action}"
                )

            return new_puzzles, suggestions

        except Exception as e:
            logger.error(f"Puzzle agent evaluation failed: {e}")
            self._last_response = None
            return [], []

    def _build_evaluation_message(
        self,
        game_output: str,
        current_room: Room | None,
        inventory: list[Item],
        all_items: list[Item],
        map_summary: dict,
        recent_actions: list[tuple[str, str]],
    ) -> str:
        """
        Build the context message for puzzle evaluation.

        Args:
            game_output: Latest game output.
            current_room: Current room.
            inventory: Inventory items.
            all_items: All known items.
            map_summary: Map stats.
            recent_actions: Recent action history.

        Returns:
            Formatted context string.
        """
        parts = []

        # Game output
        parts.append(f"== LATEST GAME OUTPUT ==\n{game_output}\n")

        # Current room
        if current_room:
            parts.append(
                f"== CURRENT ROOM ==\n"
                f"ID: {current_room.room_id}\n"
                f"Name: {current_room.name}\n"
                f"Description: {current_room.description}\n"
            )

        # Inventory
        if inventory:
            inv_lines = [f"- {item.name} ({item.item_id})" for item in inventory]
            parts.append(f"== INVENTORY ==\n" + "\n".join(inv_lines) + "\n")
        else:
            parts.append("== INVENTORY ==\nEmpty\n")

        # All known items (excluding inventory for brevity)
        non_inv_items = [i for i in all_items if i.location != "inventory"]
        if non_inv_items:
            item_lines = [
                f"- {item.name} ({item.item_id}) at {item.location}"
                for item in non_inv_items[:30]  # Limit to avoid token bloat
            ]
            parts.append(f"== KNOWN ITEMS ==\n" + "\n".join(item_lines) + "\n")

        # Open puzzles from database
        open_puzzles = self.database.get_puzzles(self.game_id, status="open")
        in_progress = self.database.get_puzzles(self.game_id, status="in_progress")
        all_open = open_puzzles + in_progress

        if all_open:
            puzzle_lines = []
            for p in all_open:
                line = f"- [ID:{p.puzzle_id}] {p.description} (at {p.location})"
                if p.attempts:
                    attempts_str = "; ".join(
                        f"{a.get('action', '?')} -> {a.get('result', '?')}"
                        for a in p.attempts[-3:]  # Last 3 attempts
                    )
                    line += f"\n  Recent attempts: {attempts_str}"
                if p.related_items:
                    line += f"\n  Related items: {', '.join(p.related_items)}"
                puzzle_lines.append(line)
            parts.append(
                f"== OPEN PUZZLES ({len(all_open)}) ==\n"
                + "\n".join(puzzle_lines) + "\n"
            )
        else:
            parts.append("== OPEN PUZZLES ==\nNone\n")

        # Map summary
        if map_summary:
            parts.append(
                f"== MAP ==\n"
                f"Rooms: {map_summary.get('rooms_visited', 0)} visited / "
                f"{map_summary.get('rooms_total', 0)} total\n"
                f"Unexplored exits: {map_summary.get('unexplored_exits_count', 0)}\n"
            )

        # Recent actions
        if recent_actions:
            action_lines = []
            for cmd, result in recent_actions[-8:]:
                short_result = result[:80] + "..." if len(result) > 80 else result
                action_lines.append(f"> {cmd}\n  {short_result}")
            parts.append(
                f"== RECENT ACTIONS ==\n" + "\n".join(action_lines) + "\n"
            )

        return "\n".join(parts)

    def record_attempt(self, puzzle_id: int, action: str, result: str) -> None:
        """
        Record a failed attempt at solving a puzzle.

        Args:
            puzzle_id: ID of the puzzle attempted.
            action: The action that was tried.
            result: The game's response to the action.
        """
        puzzles = self.database.get_puzzles(self.game_id)
        for puzzle in puzzles:
            if puzzle.puzzle_id == puzzle_id:
                puzzle.attempts.append({"action": action, "result": result})
                puzzle.status = "in_progress"
                self.database.update_puzzle(puzzle)
                logger.debug(f"Recorded attempt on puzzle {puzzle_id}: {action}")
                return

        logger.warning(f"Puzzle {puzzle_id} not found for attempt recording")

    def mark_solved(self, puzzle_id: int, turn: int) -> None:
        """
        Mark a puzzle as solved.

        Args:
            puzzle_id: ID of the solved puzzle.
            turn: Turn number when the puzzle was solved.
        """
        puzzles = self.database.get_puzzles(self.game_id)
        for puzzle in puzzles:
            if puzzle.puzzle_id == puzzle_id:
                puzzle.status = "solved"
                puzzle.solved_turn = turn
                self.database.update_puzzle(puzzle)
                logger.info(f"Puzzle {puzzle_id} marked as solved at turn {turn}")
                return

        logger.warning(f"Puzzle {puzzle_id} not found for solving")

    def detect_stuck(
        self,
        recent_actions: list[tuple[str, str]],
        recent_rooms: list[str],
    ) -> str | None:
        """
        Algorithmically detect if the game agent is stuck.

        Checks for repeated commands, room cycling, and repeated failures.
        No LLM call -- this is purely algorithmic.

        Args:
            recent_actions: Recent (command, output) pairs.
            recent_rooms: Recent room IDs visited (in order).

        Returns:
            Suggestion string if stuck behavior detected, None otherwise.
        """
        if not recent_actions:
            return None

        # Check 1: Repeated commands (same command >2 times in last 10)
        recent_commands = [cmd for cmd, _ in recent_actions[-10:]]
        command_counts = Counter(recent_commands)
        for cmd, count in command_counts.items():
            if count > 2:
                logger.warning(f"Stuck detection: command '{cmd}' repeated {count} times")
                return (
                    f"You have been repeating the command '{cmd}' frequently. "
                    f"Try a completely different approach or explore a new area."
                )

        # Check 2: Room cycling (3 or fewer unique rooms in last 15 actions)
        if len(recent_rooms) >= 15:
            last_15_rooms = recent_rooms[-15:]
            unique_rooms = set(last_15_rooms)
            if len(unique_rooms) <= 3:
                logger.warning(
                    f"Stuck detection: cycling through {len(unique_rooms)} rooms "
                    f"for 15+ turns"
                )
                return (
                    f"You have been cycling through the same {len(unique_rooms)} "
                    f"rooms for many turns. Consider exploring unexplored exits "
                    f"or trying items on puzzles in different areas."
                )

        # Check 3: Repeated failure responses (same error >2 times in last 10)
        recent_outputs = [output for _, output in recent_actions[-10:]]
        # Normalize outputs for comparison (first 50 chars as a fingerprint)
        output_fingerprints = [o[:50].lower().strip() for o in recent_outputs]
        output_counts = Counter(output_fingerprints)
        for fp, count in output_counts.items():
            if count > 2 and any(
                keyword in fp
                for keyword in ["can't", "cannot", "won't", "doesn't", "nothing happens",
                                "not possible", "you can't do that"]
            ):
                logger.warning(f"Stuck detection: repeated failure response")
                return (
                    "You keep getting the same failure response. "
                    "This approach is not working. Try using a different item, "
                    "verb, or target. Consider whether you need something "
                    "from another part of the map."
                )

        return None

    def get_last_metrics(self) -> LLMMetric | None:
        """
        Get metrics from the last LLM call.

        Returns:
            LLMMetric if a call was made, None otherwise.
        """
        if self._last_response is None:
            return None

        return LLMMetric(
            agent_name="puzzle_agent",
            provider=getattr(self.llm, "provider_name", "unknown"),
            model=self.llm.model,
            input_tokens=self._last_response.input_tokens,
            output_tokens=self._last_response.output_tokens,
            cached_tokens=self._last_response.cached_tokens,
            cost_estimate=self._last_response.cost_estimate,
            latency_ms=self._last_response.latency_ms,
        )
