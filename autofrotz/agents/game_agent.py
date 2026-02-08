"""
Game agent for AutoFrotz v2.

The primary decision-making agent. Receives a structured context each turn
and returns a single game command. Stateless -- it does not maintain its own
memory; the orchestrator assembles all needed context from the managers.
"""

import logging
import re
from pathlib import Path

from autofrotz.llm.base import BaseLLM
from autofrotz.storage.models import (
    Item,
    LLMMetric,
    LLMResponse,
    Puzzle,
    PuzzleSuggestion,
    Room,
)

logger = logging.getLogger(__name__)


class GameAgent:
    """
    Primary decision-making agent for gameplay.

    Receives assembled context each turn and uses LLM reasoning to decide
    the next game command. Does not maintain its own state or memory.
    """

    def __init__(
        self,
        llm: BaseLLM,
        prompt_path: str = "autofrotz/prompts/game_agent.txt",
    ) -> None:
        """
        Initialize the game agent.

        Args:
            llm: LLM instance for decision-making.
            prompt_path: Path to the system prompt file.
        """
        self.llm = llm
        self._last_response: LLMResponse | None = None

        # Load system prompt from file
        try:
            self._system_prompt = Path(prompt_path).read_text()
            logger.info(f"Game agent system prompt loaded from {prompt_path}")
        except FileNotFoundError:
            logger.error(f"Game agent prompt not found at {prompt_path}")
            self._system_prompt = (
                "You are an expert text adventure player. "
                "Analyze the game state and choose the best action. "
                "End your response with ACTION: <command>"
            )

    def decide_action(self, context: dict) -> tuple[str, str]:
        """
        Decide the next game command based on assembled context.

        Args:
            context: Dictionary containing:
                - game_output (str): Latest game output text
                - room (Room | None): Current room object
                - inventory (list[Item]): Items in inventory
                - room_items (list[Item]): Items visible in current room
                - map_summary (dict): Map exploration statistics
                - open_puzzles (list[Puzzle]): Currently open puzzles
                - puzzle_suggestions (list[PuzzleSuggestion]): Suggestions from puzzle agent
                - recent_actions (list[tuple[str, str]]): Recent (command, output) pairs
                - special_instructions (str): Extra instructions (e.g., death warning)

        Returns:
            Tuple of (command, reasoning) where command is the game command
            string and reasoning is the agent's explanation.
        """
        # Build the user message from context
        user_message = self._build_context_message(context)

        messages = [{"role": "user", "content": user_message}]

        try:
            response = self.llm.complete(
                messages=messages,
                system_prompt=self._system_prompt,
                temperature=0.7,
                max_tokens=1024,
            )
            self._last_response = response

            # Parse the response to extract ACTION and reasoning
            command, reasoning = self._parse_response(response.text)

            logger.info(f"Game agent decided: {command}")
            logger.debug(f"Reasoning: {reasoning}")

            return command, reasoning

        except Exception as e:
            logger.error(f"Game agent LLM call failed: {e}")
            self._last_response = None
            # Fallback: a safe default command
            return "look", f"LLM call failed ({e}), defaulting to 'look'"

    def _build_context_message(self, context: dict) -> str:
        """
        Format the context dictionary into a structured text message for the LLM.

        Args:
            context: Game state context dictionary.

        Returns:
            Formatted string for the user message.
        """
        parts = []

        # Special instructions (e.g., death recovery warning)
        special = context.get("special_instructions", "")
        if special:
            parts.append(f"== IMPORTANT ==\n{special}\n")

        # Latest game output
        game_output = context.get("game_output", "")
        parts.append(f"== LATEST GAME OUTPUT ==\n{game_output}\n")

        # Current room info
        room = context.get("room")
        if room and isinstance(room, Room):
            room_section = f"== CURRENT ROOM ==\nName: {room.name}\n"
            if room.description:
                room_section += f"Description: {room.description}\n"
            if room.exits:
                exits_str = ", ".join(
                    f"{d} -> {dest or '???'}" for d, dest in room.exits.items()
                )
                room_section += f"Exits: {exits_str}\n"
            if room.is_dark:
                room_section += "WARNING: This room is dark!\n"
            room_section += f"Visits: {room.visit_count}\n"
            parts.append(room_section)

        # Inventory
        inventory = context.get("inventory", [])
        if inventory:
            inv_names = [item.name for item in inventory]
            parts.append(f"== INVENTORY ({len(inv_names)} items) ==\n{', '.join(inv_names)}\n")
        else:
            parts.append("== INVENTORY ==\nEmpty\n")

        # Room items
        room_items = context.get("room_items", [])
        if room_items:
            item_names = [item.name for item in room_items]
            parts.append(f"== ITEMS HERE ==\n{', '.join(item_names)}\n")

        # Map summary
        map_summary = context.get("map_summary", {})
        if map_summary:
            parts.append(
                f"== MAP ==\n"
                f"Rooms explored: {map_summary.get('rooms_visited', 0)} / "
                f"{map_summary.get('rooms_total', 0)}\n"
                f"Unexplored exits: {map_summary.get('unexplored_exits_count', 0)}\n"
            )

        # Open puzzles
        open_puzzles = context.get("open_puzzles", [])
        if open_puzzles:
            puzzle_lines = []
            for p in open_puzzles:
                line = f"- [{p.status}] {p.description} (at {p.location})"
                if p.attempts:
                    line += f" [{len(p.attempts)} attempts]"
                puzzle_lines.append(line)
            parts.append(f"== OPEN PUZZLES ({len(open_puzzles)}) ==\n" + "\n".join(puzzle_lines) + "\n")

        # Puzzle suggestions
        suggestions = context.get("puzzle_suggestions", [])
        if suggestions:
            sugg_lines = []
            for s in suggestions:
                sugg_lines.append(
                    f"- [{s.confidence.upper()}] {s.description}: {s.proposed_action}"
                )
                if s.items_to_use:
                    sugg_lines.append(f"  Items: {', '.join(s.items_to_use)}")
            parts.append(
                f"== PUZZLE SUGGESTIONS ==\n" + "\n".join(sugg_lines) + "\n"
            )

        # Recent actions
        recent_actions = context.get("recent_actions", [])
        if recent_actions:
            action_lines = []
            for cmd, result in recent_actions[-10:]:  # Last 10 actions
                # Truncate long results
                short_result = result[:100] + "..." if len(result) > 100 else result
                action_lines.append(f"> {cmd}\n  {short_result}")
            parts.append(
                f"== RECENT ACTIONS ==\n" + "\n".join(action_lines) + "\n"
            )

        return "\n".join(parts)

    def _parse_response(self, response_text: str) -> tuple[str, str]:
        """
        Parse the LLM response to extract the ACTION command and reasoning.

        Expected format:
            <reasoning paragraph>
            ACTION: <command>

        Args:
            response_text: Raw LLM response text.

        Returns:
            Tuple of (command, reasoning).
        """
        # Look for ACTION: pattern (case-insensitive)
        match = re.search(r"ACTION:\s*(.+?)$", response_text, re.IGNORECASE | re.MULTILINE)

        if match:
            command = match.group(1).strip()
            # Everything before the ACTION line is reasoning
            action_start = match.start()
            reasoning = response_text[:action_start].strip()
        else:
            # Fallback: try to extract a command from the last line
            lines = [l.strip() for l in response_text.strip().split("\n") if l.strip()]
            if lines:
                # Use the last line as the command, everything else as reasoning
                command = lines[-1]
                reasoning = "\n".join(lines[:-1])
                logger.warning(
                    f"No ACTION: prefix found in response, using last line: {command}"
                )
            else:
                command = "look"
                reasoning = "Empty response from LLM"
                logger.error("Empty response from game agent LLM")

        # Clean up command -- remove quotes, extra whitespace
        command = command.strip().strip('"').strip("'")

        return command, reasoning

    def get_last_metrics(self) -> LLMMetric | None:
        """
        Get metrics from the last LLM call.

        Returns:
            LLMMetric if a call was made, None otherwise.
        """
        if self._last_response is None:
            return None

        return LLMMetric(
            agent_name="game_agent",
            provider=getattr(self.llm, "provider_name", "unknown"),
            model=self.llm.model,
            input_tokens=self._last_response.input_tokens,
            output_tokens=self._last_response.output_tokens,
            cached_tokens=self._last_response.cached_tokens,
            cost_estimate=self._last_response.cost_estimate,
            latency_ms=self._last_response.latency_ms,
        )
