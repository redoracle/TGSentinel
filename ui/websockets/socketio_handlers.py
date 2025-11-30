"""Socket.IO event handlers for TG Sentinel UI.

This module handles WebSocket connections for real-time updates including:
- Client connection/disconnection
- Log broadcasting
- Status updates
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_socketio_handlers(socketio: Any) -> None:
    """Register Socket.IO event handlers.

    Args:
        socketio: Flask-SocketIO instance
    """

    @socketio.on("connect")
    def handle_connect():
        """Handle client connection event."""
        logger.info("Client connected via Socket.IO")

    @socketio.on("disconnect")
    def handle_disconnect():
        """Handle client disconnection event."""
        logger.info("Client disconnected from Socket.IO")

    logger.info("Socket.IO handlers registered")


def broadcast_log(socketio: Any, level: str, message: str) -> None:
    """Broadcast a log message to all connected clients.

    Args:
        socketio: Flask-SocketIO instance
        level: Log level (info, warning, error, etc.)
        message: Log message to broadcast
    """
    socketio.emit("log", {"level": level, "message": message})
