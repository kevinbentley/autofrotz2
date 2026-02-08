# CLAUDE.md

## Project Overview

AutoFrotz v2 is a multi-agent AI system that autonomously plays classic Infocom text adventure games (Zork, Hitchhiker's Guide, Planetfall, etc.) via the Frotz Z-Machine interpreter. The system uses specialized AI agents coordinating through a central orchestrator, with each agent responsible for a different cognitive task: playing the game, mapping the world, tracking items, and solving puzzles.

The previous version (https://github.com/kevinbentley/autofrotz) was a monolithic Python script with basic LLM integration. This rewrite is a ground-up redesign with proper separation of concerns, persistent state, and an extensible architecture.

Game files from https://github.com/danielricks/textplayer/tree/master/games will be downloaded and included in the repository

## Tech Stack

- **Language:** Python 3.11+
- **Game Engine Interface:** pyFrotz (`pip install pyfrotz`) wrapping dfrotz
- **Database:** SQLite via `sqlite3` (stdlib) for game state, metrics, and replay
- **Web Framework:** FastAPI with WebSocket support for the monitoring/replay UI
- **Frontend:** Vanilla JS with server-sent events or WebSocket for live game watching
- **LLM Providers:** OpenAI, Anthropic (Claude), Google Gemini, plus any OpenAI-compatible local server
- **Graph/Pathfinding:** NetworkX for the map manager's directed graph and shortest-path queries
- **Configuration:** JSON (`config.json`) for all runtime settings including per-agent LLM assignment
- **Testing:** pytest

## Repository Structure

```
autofrotz/
    config.json              # Runtime configuration
    main.py                  # Entry point
    requirements.txt

    autofrotz/
        __init__.py
        orchestrator.py      # Central game loop and agent coordination
        game_interface.py    # pyFrotz wrapper, sends commands, receives output

        llm/
            __init__.py
            base.py          # Abstract LLM interface
            openai_llm.py    # OpenAI and OpenAI-compatible endpoints
            claude_llm.py    # Anthropic Claude
            gemini_llm.py    # Google Gemini
            factory.py       # Instantiates the right LLM from config

        agents/
            __init__.py
            game_agent.py    # Main gameplay agent
            puzzle_agent.py  # Puzzle tracking and suggestion agent

        managers/
            __init__.py
            map_manager.py   # Directed graph, pathfinding, exploration tracking
            item_manager.py  # Item registry with location tracking

        storage/
            __init__.py
            database.py      # SQLite schema, read/write, migrations
            models.py        # Dataclasses for rooms, items, puzzles, maze groups, turns, metrics

        hooks/
            __init__.py
            base.py          # Hook interface (on_turn, on_room_enter, on_item_found, etc.)
            multimedia.py    # Placeholder for future image gen / TTS hooks

        web/
            __init__.py
            server.py        # FastAPI app, REST + WebSocket endpoints
            static/          # HTML/JS/CSS for the monitoring UI
                index.html
                app.js
                style.css

        prompts/
            game_agent.txt
            puzzle_agent.txt
            map_update.txt
            item_update.txt

    games/                   # Z-Machine game files (.z5, .z8, .dat)
    tests/
        test_map_manager.py
        test_item_manager.py
        test_llm_factory.py
        test_orchestrator.py
```

## Architecture Notes

### Orchestrator (orchestrator.py)

The orchestrator runs the main game loop. Each iteration is one "turn" and follows this sequence:

1. Receive game output from pyFrotz (room description, narrative text, error messages).
2. Pass the output to the map manager and item manager for state updates (these use LLM calls to parse natural language output into structured updates).
3. Query the puzzle agent for any suggestions based on current state.
4. Assemble context for the game agent: current room, inventory, nearby items, map knowledge, open puzzles, and puzzle agent suggestions.
5. Game agent decides on an action (a text command like "go north" or "take lamp").
6. Send the action to pyFrotz via `game.do_command()`.
7. Log everything to SQLite.
8. Fire hooks (for live monitoring, future multimedia, etc.).
9. Repeat.

The orchestrator should detect game-over conditions (death, victory, unrecoverable states) and handle save/restore when the agent dies.

The orchestrator also manages a **maze-solving mode**. When the map manager detects a maze condition (via `check_maze_condition()`), the orchestrator suspends normal game agent reasoning and switches to an algorithmic maze-solving protocol. In this mode, the orchestrator directly issues commands (drop item, go direction, look) according to the maze solver's depth-first search strategy, bypassing the game agent's LLM calls entirely. This avoids wasting expensive reasoning tokens on what is fundamentally a mechanical exploration problem. Normal mode resumes once the maze is fully mapped. See GAME.md section 13 (Mazes) for the full protocol.

### LLM Abstraction (llm/)

Every LLM interaction goes through the abstract base class. The interface must support:

- `complete(messages, system_prompt, temperature, max_tokens) -> LLMResponse` for standard completions. The `LLMResponse` dataclass carries the response text alongside metadata: input tokens, output tokens, cached tokens, estimated cost, and latency in milliseconds.
- `complete_json(messages, system_prompt, schema, temperature, max_tokens) -> dict` for structured output (used heavily by the map and item managers). Use each provider's native JSON mode or structured output where available.
- Token counting and cost tracking per call, accumulated into metrics.
- Context caching integration:
  - **OpenAI:** Automatic. Prompt caching kicks in for prompts over 1024 tokens with matching prefixes. Structure prompts so static content (system prompt, game rules, agent instructions) comes first. No code changes needed beyond prompt ordering.
  - **Anthropic:** Explicit. Use `cache_control` breakpoints in the messages array. Cache the system prompt and any large static context blocks. Write cost is 25% higher but cache hits are 90% cheaper. 5-minute TTL, refreshed on use.
  - **Google Gemini:** Explicit. Use the `caches.create()` API to create named cached content objects with configurable TTL (default 1 hour). Reference the cache by name in subsequent requests.
  - **Local/OpenAI-compatible:** No caching assumed. The abstraction layer should gracefully skip caching logic when the endpoint does not support it.

The factory (`factory.py`) reads `config.json` and instantiates the correct provider for each agent by name.

### Game Agent (agents/game_agent.py)

This is the primary decision-maker. It receives a structured context object and returns a game command string. Its system prompt should establish it as an experienced text adventure player who thinks methodically, explores thoroughly, and tries creative solutions to puzzles.

The game agent should NOT try to maintain its own memory of the world. That is the job of the managers and the puzzle agent. The game agent receives a pre-built context summary each turn and focuses purely on deciding the next action.

The game agent should be able to handle common text adventure patterns: examining objects, trying verb-noun combinations, managing inventory limits, dealing with light sources and timed events, and recognizing when it is stuck.

### Map Manager (managers/map_manager.py)

Uses a NetworkX DiGraph internally. Each node is a room with attributes:

- `room_id` (string, generated from the room name, normalized; for maze rooms use `maze_<group>_<seq>`)
- `name` (display name as seen in game)
- `description` (latest room description text, updated on revisit)
- `visited` (bool)
- `visit_count` (int)
- `items_here` (list, cross-referenced with item manager)
- `maze_group` (string or None, links maze rooms to their MazeGroup)
- `maze_marker_item` (string or None, item_id of the marker dropped here during maze solving)
- `is_dark` (bool, True if the room returned a darkness message)

Each edge is a connection with attributes:

- `direction` (the command used, e.g., "north", "up", "enter building")
- `bidirectional` (bool, default True until proven otherwise)
- `blocked` (bool, with optional reason like "locked door", "troll guarding")
- `teleport` (bool, for one-way jumps that do not map to a simple reverse)
- `random` (bool, for maze connections that lead to different destinations on each traversal; stores list of observed destinations)

Key operations:

- `update_from_game_output(output_text, command_used) -> RoomUpdate`: Uses an LLM call to parse game output and determine if a room transition occurred, what the room name is, what exits are mentioned, etc. Returns a structured update.
- `get_path(from_room, to_room) -> list[str]`: Returns a list of direction commands to travel between rooms, using Dijkstra's on the DiGraph.
- `get_next_step(from_room, to_room) -> str`: Returns just the next direction command.
- `get_unexplored_exits(room_id=None) -> list[tuple]`: Returns exits that have been mentioned but never traversed. If `room_id` is None, returns all unexplored exits across the map.
- `get_nearest_unexplored(from_room) -> tuple[str, list[str]]`: Returns the nearest room with unexplored exits and the path to get there.
- `mark_blocked(from_room, direction, reason)` and `unblock(from_room, direction)`: For dynamic map changes.
- `check_maze_condition(room_id, description) -> bool`: Compares description against known rooms using normalized string similarity. Returns True if a maze condition is detected (3+ rooms with 95%+ similar descriptions within recent exploration). Sets `maze_active` flag and records the `MazeGroup`.
- `is_maze_active() -> bool`: Whether the system is currently in maze-solving mode.
- `get_active_maze() -> MazeGroup | None`: Returns the current maze group being solved.
- `assign_maze_marker(room_id, item_id)`: Records which marker item was dropped in which maze room.
- `identify_maze_room_by_marker(item_id) -> str | None`: Given a marker item visible in the current room, returns the room_id it was assigned to.
- `get_maze_rooms(group_id) -> list[str]`: All room IDs in a maze group.
- `complete_maze(group_id)`: Marks a maze group as fully mapped, clears `maze_active`.
- `to_dict() / from_dict()`: Serialization for database storage.

The map manager does not make gameplay decisions. It is a data structure with pathfinding capabilities, augmented by LLM parsing of natural language room descriptions.

### Item Manager (managers/item_manager.py)

Maintains a registry of all known items as a dictionary keyed by a normalized item ID. Each item has:

- `item_id` (string, normalized from item name)
- `name` (display name)
- `description` (from "examine" output, if available)
- `location` (room_id or "inventory" or "unknown")
- `portable` (bool or None if unknown, distinguishing inventoriable items from fixtures)
- `properties` (dict for freeform attributes like "lit", "open", "edible", "alive")
- `first_seen_turn` (int)
- `last_seen_turn` (int)

Key operations:

- `update_from_game_output(output_text, current_room, command_used) -> list[ItemUpdate]`: LLM-assisted parsing of game output to detect new items, item state changes, items taken or dropped.
- `take_item(item_id)`: Moves item to "inventory".
- `drop_item(item_id, room_id)`: Moves item to a room.
- `get_inventory() -> list[Item]`: All items with location "inventory".
- `get_items_in_room(room_id) -> list[Item]`: All items at a location.
- `get_all_items() -> list[Item]`: Full registry.
- `find_items_by_property(key, value) -> list[Item]`: Search by properties, e.g., find all items where "locked" is True.
- `get_droppable_items(puzzle_items: list[str] = None) -> list[Item]`: Returns portable inventory items sorted by estimated safety for use as maze markers. Items not referenced by any open puzzle are listed first. Accepts an optional list of item_ids to exclude (quest-critical items the puzzle agent has flagged).

### Puzzle Agent (agents/puzzle_agent.py)

This is the second AI agent. It maintains a list of open puzzles in the database and periodically evaluates whether any can be solved with current knowledge. A puzzle record has:

- `puzzle_id` (auto-increment)
- `description` (natural language, e.g., "Locked door in the stone hallway, requires a key")
- `status` (open, in_progress, solved, abandoned)
- `location` (room_id where the puzzle was encountered)
- `related_items` (list of item_ids that might be relevant)
- `attempts` (list of actions tried and their results)
- `created_turn` (int)
- `solved_turn` (int, nullable)

Each turn, the puzzle agent receives the current game state and performs two tasks:

1. **Detection:** Analyze the latest game output for new puzzles (locked doors, blocked paths, cryptic messages, NPCs with demands, inaccessible areas).
2. **Suggestion:** Cross-reference open puzzles with the current inventory and known items. If it sees a potential match (like a key in inventory and a locked door puzzle), it generates a suggestion for the game agent. Suggestions include the puzzle description, the proposed action, and which items to use.

The puzzle agent should also notice when the game agent has been revisiting the same rooms or repeating actions (a sign of being stuck), and suggest a different approach or unexplored area to try.

### Storage (storage/)

SQLite database with these tables:

**games** - one row per game session
- game_id, game_file, start_time, end_time, status (playing, won, lost, abandoned), total_turns

**turns** - one row per game turn, the primary replay data
- turn_id, game_id, turn_number, timestamp, command_sent, game_output, room_id, inventory_snapshot (JSON), agent_reasoning (the LLM's explanation of why it chose this action)

**rooms** - the map state
- room_id, game_id, name, description, visit_count, first_visited_turn, last_visited_turn, exits (JSON), maze_group (nullable), maze_marker_item (nullable), is_dark (bool)

**connections** - map edges
- connection_id, game_id, from_room_id, to_room_id, direction, bidirectional, blocked, teleport, random (bool), observed_destinations (JSON, nullable, for random maze connections)

**items** - item registry
- item_id, game_id, name, description, location, portable, properties (JSON), first_seen_turn, last_seen_turn

**puzzles** - puzzle tracker state
- puzzle_id, game_id, description, status, location, related_items (JSON), attempts (JSON), created_turn, solved_turn

**maze_groups** - maze detection and resolution state
- group_id, game_id, entry_room_id, room_ids (JSON), exit_room_ids (JSON), markers (JSON, room_id -> item_id mapping), fully_mapped (bool), created_turn, completed_turn (nullable)

**metrics** - per-turn LLM usage
- metric_id, game_id, turn_number, agent_name, provider, model, input_tokens, output_tokens, cached_tokens, cost_estimate, latency_ms

### Hooks (hooks/)

The hook system uses a simple observer pattern. The orchestrator holds a list of hook instances and calls their methods at defined points:

- `on_game_start(game_id, game_file)`
- `on_turn_start(turn_number, room_id)`
- `on_turn_end(turn_number, command, output, room_id)`
- `on_room_enter(room_id, room_name, description, is_new)`
- `on_item_found(item_id, item_name, room_id)`
- `on_item_taken(item_id, item_name)`
- `on_puzzle_found(puzzle_id, description)`
- `on_puzzle_solved(puzzle_id, description)`
- `on_maze_detected(maze_group_id, entry_room_id, suspected_room_count)`
- `on_maze_room_marked(maze_group_id, room_id, marker_item_id)`
- `on_maze_completed(maze_group_id, total_rooms, total_exits)`
- `on_game_end(game_id, status, total_turns)`

The web monitoring hook sends events through a WebSocket to the frontend. Future multimedia hooks will trigger image generation and TTS at appropriate moments (room entry, significant events).

### Web Monitoring UI (web/)

FastAPI server with two modes:

**Live mode:** WebSocket connection that receives events in real time as the game is played. The frontend displays the current room, a scrolling transcript, the inventory, and the map (rendered as a simple node-link diagram using canvas or SVG).

**Replay mode:** REST endpoints to list past games and fetch turn-by-turn data. The frontend can step through turns forward and backward, showing the game state as it was at each point. Playback speed should be adjustable.

Endpoints:
- `GET /api/games` - list all game sessions
- `GET /api/games/{game_id}` - game metadata
- `GET /api/games/{game_id}/turns` - all turns for a game
- `GET /api/games/{game_id}/turns/{turn_number}` - single turn with full state
- `GET /api/games/{game_id}/map` - current map as JSON (nodes + edges)
- `GET /api/games/{game_id}/items` - current item registry
- `GET /api/games/{game_id}/puzzles` - current puzzle state
- `GET /api/games/{game_id}/metrics` - aggregated LLM usage stats
- `WS /ws/live/{game_id}` - WebSocket for live game events

### config.json Structure

```json
{
  "game_file": "games/zork1.z5",
  "max_turns": 1000,
  "save_on_death": true,
  "database_path": "autofrotz.db",
  "web_server": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "agents": {
    "game_agent": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-20250514",
      "temperature": 0.7,
      "max_tokens": 1024
    },
    "puzzle_agent": {
      "provider": "openai",
      "model": "gpt-4o",
      "temperature": 0.5,
      "max_tokens": 1024
    },
    "map_parser": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "temperature": 0.1,
      "max_tokens": 512
    },
    "item_parser": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "temperature": 0.1,
      "max_tokens": 512
    }
  },
  "providers": {
    "openai": {
      "api_key_env": "OPENAI_API_KEY",
      "base_url": null
    },
    "anthropic": {
      "api_key_env": "ANTHROPIC_API_KEY"
    },
    "gemini": {
      "api_key_env": "GEMINI_API_KEY"
    },
    "local": {
      "base_url": "http://localhost:1234/v1",
      "api_key": "not-needed",
      "provider_type": "openai"
    }
  },
  "hooks": ["web_monitor"]
}
```

## Development Guidelines

- Use type hints everywhere. Dataclasses for all data structures.
- Async where it makes sense (the web server, LLM calls), but the main game loop can be synchronous since the game itself is turn-based and sequential.
- All LLM prompt templates live in `prompts/` as plain text files. Do not embed prompts as string literals in Python code.
- Every LLM call should be logged (prompt, response, tokens, latency) for debugging and metric tracking.
- The database is the source of truth for game state. If the process crashes and restarts, it should be able to resume from the last recorded turn.
- Use `logging` (stdlib) at appropriate levels. DEBUG for LLM prompts/responses, INFO for turn summaries, WARNING for unexpected game states, ERROR for failures.
- Keep the map manager and item manager as pure data managers. They use LLM calls only for parsing natural language, not for decision-making.
- The game agent and puzzle agent are the only components that use LLM calls for reasoning and planning.

## Testing Strategy

Refer to TESTING.md for full test instructions.

## Dependencies (requirements.txt)

```
pyfrotz
networkx
fastapi
uvicorn[standard]
websockets
openai
anthropic
google-genai
pytest
```