# Web Stack Builder - Memory

## CRITICAL: Corrections from authoritative spec (CLAUDE.md / GAME.md)

The agent definition file (`.claude/agents/web-stack-builder.md`) contains errors that conflict with the authoritative CLAUDE.md and GAME.md. Always follow the spec files over the agent definition when there is a conflict.

### Database layer: stdlib sqlite3, NOT SQLAlchemy
CLAUDE.md explicitly states:
> **Database:** SQLite via `sqlite3` (stdlib) for game state, metrics, and replay

- Do NOT use SQLAlchemy. Use Python's built-in `sqlite3` module.
- Do NOT use Alembic for migrations. Schema management is done directly via SQL in `storage/database.py`.
- The database module uses raw SQL queries and dataclasses, not ORM models.

### No authentication
The spec does not mention authentication anywhere. The web UI is a local monitoring/replay tool, not a multi-user service.
- Do NOT add authentication middleware to FastAPI endpoints.
- Do NOT add authentication for WebSocket connections.
- Keep endpoints simple and open.

### This is a single-AI game system, not multiplayer
The project is an autonomous AI playing single-player text adventure games. There are no "players" in the multiplayer sense. The web UI is for monitoring and replaying AI game sessions.

### Correct endpoint list (from CLAUDE.md)
```
GET  /api/games                          - list all game sessions
GET  /api/games/{game_id}                - game metadata
GET  /api/games/{game_id}/turns          - all turns for a game
GET  /api/games/{game_id}/turns/{turn_n} - single turn with full state
GET  /api/games/{game_id}/map            - current map as JSON (nodes + edges)
GET  /api/games/{game_id}/items          - current item registry
GET  /api/games/{game_id}/puzzles        - current puzzle state
GET  /api/games/{game_id}/metrics        - aggregated LLM usage stats
WS   /ws/live/{game_id}                  - WebSocket for live game events
```

### Frontend: Vanilla JS, not a framework
CLAUDE.md specifies:
> **Frontend:** Vanilla JS with server-sent events or WebSocket for live game watching

Static files are in `web/static/` with `index.html`, `app.js`, `style.css`.

## Storage Layer Implementation (2026-02-08)

### Location and Structure
- `/home/ubuntu/workspace/autofrotz2/autofrotz/storage/models.py` - All dataclasses
- `/home/ubuntu/workspace/autofrotz2/autofrotz/storage/database.py` - SQLite manager
- `/home/ubuntu/workspace/autofrotz2/autofrotz/storage/__init__.py` - Public exports

### Key Implementation Details

**Database Schema:**
- 8 tables: games, turns, rooms, connections, items, puzzles, maze_groups, metrics
- Uses WAL mode for better concurrency: `PRAGMA journal_mode=WAL`
- JSON serialization for list/dict fields using stdlib `json` module
- Indexes on `(game_id, turn_number)` and individual `game_id` columns
- UNIQUE constraints: `(game_id, turn_number)` for turns, `(game_id, room_id)` for rooms, `(game_id, item_id)` for items, etc.
- UPSERT pattern using `ON CONFLICT ... DO UPDATE SET` for idempotent saves

**Boolean Storage:**
- SQLite stores booleans as INTEGER (0/1)
- Convert Python bool to int on insert: `1 if value else 0`
- Convert back on read: `bool(row['field'])`
- Nullable booleans (like Item.portable) need special handling: check for None before converting

**Context Manager Support:**
- Database class implements `__enter__` and `__exit__` for use with `with` statement
- Automatically closes connection on context exit

**Logging:**
- Uses stdlib `logging` module
- DEBUG for save operations, INFO for major operations (game start/end)
- Logs include game_id and relevant identifiers

### Testing Pattern
All storage operations tested with in-memory database (`:memory:`):
```python
from autofrotz.storage import Database, Room, Item, ...
db = Database(':memory:')
game_id = db.create_game('test.z5')
# ... test operations ...
```

### Known Issues
- `update_maze_group()` method has awkward signature (no game_id parameter) - documented as legacy method, use `save_maze_group()` instead
- `datetime.utcnow()` is deprecated in Python 3.12+ - should use `datetime.now(datetime.UTC)` but kept for 3.11 compatibility

### Dependencies
- No external dependencies for storage layer (uses stdlib only)
- `json` for serialization
- `sqlite3` for database
- `dataclasses` for models
- `logging` for logging
- `pathlib` for file path handling

## Web Stack Implementation (2026-02-08)

### Location and Structure
- `/home/ubuntu/workspace/autofrotz2/autofrotz/web/server.py` - FastAPI app with REST + WebSocket
- `/home/ubuntu/workspace/autofrotz2/autofrotz/web/static/index.html` - Single-page UI
- `/home/ubuntu/workspace/autofrotz2/autofrotz/web/static/app.js` - Vanilla JS frontend
- `/home/ubuntu/workspace/autofrotz2/autofrotz/web/static/style.css` - Dark theme styling

### FastAPI Server Architecture

**ConnectionManager Pattern:**
- Singleton instance exported as `connection_manager`
- Manages list of active WebSocket connections
- `connect(websocket)`, `disconnect(websocket)`, `broadcast(message)`
- Auto-cleanup of disconnected clients
- Hook system will import and use this for live event broadcasting

**Database Access:**
- Uses context manager pattern: `with Database(DATABASE_PATH) as db:`
- Default database path: `"autofrotz.db"` (configurable via module-level `DATABASE_PATH`)
- All endpoints handle database errors gracefully with 500 status codes

**REST Endpoints:**
All return `JSONResponse` with appropriate status codes. See CLAUDE.md for full list.

**WebSocket Endpoint:**
- `WS /ws/live/{game_id}` - Bidirectional WebSocket connection
- Sends initial `{type: "connected"}` message on connect
- Echo/pong support for keepalive
- Auto-reconnect logic on client side (5 second delay)
- Handles `WebSocketDisconnect` gracefully

**Static File Serving:**
- Mounted LAST after API routes using `StaticFiles`
- Serves from `web/static/` directory
- `html=True` enables `index.html` as default

### Frontend Architecture

**Vanilla JS - No Framework:**
- Pure JavaScript, no build step needed
- Event-driven architecture with global `state` object
- Async/await for API calls
- WebSocket for live mode, polling/REST for replay mode

**Mode Detection:**
- Checks game `status` field to determine mode
- `status === 'playing'` → Live mode with WebSocket
- Other statuses → Replay mode with REST API

**Map Rendering:**
- SVG-based visualization in `#map-svg`
- Simple grid layout (calculated from sqrt of node count)
- Node positions: `{x, y}` based on index
- Edge rendering with direction labels and blocked/teleport styling
- Current room highlighted with `node-current` class

**UI Components:**
- Transcript: Monospace, turn-by-turn entries with reasoning collapsible details
- Map: SVG with nodes (rects) and edges (lines with labels)
- Inventory: List of items currently in inventory
- Puzzles: List with status badges (open=yellow, solved=green, abandoned=red)
- Metrics: Grid of totals + collapsible agent breakdown
- Replay controls: Standard media controls + speed slider (0.5x to 5x)

### CSS Styling

**Grid Layout:**
- CSS Grid with 2 columns, 4 rows
- Transcript: Left side, rows 1-3 (tall)
- Map: Right top (row 1)
- Inventory: Right middle (row 2)
- Puzzles: Right bottom (row 3)
- Metrics: Full width bottom (row 4)
- Replay controls: Footer bar (outside grid, hidden by default)

### Integration Points

**WebMonitorHook (future):**
Should import and use the connection manager:
```python
from autofrotz.web.server import connection_manager
await connection_manager.broadcast({
    "type": "turn",
    "turn_number": n,
    "command": cmd,
    "output": output,
    "room_id": room_id,
    "reasoning": reasoning
})
```

**Running the Server:**
```bash
python3 -m uvicorn autofrotz.web.server:app --host 0.0.0.0 --port 8080
```

### Testing Status

**Verified:**
- Server imports successfully without errors
- Uvicorn starts and shuts down cleanly
- Static files mounted correctly

**Not Yet Tested:**
- Actual database queries (no test data yet)
- WebSocket live events (no hook implementation yet)
- Replay mode with real turn data
- Map rendering with actual room/connection data
