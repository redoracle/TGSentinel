"""WebSocket handlers for TG Sentinel UI.

This package contains Socket.IO event handlers for real-time communication.
"""

from .socketio_handlers import register_socketio_handlers

__all__ = ["register_socketio_handlers"]
