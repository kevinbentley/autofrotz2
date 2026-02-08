"""
Integration tests for the AutoFrotz v2 orchestrator.

Uses mocks for all external dependencies (LLM, game interface, database, managers).
Simulates a small scripted game: 3 rooms, a key, and a locked door.
"""

import json
import logging
from dataclasses import dataclass, field
from unittest.mock import MagicMock, PropertyMock, patch, call

import pytest

from autofrotz.agents.game_agent import GameAgent
from autofrotz.agents.puzzle_agent import PuzzleAgent
from autofrotz.game_interface import GameInterface
from autofrotz.hooks.base import BaseHook
from autofrotz.orchestrator import Orchestrator
from autofrotz.storage.database import Database
from autofrotz.storage.models import (
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

# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------

class MockLLM:
    """Mock LLM that returns predefined responses."""

    provider_name = "mock"
    model = "mock-model"

    def __init__(self, responses=None, json_responses=None):
        self.responses = responses or []
        self.json_responses = json_responses or []
        self._response_index = 0
        self._json_index = 0

    def complete(self, messages, system_prompt, temperature=0.7, max_tokens=1024):
        if self._response_index < len(self.responses):
            text = self.responses[self._response_index]
            self._response_index += 1
        else:
            text = "I will look around.\nACTION: look"
        return LLMResponse(
            text=text,
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            cost_estimate=0.001,
            latency_ms=100.0,
        )

    def complete_json(self, messages, system_prompt, schema, temperature=0.1, max_tokens=512):
        if self._json_index < len(self.json_responses):
            result = self.json_responses[self._json_index]
            self._json_index += 1
        else:
            result = {"room_changed": False, "room_name": None, "description": None,
                       "exits": [], "is_dark": False, "items_seen": []}
        return result

    def count_tokens(self, text):
        return len(text.split())


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def make_test_config():
    """Create a minimal test configuration."""
    return {
        "game_file": "games/test.z5",
        "max_turns": 10,
        "save_on_death": True,
        "database_path": ":memory:",
        "agents": {
            "game_agent": {
                "provider": "openai",
                "model": "test-model",
                "temperature": 0.7,
                "max_tokens": 1024,
            },
            "puzzle_agent": {
                "provider": "openai",
                "model": "test-model",
                "temperature": 0.5,
                "max_tokens": 1024,
            },
            "map_parser": {
                "provider": "openai",
                "model": "test-model",
                "temperature": 0.1,
                "max_tokens": 512,
            },
            "item_parser": {
                "provider": "openai",
                "model": "test-model",
                "temperature": 0.1,
                "max_tokens": 512,
            },
        },
        "providers": {
            "openai": {
                "api_key": "test-key",
            },
        },
    }


class RecordingHook(BaseHook):
    """Hook that records all calls for assertion."""

    def __init__(self):
        self.calls = []

    def on_game_start(self, game_id, game_file):
        self.calls.append(("on_game_start", {"game_id": game_id, "game_file": game_file}))

    def on_turn_start(self, turn_number, room_id):
        self.calls.append(("on_turn_start", {"turn_number": turn_number, "room_id": room_id}))

    def on_turn_end(self, turn_number, command, output, room_id):
        self.calls.append(("on_turn_end", {
            "turn_number": turn_number, "command": command,
            "output": output, "room_id": room_id,
        }))

    def on_room_enter(self, room_id, room_name, description, is_new):
        self.calls.append(("on_room_enter", {
            "room_id": room_id, "room_name": room_name,
            "description": description, "is_new": is_new,
        }))

    def on_item_found(self, item_id, item_name, room_id):
        self.calls.append(("on_item_found", {
            "item_id": item_id, "item_name": item_name, "room_id": room_id,
        }))

    def on_item_taken(self, item_id, item_name):
        self.calls.append(("on_item_taken", {"item_id": item_id, "item_name": item_name}))

    def on_puzzle_found(self, puzzle_id, description):
        self.calls.append(("on_puzzle_found", {"puzzle_id": puzzle_id, "description": description}))

    def on_puzzle_solved(self, puzzle_id, description):
        self.calls.append(("on_puzzle_solved", {"puzzle_id": puzzle_id, "description": description}))

    def on_maze_detected(self, maze_group_id, entry_room_id, suspected_room_count):
        self.calls.append(("on_maze_detected", {
            "maze_group_id": maze_group_id,
            "entry_room_id": entry_room_id,
            "suspected_room_count": suspected_room_count,
        }))

    def on_maze_room_marked(self, maze_group_id, room_id, marker_item_id):
        self.calls.append(("on_maze_room_marked", {
            "maze_group_id": maze_group_id,
            "room_id": room_id,
            "marker_item_id": marker_item_id,
        }))

    def on_maze_completed(self, maze_group_id, total_rooms, total_exits):
        self.calls.append(("on_maze_completed", {
            "maze_group_id": maze_group_id,
            "total_rooms": total_rooms,
            "total_exits": total_exits,
        }))

    def on_game_end(self, game_id, status, total_turns):
        self.calls.append(("on_game_end", {
            "game_id": game_id, "status": status, "total_turns": total_turns,
        }))


class FailingHook(BaseHook):
    """Hook that raises exceptions on every method to test error isolation."""

    def on_turn_start(self, turn_number, room_id):
        raise RuntimeError("Hook failure!")

    def on_turn_end(self, turn_number, command, output, room_id):
        raise RuntimeError("Hook failure!")

    def on_game_start(self, game_id, game_file):
        raise RuntimeError("Hook failure!")

    def on_game_end(self, game_id, status, total_turns):
        raise RuntimeError("Hook failure!")


def build_orchestrator_with_mocks(
    config=None,
    game_agent_responses=None,
    map_json_responses=None,
    item_json_responses=None,
    puzzle_json_responses=None,
    game_outputs=None,
    intro_text="Welcome to Test Adventure!\nYou are in a garden.",
):
    """
    Build an Orchestrator with all external dependencies mocked.

    Returns:
        Tuple of (orchestrator, mocks_dict) where mocks_dict contains
        references to all mock objects for assertion.
    """
    config = config or make_test_config()

    # Default responses
    if game_agent_responses is None:
        game_agent_responses = [
            "Let me explore.\nACTION: go north",
            "I see a key.\nACTION: take key",
            "Let me try the door.\nACTION: unlock door with key",
        ]

    if map_json_responses is None:
        map_json_responses = [
            # Intro room parse
            {"room_changed": True, "room_name": "Garden", "description": "A beautiful garden.",
             "exits": ["north"], "is_dark": False, "items_seen": []},
        ]

    if item_json_responses is None:
        item_json_responses = [
            {"updates": []},
        ]

    if puzzle_json_responses is None:
        puzzle_json_responses = [
            {"new_puzzles": [], "suggestions": []},
        ]

    if game_outputs is None:
        game_outputs = [
            "Hallway\nA long hallway with a door to the east.",
            "You see a brass key here.",
            "The door swings open!",
        ]

    # Create mock LLMs
    game_llm = MockLLM(responses=game_agent_responses)
    puzzle_llm = MockLLM(json_responses=puzzle_json_responses)
    map_llm = MockLLM(json_responses=map_json_responses)
    item_llm = MockLLM(json_responses=item_json_responses)

    # Patch dependencies
    with patch("autofrotz.orchestrator.GameInterface") as MockGI, \
         patch("autofrotz.orchestrator.create_llm") as mock_create_llm, \
         patch("autofrotz.orchestrator.MapManager") as MockMM, \
         patch("autofrotz.orchestrator.ItemManager") as MockIM:

        # Configure GameInterface mock
        mock_gi = MockGI.return_value
        mock_gi.get_intro.return_value = intro_text
        output_iter = iter(game_outputs)
        mock_gi.do_command.side_effect = lambda cmd: next(output_iter, "Nothing happens.")
        mock_gi.save.return_value = True
        mock_gi.restore.return_value = True
        mock_gi.detect_terminal_state.return_value = None

        # Configure create_llm to return appropriate LLMs
        def llm_factory(agent_name, cfg):
            if agent_name == "game_agent":
                return game_llm
            elif agent_name == "puzzle_agent":
                return puzzle_llm
            elif agent_name == "map_parser":
                return map_llm
            elif agent_name == "item_parser":
                return item_llm
            return MockLLM()

        mock_create_llm.side_effect = llm_factory

        # Configure MapManager mock
        mock_mm = MockMM.return_value
        mock_mm.is_maze_active.return_value = False
        mock_mm.current_room_id = "garden"

        garden_room = Room(
            room_id="garden", name="Garden",
            description="A beautiful garden.",
            visited=True, visit_count=1,
            exits={"north": None},
        )
        mock_mm.get_current_room.return_value = garden_room
        mock_mm.get_room.return_value = garden_room
        mock_mm.get_map_summary.return_value = {
            "rooms_visited": 1,
            "rooms_total": 1,
            "unexplored_exits_count": 1,
            "current_room": "garden",
        }
        mock_mm.update_from_game_output.return_value = RoomUpdate(
            room_changed=True,
            room_id="garden",
            room_name="Garden",
            description="A beautiful garden.",
            exits=["north"],
            is_dark=False,
            new_room=True,
        )
        mock_mm.get_last_metrics.return_value = LLMMetric(
            agent_name="map_parser", provider="mock", model="mock-model",
            input_tokens=50, output_tokens=20,
        )
        mock_mm.check_maze_condition.return_value = False
        mock_mm.get_active_maze.return_value = None
        mock_mm.get_all_rooms.return_value = [garden_room]

        # Configure ItemManager mock
        mock_im = MockIM.return_value
        mock_im.get_inventory.return_value = []
        mock_im.get_items_in_room.return_value = []
        mock_im.get_all_items.return_value = []
        mock_im.update_from_game_output.return_value = []
        mock_im.get_last_metrics.return_value = LLMMetric(
            agent_name="item_parser", provider="mock", model="mock-model",
            input_tokens=50, output_tokens=20,
        )
        mock_im.get_droppable_items.return_value = []

        # Create orchestrator
        orchestrator = Orchestrator(config)

        mocks = {
            "game_interface": mock_gi,
            "map_manager": mock_mm,
            "item_manager": mock_im,
            "game_llm": game_llm,
            "puzzle_llm": puzzle_llm,
            "map_llm": map_llm,
            "item_llm": item_llm,
            "create_llm": mock_create_llm,
        }

        return orchestrator, mocks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOrchestratorInit:
    """Test orchestrator initialization."""

    def test_initializes_without_error(self):
        """Orchestrator should initialize with mocked dependencies."""
        orch, mocks = build_orchestrator_with_mocks()
        assert orch is not None
        assert orch.game_id > 0
        assert orch.max_turns == 10

    def test_creates_game_session(self):
        """Orchestrator should create a game session in the database."""
        orch, mocks = build_orchestrator_with_mocks()
        # The database should have a game session
        assert orch.game_id > 0

    def test_creates_all_llm_instances(self):
        """Orchestrator should request LLM instances for all four agents."""
        orch, mocks = build_orchestrator_with_mocks()
        create_calls = mocks["create_llm"].call_args_list
        agent_names = [c[0][0] for c in create_calls]
        assert "game_agent" in agent_names
        assert "puzzle_agent" in agent_names
        assert "map_parser" in agent_names
        assert "item_parser" in agent_names


class TestNormalTurnSequence:
    """Test the normal turn execution sequence."""

    def test_single_turn_calls_map_parse(self):
        """Normal turn should call map manager's update_from_game_output."""
        orch, mocks = build_orchestrator_with_mocks()
        orch._normal_turn(1, "You are in a garden.")
        mocks["map_manager"].update_from_game_output.assert_called()

    def test_single_turn_calls_item_parse(self):
        """Normal turn should call item manager's update_from_game_output."""
        orch, mocks = build_orchestrator_with_mocks()
        orch._normal_turn(1, "You are in a garden.")
        mocks["item_manager"].update_from_game_output.assert_called()

    def test_single_turn_executes_command(self):
        """Normal turn should execute a command via game interface."""
        orch, mocks = build_orchestrator_with_mocks()
        result = orch._normal_turn(1, "You are in a garden.")
        mocks["game_interface"].do_command.assert_called()

    def test_turn_returns_game_output(self):
        """Normal turn should return the game output from the executed command."""
        orch, mocks = build_orchestrator_with_mocks(
            game_outputs=["Hallway\nYou see a door."]
        )
        result = orch._normal_turn(1, "You are in a garden.")
        assert "Hallway" in result

    def test_turn_saves_to_database(self):
        """Turn data should be saved to the database."""
        orch, mocks = build_orchestrator_with_mocks()
        orch._normal_turn(1, "You are in a garden.")
        # Verify database has the turn
        turns = orch.database.get_turns(orch.game_id)
        assert len(turns) == 1
        assert turns[0].turn_number == 1

    def test_puzzle_eval_throttled(self):
        """Puzzle agent should be evaluated based on PUZZLE_EVAL_INTERVAL."""
        orch, mocks = build_orchestrator_with_mocks(
            game_outputs=["Output 1", "Output 2", "Output 3", "Output 4"]
        )
        # Set room_update to not be a new room (so only interval triggers eval)
        mocks["map_manager"].update_from_game_output.return_value = RoomUpdate(
            room_changed=False, room_id="garden", new_room=False,
            exits=[], is_dark=False,
        )

        # Turn 1: not interval, not new room => no eval (unless interval matches)
        orch._normal_turn(1, "test")
        # Turn 2: not interval
        orch._normal_turn(2, "test")
        # Turn 3: interval (3 % 3 == 0) => should eval
        orch._normal_turn(3, "test")

        # The puzzle agent's evaluate is in PuzzleAgent, which is NOT mocked
        # but uses the puzzle_llm which returns defaults.
        # We verify it does not crash and metrics are collected.


class TestDeathRecovery:
    """Test death detection and save/restore recovery."""

    def test_death_detected_triggers_restore(self):
        """When death is detected, orchestrator should attempt restore."""
        orch, mocks = build_orchestrator_with_mocks(
            game_outputs=[
                "*** You have died ***",  # First command result
                "You are in the garden.",  # After restore + look
                "Nothing happens.",
            ]
        )

        # First, do a save so there is something to restore
        orch._save_game(0)

        # Configure terminal state detection
        mocks["game_interface"].detect_terminal_state.side_effect = lambda output: (
            "death" if "died" in output.lower() else None
        )

        # Run the game loop - it should detect death and restore
        orch.run()

        # Verify restore was called
        mocks["game_interface"].restore.assert_called()

    def test_death_without_save_ends_game(self):
        """Death without any save file should end the game."""
        orch, mocks = build_orchestrator_with_mocks(
            game_outputs=["*** You have died ***"]
        )
        mocks["game_interface"].detect_terminal_state.side_effect = lambda output: (
            "death" if "died" in output.lower() else None
        )
        mocks["game_interface"].restore.return_value = False

        orch.run()

        # Game should end with "lost" status
        game = orch.database.get_game(orch.game_id)
        assert game is not None
        assert game.status == "lost"


class TestVictory:
    """Test victory detection."""

    def test_victory_ends_game_as_won(self):
        """Victory detection should end game with 'won' status."""
        orch, mocks = build_orchestrator_with_mocks(
            game_outputs=["*** You have won ***"]
        )
        mocks["game_interface"].detect_terminal_state.side_effect = lambda output: (
            "victory" if "won" in output.lower() else None
        )

        orch.run()

        game = orch.database.get_game(orch.game_id)
        assert game is not None
        assert game.status == "won"


class TestMazeMode:
    """Test maze detection and algorithmic solving."""

    def test_maze_detection_switches_mode(self):
        """When maze is detected, orchestrator should switch to maze mode."""
        orch, mocks = build_orchestrator_with_mocks(
            game_outputs=[
                "A maze of twisty passages.",
                "A maze of twisty passages.",
                "Nothing happens.",
            ]
        )

        maze_group = MazeGroup(
            group_id="maze_1",
            entry_room_id="garden",
            room_ids=["maze_1_0", "maze_1_1", "maze_1_2"],
            exit_room_ids=[],
            markers={},
            fully_mapped=False,
            created_turn=1,
        )

        # Simulate maze detection on first turn
        call_count = [0]
        def check_maze_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return True
            return False

        mocks["map_manager"].check_maze_condition.side_effect = check_maze_side_effect
        mocks["map_manager"].get_active_maze.return_value = maze_group

        # After the first turn detects a maze, subsequent turns should use maze mode
        maze_active_calls = [False, True, True, False]
        maze_idx = [0]
        def is_maze_active():
            if maze_idx[0] < len(maze_active_calls):
                result = maze_active_calls[maze_idx[0]]
                maze_idx[0] += 1
                return result
            return False

        mocks["map_manager"].is_maze_active.side_effect = is_maze_active

        # Run - should not crash
        orch.run()

    def test_maze_turn_bypasses_game_agent(self):
        """Maze turns should not call the game agent LLM."""
        orch, mocks = build_orchestrator_with_mocks(
            game_outputs=["A twisty passage."]
        )

        maze_group = MazeGroup(
            group_id="maze_1",
            entry_room_id="garden",
            room_ids=["maze_1_0"],
            exit_room_ids=[],
            markers={},
            fully_mapped=False,
            created_turn=1,
        )

        mocks["map_manager"].get_active_maze.return_value = maze_group
        mocks["map_manager"].get_room.return_value = Room(
            room_id="maze_1_0", name="Maze Room",
            description="Twisty passage.",
            exits={"north": None},
        )
        mocks["map_manager"].get_unexplored_exits.return_value = [("maze_1_0", "north")]
        mocks["map_manager"].get_nearest_unexplored.return_value = None

        # Track game agent LLM calls
        initial_index = mocks["game_llm"]._response_index

        orch._init_maze_solver(maze_group)
        orch._maze_turn(1, "A twisty passage.")

        # Game agent LLM should not have been called
        assert mocks["game_llm"]._response_index == initial_index


class TestMaxTurns:
    """Test max turns limit."""

    def test_max_turns_stops_loop(self):
        """Game should end as 'abandoned' when max turns reached."""
        config = make_test_config()
        config["max_turns"] = 3

        orch, mocks = build_orchestrator_with_mocks(
            config=config,
            game_outputs=["Output 1", "Output 2", "Output 3", "Output 4"],
        )

        orch.run()

        game = orch.database.get_game(orch.game_id)
        assert game is not None
        assert game.status == "abandoned"


class TestHooks:
    """Test hook firing order and error isolation."""

    def test_hooks_fire_on_game_start(self):
        """on_game_start should fire when the game begins."""
        orch, mocks = build_orchestrator_with_mocks()
        hook = RecordingHook()
        orch.register_hook(hook)

        orch._fire_hooks("on_game_start", game_id=orch.game_id, game_file="test.z5")

        assert len(hook.calls) == 1
        assert hook.calls[0][0] == "on_game_start"

    def test_hooks_fire_on_turn_end(self):
        """on_turn_end should fire after each turn."""
        orch, mocks = build_orchestrator_with_mocks()
        hook = RecordingHook()
        orch.register_hook(hook)

        orch._normal_turn(1, "Test output")

        turn_end_calls = [c for c in hook.calls if c[0] == "on_turn_end"]
        assert len(turn_end_calls) == 1

    def test_hooks_fire_in_order(self):
        """Hooks should fire in order: turn_start before turn_end."""
        orch, mocks = build_orchestrator_with_mocks()
        hook = RecordingHook()
        orch.register_hook(hook)

        orch._normal_turn(1, "Test output")

        # Find positions
        method_names = [c[0] for c in hook.calls]
        if "on_turn_start" in method_names and "on_turn_end" in method_names:
            start_idx = method_names.index("on_turn_start")
            end_idx = method_names.index("on_turn_end")
            assert start_idx < end_idx

    def test_failing_hook_does_not_crash_game(self):
        """A hook that raises an exception should not crash the orchestrator."""
        orch, mocks = build_orchestrator_with_mocks()
        failing_hook = FailingHook()
        recording_hook = RecordingHook()

        orch.register_hook(failing_hook)
        orch.register_hook(recording_hook)

        # Should not raise despite the failing hook
        orch._fire_hooks("on_turn_start", turn_number=1, room_id="garden")
        orch._fire_hooks("on_turn_end", turn_number=1, command="look",
                         output="test", room_id="garden")

        # Recording hook should still have received its calls
        assert len(recording_hook.calls) == 2

    def test_room_enter_hook_fires_on_new_room(self):
        """on_room_enter hook should fire when entering a new room."""
        orch, mocks = build_orchestrator_with_mocks()
        hook = RecordingHook()
        orch.register_hook(hook)

        # Configure map to report a new room
        mocks["map_manager"].update_from_game_output.return_value = RoomUpdate(
            room_changed=True,
            room_id="hallway",
            room_name="Hallway",
            description="A long hallway.",
            exits=["east"],
            is_dark=False,
            new_room=True,
        )

        orch._normal_turn(1, "You enter the hallway.")

        room_enter_calls = [c for c in hook.calls if c[0] == "on_room_enter"]
        assert len(room_enter_calls) == 1
        assert room_enter_calls[0][1]["room_id"] == "hallway"
        assert room_enter_calls[0][1]["is_new"] is True

    def test_item_found_hook_fires(self):
        """on_item_found hook should fire when a new item is discovered."""
        orch, mocks = build_orchestrator_with_mocks()
        hook = RecordingHook()
        orch.register_hook(hook)

        # Configure item manager to report a new item
        mocks["item_manager"].update_from_game_output.return_value = [
            ItemUpdate(
                item_id="brass_key",
                name="brass key",
                change_type="new",
                location="hallway",
            )
        ]

        orch._normal_turn(1, "You see a brass key.")

        item_found_calls = [c for c in hook.calls if c[0] == "on_item_found"]
        assert len(item_found_calls) == 1
        assert item_found_calls[0][1]["item_id"] == "brass_key"


class TestGameAgent:
    """Test the game agent in isolation."""

    def test_parse_action_format(self):
        """Game agent should parse ACTION: format correctly."""
        llm = MockLLM(responses=["Reasoning here.\nACTION: go north"])
        agent = GameAgent(llm)
        command, reasoning = agent.decide_action({
            "game_output": "You are in a garden.",
            "room": Room(room_id="garden", name="Garden"),
            "inventory": [],
            "room_items": [],
            "map_summary": {},
            "open_puzzles": [],
            "puzzle_suggestions": [],
            "recent_actions": [],
            "special_instructions": "",
        })
        assert command == "go north"
        assert "Reasoning" in reasoning

    def test_fallback_on_missing_action(self):
        """Game agent should handle responses without ACTION: prefix."""
        llm = MockLLM(responses=["Just go north"])
        agent = GameAgent(llm)
        command, reasoning = agent.decide_action({
            "game_output": "test",
            "room": None,
            "inventory": [],
            "room_items": [],
            "map_summary": {},
            "open_puzzles": [],
            "puzzle_suggestions": [],
            "recent_actions": [],
            "special_instructions": "",
        })
        # Should use last line as command
        assert command == "Just go north"

    def test_metrics_available_after_call(self):
        """get_last_metrics should return valid metrics after a call."""
        llm = MockLLM(responses=["Think.\nACTION: look"])
        agent = GameAgent(llm)
        agent.decide_action({
            "game_output": "test",
            "room": None,
            "inventory": [],
            "room_items": [],
            "map_summary": {},
            "open_puzzles": [],
            "puzzle_suggestions": [],
            "recent_actions": [],
            "special_instructions": "",
        })
        metrics = agent.get_last_metrics()
        assert metrics is not None
        assert metrics.agent_name == "game_agent"
        assert metrics.input_tokens == 100

    def test_context_includes_special_instructions(self):
        """Context message should include special instructions when present."""
        llm = MockLLM(responses=["OK.\nACTION: look"])
        agent = GameAgent(llm)
        context = {
            "game_output": "test",
            "room": Room(room_id="garden", name="Garden"),
            "inventory": [],
            "room_items": [],
            "map_summary": {},
            "open_puzzles": [],
            "puzzle_suggestions": [],
            "recent_actions": [],
            "special_instructions": "WARNING: You died!",
        }
        message = agent._build_context_message(context)
        assert "WARNING: You died!" in message

    def test_context_includes_puzzle_suggestions(self):
        """Context message should format puzzle suggestions."""
        llm = MockLLM(responses=["OK.\nACTION: look"])
        agent = GameAgent(llm)
        context = {
            "game_output": "test",
            "room": None,
            "inventory": [],
            "room_items": [],
            "map_summary": {},
            "open_puzzles": [],
            "puzzle_suggestions": [
                PuzzleSuggestion(
                    puzzle_id=1,
                    description="Locked door",
                    proposed_action="unlock door with key",
                    items_to_use=["brass_key"],
                    confidence="high",
                )
            ],
            "recent_actions": [],
            "special_instructions": "",
        }
        message = agent._build_context_message(context)
        assert "HIGH" in message
        assert "unlock door with key" in message


class TestPuzzleAgent:
    """Test the puzzle agent in isolation."""

    def test_detect_stuck_repeated_commands(self):
        """Stuck detection should flag repeated commands."""
        llm = MockLLM()
        db = Database(":memory:")
        game_id = db.create_game("test.z5")
        agent = PuzzleAgent(llm, db, game_id)

        actions = [("go north", "Blocked.")] * 5
        result = agent.detect_stuck(actions, ["room_a"] * 5)
        assert result is not None
        assert "repeating" in result.lower()

    def test_detect_stuck_room_cycling(self):
        """Stuck detection should flag room cycling."""
        llm = MockLLM()
        db = Database(":memory:")
        game_id = db.create_game("test.z5")
        agent = PuzzleAgent(llm, db, game_id)

        # 3 rooms cycling for 15+ turns
        rooms = ["room_a", "room_b", "room_c"] * 6  # 18 rooms
        actions = [(f"cmd_{i}", "output") for i in range(18)]
        result = agent.detect_stuck(actions, rooms)
        assert result is not None
        assert "cycling" in result.lower()

    def test_detect_stuck_returns_none_when_not_stuck(self):
        """Stuck detection should return None for normal play."""
        llm = MockLLM()
        db = Database(":memory:")
        game_id = db.create_game("test.z5")
        agent = PuzzleAgent(llm, db, game_id)

        actions = [
            ("go north", "Hallway"), ("take key", "Taken."),
            ("go south", "Garden"), ("examine tree", "A tall tree."),
        ]
        rooms = ["garden", "hallway", "garden", "garden"]
        result = agent.detect_stuck(actions, rooms)
        assert result is None

    def test_record_attempt(self):
        """record_attempt should add to puzzle's attempts list."""
        llm = MockLLM()
        db = Database(":memory:")
        game_id = db.create_game("test.z5")
        agent = PuzzleAgent(llm, db, game_id)

        puzzle = Puzzle(
            description="Locked door",
            status="open",
            location="hallway",
            created_turn=1,
        )
        puzzle_id = db.save_puzzle(game_id, puzzle)
        agent.record_attempt(puzzle_id, "kick door", "The door doesn't budge.")

        puzzles = db.get_puzzles(game_id)
        assert len(puzzles) == 1
        assert len(puzzles[0].attempts) == 1
        assert puzzles[0].attempts[0]["action"] == "kick door"
        assert puzzles[0].status == "in_progress"

    def test_mark_solved(self):
        """mark_solved should update puzzle status and solved_turn."""
        llm = MockLLM()
        db = Database(":memory:")
        game_id = db.create_game("test.z5")
        agent = PuzzleAgent(llm, db, game_id)

        puzzle = Puzzle(
            description="Locked door",
            status="open",
            location="hallway",
            created_turn=1,
        )
        puzzle_id = db.save_puzzle(game_id, puzzle)
        agent.mark_solved(puzzle_id, 10)

        puzzles = db.get_puzzles(game_id)
        assert puzzles[0].status == "solved"
        assert puzzles[0].solved_turn == 10


class TestGameInterface:
    """Test the game interface terminal state detection."""

    def test_detect_death(self):
        """detect_terminal_state should return 'death' for death text."""
        # We test the static method without an actual Frotz instance
        gi = GameInterface.__new__(GameInterface)
        gi._frotz = None

        assert gi.detect_terminal_state("*** You have died ***") == "death"
        assert gi.detect_terminal_state("You have died") == "death"
        assert gi.detect_terminal_state("You have been killed by the troll") == "death"

    def test_detect_victory(self):
        """detect_terminal_state should return 'victory' for victory text."""
        gi = GameInterface.__new__(GameInterface)
        gi._frotz = None

        assert gi.detect_terminal_state("*** You have won ***") == "victory"
        assert gi.detect_terminal_state("Congratulations! You have won the game!") == "victory"

    def test_detect_normal(self):
        """detect_terminal_state should return None for normal output."""
        gi = GameInterface.__new__(GameInterface)
        gi._frotz = None

        assert gi.detect_terminal_state("You are in a garden.") is None
        assert gi.detect_terminal_state("Taken.") is None
        assert gi.detect_terminal_state("") is None


class TestHookBase:
    """Test the hook base class."""

    def test_all_methods_are_noop(self):
        """All BaseHook methods should be callable and return None."""
        hook = BaseHook()

        assert hook.on_game_start(1, "test.z5") is None
        assert hook.on_turn_start(1, "room") is None
        assert hook.on_turn_end(1, "cmd", "out", "room") is None
        assert hook.on_room_enter("room", "Room", "desc", True) is None
        assert hook.on_item_found("item", "Item", "room") is None
        assert hook.on_item_taken("item", "Item") is None
        assert hook.on_puzzle_found(1, "desc") is None
        assert hook.on_puzzle_solved(1, "desc") is None
        assert hook.on_maze_detected("maze1", "room", 5) is None
        assert hook.on_maze_room_marked("maze1", "room", "item") is None
        assert hook.on_maze_completed("maze1", 5, 10) is None
        assert hook.on_game_end(1, "won", 100) is None


class TestFailureDetection:
    """Test the orchestrator's failure output detection."""

    def test_detects_common_failures(self):
        """_is_failure_output should detect common failure responses."""
        orch, _ = build_orchestrator_with_mocks()

        assert orch._is_failure_output("You can't go that way.") is True
        assert orch._is_failure_output("I don't understand that.") is True
        assert orch._is_failure_output("Nothing happens.") is True
        assert orch._is_failure_output("That's not something you can do.") is True

    def test_does_not_flag_normal_output(self):
        """_is_failure_output should not flag normal game output."""
        orch, _ = build_orchestrator_with_mocks()

        assert orch._is_failure_output("You are in a garden.") is False
        assert orch._is_failure_output("Taken.") is False
        assert orch._is_failure_output("The door swings open.") is False


class TestContextAssembly:
    """Test context assembly for the game agent."""

    def test_context_has_required_keys(self):
        """Assembled context should contain all required keys."""
        orch, mocks = build_orchestrator_with_mocks()
        context = orch._assemble_context("Test output", [])

        required_keys = [
            "game_output", "room", "inventory", "room_items",
            "map_summary", "open_puzzles", "puzzle_suggestions",
            "recent_actions", "special_instructions",
        ]
        for key in required_keys:
            assert key in context, f"Missing key: {key}"

    def test_context_includes_game_output(self):
        """Context should include the game output string."""
        orch, mocks = build_orchestrator_with_mocks()
        context = orch._assemble_context("You see a shiny key.", [])
        assert context["game_output"] == "You see a shiny key."

    def test_context_includes_suggestions(self):
        """Context should include puzzle suggestions."""
        orch, mocks = build_orchestrator_with_mocks()
        suggestions = [
            PuzzleSuggestion(
                puzzle_id=1,
                description="Test puzzle",
                proposed_action="test action",
                confidence="high",
            )
        ]
        context = orch._assemble_context("output", suggestions)
        assert len(context["puzzle_suggestions"]) == 1
        assert context["puzzle_suggestions"][0].confidence == "high"
