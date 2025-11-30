"""
Console API Routes Blueprint

Handles interactive console commands for administrative operations.
"""

import logging
import os
from typing import Any, Callable

import requests
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
                        "message": "Database purge requires explicit confirmation. "
                        "Send 'confirm': 'DELETE_ALL_DATA' to proceed.",
                    }
                ),
                400,
            )  # Call the existing clean database endpoint logic
        try:
            if api_clean_database:
                result = api_clean_database()
                if isinstance(result, tuple):
                    response, status_code = result
                    if status_code != 200:
                        return response, status_code
                    data = response.get_json()
                else:
                    data = result.get_json()
            else:
                # Fallback: call Sentinel purge endpoint directly
                sentinel_base = os.getenv(
                    "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
                ).rstrip("/")
                resp = requests.post(f"{sentinel_base}/database/purge", timeout=30)
                data = resp.json()
                if resp.status_code != 200:
                    return jsonify(data), resp.status_code

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
        # Proxy VACUUM request to Sentinel database (async job-based API)
        try:
            raw_base = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
            sentinel_api_url = raw_base.rstrip("/")

            # Build request URL
            url = f"{sentinel_api_url}/database/vacuum?maintenance_window=true"

            # Add admin authentication header
            headers = {}
            admin_token = os.getenv("ADMIN_TOKEN", "")
            if admin_token:
                headers["X-Admin-Token"] = admin_token

            # Initiate VACUUM job (returns 202 Accepted with job_id)
            response = requests.post(url, timeout=120, headers=headers)

            if response.status_code == 202:
                # Job accepted - extract job_id
                data = response.json()
                if data.get("status") == "accepted":
                    job_data = data.get("data", {})
                    job_id = job_data.get("job_id")
                    status_url = job_data.get(
                        "status_url", f"/api/database/vacuum/status/{job_id}"
                    )

                    logger.info(f"Sentinel VACUUM job accepted: {job_id}")

                    return jsonify(
                        {
                            "status": "accepted",
                            "command": command,
                            "message": "Database VACUUM started. This may take several minutes.",
                            "job_id": job_id,
                            "status_url": status_url,
                        }
                    )
                else:
                    error_msg = data.get("error", "Unknown response format")
                    logger.error(f"Sentinel VACUUM unexpected response: {error_msg}")
                    return jsonify({"status": "error", "message": error_msg}), 500

            elif response.status_code == 409:
                # Conflict - VACUUM already in progress
                try:
                    data = response.json()
                    error_msg = data.get("error", "VACUUM already in progress")
                except Exception:
                    error_msg = "VACUUM already in progress"
                logger.warning(f"Sentinel VACUUM conflict: {error_msg}")
                return jsonify({"status": "error", "message": error_msg}), 409

            else:
                # Error response
                logger.error(
                    f"Sentinel VACUUM request failed: HTTP {response.status_code}"
                )
                try:
                    data = response.json()
                    error_msg = (
                        data.get("error") or data.get("message") or response.text
                    )
                except Exception:
                    error_msg = response.text or f"HTTP {response.status_code}"

                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": error_msg,
                        }
                    ),
                    502,
                )

        except Exception as exc:
            logger.error("VACUUM proxy failed: %s", exc, exc_info=True)
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
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Session backup not implemented. Use session download instead.",
                }
            ),
            501,
        )

    # Command: /restart (restart Sentinel container)
    if command.lower() == "/restart":
        try:
            logger.info("Sentinel restart requested via console")

            # Forward restart request to Sentinel API
            # Sentinel will trigger graceful shutdown; Docker will restart the container
            sentinel_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            ).rstrip("/")
            response = requests.post(
                f"{sentinel_url}/restart",
                headers={"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")},
                timeout=5,
            )

            if response.status_code == 200:
                # Safe JSON parsing with fallback
                try:
                    data = response.json()
                    message = data.get("data", {}).get(
                        "message", "Sentinel restarting..."
                    )
                except (ValueError, TypeError, AttributeError):
                    message = "Sentinel restarting..."

                logger.info("Sentinel restart initiated successfully")
                return jsonify(
                    {
                        "status": "accepted",
                        "command": command,
                        "message": message,
                    }
                )
            else:
                # Safe JSON parsing with fallback to text
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", "Unknown error")
                except (ValueError, TypeError):
                    error_msg = response.text or "Unknown error"

                logger.error(f"Failed to restart sentinel: {error_msg}")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "command": command,
                            "message": f"Restart failed: {error_msg}",
                        }
                    ),
                    response.status_code,
                )
        except requests.exceptions.RequestException as exc:
            logger.error(f"Failed to connect to Sentinel API: {exc}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "command": command,
                        "message": f"Cannot reach Sentinel API: {exc}",
                    }
                ),
                503,
            )
        except Exception as exc:
            logger.error(f"Unexpected error restarting sentinel: {exc}")
            return (
                jsonify({"status": "error", "command": command, "message": str(exc)}),
                500,
            )

    # Default: accept with no-op
    return jsonify(
        {
            "status": "accepted",
            "command": command,
            "message": f"Command '{command}' executed (no specific handler)",
        }
    )


@console_bp.get("/vacuum/status/<job_id>")
def api_vacuum_status(job_id: str):
    """Check status of a VACUUM job by proxying to Sentinel API.

    Returns:
        JSON with job status, progress, and results if completed
    """
    try:
        sentinel_api_base = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        ).rstrip("/")
        url = f"{sentinel_api_base}/database/vacuum/status/{job_id}"

        # Add admin authentication header
        headers = {}
        admin_token = os.getenv("ADMIN_TOKEN", "")
        if admin_token:
            headers["X-Admin-Token"] = admin_token

        response = requests.get(url, timeout=30, headers=headers)

        if response.ok:
            data = response.json()
            return jsonify(data), 200
        else:
            # Forward error response
            try:
                data = response.json()
                return jsonify(data), response.status_code
            except Exception:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": f"Sentinel returned status {response.status_code}",
                        }
                    ),
                    response.status_code,
                )

    except Exception as exc:
        logger.error(f"VACUUM status check failed: {exc}", exc_info=True)
        return jsonify({"status": "error", "data": None, "error": str(exc)}), 500
