"""
Game interface for AutoFrotz v2.

Wraps pyFrotz to provide a clean API for sending commands, receiving output,
saving/restoring game state, and detecting terminal conditions. This is the
ONLY file in the project that imports pyfrotz.
"""

import logging
import re

from pyfrotz import Frotz

logger = logging.getLogger(__name__)


class GameInterface:
    """
    Wrapper around pyFrotz's Frotz interpreter.

    Handles all direct communication with the Z-Machine game process,
    including command execution, save/restore, and terminal state detection.
    """

    # Patterns indicating player death
    DEATH_PATTERNS = [
        r"\*\*\*\s*You have died\s*\*\*\*",
        r"You have died",
        r"\*\*\*\s*You are dead\s*\*\*\*",
        r"You are dead",
        r"You have been killed",
        r"You are killed",
        r"\*\*\*\s*You died\s*\*\*\*",
        r"It appears that last command .* fatal",
        r"Your adventure is over",
        r"You are swallowed",
        r"You have perished",
    ]

    # Patterns indicating victory or game completion
    VICTORY_PATTERNS = [
        r"\*\*\*\s*You have won\s*\*\*\*",
        r"You have won",
        r"Congratulations!.*won",
        r"\*\*\*\s*The End\s*\*\*\*",
        r"You have finished",
    ]

    def __init__(self, game_file: str) -> None:
        """
        Initialize the game interface with a Z-Machine game file.

        Creates the Frotz interpreter process and captures the intro text.

        Args:
            game_file: Path to the Z-Machine game file (.z5, .z8, .dat)
        """
        self.game_file = game_file
        self._frotz: Frotz | None = None
        self._intro_text: str = ""

        try:
            self._frotz = Frotz(game_file)
            # Get the intro text that appears when the game starts
            intro_output = self._frotz.get_intro()
            if isinstance(intro_output, tuple):
                # pyFrotz may return (room_name, description) or just a string
                self._intro_text = "\n".join(str(part) for part in intro_output if part)
            else:
                self._intro_text = str(intro_output) if intro_output else ""

            logger.info(f"Game loaded: {game_file}")
            logger.debug(f"Intro text: {self._intro_text[:200]}...")
        except Exception as e:
            logger.error(f"Failed to initialize game: {game_file} - {e}")
            raise

    def get_intro(self) -> str:
        """
        Get the intro text displayed when the game starts.

        Returns:
            The introductory game text including initial room description.
        """
        return self._intro_text

    def do_command(self, command: str) -> str:
        """
        Send a command to the game and return the output.

        Args:
            command: Text command to send to the game (e.g., "go north", "take lamp")

        Returns:
            Game output text resulting from the command.

        Raises:
            RuntimeError: If the game interface is not initialized.
        """
        if self._frotz is None:
            raise RuntimeError("Game interface not initialized")

        try:
            result = self._frotz.do_command(command)

            # pyFrotz returns (room_name, description) tuple
            if isinstance(result, tuple):
                output = "\n".join(str(part) for part in result if part)
            else:
                output = str(result) if result else ""

            logger.debug(f"Command: '{command}' -> Output: {output[:200]}...")
            return output

        except Exception as e:
            logger.error(f"Error executing command '{command}': {e}")
            return f"[Error: {e}]"

    def save(self, filename: str = "save.qzl") -> bool:
        """
        Save the current game state.

        Args:
            filename: Save file name (default: save.qzl)

        Returns:
            True if save was successful, False otherwise.
        """
        if self._frotz is None:
            logger.error("Cannot save: game interface not initialized")
            return False

        try:
            self._frotz.save(filename)
            logger.info(f"Game saved to {filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to save game to {filename}: {e}")
            return False

    def restore(self, filename: str = "save.qzl") -> bool:
        """
        Restore a previously saved game state.

        Args:
            filename: Save file name to restore from (default: save.qzl)

        Returns:
            True if restore was successful, False otherwise.
        """
        if self._frotz is None:
            logger.error("Cannot restore: game interface not initialized")
            return False

        try:
            self._frotz.restore(filename)
            logger.info(f"Game restored from {filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore game from {filename}: {e}")
            return False

    def detect_terminal_state(self, output: str) -> str | None:
        """
        Check game output for death or victory conditions.

        Args:
            output: Game output text to analyze.

        Returns:
            "death" if a death pattern is detected,
            "victory" if a victory pattern is detected,
            None if the game continues normally.
        """
        if not output:
            return None

        # Check death patterns first (more common in gameplay)
        for pattern in self.DEATH_PATTERNS:
            if re.search(pattern, output, re.IGNORECASE):
                logger.warning(f"Death detected in output: {output[:100]}...")
                return "death"

        # Check victory patterns
        for pattern in self.VICTORY_PATTERNS:
            if re.search(pattern, output, re.IGNORECASE):
                logger.info(f"Victory detected in output: {output[:100]}...")
                return "victory"

        return None

    def quit(self) -> None:
        """Shut down the game process cleanly."""
        if self._frotz is not None:
            try:
                self._frotz.frotz.terminate()
                logger.info("Game process terminated")
            except Exception as e:
                logger.error(f"Error quitting game: {e}")
            finally:
                self._frotz = None
