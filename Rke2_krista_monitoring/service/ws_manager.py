"""
WebSocket connection manager for broadcasting live cluster updates.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Set

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts updates to all clients."""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        log.info("WebSocket client connected (%d total)", len(self._connections))

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections.discard(ws)
        log.info("WebSocket client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, message: Dict[str, Any]):
        """Send a message to all connected clients. Removes dead connections."""
        data = json.dumps(message, default=str)
        dead: List[WebSocket] = []

        async with self._lock:
            targets = list(self._connections)

        for ws in targets:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)
