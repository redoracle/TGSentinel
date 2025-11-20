"""
Console API Routes Blueprint

Handles interactive console commands for administrative operations.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# Create blueprint
console_bp = Blueprint("console", __name__, url_prefix="/api/console")

# Dependencies (injected during registration)
_query_all: Callable | None = None
_get_stream_name: Callable | None = None
reload_config: Callable | None = None
api_clean_database: Callable | None = None
config = None
redis_client = None
socketio = None


def init_blueprint(
    query_all: Callable,
    get_stream_name: Callable,
    reload_config_fn: Callable,
    clean_database_fn: Callable | None,  # Optional - moved to admin blueprint
    config_obj: Any,
    redis_obj: Any,
    socketio_obj: Any,
    ensure_init_decorator: Callable,
) -> None:
    """Initialize blueprint with dependencies."""
    global _query_all, _get_stream_name, reload_config, api_clean_database
    global config, redis_client, socketio

    _query_all = query_all
    _get_stream_name = get_stream_name
    reload_config = reload_config_fn
    api_clean_database = clean_database_fn
    config = config_obj
    redis_client = redis_obj
    socketio = socketio_obj


def _flush_redis() -> int:
    """Flush Redis stream and common caches."""
    count = 0
    if not redis_client or not _get_stream_name:
        return 0
    try:
        stream_name = _get_stream_name()
        try:
            count += int(redis_client.xlen(stream_name) or 0)
        except Exception:
            pass
        try:
            redis_client.delete(stream_name)
        except Exception:
            pass

        # Delete common caches
        patterns = [
            "tgsentinel:participant:*",
            "tgsentinel:user_avatar:*",
            "tgsentinel:chat_avatar:*",
            "tgsentinel:telegram_users_cache",
            "tgsentinel:chats_cache",
            "tgsentinel:user_info",
        ]
        for pat in patterns:
            try:
                scan_iter = getattr(redis_client, "scan_iter", None)
                if callable(scan_iter):
                    # scan_iter returns a generator, convert to list
                    try:
                        keys = [k for k in scan_iter(match=pat)]  # type: ignore
                    except Exception:
                        keys = []
                else:
                    result = redis_client.keys(pat)  # type: ignore
                    keys = list(result) if result else []
            except Exception:
                keys = []
            if keys:
                try:
                    count += int(redis_client.delete(*keys) or 0)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Flush Redis encountered an error: %s", exc)
    return count


@console_bp.post("/command")
def api_console_command():
    """Execute administrative console commands."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    command = payload.get("command", "").strip()
    logger.info("Console command requested: %s", command)

    # Command: /flush redis
    if command.lower().startswith("/flush") and "redis" in command.lower():
        deleted = _flush_redis()
        # Note: socketio.emit() removed - requires Flask-SocketIO request context
        return jsonify({"status": "accepted", "command": command, "deleted": deleted})

    # Command: /purge db
    if command.lower().startswith("/purge") and "db" in command.lower():
        # Require explicit confirmation before destructive database wipe
        confirmation = payload.get("confirm")
        if not confirmation or confirmation != "DELETE_ALL_DATA":
            return (
                jsonify(
                    {
                        "status": "confirmation_required",
                        "message": "Database purge requires explicit confirmation. Send 'confirm': 'DELETE_ALL_DATA' to proceed.",
                    }
                ),
                400,
            )

        # Call the existing clean database endpoint logic
        try:
            if not api_clean_database:
                return (
                    jsonify({"status": "error", "message": "Service not initialized"}),
                    503,
                )

            result = api_clean_database()
            if isinstance(result, tuple):
                response, status_code = result
                if status_code != 200:
                    return response, status_code
                data = response.get_json()
            else:
                data = result.get_json()

            return jsonify(
                {
                    "status": "accepted",
                    "command": command,
                    "deleted": data.get("deleted", 0),
                    "redis_cleared": data.get("redis_cleared", 0),
                }
            )
        except Exception as exc:
            logger.error("Purge DB failed: %s", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500

    # Command: vacuum
    if command.lower() == "vacuum":
        # Proxy VACUUM request to Sentinel database
        try:
            import requests

            sentinel_api_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            )

            # Forward VACUUM request to Sentinel
            response = requests.post(
                f"{sentinel_api_url}/database/vacuum",
                timeout=120,  # VACUUM can take time
            )

            if response.ok:
                data = response.json()
                if data.get("status") == "ok":
                    result = data.get("data", {})
                    reclaimed_mb = result.get("reclaimed_mb", 0)
                    logger.info(
                        "Sentinel database VACUUM completed via proxy. Reclaimed %.2f MB",
                        reclaimed_mb,
                    )
                    return jsonify(
                        {
                            "status": "accepted",
                            "command": command,
                            "message": f"Database optimized. Reclaimed {reclaimed_mb:.2f} MB",
                            "size_before_mb": result.get("size_before_mb", 0),
                            "size_after_mb": result.get("size_after_mb", 0),
                        }
                    )
                else:
                    error_msg = data.get("error", "Unknown error from Sentinel")
                    logger.error(f"Sentinel VACUUM failed: {error_msg}")
                    return jsonify({"status": "error", "message": error_msg}), 500
            else:
                logger.error(
                    f"Sentinel VACUUM request failed: HTTP {response.status_code}"
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Sentinel request failed: {response.status_code}",
                        }
                    ),
                    502,
                )

        except Exception as exc:
            logger.error("VACUUM proxy failed: %s", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500

    # Command: /reload config
    if command.lower().startswith("/reload") and "config" in command.lower():
        try:
            if not reload_config:
                return (
                    jsonify({"status": "error", "message": "Service not initialized"}),
                    503,
                )

            reload_config()
            # Note: socketio.emit() removed - requires Flask-SocketIO request context
        except Exception as exc:
            logger.warning("Reload config failed: %s", exc)
        return jsonify(
            {
                "status": "accepted",
                "command": command,
                "message": "Configuration reloaded successfully",
            }
        )

    # Command: rotate (log rotation)
    if command.lower() == "rotate":
        # Log rotation command - placeholder for future implementation
        logger.info("Log rotation requested (not yet implemented)")
        return jsonify(
            {
                "status": "accepted",
                "command": command,
                "message": "Log rotation not yet implemented. Logs are rotated automatically by Docker/system.",
            }
        )

    # Command: backup (session backup)
    if command.lower() == "backup":
        # Session backup command - placeholder for future implementation
        logger.info("Session backup requested (not yet implemented)")
        return jsonify(
            {
                "status": "accepted",
                "command": command,
                "message": "Session backup not yet implemented. Use session download feature instead.",
            }
        )

    # Default: accept with no-op
    return jsonify(
        {
            "status": "accepted",
            "command": command,
            "message": f"Command '{command}' executed (no specific handler)",
        }
    )
