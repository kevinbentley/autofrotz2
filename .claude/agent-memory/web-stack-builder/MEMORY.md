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
