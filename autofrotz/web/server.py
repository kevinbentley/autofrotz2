"""FastAPI web server for AutoFrotz monitoring and replay."""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from typing import Optional
import asyncio
import json
import logging
from pathlib import Path

from autofrotz.storage.database import Database

logger = logging.getLogger(__name__)

# Configurable database path
DATABASE_PATH = "autofrotz.db"


class ConnectionManager:
    """Manages WebSocket connections for live game monitoring."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, websocket: WebSocket):
        """Accept and register a new WebSocket connection."""
        # Capture the running event loop so sync code can schedule broadcasts
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Send a message to all connected clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.disconnect(conn)


# Global connection manager instance
connection_manager = ConnectionManager()

# Create FastAPI app
app = FastAPI(title="AutoFrotz v2 Monitoring", version="2.0.0")


@app.on_event("startup")
async def startup_event():
    """Capture the event loop so sync code can schedule broadcasts."""
    connection_manager.loop = asyncio.get_running_loop()


@app.get("/api/games")
async def get_games():
    """List all game sessions."""
    try:
        with Database(DATABASE_PATH) as db:
            games = db.get_all_games()
            return JSONResponse([
                {
                    "game_id": g.game_id,
                    "game_file": g.game_file,
                    "start_time": g.start_time,
                    "end_time": g.end_time,
                    "status": g.status,
                    "total_turns": g.total_turns
                }
                for g in games
            ])
    except Exception as e:
        logger.error(f"Error fetching games: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/games/{game_id}")
async def get_game(game_id: int):
    """Get metadata for a single game session."""
    try:
        with Database(DATABASE_PATH) as db:
            game = db.get_game(game_id)
            if not game:
                return JSONResponse({"error": "Game not found"}, status_code=404)

            return JSONResponse({
                "game_id": game.game_id,
                "game_file": game.game_file,
                "start_time": game.start_time,
                "end_time": game.end_time,
                "status": game.status,
                "total_turns": game.total_turns
            })
    except Exception as e:
        logger.error(f"Error fetching game {game_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/games/{game_id}/turns")
async def get_turns(
    game_id: int,
    limit: Optional[int] = Query(None, ge=1),
    offset: Optional[int] = Query(0, ge=0)
):
    """Get all turns for a game with optional pagination."""
    try:
        with Database(DATABASE_PATH) as db:
            turns = db.get_turns(game_id)

            if not turns:
                return JSONResponse([])

            # Apply pagination
            if offset:
                turns = turns[offset:]
            if limit:
                turns = turns[:limit]

            return JSONResponse([
                {
                    "turn_number": t.turn_number,
                    "timestamp": t.timestamp,
                    "command_sent": t.command_sent,
                    "game_output": t.game_output,
                    "room_id": t.room_id,
                    "inventory_snapshot": t.inventory_snapshot,
                    "agent_reasoning": t.agent_reasoning
                }
                for t in turns
            ])
    except Exception as e:
        logger.error(f"Error fetching turns for game {game_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/games/{game_id}/turns/{turn_number}")
async def get_turn(game_id: int, turn_number: int):
    """Get a single turn with full state."""
    try:
        with Database(DATABASE_PATH) as db:
            turn = db.get_turn(game_id, turn_number)
            if not turn:
                return JSONResponse({"error": "Turn not found"}, status_code=404)

            return JSONResponse({
                "turn_number": turn.turn_number,
                "timestamp": turn.timestamp,
                "command_sent": turn.command_sent,
                "game_output": turn.game_output,
                "room_id": turn.room_id,
                "inventory_snapshot": turn.inventory_snapshot,
                "agent_reasoning": turn.agent_reasoning
            })
    except Exception as e:
        logger.error(f"Error fetching turn {turn_number} for game {game_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/games/{game_id}/map")
async def get_map(game_id: int):
    """Get current map as JSON (nodes + edges)."""
    try:
        with Database(DATABASE_PATH) as db:
            rooms = db.get_rooms(game_id)
            connections = db.get_connections(game_id)

            nodes = [
                {
                    "id": r.room_id,
                    "name": r.name,
                    "description": r.description,
                    "visited": r.visited,
                    "visit_count": r.visit_count,
                    "items_here": r.items_here,
                    "maze_group": r.maze_group,
                    "maze_marker_item": r.maze_marker_item,
                    "is_dark": r.is_dark
                }
                for r in rooms
            ]

            edges = [
                {
                    "from": c.from_room_id,
                    "to": c.to_room_id,
                    "direction": c.direction,
                    "bidirectional": c.bidirectional,
                    "blocked": c.blocked,
                    "teleport": c.teleport,
                    "random": c.random,
                    "observed_destinations": c.observed_destinations
                }
                for c in connections
            ]

            return JSONResponse({
                "nodes": nodes,
                "edges": edges
            })
    except Exception as e:
        logger.error(f"Error fetching map for game {game_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/games/{game_id}/items")
async def get_items(game_id: int):
    """Get all items for a game."""
    try:
        with Database(DATABASE_PATH) as db:
            items = db.get_items(game_id)

            return JSONResponse([
                {
                    "item_id": i.item_id,
                    "name": i.name,
                    "description": i.description,
                    "location": i.location,
                    "portable": i.portable,
                    "properties": i.properties,
                    "first_seen_turn": i.first_seen_turn,
                    "last_seen_turn": i.last_seen_turn
                }
                for i in items
            ])
    except Exception as e:
        logger.error(f"Error fetching items for game {game_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/games/{game_id}/puzzles")
async def get_puzzles(game_id: int):
    """Get all puzzles for a game."""
    try:
        with Database(DATABASE_PATH) as db:
            puzzles = db.get_puzzles(game_id)

            return JSONResponse([
                {
                    "puzzle_id": p.puzzle_id,
                    "description": p.description,
                    "status": p.status,
                    "location": p.location,
                    "related_items": p.related_items,
                    "attempts": p.attempts,
                    "created_turn": p.created_turn,
                    "solved_turn": p.solved_turn
                }
                for p in puzzles
            ])
    except Exception as e:
        logger.error(f"Error fetching puzzles for game {game_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/games/{game_id}/metrics")
async def get_metrics(game_id: int):
    """Get aggregated metrics with per-agent breakdowns and totals."""
    try:
        with Database(DATABASE_PATH) as db:
            metrics = db.get_metrics(game_id)

            if not metrics:
                return JSONResponse({
                    "total": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "cost_estimate": 0.0,
                        "total_latency_ms": 0
                    },
                    "by_agent": {}
                })

            # Aggregate by agent
            by_agent = {}
            total_input = 0
            total_output = 0
            total_cached = 0
            total_cost = 0.0
            total_latency = 0

            for m in metrics:
                if m.agent_name not in by_agent:
                    by_agent[m.agent_name] = {
                        "provider": m.provider,
                        "model": m.model,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "cost_estimate": 0.0,
                        "total_latency_ms": 0,
                        "call_count": 0
                    }

                agent_data = by_agent[m.agent_name]
                agent_data["input_tokens"] += m.input_tokens
                agent_data["output_tokens"] += m.output_tokens
                agent_data["cached_tokens"] += m.cached_tokens
                agent_data["cost_estimate"] += m.cost_estimate
                agent_data["total_latency_ms"] += m.latency_ms
                agent_data["call_count"] += 1

                total_input += m.input_tokens
                total_output += m.output_tokens
                total_cached += m.cached_tokens
                total_cost += m.cost_estimate
                total_latency += m.latency_ms

            return JSONResponse({
                "total": {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "cached_tokens": total_cached,
                    "cost_estimate": round(total_cost, 4),
                    "total_latency_ms": total_latency
                },
                "by_agent": by_agent
            })
    except Exception as e:
        logger.error(f"Error fetching metrics for game {game_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.websocket("/ws/live/{game_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str):
    """WebSocket endpoint for live game events."""
    await connection_manager.connect(websocket)
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "game_id": game_id,
            "message": "Connected to live game feed"
        })

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_text()
            # Echo back for ping/pong if needed
            await websocket.send_json({
                "type": "pong",
                "data": data
            })
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
        logger.info(f"WebSocket disconnected for game {game_id}")
    except Exception as e:
        logger.error(f"WebSocket error for game {game_id}: {e}")
        connection_manager.disconnect(websocket)


# Mount static files LAST (after API routes)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
else:
    logger.warning(f"Static directory not found: {static_dir}")
