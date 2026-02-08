# GAME.md - AutoFrotz v2 Design Specification

## 1. Problem Statement

Classic Infocom text adventures (Zork, Hitchhiker's Guide to the Galaxy, Planetfall, etc.) present a challenging AI problem. These games require spatial reasoning, inventory management, multi-step puzzle solving, and creative experimentation with verb-noun commands. A bare LLM given the game transcript as context will quickly degrade: it loses track of the map, forgets where items are, revisits solved areas, and fails to connect clues found dozens of turns apart.

AutoFrotz v2 solves this by decomposing the problem into specialized agents and structured data managers that collectively maintain a far richer and more reliable world model than any single LLM context window can provide. The goal is to complete games autonomously, without any prior knowledge of the game's puzzles or walkthrough, while producing a watchable, replayable record of the entire session.

## 2. System Architecture

The system is organized into five major components that communicate through the orchestrator. Think of the orchestrator as a dispatcher sitting at the center of a hub, where each spoke is a specialist.

**Game Interface** handles all communication with the Frotz interpreter. It wraps pyFrotz and exposes a clean API: send a command string, receive the game's text response. It also handles save/restore operations and detects terminal game states (death, victory). The game interface is the only component that touches the actual game process.

**Map Manager** maintains a directed graph of rooms and connections. It receives raw game output each turn and uses a lightweight LLM call to parse room transitions, exit lists, and description changes. It provides pathfinding queries so the game agent can navigate efficiently ("how do I get from the cellar to the trophy case?"). It tracks which exits have never been traversed, enabling systematic exploration.

**Item Manager** maintains a registry of every object the system has encountered, whether portable (sword, lamp, key) or fixed (door, inscription, troll). Each item has a current location that updates as the player picks things up, drops them, or observes them moving in the game world. The item manager also uses an LLM call to parse game output for item-related changes.

**Game Agent** is the primary decision-making LLM. Each turn, it receives a structured briefing assembled by the orchestrator: current room details, inventory, nearby items, known map information, open puzzles, and any suggestions from the puzzle agent. It returns a single game command. It does not maintain its own memory or world model; it relies entirely on the structured data provided to it. This keeps its context window focused on the current decision rather than cluttered with stale transcript history.

**Puzzle Agent** is a secondary LLM that acts as the system's long-term strategic memory. It maintains a database of open puzzles (locked doors, blocked paths, cryptic clues, NPC interactions) and periodically cross-references them against the current inventory and known items. When it spots a potential connection, it formulates a suggestion that the orchestrator passes to the game agent. It also detects when the game agent is stuck (looping, repeating failed actions) and recommends a change in strategy.

```
                          +-----------------+
                          |   Orchestrator  |
                          |   (game loop)   |
                          +---+----+----+---+
                              |    |    |
              +---------------+    |    +---------------+
              |                    |                    |
      +-------v-------+   +-------v-------+   +-------v-------+
      |  Game Agent    |   | Puzzle Agent  |   | Game Interface|
      |  (LLM)        |   | (LLM)         |   | (pyFrotz)     |
      +---------------+   +-------+-------+   +---------------+
                                  |
                    +-------------+-------------+
                    |                           |
            +-------v-------+          +-------v-------+
            |  Map Manager  |          | Item Manager  |
            |  (NetworkX)   |          | (Registry)    |
            +---------------+          +---------------+
                    |                           |
                    +-------------+-------------+
                                  |
                          +-------v-------+
                          |    SQLite     |
                          |   Database   |
                          +-------+-------+
                                  |
                          +-------v-------+
                          |   Web UI     |
                          | (FastAPI +   |
                          |  WebSocket)  |
                          +---------------+
```

## 3. The Turn Loop in Detail

Every turn follows a fixed sequence. This deterministic structure ensures that each component sees consistent, up-to-date state.

**Phase 1: Receive.** The orchestrator reads the latest output from pyFrotz. This could be a room description after movement, the result of an action ("The door is locked."), a death message, or any other game text.

**Phase 2: Parse and Update.** The output text is sent to both the map manager and item manager for parsing. The map manager determines if a room transition occurred and updates the graph accordingly. The item manager scans for newly mentioned items, items that changed state (a door being opened, a lamp going out), or items changing location. Both managers use focused LLM calls with structured JSON output to extract this information reliably. These parsing calls use a low-temperature, small model since they are classification/extraction tasks, not creative reasoning.

**Phase 3: Puzzle Evaluation.** The puzzle agent receives the current game state (latest output, current room, inventory, all open puzzles) and performs two operations. First, it checks whether the latest output reveals a new puzzle and, if so, adds it to the database. Second, it cross-references open puzzles against the current inventory and known items, looking for actionable matches. If it finds one, it produces a suggestion object with the puzzle description, the proposed action, and its reasoning.

**Phase 4: Context Assembly.** The orchestrator builds the game agent's input by combining: the latest game output (what the player just saw), the current room details from the map manager (name, description, known exits), the current inventory from the item manager, items visible in the current room, a summary of the map (rooms visited, unexplored areas), a list of open puzzles with any suggestions from the puzzle agent, and the last few commands and their results (short-term memory for the game agent to avoid immediately repeating a failed action).

**Phase 5: Decision.** The game agent receives this assembled context and returns a single game command string. Its system prompt instructs it to think carefully, explain its reasoning briefly (logged for replay/debugging), and output a valid interactive fiction command.

**Phase 6: Execute.** The command is sent to pyFrotz via `do_command()`.

**Phase 7: Record.** The turn is written to the SQLite database: the command, the game output, the agent's reasoning, the room state, inventory snapshot, and LLM metrics (tokens, cost, latency for every LLM call made this turn).

**Phase 8: Notify.** All registered hooks are fired. The web monitoring hook pushes the turn data to any connected WebSocket clients. Future hooks (image generation, TTS) will trigger here.

**Phase 9: Check Terminal Conditions.** The orchestrator checks if the game has ended (victory text, death text, maximum turn limit reached). If the player died and `save_on_death` is enabled, the orchestrator restores from the last save and continues. The orchestrator should maintain periodic saves (every N turns) to enable this recovery.

## 4. LLM Abstraction Layer

### Design Philosophy

The LLM layer exists so that the rest of the codebase never imports `openai`, `anthropic`, or `google.genai` directly. Every LLM interaction flows through a common interface, and the factory creates the right implementation based on configuration.

### Base Interface

```python
class BaseLLM(ABC):
    @abstractmethod
    def complete(self, messages: list[dict], system_prompt: str,
                 temperature: float, max_tokens: int) -> LLMResponse:
        """Standard text completion."""

    @abstractmethod
    def complete_json(self, messages: list[dict], system_prompt: str,
                      schema: dict, temperature: float, max_tokens: int) -> dict:
        """Completion with structured JSON output."""

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Estimate token count for a string."""
```

The `LLMResponse` dataclass carries the response text alongside metadata: input tokens, output tokens, cached tokens, estimated cost, and latency in milliseconds.

### Provider-Specific Caching Strategies

For **OpenAI** (and OpenAI-compatible endpoints), prompt caching is automatic. The abstraction layer's job is simply to structure prompts correctly: static content (system prompt, agent instructions, game rules) at the beginning, dynamic content (current game state, latest output) at the end. This maximizes prefix cache hits. The response object's `usage.prompt_tokens_details.cached_tokens` field reports how many tokens were served from cache.

For **Anthropic Claude**, caching requires explicit `cache_control` breakpoints in the message array. The abstraction layer should insert a `{"type": "ephemeral"}` cache control marker after the system prompt and after any large static context blocks (like the full prompt template). This tells Claude's API to cache everything up to that breakpoint. The first request to write the cache costs 25% more on input tokens, but subsequent cache hits cost 90% less. The cache lasts 5 minutes and refreshes with each use, which is well within the turn cadence of a game session.

For **Google Gemini**, the abstraction layer should create an explicit cached content object using `client.caches.create()` when the agent is initialized, containing the system prompt and static instructions. This cached object has a configurable TTL (default 1 hour, adjustable). Subsequent requests reference the cache by name. The layer should handle cache expiry and recreation transparently.

For **local/OpenAI-compatible** servers, the layer should skip all caching logic. Many local inference servers (llama.cpp, vLLM, Ollama with OpenAI compatibility) do not support prompt caching, and sending caching-related parameters may cause errors. The factory should detect this from the config and instantiate the OpenAI provider class with caching disabled.

### Structured Output

The map manager and item manager need LLM calls that return valid JSON conforming to a specific schema. The abstraction layer should use each provider's native mechanism for this:

OpenAI supports `response_format={"type": "json_schema", "json_schema": {...}}` for guaranteed schema-conformant output. Claude supports tool use with input schemas as a reliable way to get structured output: define a "tool" whose input schema matches the desired output format, and Claude will call the tool with conformant JSON. Gemini supports `response_mime_type="application/json"` with a `response_schema`. For local models, fall back to instructing the model to output JSON and parsing the response with error handling and retries.

## 5. Map Manager

### Internal Representation

The map is a NetworkX DiGraph. Rooms are nodes; connections are directed edges. A bidirectional hallway between the Kitchen and the Living Room is represented as two directed edges (one each way). A one-way chute from the Attic to the Cellar is a single directed edge from Attic to Cellar with the `teleport` attribute set to True.

Room IDs are derived by normalizing the room name: lowercasing, replacing spaces with underscores, and stripping punctuation. If the game changes a room's name based on state (e.g., "Dark Room" becomes "Torch-Lit Room"), the map manager should recognize this as the same room (using the LLM parser to make that judgment) and update the name and description while preserving the room ID and its connections.

### Handling Unidirectional Paths and Teleportation

Text adventures frequently have asymmetric navigation. Going "down" into the cellar might land you somewhere different from where "up" takes you. The map manager handles this by defaulting new connections to bidirectional (an edge in each direction) but updating to unidirectional when evidence contradicts. If the player goes north from Room A and arrives at Room B, then goes south from Room B and arrives at Room C instead of Room A, the system removes the south edge from B to A and creates B-south-C instead. The A-north-B edge remains.

Teleportation events (falling through trap doors, being carried by an eagle, magical transportation) are detected by the LLM parser when the game output describes a sudden, involuntary, or magical transition. These are recorded as unidirectional edges with the `teleport` flag, and the pathfinder treats them as valid but knows not to expect a reverse path.

### Exploration Tracking

Every time the map manager parses a room description, it records which exits are mentioned ("Exits are north, south, and east"). Each exit is stored as a potential edge. When the player actually travels that direction, the edge transitions from "unexplored" to "explored" with a concrete destination.

The `get_nearest_unexplored(from_room)` method uses Dijkstra's algorithm to find the shortest path to any room that still has unexplored exits. This is the primary mechanism the game agent uses to systematically explore the entire map when it has no specific goal.

### Blocked Paths

Some exits are blocked by obstacles: a locked door, a guard, a darkness that requires a light source. The map manager records these as blocked edges with a reason string. When the puzzle agent detects that a blocked path might now be passable (the player found a key, defeated the guard, acquired a lamp), it can suggest that the game agent revisit the area. When the path is successfully traversed, the block is removed.

### Dynamic Map Changes

Some games change their maps during play (flood fills a passage, an earthquake opens a new cave, a bridge collapses). The LLM parser watches for these events and the map manager supports `remove_connection()`, `add_room()`, and `add_connection()` for runtime modifications. All changes are logged with the turn number for replay fidelity.

## 6. Item Manager

### Item Classification

Items fall into several categories that affect how the system reasons about them:

**Portable items** can be picked up and carried. The sword, the lantern, the leaflet. When the player takes one, its location changes to "inventory". When dropped, it moves to the current room.

**Fixed items** are part of the environment. The white house, the mailbox, the altar. They cannot be taken but may be interacted with (opened, read, pushed). Their location is always a room ID.

**NPCs and creatures** (the troll, the thief, the babel fish) are modeled as items with an "alive" property. They may move between rooms on their own, so the item manager should update their location whenever the game output mentions them in a new room. They are often puzzle-relevant.

**Consumable or transformable items** change state during the game. The lantern runs out of fuel. The water evaporates. Food is eaten. The item manager tracks these through the `properties` dict and updates them as the LLM parser detects changes.

The classification is tracked via the `portable` field (True, False, or None for unknown) and the `properties` dict. The LLM parser determines these from context. If the player tries to take something and the game says "That's hardly portable," the parser marks it as `portable=False`.

### Item-Room Cross-Reference

The map manager and item manager share data through the orchestrator. When the map manager records a room's contents (from parsing the room description), those items are also registered or updated in the item manager. The item manager's `location` field always reflects the best-known location, and the map manager's `items_here` list for each room is derived from querying the item manager.

### Inventory Limits

Many Infocom games have carry limits. The game agent needs to know about this, so the item manager tracks inventory count and alerts the game agent when it is near the limit. The exact limit is discovered empirically (by trying to take an item and getting a "your load is too heavy" response); the item manager records this threshold once discovered.

## 7. Puzzle Agent

### Puzzle Detection

The puzzle agent watches for game output patterns that signal an obstacle or challenge:

**Blocked access:** "The door is locked." "It's too dark to see." "The troll blocks your way." "You can't reach the ledge."

**Cryptic clues:** Inscriptions, notes, books, NPC dialogue that hints at a solution elsewhere.

**Failed actions that imply a solution exists:** "The glass case is firmly shut." implies it can be opened somehow. "Nothing happens." after pushing a button implies the button does something under different conditions.

**Conditional responses:** "You'll need a light source." "Perhaps you should bring something to trade."

Each detected puzzle is stored with a natural language description, the room where it was found, the turn it was detected on, and any items that seem related based on context.

### Cross-Reference and Suggestion Engine

This is the puzzle agent's most important function. Every few turns (or whenever the inventory changes or a new puzzle is detected), the puzzle agent runs a matching pass:

For each open puzzle, it considers every item in the inventory and every known item in the world. It uses LLM reasoning to evaluate whether any item might solve the puzzle. A key might open a locked door. A sword might defeat a troll. A lantern might illuminate a dark room. Food might distract a hungry creature.

When it finds a plausible match, it generates a suggestion with:

- The puzzle description and location.
- The proposed item(s) to use.
- The proposed action (e.g., "go to the stone hallway and use the brass key on the locked door").
- The navigation steps to get there (from the map manager).
- A confidence level (high: obvious match like key/lock, medium: plausible match, low: speculative).

High-confidence suggestions are surfaced to the game agent immediately. Medium and low suggestions are included in the context but flagged as speculative, letting the game agent decide whether to pursue them.

### Stuck Detection

The puzzle agent monitors the game agent's recent action history for patterns that indicate it is stuck:

**Repeated commands:** Sending the same command more than twice in the last 10 turns.

**Room cycling:** Visiting the same small set of rooms (3 or fewer) for more than 15 turns without making progress.

**Repeated failures:** Getting the same error response ("You can't do that.") to variations of the same action.

When stuck behavior is detected, the puzzle agent generates a strategic suggestion: explore an unexplored area, try a completely different item on the current puzzle, consult the map for rooms not visited recently, or attempt a creative or unusual action that the game agent might not have tried.

### Learning from Failures

When the game agent tries an action suggested by the puzzle agent and it fails, the failure is recorded in the puzzle's `attempts` list. The puzzle agent uses this history to avoid suggesting the same approach again and to refine its model of what the puzzle requires.

## 8. Database Schema and Replay System

### Schema Design Principles

Every piece of game state is timestamped by turn number. This enables full reconstruction of the game state at any point during the session. The `turns` table is the backbone of the replay system: each row is a complete snapshot of what happened during one turn.

### Replay Mechanics

To replay turn N, the web UI fetches the turn row, which contains the command, game output, room ID, inventory snapshot, and agent reasoning. It also fetches the map and item states as of that turn (by querying rooms, connections, and items where `first_seen_turn <= N`). The frontend renders the room, map graph, inventory panel, and transcript, and lets the user step forward or backward.

### Live Monitoring

During an active game, the orchestrator pushes each completed turn to the WebSocket. The web UI appends the new turn to the transcript, updates the map visualization, and refreshes the inventory and puzzle panels. A slight animation delay between turns makes it watchable at a comfortable pace.

The WebSocket message format is a JSON object:

```json
{
  "type": "turn",
  "turn_number": 42,
  "command": "open mailbox",
  "output": "Opening the small mailbox reveals a leaflet.",
  "room": {"id": "west_of_house", "name": "West of House", "exits": ["north", "south", "west"]},
  "inventory": ["leaflet"],
  "new_items": [{"id": "leaflet", "name": "leaflet", "location": "inventory"}],
  "puzzles_updated": [],
  "agent_reasoning": "The mailbox is here and I haven't opened it yet. Let me see what's inside.",
  "metrics": {"total_tokens": 847, "cost_estimate": 0.003}
}
```

## 9. Metrics and Observability

### Token and Cost Tracking

Every LLM call logs: the agent that made the call (game_agent, puzzle_agent, map_parser, item_parser), the provider and model used, input token count, output token count, cached token count, estimated cost (calculated from per-token rates defined in config), and wall-clock latency.

These are aggregated at the game level and available through the web UI:

- Total tokens used, broken down by agent.
- Total estimated cost, broken down by agent and provider.
- Average tokens per turn.
- Cache hit rate (cached tokens / total input tokens), per provider.
- Average latency per LLM call, per agent.
- Total turns played.
- Rooms discovered over time.
- Puzzles solved over time.
- Items collected over time.

### Turn-Level Debugging

The web UI's replay mode shows the full detail for any turn: the exact prompt sent to each LLM (game agent, puzzle agent, parsers), the raw response, the parsed structured output, and what state changes resulted. This is invaluable for diagnosing why the agent made a particular decision or why a room transition was misclassified.

## 10. Hook System and Extensibility

### Hook Interface

Hooks are Python classes that implement some or all of the hook methods. They register with the orchestrator at startup. The orchestrator calls each hook's methods synchronously by default, but hooks can opt into async execution if they involve I/O (like sending a WebSocket message or calling an image generation API).

### Planned Future Hooks

**Image generation hook:** On `on_room_enter`, generate an image of the room using the room description as the prompt. Use DALL-E, Stable Diffusion, or another image model. Cache generated images by room ID so revisits do not re-generate.

**Text-to-speech hook:** On `on_turn_end`, speak the game output aloud. Use ElevenLabs, OpenAI TTS, or another speech API. This turns the web UI into a narrated experience.

**Slack/Discord hook:** Post turn summaries to a channel so a group can watch the AI play.

**Analytics hook:** Stream metrics to an external system (Prometheus, Grafana, a CSV file) for long-running benchmarking across multiple games.

### Hook Registration

Hooks are specified by name in `config.json` under the `hooks` array. The orchestrator imports and instantiates them at startup. Custom hooks can be placed in the `hooks/` directory and referenced by module name.

## 11. Game Interface Details

### pyFrotz Integration

The `GameInterface` class wraps pyFrotz's `Frotz` object. On initialization, it loads the game file and captures the intro text. Each turn, it calls `do_command(cmd)` which returns a tuple of `(room_name, description)`. The interface also exposes `save(filename)` and `restore(filename)` for the orchestrator's death-recovery system.

### Output Parsing Challenges

Frotz output is raw text. Room names are not always clearly delimited from narrative text. Some games print the room name on its own line; others embed it in prose. The LLM parsers in the map and item managers handle this ambiguity. The game interface's job is simply to capture the full text output faithfully and pass it along.

### Death and Victory Detection

The game interface watches for known terminal patterns: "You have died", "your score is X of Y", "The End", "You have won", etc. Since these vary by game, the detection also uses a lightweight LLM call to classify the output as "normal", "death", or "victory". On death, the interface notifies the orchestrator, which can choose to restore and continue or end the session.

## 12. Prompt Engineering Guidelines

### Game Agent Prompt

The game agent's system prompt should establish these behaviors: methodical exploration before puzzle-solving, thorough examination of every new item and room feature, awareness of common text adventure conventions (examining things, looking under/behind objects, reading inscriptions), willingness to try creative and unusual commands, and a strong preference for following puzzle agent suggestions when confidence is high.

The prompt should NOT include any game-specific spoilers or walkthrough information. The system must be game-agnostic.

### Puzzle Agent Prompt

The puzzle agent's system prompt should emphasize lateral thinking and pattern matching. It should look for thematic connections (a gold key and a gold door, a tool that matches an obstacle), consider whether items might have secondary uses, and maintain a "difficulty ladder" sense that puzzles encountered later might require items found earlier.

### Parser Prompts (Map and Item)

These prompts are tightly focused on extraction. They should specify the exact JSON schema expected, provide a few examples of game output and the corresponding parsed output, and instruct the model to output `null` or empty values rather than hallucinate information not present in the text.

## 13. Edge Cases and Robustness

### Darkness and Light Sources

Many games have dark rooms where the player cannot see without a light source, and may die from wandering in the dark. The map manager should track which rooms are dark (from "It is pitch dark" output). The game agent should prioritize keeping a light source available and the item manager should track light source fuel levels.

### Mazes

Infocom games are famous for mazes where every room has the same or nearly identical description ("You are in a maze of twisty little passages, all alike"). Navigation is deliberately confusing: going north then south does not necessarily return you to where you started, exits listed in a room description may loop back to the same room, and standard mapping intuition breaks down entirely. The classic human technique is to drop a different item in each room, then use "look" to see which item is present, thereby giving each otherwise-identical room a unique marker.

AutoFrotz v2 handles mazes through a dedicated maze detection and resolution subsystem built into the map manager, with cooperation from the item manager and game agent.

#### Detection: Identifying Maze Entry

The map manager maintains a **description similarity index** across all known rooms. Each time a new room is visited, its description is compared against every existing room description using normalized string comparison (lowercased, whitespace-collapsed, punctuation-stripped). If the similarity exceeds a configurable threshold (default 95%), the map manager increments a **duplicate description counter** for that description text.

A maze condition is triggered when the system observes **three or more rooms** with near-identical descriptions within a short span of exploration (say, within 10 turns of each other). At that point, the map manager sets a `maze_active` flag and records the set of room IDs that appear to be maze rooms. It also records the **maze entry point**, which is the last room with a unique description visited before the duplicates started appearing.

A secondary detection heuristic catches mazes that use varied descriptions but still exhibit maze-like navigation. If the map manager detects that **four or more consecutive room transitions** produced rooms where going back the way you came did not return you to the previous room, it flags a potential maze even if the descriptions differ. This handles the rarer "maze of twisty little passages, all different" variant.

When a maze is detected, the map manager fires an `on_maze_detected(entry_room_id, suspected_room_ids)` hook event so the web UI can highlight it and the puzzle agent can register it as an open puzzle.

#### Resolution: The Item-Dropping Protocol

Once `maze_active` is True, the orchestrator switches the game agent into a specialized **maze-solving mode**. Normal exploration and puzzle-solving logic is suspended for the duration; the sole objective is to map the maze and find the exit(s).

**Phase 1: Inventory Preparation.** Before entering the maze, the system needs enough distinct droppable items to mark each room. The orchestrator queries the item manager for all portable items currently in inventory. It also queries for portable items stashed elsewhere in the world. If the total number of available portable items is fewer than a configurable minimum (default 8, since most Infocom mazes have 8-12 rooms), the game agent is instructed to go collect more items before re-entering the maze. Lightweight, low-value items are preferred as markers (the leaflet, the garlic, the lunch, etc.) since dropping a quest-critical item in a maze room is risky. The item manager provides a `get_droppable_items()` method that returns inventory items sorted by estimated importance (items not referenced by any open puzzle are considered safest to use as markers).

If the system truly cannot gather enough distinct items, it falls back to a **description annotation strategy**: it drops whatever items it has and supplements with examining room-specific micro-details (subtle wording differences, exit lists) that the LLM parser can use to differentiate rooms. This is less reliable but better than nothing.

**Phase 2: Systematic Exploration.** The maze solver uses a modified depth-first search. Starting from the maze entry room:

1. Drop a designated marker item. Record the association: "room at step 1 is marked with the brass lantern."
2. Try each available exit direction one at a time.
3. After each move, immediately execute "look" to see the room description and whether a previously dropped marker item is present.
4. If a dropped marker is visible, the system knows which room it is in (by looking up which item was dropped there). Record the connection: "going east from room-1 leads to room-3." Do not drop another item.
5. If no marker is visible, this is a new maze room. Drop the next available marker item and record it.
6. After cataloging exits from the current room, backtrack to the previous room using the known-working return direction (which may not be the compass opposite of how you got there; the solver discovers the correct backtrack direction empirically by trying directions and checking for the expected marker).
7. Continue until every reachable room has been visited and every exit from every room has been tested.

The map manager stores maze rooms with a special `maze_group` attribute linking them together. Their `room_id` values are generated as `maze_<group>_<sequence>` rather than from the (identical) room name, preventing ID collisions. Each room's node in the graph stores both the original game description and the marker item used to identify it.

**Phase 3: Exit Identification.** Any exit from a maze room that leads to a room with a unique (non-maze) description is recorded as a maze exit. There may be multiple exits, or the maze may contain items or puzzles within it. Once the maze is fully mapped, the map manager clears the `maze_active` flag and the orchestrator returns the game agent to normal mode.

**Phase 4: Marker Retrieval.** After the maze is mapped, the game agent is instructed to walk back through the maze (now trivially navigable since it is fully mapped) and pick up all the marker items. The item manager tracks which items were used as markers and their locations. The game agent collects them in an efficient order determined by the map manager's pathfinding.

#### Maze Data Model

Each maze group is tracked in a `maze_groups` structure within the map manager:

```python
@dataclass
class MazeGroup:
    group_id: str
    entry_room_id: str          # last non-maze room before entering
    room_ids: list[str]         # all rooms in this maze
    exit_room_ids: list[str]    # non-maze rooms reachable from maze exits
    markers: dict[str, str]     # room_id -> item_id used as marker
    fully_mapped: bool
    created_turn: int
    completed_turn: int | None
```

This is serialized to the database alongside the regular map data and included in replay state.

#### Edge Cases Within Mazes

**Random connections.** Some mazes have connections that are not static; going east from room A might lead to room B one time and room C the next. The maze solver detects this when a previously mapped connection produces an unexpected result (arriving at a room with a different marker than expected). When randomness is detected, the solver marks the connection as `random=True` and records all observed destinations. For pathfinding through random connections, the solver uses a probabilistic approach: try the direction, check where you ended up, and retry if necessary.

**One-way connections within the maze.** The solver already handles this naturally since it tests backtracking empirically rather than assuming compass-opposite reciprocity.

**Darkness in maze rooms.** If maze rooms are dark, the player cannot see dropped items (or anything else). The solver detects this when "look" returns a darkness message. It instructs the game agent to ensure a light source is active before entering the maze. If the light source is fuel-limited (like the lantern in Zork, which has a finite number of turns), the solver estimates remaining fuel against the expected number of moves needed and warns the game agent if the budget is tight.

**The thief (Zork-specific but representative).** In Zork, a thief wanders the maze and steals items, including dropped markers. The maze solver detects this when a previously marked room suddenly has no marker on revisit. It re-drops a replacement marker and flags the maze group as having an active item-stealing NPC. It also alerts the puzzle agent, since dealing with the thief is itself a puzzle.

**Nested mazes.** Rare, but possible. If the solver finds that a maze exit leads into another set of identical-description rooms, it creates a second maze group and maps it independently. Pathfinding across the full graph still works normally since maze rooms are regular nodes in the DiGraph.

#### Integration with Other Systems

The puzzle agent registers each detected maze as an open puzzle of type "maze" with the entry location. If the maze has internal puzzles (items to find, NPCs to deal with), those become sub-puzzles. The puzzle agent does not attempt to suggest solutions for maze navigation; that is entirely handled by the maze solver's algorithmic approach. The puzzle agent's role is limited to flagging the maze's existence and noting when it has been fully mapped and resolved.

The game agent's prompt includes awareness of mazes as a general concept ("some areas of the game are mazes where rooms look identical and navigation is non-intuitive"), but the actual maze-solving behavior is driven by the orchestrator's mode switch rather than by the game agent's reasoning. The game agent simply follows the orchestrator's instructions during maze mode: "drop the leaflet", "go north", "look", etc. This avoids wasting expensive game-agent LLM calls on what is fundamentally an algorithmic problem.

The web UI renders maze rooms as a distinct cluster in the map visualization, with marker items shown as labels on each node. During live play, watching the maze solver work should be one of the more visually interesting parts of the session.

### Timed Events and Randomness

Some games have timed sequences (a bomb counting down, a flood rising) or random events (the thief in Zork who steals items and moves around). The puzzle agent should detect urgency cues in game text and prioritize those puzzles. The item manager should handle items that disappear from inventory unexpectedly (stolen by the thief) by marking their location as "unknown" rather than assuming they are still in inventory.

### Inventory Management

When the game agent needs to carry more items than the limit allows, it should designate a "stash room" (a safe, central location) and ferry items as needed. The item manager and map manager together provide the information to do this: what items are where, and how to navigate between locations.

### Save/Restore Strategy

The orchestrator maintains a rotating set of save files (e.g., 3 slots). It saves automatically every N turns and before attempting risky actions (if the game agent's reasoning mentions uncertainty). On death, it restores the most recent save and the game agent receives a note in its context: "You died attempting [action]. Restored to [N] turns ago. Avoid repeating the same approach."

## 14. Performance Considerations

### LLM Call Budget

Each turn involves up to 4 LLM calls (map parser, item parser, puzzle agent, game agent). At typical token rates, a 1000-turn game session will make roughly 4000 LLM calls. To keep costs reasonable:

- Use the smallest effective model for parser tasks (gpt-4o-mini or similar).
- Use a capable but cost-effective model for the puzzle agent.
- Reserve the most capable model for the game agent.
- Cache hit rates above 50% are achievable with proper prompt structuring, cutting effective input costs nearly in half.
- The puzzle agent does not need to run a full evaluation every single turn. Running every 3-5 turns or whenever a trigger condition is met (new item found, new room entered, failed action) reduces costs significantly.

### Database Performance

SQLite is sufficient for this workload. A 1000-turn game produces roughly 1000 rows in the `turns` table and a few hundred rows in `rooms`, `items`, and `puzzles`. The web UI's replay queries are simple indexed lookups. No special optimization is needed beyond standard SQLite practices (WAL mode for concurrent reads during live play, indexes on `game_id` and `turn_number`).

## 15. Future Extensions (Out of Scope for v1)

These are anticipated but not part of the initial implementation. The architecture should not prevent them.

**Buddy mode:** Two game agents collaborating, taking turns suggesting actions, with different personalities or strategies. The orchestrator alternates which agent gets to decide the action.

**Human-in-the-loop mode:** A human player can override the game agent's decision through the web UI, then let the AI continue. Useful for getting past a spot where the AI is stuck.

**Multi-game benchmarking:** Run the system against a suite of games and produce comparative metrics: how many turns to complete, cost per game, rooms explored, puzzles solved.

**Adaptive difficulty:** If the game agent is making fast progress, reduce the puzzle agent's intervention. If it is struggling, increase it.

**Transfer learning across games:** Use the puzzle database from completed games to seed heuristics for new games (e.g., "keys usually open locked doors" becomes a high-priority suggestion pattern).