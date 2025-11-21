"""Sentinel HTTP API Server

This module provides a minimal HTTP/JSON API for the sentinel worker to:
1. Accept session file uploads from the UI
2. Provide health and status information
3. Expose alerts and stats to the UI

The API runs in a separate thread alongside the main sentinel worker.
"""

import base64
import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time as time_module
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

logger = logging.getLogger("tgsentinel.api")

# Global state shared with main worker
_sentinel_state: Dict[str, Any] = {
    "authorized": False,
    "connected": False,
    "user_info": None,
    "last_sync": None,
    "session_path": None,
}

_redis_client: Any = None
_config: Any = None
_engine: Any = None

# Track vacuum jobs (job_id -> status)
_vacuum_jobs: Dict[str, Dict[str, Any]] = {}
_vacuum_jobs_lock = threading.Lock()


def require_admin_auth(f):
    """Decorator to require admin/operator authentication for sensitive endpoints.

    Checks for X-Admin-Token header matching ADMIN_TOKEN environment variable.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = os.getenv("ADMIN_TOKEN", "")

        # If no admin token configured, reject all requests
        if not admin_token:
            logger.warning("Admin endpoint accessed but ADMIN_TOKEN not configured")
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Admin authentication not configured. Set ADMIN_TOKEN environment variable.",
                    }
                ),
                503,
            )

        # Check request header
        provided_token = request.headers.get("X-Admin-Token", "")
        if not provided_token or provided_token != admin_token:
            logger.warning(
                f"Unauthorized admin endpoint access from {request.remote_addr}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Unauthorized. Valid X-Admin-Token header required.",
                    }
                ),
                401,
            )

        return f(*args, **kwargs)

    return decorated_function


def set_sentinel_state(key: str, value: Any):
    """Update sentinel state (called from main worker)."""
    global _sentinel_state
    _sentinel_state[key] = value


def get_sentinel_state(key: str, default: Any = None) -> Any:
    """Get sentinel state value."""
    return _sentinel_state.get(key, default)


def set_redis_client(client: Any):
    """Set Redis client for API access."""
    global _redis_client
    _redis_client = client


def set_config(cfg: Any):
    """Set config object for API access."""
    global _config
    _config = cfg


def set_engine(eng: Any):
    """Set database engine for API access."""
    global _engine
    _engine = eng


def _validate_session_file(file_content: bytes) -> tuple[bool, str]:
    """Validate that uploaded content is a valid Telethon session file.

    Returns: (is_valid, error_message)
    """
    # Check size (max 10MB)
    if len(file_content) > 10 * 1024 * 1024:
        return False, "File too large (max 10MB)"

    if len(file_content) < 100:
        return False, "File too small to be a valid session"

    # Check SQLite magic header
    if not file_content.startswith(b"SQLite format 3\x00"):
        return False, "Not a valid SQLite database file"

    # Try to open as SQLite and verify Telethon structure
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".session") as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        try:
            conn = sqlite3.connect(tmp_path)
            cursor = conn.cursor()

            # Check for Telethon tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

            required_tables = {
                "sessions",
                "entities",
                "sent_files",
                "update_state",
                "version",
            }
            if not required_tables.issubset(tables):
                missing = required_tables - tables
                return False, f"Missing required Telethon tables: {', '.join(missing)}"

            # Check sessions table has auth key
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE auth_key IS NOT NULL")
            if cursor.fetchone()[0] == 0:
                return False, "No authorization key found in session"

            conn.close()
            return True, "Valid Telethon session file"

        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    except Exception as exc:
        return False, f"Session validation failed: {exc}"


def create_api_app() -> Flask:
    """Create and configure the Flask API application."""
    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Disable Flask default logging to avoid duplicate logs
    import logging as flask_logging

    flask_logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.route("/metrics", methods=["GET"])
    def metrics():
        """Prometheus metrics endpoint.

        Returns metrics in Prometheus text format for scraping.
        """
        from tgsentinel import metrics as metrics_module

        # Update current state gauges before export
        metrics_module.worker_authorized.set(
            1 if _sentinel_state.get("authorized") else 0
        )
        metrics_module.worker_connected.set(
            1 if _sentinel_state.get("connected") else 0
        )

        # Update database message count if engine available
        if _engine:
            try:
                with _engine.begin() as con:
                    result = con.execute(text("SELECT COUNT(*) FROM messages"))
                    count = result.scalar() or 0
                    metrics_module.db_messages_current.set(count)
            except Exception as e:
                logger.debug(f"Could not update db_messages_current metric: {e}")

        # Update Redis stream depth if client available
        if _redis_client:
            try:
                depth = _redis_client.xlen("tgsentinel:messages")
                metrics_module.redis_stream_depth.set(depth)
            except Exception as e:
                logger.debug(f"Could not update redis_stream_depth metric: {e}")

        # Generate Prometheus text format
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.route("/api/health", methods=["GET"])
    def health():
        """Health check endpoint."""
        return (
            jsonify(
                {
                    "status": "ok",
                    "service": "tgsentinel",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
            200,
        )

    @app.route("/api/status", methods=["GET"])
    def status():
        """Worker status endpoint."""
        return (
            jsonify(
                {
                    "status": "ok",
                    "data": {
                        "authorized": _sentinel_state.get("authorized", False),
                        "connected": _sentinel_state.get("connected", False),
                        "user_info": _sentinel_state.get("user_info"),
                        "last_sync": _sentinel_state.get("last_sync"),
                    },
                    "error": None,
                }
            ),
            200,
        )

    @app.route("/api/avatar/<prefix>/<int:entity_id>", methods=["GET"])
    def get_avatar(prefix: str, entity_id: int):
        """Serve avatar image from Redis cache.

        Args:
            prefix: 'user' or 'chat'
            entity_id: Telegram entity ID

        Returns:
            Base64-encoded image data or 404 if not found
        """
        if not _redis_client:
            return jsonify({"error": "Redis not available"}), 503

        try:
            # Validate prefix
            if prefix not in ("user", "chat"):
                return (
                    jsonify({"error": "Invalid prefix, must be 'user' or 'chat'"}),
                    400,
                )

            # Get avatar from Redis
            redis_key = f"tgsentinel:{prefix}_avatar:{entity_id}"
            avatar_b64 = _redis_client.get(redis_key)

            if not avatar_b64:
                return jsonify({"error": "Avatar not found"}), 404

            # Decode base64 to binary
            if isinstance(avatar_b64, bytes):
                avatar_b64 = avatar_b64.decode("utf-8")
            avatar_bytes = base64.b64decode(avatar_b64)

            # Detect MIME type from image magic bytes
            mimetype = "image/jpeg"  # Default fallback

            if len(avatar_bytes) >= 8:
                # PNG signature: 89 50 4E 47 0D 0A 1A 0A
                if avatar_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                    mimetype = "image/png"
                # WebP signature: RIFF....WEBP (check RIFF at start and WEBP at offset 8)
                elif (
                    avatar_bytes[:4] == b"RIFF"
                    and len(avatar_bytes) >= 12
                    and avatar_bytes[8:12] == b"WEBP"
                ):
                    mimetype = "image/webp"
                # JPEG signature: FF D8 FF
                elif avatar_bytes[:3] == b"\xff\xd8\xff":
                    mimetype = "image/jpeg"
                # GIF signature: GIF87a or GIF89a
                elif avatar_bytes[:6] in (b"GIF87a", b"GIF89a"):
                    mimetype = "image/gif"

            # Return as image with correct MIME type
            from flask import Response

            return Response(avatar_bytes, mimetype=mimetype)

        except Exception as exc:
            logger.error("Failed to serve avatar %s/%d: %s", prefix, entity_id, exc)
            return jsonify({"error": "Failed to retrieve avatar"}), 500

    @app.route("/api/session/import", methods=["POST"])
    def session_import():
        """Import a Telethon session file from the UI.

        Expected request:
        - JSON with base64-encoded session file: {"session_data": "base64..."}
        OR
        - multipart/form-data with file field: session_file

        Returns:
        - 200: Session imported successfully
        - 400: Invalid request or session file
        - 500: Server error during import
        """
        try:
            # Get session file content
            session_content: Optional[bytes] = None

            # Try JSON with base64
            if request.is_json:
                data = request.get_json()
                if "session_data" in data:
                    try:
                        session_content = base64.b64decode(data["session_data"])
                    except Exception as b64_exc:
                        logger.error(f"Failed to decode base64 session data: {b64_exc}")
                        return (
                            jsonify(
                                {
                                    "status": "error",
                                    "code": "INVALID_BASE64",
                                    "message": "Failed to decode base64 session data",
                                    "data": None,
                                    "error": {
                                        "code": "INVALID_BASE64",
                                        "message": str(b64_exc),
                                    },
                                }
                            ),
                            400,
                        )
                else:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "code": "MISSING_SESSION_DATA",
                                "message": "Missing 'session_data' field in JSON",
                                "data": None,
                                "error": {
                                    "code": "MISSING_SESSION_DATA",
                                    "message": "Missing 'session_data' field",
                                },
                            }
                        ),
                        400,
                    )

            # Try multipart form data
            elif "session_file" in request.files:
                file_storage = request.files["session_file"]
                if file_storage and file_storage.filename:
                    session_content = file_storage.read()

            if not session_content:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "code": "NO_SESSION_FILE",
                            "message": "No session file provided in request",
                            "data": None,
                            "error": {
                                "code": "NO_SESSION_FILE",
                                "message": "No session file in request",
                            },
                        }
                    ),
                    400,
                )

            # Validate session file
            is_valid, validation_msg = _validate_session_file(session_content)
            if not is_valid:
                logger.warning(f"Session validation failed: {validation_msg}")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "code": "INVALID_SESSION_FILE",
                            "message": validation_msg,
                            "data": None,
                            "error": {
                                "code": "INVALID_SESSION_FILE",
                                "message": validation_msg,
                            },
                        }
                    ),
                    400,
                )

            # Determine target session path
            session_path = _sentinel_state.get("session_path")
            if not session_path:
                # Fall back to config or default
                if _config:
                    session_path = Path(
                        _config.telegram_session or "/app/data/tgsentinel.session"
                    )
                else:
                    session_path = Path("/app/data/tgsentinel.session")
            else:
                session_path = Path(session_path)

            # Ensure directory exists
            session_path.parent.mkdir(parents=True, exist_ok=True)

            # Remove any existing WAL files that might conflict with the new session
            # SQLite WAL (Write-Ahead Log) files can cause the new session to be ignored
            try:
                for wal_suffix in ["-shm", "-wal", "-journal"]:
                    wal_file = Path(str(session_path) + wal_suffix)
                    if wal_file.exists():
                        wal_file.unlink()
                        logger.debug(f"[API] Removed stale WAL file: {wal_file}")
            except Exception as wal_exc:
                logger.warning(f"[API] Failed to remove WAL files: {wal_exc}")

            # Write session file atomically
            tmp_path = session_path.with_suffix(f".upload-{uuid.uuid4().hex}.tmp")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(session_content)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(tmp_path, session_path)

                # Set permissions
                try:
                    os.chmod(session_path, 0o660)
                except Exception as chmod_exc:
                    logger.warning(
                        f"Failed to set session file permissions: {chmod_exc}"
                    )

                logger.info(f"[API] Session file imported successfully: {session_path}")

                # Notify main worker via Redis (if available)
                if _redis_client:
                    try:
                        _redis_client.publish(
                            "tgsentinel:session_updated",
                            json.dumps(
                                {
                                    "event": "session_imported",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "session_path": str(session_path),
                                }
                            ),
                        )
                    except Exception as redis_exc:
                        logger.warning(
                            f"Failed to publish session update event: {redis_exc}"
                        )

                return (
                    jsonify(
                        {
                            "status": "ok",
                            "message": "Session imported successfully",
                            "data": {
                                "session_path": str(session_path),
                                "size": len(session_content),
                                "imported_at": datetime.now(timezone.utc).isoformat(),
                            },
                            "error": None,
                        }
                    ),
                    200,
                )

            except Exception as write_exc:
                logger.error(
                    f"[API] Failed to write session file: {write_exc}", exc_info=True
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "code": "WRITE_ERROR",
                            "message": "Failed to write session file to disk",
                            "data": None,
                            "error": {"code": "WRITE_ERROR", "message": str(write_exc)},
                        }
                    ),
                    500,
                )

            finally:
                # Clean up temp file if it exists
                try:
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

        except Exception as exc:
            logger.error(f"[API] Session import failed: {exc}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "code": "INTERNAL_ERROR",
                        "message": "Internal server error during session import",
                        "data": None,
                        "error": {"code": "INTERNAL_ERROR", "message": str(exc)},
                    }
                ),
                500,
            )

    @app.route("/api/session/logout", methods=["POST"])
    def session_logout():
        """Logout and clear session files.

        This endpoint:
        1. Removes all session files (*.session*)
        2. Publishes logout event to session monitor
        3. Returns success status

        Returns:
        - 200: Logout successful
        - 500: Server error during logout
        """
        import glob

        result = {"status": "ok", "message": "Logout successful", "files_removed": []}

        try:
            # Remove all session files
            session_patterns = [
                "/app/data/*.session",
                "/app/data/*.session-journal",
                "/app/data/*.session-shm",
                "/app/data/*.session-wal",
                "/app/data/tgsentinel.session*",
            ]

            for pattern in session_patterns:
                for file_path in glob.glob(pattern):
                    try:
                        Path(file_path).unlink(missing_ok=True)
                        result["files_removed"].append(file_path)
                        logger.info(f"[API] Removed session file: {file_path}")
                    except Exception as e:
                        logger.warning(f"[API] Failed to remove {file_path}: {e}")

            # Publish logout event to session monitor
            if _redis_client:
                try:
                    _redis_client.publish(
                        "tgsentinel:session_updated",
                        json.dumps(
                            {
                                "event": "session_logout",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                    logger.info("[API] Published logout event to session monitor")
                except Exception as redis_exc:
                    logger.warning(f"[API] Failed to publish logout event: {redis_exc}")

            return jsonify(result), 200

        except Exception as exc:
            logger.error(f"[API] Logout failed: {exc}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Logout failed: {str(exc)}",
                        "files_removed": result.get("files_removed", []),
                    }
                ),
                500,
            )

    @app.route("/api/alerts", methods=["GET"])
    def alerts():
        """Get recent alerts from sentinel.db messages table."""
        try:
            # Get query parameters
            limit = request.args.get("limit", default=100, type=int)
            offset = request.args.get("offset", default=0, type=int)
            unread_only = (
                request.args.get("unread_only", default="false").lower() == "true"
            )

            # Validate limit
            if limit > 1000:
                limit = 1000
            if limit < 1:
                limit = 100

            # Query alerted messages from sentinel.db
            query = """
                SELECT 
                    chat_id,
                    msg_id,
                    chat_title,
                    sender_name,
                    message_text,
                    score,
                    triggers,
                    sender_id,
                    created_at
                FROM messages 
                WHERE alerted = 1
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """

            count_query = """
                SELECT COUNT(*) as total
                FROM messages 
                WHERE alerted = 1
            """

            if not _engine:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": "Database not available",
                        }
                    ),
                    503,
                )

            with _engine.begin() as con:
                rows = con.execute(
                    text(query), {"limit": limit, "offset": offset}
                ).fetchall()

                count_result = con.execute(text(count_query)).fetchone()
                total_count = count_result[0] if count_result else 0

            # Format alerts for UI
            alerts_list = []
            for row in rows:
                # Generate alert_id for UI tracking
                alert_id = f"{row.chat_id}_{row.msg_id}"

                # Truncate message text if too long
                message_text = row.message_text or ""
                if len(message_text) > 300:
                    message_text = message_text[:300] + "..."

                alerts_list.append(
                    {
                        "alert_id": alert_id,
                        "chat_id": row.chat_id,
                        "message_id": row.msg_id,
                        "chat_title": row.chat_title or f"Chat {row.chat_id}",
                        "sender_name": row.sender_name or "Unknown",
                        "message_text": message_text,
                        "score": float(row.score) if row.score else 0.0,
                        "triggers": row.triggers or "",
                        "sender_id": row.sender_id or 0,
                        "timestamp": row.created_at,
                        "read": False,  # UI can track read state in ui.db
                        "dismissed": False,
                    }
                )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "alerts": alerts_list,
                            "count": len(alerts_list),
                            "total": total_count,
                            "limit": limit,
                            "offset": offset,
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch alerts: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch alerts: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/digests", methods=["GET"])
    def get_digests():
        """Get digest statistics grouped by date.

        Query Parameters:
            limit: Number of days to return (default: 14)

        Returns:
            JSON with digest statistics per day
        """
        if _engine is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Database not available",
                    }
                ),
                503,
            )

        try:
            limit = request.args.get("limit", default=14, type=int)
            if limit < 1 or limit > 365:
                limit = 14

            with _engine.begin() as con:
                result = con.execute(
                    text(
                        """
                        SELECT date(created_at) as digest_date,
                               COUNT(*) as items,
                               ROUND(AVG(score), 2) as avg_score
                        FROM messages
                        WHERE alerted = 1
                        GROUP BY date(created_at)
                        ORDER BY digest_date DESC
                        LIMIT :limit
                    """
                    ),
                    {"limit": limit},
                )
                rows = result.fetchall()

            digests_list = [
                {
                    "date": row[0],  # digest_date
                    "items": row[1],  # items count
                    "avg_score": float(row[2]) if row[2] is not None else 0.0,
                }
                for row in rows
            ]

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "digests": digests_list,
                            "count": len(digests_list),
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch digests: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch digests: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/digest/schedules", methods=["GET"])
    def get_digest_schedules():
        """Get all configured digest schedules across all profiles.

        Query Parameters:
            None

        Returns:
            JSON with all digest schedules and their status
        """
        if _config is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Configuration not available",
                    }
                ),
                503,
            )

        try:
            from .config import ProfileDefinition

            schedules_data = []

            # Collect all unique schedules across all profiles
            schedule_map = (
                {}
            )  # schedule_type -> {profiles: [], next_run: None, last_run: None}

            # Check global profiles
            for profile_id, profile in _config.global_profiles.items():
                if not isinstance(profile, ProfileDefinition):
                    continue
                if not profile.digest or not profile.digest.schedules:
                    continue

                for sched_cfg in profile.digest.schedules:
                    if not sched_cfg.enabled:
                        continue

                    sched_type = sched_cfg.schedule.value
                    if sched_type not in schedule_map:
                        schedule_map[sched_type] = {
                            "schedule": sched_type,
                            "enabled": True,
                            "profiles": [],
                            "next_run": None,
                            "last_run": None,
                        }

                    if profile_id not in schedule_map[sched_type]["profiles"]:
                        schedule_map[sched_type]["profiles"].append(profile_id)

            # Fetch last_run times from Redis
            if _redis_client:
                try:
                    from .redis_operations import RedisManager

                    redis_mgr = RedisManager(_redis_client)
                    schedule_times = redis_mgr.get_all_digest_schedule_times()
                    for sched_type, last_run in schedule_times.items():
                        if sched_type in schedule_map:
                            schedule_map[sched_type]["last_run"] = last_run
                except Exception as e:
                    logger.warning(f"Failed to fetch schedule times from Redis: {e}")

            schedules_data = list(schedule_map.values())

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "schedules": schedules_data,
                            "count": len(schedules_data),
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"[API] Failed to fetch digest schedules: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch digest schedules: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/digest/schedules/<profile_id>", methods=["GET"])
    def get_profile_digest_config(profile_id: str):
        """Get digest configuration for a specific profile.

        Path Parameters:
            profile_id: Profile identifier

        Returns:
            JSON with profile's digest configuration
        """
        if _config is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Configuration not available",
                    }
                ),
                503,
            )

        try:
            from .config import ProfileDefinition

            # Look up profile
            profile = _config.global_profiles.get(profile_id)
            if not profile or not isinstance(profile, ProfileDefinition):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": f"Profile '{profile_id}' not found",
                        }
                    ),
                    404,
                )

            # Extract digest configuration
            digest_cfg = profile.digest
            if not digest_cfg:
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "data": {
                                "profile_id": profile_id,
                                "schedules": [],
                                "mode": "dm",
                                "target_channel": None,
                            },
                            "error": None,
                        }
                    ),
                    200,
                )

            # Serialize schedules
            schedules_data = []
            for sched_cfg in digest_cfg.schedules:
                sched_data = {
                    "schedule": sched_cfg.schedule.value,
                    "enabled": sched_cfg.enabled,
                }

                # Add optional overrides if present
                if sched_cfg.top_n is not None:
                    sched_data["top_n"] = sched_cfg.top_n
                if sched_cfg.min_score is not None:
                    sched_data["min_score"] = sched_cfg.min_score

                # Add schedule-specific settings
                if sched_cfg.schedule.value == "daily":
                    sched_data["daily_hour"] = sched_cfg.daily_hour
                elif sched_cfg.schedule.value == "weekly":
                    sched_data["weekly_day"] = sched_cfg.weekly_day
                    sched_data["weekly_hour"] = sched_cfg.weekly_hour

                schedules_data.append(sched_data)

            response_data = {
                "profile_id": profile_id,
                "schedules": schedules_data,
                "top_n": digest_cfg.top_n,
                "min_score": digest_cfg.min_score,
                "mode": digest_cfg.mode,
                "target_channel": digest_cfg.target_channel,
            }

            return (
                jsonify({"status": "ok", "data": response_data, "error": None}),
                200,
            )

        except Exception as e:
            logger.error(
                f"[API] Failed to fetch digest config for {profile_id}: {e}",
                exc_info=True,
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch digest config: {str(e)}",
                    }
                ),
                500,
            )

    def validate_digest_config(data: dict) -> tuple[bool, str]:
        """Validate digest configuration before saving.

        Returns: (is_valid, error_message)
        """
        # 1. Max 3 schedules
        schedules = data.get("schedules", [])
        if len(schedules) > 3:
            return False, "Maximum 3 schedules allowed per profile"

        # 2. Valid schedule types
        valid_schedules = {
            "hourly",
            "every_4h",
            "every_6h",
            "every_12h",
            "daily",
            "weekly",
            "none",
        }
        for sched in schedules:
            if sched.get("schedule") not in valid_schedules:
                return False, f"Invalid schedule type: {sched.get('schedule')}"

        # 3. Valid mode
        mode = data.get("mode", "dm")
        if mode not in {"dm", "channel", "both"}:
            return False, f"Invalid mode: {mode}"

        # 4. min_score range
        min_score = data.get("min_score", 0.0)
        if not (0.0 <= min_score <= 10.0):
            return False, f"min_score must be between 0.0 and 10.0, got {min_score}"

        # 5. Hour/day ranges
        for sched in schedules:
            daily_hour = sched.get("daily_hour", 8)
            if not (0 <= daily_hour <= 23):
                return False, f"daily_hour must be 0-23, got {daily_hour}"

            weekly_hour = sched.get("weekly_hour", 8)
            if not (0 <= weekly_hour <= 23):
                return False, f"weekly_hour must be 0-23, got {weekly_hour}"

            weekly_day = sched.get("weekly_day", 0)
            if not (0 <= weekly_day <= 6):
                return False, f"weekly_day must be 0-6, got {weekly_day}"

        # 6. target_channel required if mode=channel or both
        if mode in ("channel", "both") and not data.get("target_channel"):
            return (
                False,
                'target_channel required when mode is "channel" or "both"',
            )

        return True, ""

    @app.route("/api/config/profiles/<profile_id>/digest", methods=["GET", "PUT"])
    def profile_digest_config(profile_id: str):
        """Get or update digest configuration for a global profile.

        GET returns current digest config (or null if not configured).
        PUT updates digest config in config/profiles.yml.
        """
        global _config
        if request.method == "GET":
            # Get profile from current config
            profile = _config.global_profiles.get(profile_id)
            if not profile:
                return (
                    jsonify({"status": "error", "message": "Profile not found"}),
                    404,
                )

            digest_dict = None
            if profile.digest:
                digest_dict = {
                    "schedules": [
                        {
                            "schedule": s.schedule.value,
                            "enabled": s.enabled,
                            "min_score": s.min_score,
                            "top_n": s.top_n,
                            "daily_hour": s.daily_hour,
                            "weekly_day": s.weekly_day,
                            "weekly_hour": s.weekly_hour,
                        }
                        for s in profile.digest.schedules
                    ],
                    "mode": profile.digest.mode,
                    "target_channel": profile.digest.target_channel,
                    "top_n": profile.digest.top_n,
                    "min_score": profile.digest.min_score,
                }

            return jsonify(
                {"status": "ok", "profile_id": profile_id, "digest": digest_dict}
            )

        elif request.method == "PUT":
            data = request.get_json()

            # Validate request
            if not data:
                return (
                    jsonify({"status": "error", "message": "No data provided"}),
                    400,
                )

            # Validate digest config
            is_valid, error_msg = validate_digest_config(data)
            if not is_valid:
                return jsonify({"status": "error", "message": error_msg}), 400

            # Load config/profiles.yml
            profiles_path = Path(_config.config_dir) / "profiles.yml"
            if not profiles_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "profiles.yml not found",
                        }
                    ),
                    500,
                )

            lock_fd = None
            temp_file = None

            try:
                # Acquire exclusive file lock to prevent TOCTOU race
                lock_fd = os.open(str(profiles_path), os.O_RDONLY)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)

                # Read configuration while holding lock
                with open(profiles_path, "r") as f:
                    profiles_config = yaml.safe_load(f) or {}

                # Find profile in YAML
                if profile_id not in profiles_config.get("global_profiles", {}):
                    return (
                        jsonify({"status": "error", "message": "Profile not found"}),
                        404,
                    )

                # Update digest section
                profile_data = profiles_config["global_profiles"][profile_id]
                profile_data["digest"] = data  # Replace entire digest config

                # Write atomically: write to temp file, then rename
                # Use same directory as target to ensure atomic rename
                temp_fd, temp_path = tempfile.mkstemp(
                    dir=profiles_path.parent,
                    prefix=f".{profiles_path.name}.tmp.",
                    suffix=".yml",
                    text=True,
                )
                temp_file = temp_path

                try:
                    # Write to temp file
                    with os.fdopen(temp_fd, "w") as f:
                        yaml.safe_dump(
                            profiles_config,
                            f,
                            default_flow_style=False,
                            sort_keys=False,
                        )

                    # Atomic rename over original file
                    os.replace(temp_path, profiles_path)
                    temp_file = None  # Successfully renamed, no cleanup needed

                except Exception as write_error:
                    # Clean up temp file on write/rename failure
                    if temp_file and os.path.exists(temp_file):
                        try:
                            os.unlink(temp_file)
                        except Exception:
                            pass
                    raise write_error

                logger.info(
                    f"[API] Updated digest config for profile {profile_id}",
                    extra={
                        "profile_id": profile_id,
                        "schedules": len(data.get("schedules", [])),
                    },
                )

                # Reload configuration into memory
                try:
                    from tgsentinel.config import load_config

                    _config = load_config()
                    logger.info(
                        f"[API] Reloaded configuration after digest update for {profile_id}"
                    )
                except Exception as reload_error:
                    logger.error(
                        f"[API] Failed to reload config after digest update: {reload_error}",
                        exc_info=True,
                    )
                    # Non-fatal: config will reload on next restart

                # Publish config change event via Redis for other workers
                if _redis_client:
                    try:
                        from tgsentinel.redis_operations import RedisManager

                        redis_mgr = RedisManager(_redis_client)
                        redis_mgr.publish_config_event(
                            event="profile_digest_updated",
                            config_keys=[f"global_profiles.{profile_id}.digest"],
                            profile_id=profile_id,
                        )
                    except Exception as redis_error:
                        logger.warning(
                            f"[API] Failed to publish config event to Redis: {redis_error}"
                        )
                        # Non-fatal: other workers will pick up on next reload

                return jsonify(
                    {
                        "status": "ok",
                        "profile_id": profile_id,
                        "digest": data,
                        "message": "Digest configuration updated successfully",
                    }
                )

            except Exception as e:
                logger.error(
                    f"[API] Failed to update digest config for {profile_id}: {e}",
                    exc_info=True,
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to update configuration: {str(e)}",
                        }
                    ),
                    500,
                )

            finally:
                # Release file lock
                if lock_fd is not None:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        os.close(lock_fd)
                    except Exception as unlock_error:
                        logger.warning(
                            f"[API] Failed to release file lock: {unlock_error}"
                        )

                # Clean up temp file if it still exists (error case)
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except Exception as cleanup_error:
                        logger.warning(
                            f"[API] Failed to clean up temp file {temp_file}: {cleanup_error}"
                        )

        # Fallback for unexpected request methods
        return jsonify({"status": "error", "message": "Method not allowed"}), 405

    @app.route("/api/config/channels/<int:channel_id>/digest", methods=["GET", "PUT"])
    def channel_digest_config(channel_id: int):
        """Get or update digest configuration for a channel.

        Supports both direct channel.digest configuration.
        """
        if request.method == "GET":
            # Find channel in current config
            channel = next((c for c in _config.channels if c.id == channel_id), None)
            if not channel:
                return (
                    jsonify({"status": "error", "message": "Channel not found"}),
                    404,
                )

            digest_dict = None
            if channel.digest:
                digest_dict = {
                    "schedules": [
                        {
                            "schedule": s.schedule.value,
                            "enabled": s.enabled,
                            "min_score": s.min_score,
                            "top_n": s.top_n,
                            "daily_hour": s.daily_hour,
                            "weekly_day": s.weekly_day,
                            "weekly_hour": s.weekly_hour,
                        }
                        for s in channel.digest.schedules
                    ],
                    "mode": channel.digest.mode,
                    "target_channel": channel.digest.target_channel,
                    "top_n": channel.digest.top_n,
                    "min_score": channel.digest.min_score,
                }

            return jsonify(
                {"status": "ok", "channel_id": channel_id, "digest": digest_dict}
            )

        elif request.method == "PUT":
            data = request.get_json()

            if not data:
                return (
                    jsonify({"status": "error", "message": "No data provided"}),
                    400,
                )

            # Validate digest config
            is_valid, error_msg = validate_digest_config(data)
            if not is_valid:
                return jsonify({"status": "error", "message": error_msg}), 400

            # Load config/tgsentinel.yml
            config_path = Path(_config.config_dir) / "tgsentinel.yml"
            if not config_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "tgsentinel.yml not found",
                        }
                    ),
                    500,
                )

            try:
                with open(config_path, "r") as f:
                    tg_config = yaml.safe_load(f) or {}

                # Find channel in YAML
                channels = tg_config.get("channels", [])
                channel_data = next(
                    (c for c in channels if c.get("id") == channel_id), None
                )

                if not channel_data:
                    return (
                        jsonify({"status": "error", "message": "Channel not found"}),
                        404,
                    )

                # Update digest section
                channel_data["digest"] = data

                # Write back to YAML
                with open(config_path, "w") as f:
                    yaml.safe_dump(
                        tg_config, f, default_flow_style=False, sort_keys=False
                    )

                logger.info(
                    f"[API] Updated digest config for channel {channel_id}",
                    extra={
                        "channel_id": channel_id,
                        "schedules": len(data.get("schedules", [])),
                    },
                )

                return jsonify(
                    {
                        "status": "ok",
                        "channel_id": channel_id,
                        "digest": data,
                        "message": "Digest configuration updated successfully",
                    }
                )

            except Exception as e:
                logger.error(
                    f"[API] Failed to update channel digest config for {channel_id}: {e}",
                    exc_info=True,
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to update configuration: {str(e)}",
                        }
                    ),
                    500,
                )

        # Fallback for unexpected request methods
        return jsonify({"status": "error", "message": "Method not allowed"}), 405

    @app.route(
        "/api/config/channels/<int:channel_id>/overrides/digest",
        methods=["GET", "PUT"],
    )
    def channel_overrides_digest_config(channel_id: int):
        """Get or update digest overrides for a channel.

        Writes to config/tgsentinel.yml → channels[*].overrides.digest
        """
        if request.method == "GET":
            channel = next((c for c in _config.channels if c.id == channel_id), None)
            if not channel:
                return (
                    jsonify({"status": "error", "message": "Channel not found"}),
                    404,
                )

            digest_dict = None
            if channel.overrides and channel.overrides.digest:
                digest_dict = {
                    "schedules": [
                        {
                            "schedule": s.schedule.value,
                            "enabled": s.enabled,
                            "min_score": s.min_score,
                            "top_n": s.top_n,
                            "daily_hour": s.daily_hour,
                            "weekly_day": s.weekly_day,
                            "weekly_hour": s.weekly_hour,
                        }
                        for s in channel.overrides.digest.schedules
                    ],
                    "mode": channel.overrides.digest.mode,
                    "target_channel": channel.overrides.digest.target_channel,
                    "top_n": channel.overrides.digest.top_n,
                    "min_score": channel.overrides.digest.min_score,
                }

            return jsonify(
                {"status": "ok", "channel_id": channel_id, "digest": digest_dict}
            )

        elif request.method == "PUT":
            data = request.get_json()

            if not data:
                return (
                    jsonify({"status": "error", "message": "No data provided"}),
                    400,
                )

            # Validate digest config
            is_valid, error_msg = validate_digest_config(data)
            if not is_valid:
                return jsonify({"status": "error", "message": error_msg}), 400

            # Load config/tgsentinel.yml
            config_path = Path(_config.config_dir) / "tgsentinel.yml"
            if not config_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "tgsentinel.yml not found",
                        }
                    ),
                    500,
                )

            try:
                with open(config_path, "r") as f:
                    tg_config = yaml.safe_load(f) or {}

                channels = tg_config.get("channels", [])
                channel_data = next(
                    (c for c in channels if c.get("id") == channel_id), None
                )

                if not channel_data:
                    return (
                        jsonify({"status": "error", "message": "Channel not found"}),
                        404,
                    )

                # Ensure overrides section exists
                if "overrides" not in channel_data:
                    channel_data["overrides"] = {}

                # Update digest in overrides
                channel_data["overrides"]["digest"] = data

                # Write back to YAML
                with open(config_path, "w") as f:
                    yaml.safe_dump(
                        tg_config, f, default_flow_style=False, sort_keys=False
                    )

                logger.info(
                    f"[API] Updated digest overrides for channel {channel_id}",
                    extra={
                        "channel_id": channel_id,
                        "schedules": len(data.get("schedules", [])),
                    },
                )

                return jsonify(
                    {
                        "status": "ok",
                        "channel_id": channel_id,
                        "digest": data,
                        "message": "Digest overrides updated successfully",
                    }
                )

            except Exception as e:
                logger.error(
                    f"[API] Failed to update channel digest overrides for {channel_id}: {e}",
                    exc_info=True,
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to update configuration: {str(e)}",
                        }
                    ),
                    500,
                )

        # Fallback for unexpected request methods
        return jsonify({"status": "error", "message": "Method not allowed"}), 405

    @app.route("/api/config/users/<int:user_id>/digest", methods=["GET", "PUT"])
    def user_digest_config(user_id: int):
        """Get or update digest configuration for a monitored user."""
        if request.method == "GET":
            user = next((u for u in _config.users if u.id == user_id), None)
            if not user:
                return (
                    jsonify({"status": "error", "message": "User not found"}),
                    404,
                )

            digest_dict = None
            if user.digest:
                digest_dict = {
                    "schedules": [
                        {
                            "schedule": s.schedule.value,
                            "enabled": s.enabled,
                            "min_score": s.min_score,
                            "top_n": s.top_n,
                            "daily_hour": s.daily_hour,
                            "weekly_day": s.weekly_day,
                            "weekly_hour": s.weekly_hour,
                        }
                        for s in user.digest.schedules
                    ],
                    "mode": user.digest.mode,
                    "target_channel": user.digest.target_channel,
                    "top_n": user.digest.top_n,
                    "min_score": user.digest.min_score,
                }

            return jsonify({"status": "ok", "user_id": user_id, "digest": digest_dict})

        elif request.method == "PUT":
            data = request.get_json()

            if not data:
                return (
                    jsonify({"status": "error", "message": "No data provided"}),
                    400,
                )

            # Validate digest config
            is_valid, error_msg = validate_digest_config(data)
            if not is_valid:
                return jsonify({"status": "error", "message": error_msg}), 400

            # Load config/tgsentinel.yml
            config_path = Path(_config.config_dir) / "tgsentinel.yml"
            if not config_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "tgsentinel.yml not found",
                        }
                    ),
                    500,
                )

            try:
                with open(config_path, "r") as f:
                    tg_config = yaml.safe_load(f) or {}

                users = tg_config.get("monitored_users", [])
                user_data = next((u for u in users if u.get("id") == user_id), None)

                if not user_data:
                    return (
                        jsonify({"status": "error", "message": "User not found"}),
                        404,
                    )

                # Update digest section
                user_data["digest"] = data

                # Write back to YAML
                with open(config_path, "w") as f:
                    yaml.safe_dump(
                        tg_config, f, default_flow_style=False, sort_keys=False
                    )

                logger.info(
                    f"[API] Updated digest config for user {user_id}",
                    extra={
                        "user_id": user_id,
                        "schedules": len(data.get("schedules", [])),
                    },
                )

                return jsonify(
                    {
                        "status": "ok",
                        "user_id": user_id,
                        "digest": data,
                        "message": "Digest configuration updated successfully",
                    }
                )

            except Exception as e:
                logger.error(
                    f"[API] Failed to update user digest config for {user_id}: {e}",
                    exc_info=True,
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to update configuration: {str(e)}",
                        }
                    ),
                    500,
                )

        # Fallback for unexpected request methods
        return jsonify({"status": "error", "message": "Method not allowed"}), 405

    @app.route(
        "/api/config/users/<int:user_id>/overrides/digest", methods=["GET", "PUT"]
    )
    def user_overrides_digest_config(user_id: int):
        """Get or update digest overrides for a monitored user."""
        if request.method == "GET":
            user = next((u for u in _config.users if u.id == user_id), None)
            if not user:
                return (
                    jsonify({"status": "error", "message": "User not found"}),
                    404,
                )

            digest_dict = None
            if user.overrides and user.overrides.digest:
                digest_dict = {
                    "schedules": [
                        {
                            "schedule": s.schedule.value,
                            "enabled": s.enabled,
                            "min_score": s.min_score,
                            "top_n": s.top_n,
                            "daily_hour": s.daily_hour,
                            "weekly_day": s.weekly_day,
                            "weekly_hour": s.weekly_hour,
                        }
                        for s in user.overrides.digest.schedules
                    ],
                    "mode": user.overrides.digest.mode,
                    "target_channel": user.overrides.digest.target_channel,
                    "top_n": user.overrides.digest.top_n,
                    "min_score": user.overrides.digest.min_score,
                }

            return jsonify({"status": "ok", "user_id": user_id, "digest": digest_dict})

        elif request.method == "PUT":
            data = request.get_json()

            if not data:
                return (
                    jsonify({"status": "error", "message": "No data provided"}),
                    400,
                )

            # Validate digest config
            is_valid, error_msg = validate_digest_config(data)
            if not is_valid:
                return jsonify({"status": "error", "message": error_msg}), 400

            # Load config/tgsentinel.yml
            config_path = Path(_config.config_dir) / "tgsentinel.yml"
            if not config_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "tgsentinel.yml not found",
                        }
                    ),
                    500,
                )

            try:
                with open(config_path, "r") as f:
                    tg_config = yaml.safe_load(f) or {}

                users = tg_config.get("monitored_users", [])
                user_data = next((u for u in users if u.get("id") == user_id), None)

                if not user_data:
                    return (
                        jsonify({"status": "error", "message": "User not found"}),
                        404,
                    )

                # Ensure overrides section exists
                if "overrides" not in user_data:
                    user_data["overrides"] = {}

                # Update digest in overrides
                user_data["overrides"]["digest"] = data

                # Write back to YAML
                with open(config_path, "w") as f:
                    yaml.safe_dump(
                        tg_config, f, default_flow_style=False, sort_keys=False
                    )

                logger.info(
                    f"[API] Updated digest overrides for user {user_id}",
                    extra={
                        "user_id": user_id,
                        "schedules": len(data.get("schedules", [])),
                    },
                )

                return jsonify(
                    {
                        "status": "ok",
                        "user_id": user_id,
                        "digest": data,
                        "message": "Digest overrides updated successfully",
                    }
                )

            except Exception as e:
                logger.error(
                    f"[API] Failed to update user digest overrides for {user_id}: {e}",
                    exc_info=True,
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to update configuration: {str(e)}",
                        }
                    ),
                    500,
                )

        # Fallback for unexpected request methods
        return jsonify({"status": "error", "message": "Method not allowed"}), 405

    @app.route("/api/stats", methods=["GET"])
    def stats():
        """Get dashboard statistics from Sentinel database.

        Query Parameters:
            hours: Number of hours to look back (default: 24)

        Returns:
            JSON with messages_ingested, alerts_sent, avg_importance, feedback_accuracy
        """
        if _engine is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Database not available",
                    }
                ),
                503,
            )

        try:
            hours = request.args.get("hours", default=24, type=int)
            if hours < 1 or hours > 168:  # Max 1 week
                hours = 24

            # Calculate cutoff timestamp
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            with _engine.begin() as con:
                # Messages ingested in the last N hours
                messages_result = con.execute(
                    text(
                        """
                        SELECT COUNT(*) as count
                        FROM messages
                        WHERE datetime(created_at) >= datetime(:cutoff)
                    """
                    ),
                    {"cutoff": cutoff},
                )
                messages_ingested = messages_result.scalar() or 0

                # Alerts sent (alerted=1)
                alerts_result = con.execute(
                    text(
                        """
                        SELECT COUNT(*) as count
                        FROM messages
                        WHERE alerted = 1
                          AND datetime(created_at) >= datetime(:cutoff)
                    """
                    ),
                    {"cutoff": cutoff},
                )
                alerts_sent = alerts_result.scalar() or 0

                # Average importance (score)
                avg_result = con.execute(
                    text(
                        """
                        SELECT AVG(score) as avg_score
                        FROM messages
                        WHERE datetime(created_at) >= datetime(:cutoff)
                    """
                    ),
                    {"cutoff": cutoff},
                )
                avg_importance = avg_result.scalar() or 0.0

                # Feedback accuracy (if feedback table exists)
                try:
                    feedback_result = con.execute(
                        text(
                            """
                            SELECT 
                                SUM(CASE WHEN label = 1 THEN 1 ELSE 0 END) as positive,
                                COUNT(*) as total
                            FROM feedback
                            WHERE datetime(created_at) >= datetime(:cutoff)
                        """
                        ),
                        {"cutoff": cutoff},
                    )
                    feedback_row = feedback_result.fetchone()
                    if feedback_row and feedback_row[1] > 0:
                        feedback_accuracy = (feedback_row[0] / feedback_row[1]) * 100
                    else:
                        # Fallback: use high-score alerts as proxy
                        high_score_result = con.execute(
                            text(
                                """
                                SELECT COUNT(*) as count
                                FROM messages
                                WHERE alerted = 1
                                  AND score >= 0.7
                                  AND datetime(created_at) >= datetime(:cutoff)
                            """
                            ),
                            {"cutoff": cutoff},
                        )
                        high_score_count = high_score_result.scalar() or 0
                        feedback_accuracy = (
                            (high_score_count / alerts_sent * 100)
                            if alerts_sent
                            else 0.0
                        )
                except Exception:
                    # Feedback table doesn't exist, use fallback
                    high_score_result = con.execute(
                        text(
                            """
                            SELECT COUNT(*) as count
                            FROM messages
                            WHERE alerted = 1
                              AND score >= 0.7
                              AND datetime(created_at) >= datetime(:cutoff)
                        """
                        ),
                        {"cutoff": cutoff},
                    )
                    high_score_count = high_score_result.scalar() or 0
                    feedback_accuracy = (
                        (high_score_count / alerts_sent * 100) if alerts_sent else 0.0
                    )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "messages_ingested": int(messages_ingested),
                            "alerts_sent": int(alerts_sent),
                            "avg_importance": round(float(avg_importance), 2),
                            "feedback_accuracy": round(feedback_accuracy, 1),
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch stats: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch stats: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/feedback", methods=["POST"])
    def submit_feedback():
        """Record user feedback (thumbs up/down) for an alert.

        Request JSON:
            chat_id: Chat ID (integer)
            msg_id: Message ID (integer)
            label: "up" or "down"

        Returns:
            JSON status response
        """
        if _engine is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Database not available",
                    }
                ),
                503,
            )

        if not request.is_json:
            return (
                jsonify(
                    {
                        "status": "error",
                        "error": "Content-Type must be application/json",
                    }
                ),
                400,
            )

        try:
            payload = request.get_json(silent=True)
            if payload is None:
                return (
                    jsonify({"status": "error", "error": "Invalid JSON payload"}),
                    400,
                )

            chat_id = payload.get("chat_id")
            msg_id = payload.get("msg_id")
            label = payload.get("label")  # "up" or "down"

            if not chat_id or not msg_id or label not in ("up", "down"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "error": "Missing or invalid parameters: chat_id, msg_id, label",
                        }
                    ),
                    400,
                )

            # Validate and convert to integers
            try:
                chat_id_int = int(chat_id)
                msg_id_int = int(msg_id)
            except (ValueError, TypeError):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "error": "Invalid chat_id or msg_id: must be integers",
                        }
                    ),
                    400,
                )

            label_value = 1 if label == "up" else 0

            with _engine.begin() as con:
                con.execute(
                    text(
                        """
                        INSERT INTO feedback(chat_id, msg_id, label)
                        VALUES(:c, :m, :l)
                        ON CONFLICT(chat_id, msg_id) DO UPDATE SET label=excluded.label
                    """
                    ),
                    {"c": chat_id_int, "m": msg_id_int, "l": label_value},
                )

            logger.info(
                f"Feedback recorded: chat_id={chat_id_int}, msg_id={msg_id_int}, label={label}"
            )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "chat_id": chat_id_int,
                            "msg_id": msg_id_int,
                            "label": label,
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to record feedback: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to record feedback: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/database/vacuum", methods=["POST"])
    @require_admin_auth
    def vacuum_database_endpoint():
        """Run VACUUM on Sentinel database to reclaim space and optimize.

        **REQUIRES ADMIN AUTHENTICATION** via X-Admin-Token header.

        **WARNING**: This is a maintenance operation that:
        - Requires exclusive DB access (blocks all writes)
        - Needs up to 2x database size in temporary disk space
        - Should only be run during scheduled maintenance windows
        - Can take several minutes on large databases

        Returns 202 Accepted immediately and runs VACUUM in background.
        Use GET /api/database/vacuum/status/{job_id} to check progress.

        Query Parameters:
            maintenance_window: Must be "true" to acknowledge maintenance mode

        Returns:
            JSON with job_id for tracking vacuum progress
        """
        if _engine is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Database not available",
                    }
                ),
                503,
            )

        # Require explicit maintenance window acknowledgment
        maintenance_ack = request.args.get("maintenance_window", "").lower()
        if maintenance_ack != "true":
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "VACUUM requires maintenance_window=true parameter to acknowledge exclusive DB access and potential downtime",
                    }
                ),
                400,
            )

        # Check Redis availability for distributed locking
        if not _redis_client:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Redis not available for distributed locking",
                    }
                ),
                503,
            )

        # Try to acquire distributed lock (prevent concurrent VACUUMs)
        lock_key = "tgsentinel:vacuum:lock"
        lock_acquired = False

        try:
            # Try to acquire lock with 4-hour timeout (max expected VACUUM duration)
            lock_acquired = _redis_client.set(
                lock_key,
                f"vacuum-{uuid.uuid4()}",
                nx=True,  # Only set if not exists
                ex=14400,  # 4 hour timeout
            )

            if not lock_acquired:
                # Check if lock is stale
                ttl = _redis_client.ttl(lock_key)
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": f"VACUUM already in progress or lock held. TTL: {ttl}s. Wait or manually clear lock if stale.",
                        }
                    ),
                    409,  # Conflict
                )

            # Get database path
            db_url = str(_engine.url)
            if not db_url.startswith("sqlite:///"):
                _redis_client.delete(lock_key)  # Release lock
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": "VACUUM only supported for SQLite databases",
                        }
                    ),
                    400,
                )

            db_path = db_url.replace("sqlite:///", "")

            # Create job tracking
            job_id = str(uuid.uuid4())
            job_info = {
                "job_id": job_id,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "progress": "Starting VACUUM operation...",
            }

            with _vacuum_jobs_lock:
                _vacuum_jobs[job_id] = job_info

            # Run VACUUM in background thread
            def run_vacuum_background():
                try:
                    from pathlib import Path

                    from .store import vacuum_database

                    logger.info(
                        f"[VACUUM-JOB-{job_id}] Starting database VACUUM (maintenance mode)"
                    )

                    # Get size before
                    size_before = 0
                    try:
                        if Path(db_path).exists():
                            size_before = Path(db_path).stat().st_size
                            with _vacuum_jobs_lock:
                                _vacuum_jobs[job_id]["size_before_mb"] = size_before / (
                                    1024 * 1024
                                )
                                _vacuum_jobs[job_id][
                                    "progress"
                                ] = f"Database size: {size_before / (1024 * 1024):.2f} MB. Running VACUUM..."
                    except Exception as e:
                        logger.warning(
                            f"[VACUUM-JOB-{job_id}] Could not get size before: {e}"
                        )

                    # Run VACUUM with timeout monitoring
                    start_time = time_module.time()
                    vacuum_stats = vacuum_database(_engine)
                    duration = time_module.time() - start_time

                    # Get size after
                    size_after = 0
                    reclaimed_bytes = 0
                    try:
                        if Path(db_path).exists():
                            size_after = Path(db_path).stat().st_size
                            reclaimed_bytes = size_before - size_after
                    except Exception as e:
                        logger.warning(
                            f"[VACUUM-JOB-{job_id}] Could not get size after: {e}"
                        )

                    reclaimed_mb = reclaimed_bytes / (1024 * 1024)

                    if vacuum_stats["success"]:
                        logger.info(
                            f"[VACUUM-JOB-{job_id}] VACUUM completed in {duration:.2f}s, reclaimed {reclaimed_mb:.2f} MB"
                        )
                        with _vacuum_jobs_lock:
                            _vacuum_jobs[job_id].update(
                                {
                                    "status": "completed",
                                    "completed_at": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                    "duration_seconds": duration,
                                    "size_before_mb": size_before / (1024 * 1024),
                                    "size_after_mb": size_after / (1024 * 1024),
                                    "reclaimed_mb": reclaimed_mb,
                                    "progress": "VACUUM completed successfully",
                                }
                            )
                    else:
                        error_msg = vacuum_stats.get("error", "Unknown error")
                        logger.error(
                            f"[VACUUM-JOB-{job_id}] VACUUM failed: {error_msg}"
                        )
                        with _vacuum_jobs_lock:
                            _vacuum_jobs[job_id].update(
                                {
                                    "status": "failed",
                                    "completed_at": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                    "error": error_msg,
                                    "progress": f"VACUUM failed: {error_msg}",
                                }
                            )

                except Exception as e:
                    logger.error(
                        f"[VACUUM-JOB-{job_id}] VACUUM exception: {e}", exc_info=True
                    )
                    with _vacuum_jobs_lock:
                        _vacuum_jobs[job_id].update(
                            {
                                "status": "failed",
                                "completed_at": datetime.now(timezone.utc).isoformat(),
                                "error": str(e),
                                "progress": f"VACUUM failed with exception: {str(e)}",
                            }
                        )
                finally:
                    # Always release lock
                    try:
                        _redis_client.delete(lock_key)
                        logger.info(f"[VACUUM-JOB-{job_id}] Released VACUUM lock")
                    except Exception as e:
                        logger.error(
                            f"[VACUUM-JOB-{job_id}] Failed to release lock: {e}"
                        )

            # Start background thread
            vacuum_thread = threading.Thread(
                target=run_vacuum_background,
                name=f"VACUUM-{job_id[:8]}",
                daemon=True,
            )
            vacuum_thread.start()

            logger.info(
                f"[VACUUM-JOB-{job_id}] VACUUM job accepted and started in background"
            )

            return (
                jsonify(
                    {
                        "status": "accepted",
                        "data": {
                            "job_id": job_id,
                            "message": "VACUUM operation started in background",
                            "status_url": f"/api/database/vacuum/status/{job_id}",
                        },
                        "error": None,
                    }
                ),
                202,  # Accepted
            )

        except Exception as e:
            # Release lock on error
            if lock_acquired:
                try:
                    _redis_client.delete(lock_key)
                except Exception:
                    pass

            logger.error(f"Failed to initiate VACUUM: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to initiate VACUUM: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/database/vacuum/status/<job_id>", methods=["GET"])
    @require_admin_auth
    def vacuum_status_endpoint(job_id):
        """Get status of a VACUUM job.

        **REQUIRES ADMIN AUTHENTICATION** via X-Admin-Token header.

        Returns:
            JSON with job status, progress, and results if completed
        """
        with _vacuum_jobs_lock:
            # Cleanup old jobs to prevent unbounded memory growth
            _cleanup_vacuum_jobs()

            job_info = _vacuum_jobs.get(job_id)

        if not job_info:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Job not found",
                    }
                ),
                404,
            )

        return (
            jsonify(
                {
                    "status": "ok",
                    "data": job_info,
                    "error": None,
                }
            ),
            200,
        )

    def _cleanup_vacuum_jobs():
        """Clean up old vacuum jobs from memory.

        Removes jobs that are:
        - Finished more than 24 hours ago
        - Beyond the maximum retention count (keeps 100 most recent)

        **MUST be called while holding _vacuum_jobs_lock**
        """
        if not _vacuum_jobs:
            return

        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(hours=24)

        # Phase 1: Remove jobs older than 24 hours
        jobs_to_remove = []
        for job_id, job_info in _vacuum_jobs.items():
            finished_at = job_info.get("finished_at")
            if finished_at:
                try:
                    finished_dt = datetime.fromisoformat(
                        finished_at.replace("Z", "+00:00")
                    )
                    if finished_dt < cutoff_time:
                        jobs_to_remove.append(job_id)
                except (ValueError, AttributeError):
                    # Invalid timestamp, skip
                    pass

        for job_id in jobs_to_remove:
            del _vacuum_jobs[job_id]
            logger.info(f"[VACUUM-CLEANUP] Removed old job {job_id}")

        # Phase 2: Enforce max size (keep 100 most recent jobs)
        MAX_JOBS = 100
        if len(_vacuum_jobs) > MAX_JOBS:
            # Sort by started_at timestamp (most recent first)
            sorted_jobs = sorted(
                _vacuum_jobs.items(),
                key=lambda item: item[1].get("started_at", ""),
                reverse=True,
            )

            # Keep only the most recent MAX_JOBS
            jobs_to_keep = {job_id: info for job_id, info in sorted_jobs[:MAX_JOBS]}
            removed_count = len(_vacuum_jobs) - len(jobs_to_keep)
            _vacuum_jobs.clear()
            _vacuum_jobs.update(jobs_to_keep)

            if removed_count > 0:
                logger.info(
                    f"[VACUUM-CLEANUP] Pruned {removed_count} excess jobs (kept {MAX_JOBS} most recent)"
                )

    @app.route("/api/database/vacuum/cleanup", methods=["POST"])
    @require_admin_auth
    def vacuum_cleanup_endpoint():
        """Manually trigger cleanup of old VACUUM job records.

        **REQUIRES ADMIN AUTHENTICATION** via X-Admin-Token header.

        Removes jobs older than 24 hours and enforces maximum job count.
        Useful for explicit memory management.

        Returns:
            JSON with cleanup statistics
        """
        with _vacuum_jobs_lock:
            initial_count = len(_vacuum_jobs)
            _cleanup_vacuum_jobs()
            final_count = len(_vacuum_jobs)
            removed_count = initial_count - final_count

        logger.info(
            f"[VACUUM-CLEANUP] Manual cleanup: removed {removed_count} jobs ({initial_count} -> {final_count})"
        )

        return (
            jsonify(
                {
                    "status": "ok",
                    "data": {
                        "initial_count": initial_count,
                        "final_count": final_count,
                        "removed_count": removed_count,
                    },
                    "error": None,
                }
            ),
            200,
        )

    @app.route("/api/database/cleanup", methods=["POST"])
    def cleanup_database_endpoint():
        """Clean up old messages from Sentinel database.

        Query Parameters:
            days: Number of days to keep messages (default: 30)

        Returns:
            JSON with cleanup statistics including deleted count and remaining messages
        """
        if _engine is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Database not available",
                    }
                ),
                503,
            )

        try:
            from .store import cleanup_old_messages

            # Get retention days from query params or use default
            days = request.args.get("days", default=30, type=int)
            if days < 1:
                days = 30
            if days > 365:
                days = 365

            # Get config values if available
            max_messages = 200
            preserve_multiplier = 2
            if _config:
                try:
                    max_messages = getattr(_config.system.database, "max_messages", 200)
                    preserve_multiplier = getattr(
                        _config.system.database, "preserve_alerted_multiplier", 2
                    )
                except (AttributeError, KeyError):
                    pass

            # Run cleanup
            cleanup_stats = cleanup_old_messages(
                _engine,
                retention_days=days,
                max_messages=max_messages,
                preserve_alerted_multiplier=preserve_multiplier,
            )

            logger.info(
                f"Database cleanup completed: deleted {cleanup_stats['total_deleted']} messages, "
                f"{cleanup_stats['remaining_count']} remaining"
            )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "deleted_by_age": cleanup_stats["deleted_by_age"],
                            "deleted_by_count": cleanup_stats["deleted_by_count"],
                            "total_deleted": cleanup_stats["total_deleted"],
                            "remaining_count": cleanup_stats["remaining_count"],
                            "retention_days": days,
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to clean up database: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to clean up database: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/config", methods=["GET"])
    def get_config():
        """Get current configuration from Sentinel (single source of truth)."""

        def safe_getattr(obj, path, default=None):
            """Safely get nested attribute using dot notation path.

            Args:
                obj: Object to query
                path: Dot-separated path (e.g., "system.redis.host")
                default: Default value if path doesn't exist

            Returns:
                Attribute value or default
            """
            try:
                parts = path.split(".")
                result = obj
                for part in parts:
                    result = getattr(result, part, None)
                    if result is None:
                        return default
                return result
            except (AttributeError, TypeError):
                return default

        try:
            from tgsentinel.config import load_config

            cfg = load_config()

            # Serialize config to JSON-friendly format with safe attribute access
            config_data = {
                "telegram": {
                    "session": getattr(cfg, "telegram_session", None),
                },
                "alerts": {
                    "mode": safe_getattr(cfg, "alerts.mode", "dm"),
                    "target_channel": safe_getattr(cfg, "alerts.target_channel", None),
                    "min_score": safe_getattr(cfg, "alerts.min_score", 0.5),
                },
                "digest": {
                    "hourly": safe_getattr(cfg, "alerts.digest.hourly", False),
                    "daily": safe_getattr(cfg, "alerts.digest.daily", False),
                    "top_n": safe_getattr(cfg, "alerts.digest.top_n", 10),
                },
                "channels": [
                    {
                        "id": getattr(ch, "id", None),
                        "name": getattr(ch, "name", ""),
                        "vip_senders": getattr(ch, "vip_senders", []),
                        "keywords": getattr(ch, "keywords", []),
                        "reaction_threshold": getattr(ch, "reaction_threshold", 5),
                        "reply_threshold": getattr(ch, "reply_threshold", 3),
                        "rate_limit_per_hour": getattr(ch, "rate_limit_per_hour", 10),
                    }
                    for ch in getattr(cfg, "channels", [])
                ],
                "monitored_users": [
                    {
                        "id": getattr(user, "id", None),
                        "name": getattr(user, "name", ""),
                        "username": getattr(user, "username", None),
                        "enabled": getattr(user, "enabled", True),
                    }
                    for user in getattr(cfg, "monitored_users", [])
                ],
                "interests": getattr(cfg, "interests", []),
                "system": {
                    "redis": {
                        "host": safe_getattr(cfg, "system.redis.host", "localhost"),
                        "port": safe_getattr(cfg, "system.redis.port", 6379),
                        "stream": safe_getattr(
                            cfg, "system.redis.stream", "tgsentinel:messages"
                        ),
                        "group": safe_getattr(
                            cfg, "system.redis.group", "tgsentinel-workers"
                        ),
                        "consumer": safe_getattr(
                            cfg, "system.redis.consumer", "worker-1"
                        ),
                    },
                    "database_uri": safe_getattr(
                        cfg, "system.database_uri", "sqlite:////app/data/sentinel.db"
                    ),
                    "database": {
                        "max_messages": safe_getattr(
                            cfg, "system.database.max_messages", 200
                        ),
                        "retention_days": safe_getattr(
                            cfg, "system.database.retention_days", 30
                        ),
                        "cleanup_enabled": safe_getattr(
                            cfg, "system.database.cleanup_enabled", True
                        ),
                        "cleanup_interval_hours": safe_getattr(
                            cfg, "system.database.cleanup_interval_hours", 24
                        ),
                        "vacuum_on_cleanup": safe_getattr(
                            cfg, "system.database.vacuum_on_cleanup", True
                        ),
                        "vacuum_hour": safe_getattr(
                            cfg, "system.database.vacuum_hour", 3
                        ),
                    },
                    "logging": {
                        "level": safe_getattr(cfg, "system.logging.level", "info"),
                        "retention_days": safe_getattr(
                            cfg, "system.logging.retention_days", 7
                        ),
                    },
                    "metrics_endpoint": safe_getattr(
                        cfg, "system.metrics_endpoint", ""
                    ),
                    "auto_restart": safe_getattr(cfg, "system.auto_restart", False),
                },
                "redis": getattr(cfg, "redis", {}),  # Legacy compatibility
                "database_uri": getattr(
                    cfg, "db_uri", "sqlite:////app/data/sentinel.db"
                ),  # Legacy compatibility
                "embeddings_model": getattr(
                    cfg, "embeddings_model", "all-MiniLM-L6-v2"
                ),
                "similarity_threshold": getattr(cfg, "similarity_threshold", 0.7),
            }

            return jsonify({"status": "ok", "data": config_data, "error": None}), 200

        except Exception as e:
            logger.error(f"[API] Failed to load config: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "code": "CONFIG_LOAD_FAILED",
                        "message": f"Failed to load configuration: {str(e)}",
                        "data": None,
                        "error": {"code": "CONFIG_LOAD_FAILED", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/config", methods=["POST"])
    def update_config():
        """Update configuration on Sentinel (single source of truth)."""
        try:
            import yaml

            from tgsentinel.config import load_config

            if not request.is_json:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "code": "INVALID_REQUEST",
                            "message": "Content-Type must be application/json",
                            "data": None,
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "JSON payload required",
                            },
                        }
                    ),
                    400,
                )

            updates = request.get_json()
            if not updates:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "code": "EMPTY_PAYLOAD",
                            "message": "Empty update payload",
                            "data": None,
                            "error": {
                                "code": "EMPTY_PAYLOAD",
                                "message": "No updates provided",
                            },
                        }
                    ),
                    400,
                )

            # Load current config
            config_path = Path(os.getenv("TG_CONFIG_PATH", "config/tgsentinel.yml"))

            # Read existing YAML
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    current_config = yaml.safe_load(f) or {}
            else:
                current_config = {}

            # Merge updates (deep merge for nested dicts)
            def deep_merge(base: dict, updates: dict) -> dict:
                result = base.copy()
                for key, value in updates.items():
                    if (
                        key in result
                        and isinstance(result[key], dict)
                        and isinstance(value, dict)
                    ):
                        result[key] = deep_merge(result[key], value)
                    else:
                        result[key] = value
                return result

            updated_config = deep_merge(current_config, updates)

            # Validate the merged configuration before writing
            try:
                # Write to temporary YAML file for validation
                import tempfile

                temp_fd, temp_path = tempfile.mkstemp(
                    suffix=".yml", prefix="tgsentinel_config_", text=True
                )
                try:
                    with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                        yaml.safe_dump(
                            updated_config,
                            temp_f,
                            default_flow_style=False,
                            allow_unicode=True,
                            sort_keys=False,
                        )

                    # Validate by attempting to load with existing validation logic
                    validated_config = load_config(temp_path)

                    # Validation succeeded - perform atomic write
                    config_path.parent.mkdir(parents=True, exist_ok=True)

                    # Atomic write: write to temp file in same directory, then rename
                    temp_final_fd, temp_final_path = tempfile.mkstemp(
                        dir=config_path.parent,
                        suffix=".tmp",
                        prefix=".tgsentinel_config_",
                        text=True,
                    )
                    try:
                        with os.fdopen(temp_final_fd, "w", encoding="utf-8") as final_f:
                            yaml.safe_dump(
                                updated_config,
                                final_f,
                                default_flow_style=False,
                                allow_unicode=True,
                                sort_keys=False,
                            )

                        # Atomic rename (overwrites existing file)
                        os.replace(temp_final_path, config_path)

                        logger.info(
                            f"[API] Configuration validated and updated: {list(updates.keys())}"
                        )

                        # Update global config with validated object
                        global _config
                        _config = validated_config

                        # Notify main worker via Redis about config update
                        if _redis_client:
                            try:
                                _redis_client.publish(
                                    "tgsentinel:config_updated",
                                    json.dumps(
                                        {
                                            "event": "config_reloaded",
                                            "timestamp": datetime.now(
                                                timezone.utc
                                            ).isoformat(),
                                            "config_keys": list(updates.keys()),
                                        }
                                    ),
                                )
                                logger.info(
                                    "[API] Published config_reloaded event to worker"
                                )
                            except Exception as redis_exc:
                                logger.warning(
                                    f"[API] Failed to publish config update event: {redis_exc}"
                                )

                    except Exception as write_exc:
                        # Clean up temp file on write failure
                        if os.path.exists(temp_final_path):
                            os.unlink(temp_final_path)
                        raise write_exc

                finally:
                    # Clean up validation temp file
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)

            except ValueError as val_exc:
                # Config validation failed
                logger.warning(
                    f"[API] Configuration validation failed: {val_exc}", exc_info=True
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "code": "CONFIG_VALIDATION_FAILED",
                            "message": f"Configuration validation failed: {str(val_exc)}",
                            "data": None,
                            "error": {
                                "code": "CONFIG_VALIDATION_FAILED",
                                "message": str(val_exc),
                                "details": "The updated configuration contains invalid values or missing required fields",
                            },
                        }
                    ),
                    400,
                )
            except Exception as config_exc:
                # Other config processing errors
                logger.error(
                    f"[API] Failed to process configuration: {config_exc}",
                    exc_info=True,
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "code": "CONFIG_PROCESSING_FAILED",
                            "message": f"Failed to process configuration: {str(config_exc)}",
                            "data": None,
                            "error": {
                                "code": "CONFIG_PROCESSING_FAILED",
                                "message": str(config_exc),
                            },
                        }
                    ),
                    500,
                )

            return (
                jsonify(
                    {"status": "ok", "message": "Configuration updated", "error": None}
                ),
                200,
            )

        except Exception as e:
            logger.error(f"[API] Failed to update config: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "code": "CONFIG_UPDATE_FAILED",
                        "message": f"Failed to update configuration: {str(e)}",
                        "data": None,
                        "error": {"code": "CONFIG_UPDATE_FAILED", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/profiles/interest/backtest", methods=["POST"])
    def backtest_interest_profile():
        """Backtest an interest profile using semantic scoring.

        This endpoint runs in the Sentinel container where the embeddings model is loaded.
        The UI should call this endpoint instead of loading the model itself.

        Request body:
            {
                "profile_name": "my_profile",
                "profile": {
                    "threshold": 0.42,
                    ...
                },
                "hours_back": 24,
                "max_messages": 500
            }

        Returns:
            {
                "status": "ok",
                "profile_name": "...",
                "test_date": "...",
                "parameters": {...},
                "matches": [...],
                "stats": {
                    "total_messages": N,
                    "matched_messages": M,
                    "match_rate": X.X,
                    "avg_score": X.XXX,
                    "threshold": X.XX
                }
            }
        """
        try:
            if not request.is_json:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Content-Type must be application/json",
                        }
                    ),
                    400,
                )

            data = request.get_json()
            profile_name = data.get("profile_name", "unnamed")
            profile = data.get("profile", {})
            hours_back = int(data.get("hours_back", 24))
            max_messages = int(data.get("max_messages", 500))

            # Validate parameters
            if not (0 <= hours_back <= 168):  # Max 7 days
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "hours_back must be between 0 and 168",
                        }
                    ),
                    400,
                )

            if not (1 <= max_messages <= 1000):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "max_messages must be between 1 and 1000",
                        }
                    ),
                    400,
                )

            # Import semantic module (only available in Sentinel container)
            try:
                from tgsentinel.semantic import _model, load_interests, score_text

                logger.info(
                    f"[API] Backtest: _model is {'loaded' if _model else 'None'}"
                )

                if _model is None:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Semantic model not loaded in Sentinel container",
                            }
                        ),
                        500,
                    )

                # Load profile's positive samples into the semantic scorer
                positive_samples = profile.get("positive_samples", [])
                if not positive_samples:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Profile must have positive_samples for semantic scoring",
                            }
                        ),
                        400,
                    )

                logger.info(
                    f"[API] Backtest: loading {len(positive_samples)} positive samples into scorer"
                )
                load_interests(positive_samples)
                logger.info(f"[API] Backtest: positive samples loaded")

            except ImportError as ie:
                logger.error(f"[API] Semantic module import failed: {ie}")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Semantic module not available in Sentinel container",
                        }
                    ),
                    500,
                )

            # Query messages from database
            if not _engine:
                return (
                    jsonify({"status": "error", "message": "Database not initialized"}),
                    500,
                )

            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
            query = text(
                """
                SELECT msg_id, chat_id, chat_title, sender_name, message_text, 
                       score, created_at
                FROM messages
                WHERE created_at >= :cutoff
                ORDER BY created_at DESC
                LIMIT :limit
                """
            )

            with _engine.connect() as conn:
                result = conn.execute(
                    query, {"cutoff": cutoff.isoformat(), "limit": max_messages}
                )
                messages = [
                    {
                        "msg_id": row[0],
                        "chat_id": row[1],
                        "chat_title": row[2],
                        "sender_name": row[3],
                        "message_text": row[4],
                        "score": row[5],
                        "created_at": row[6],
                    }
                    for row in result
                ]

            # Score messages using semantic model
            matches = []
            threshold = profile.get("threshold", 0.42)

            logger.info(
                f"[API] Backtest: scoring {len(messages)} messages with threshold {threshold}"
            )

            for msg in messages:
                message_text = msg.get("message_text", "")
                if not message_text:
                    logger.debug(
                        f"[API] Backtest: skipping message {msg.get('msg_id')} - no text"
                    )
                    continue

                # Score text with semantic model
                score = score_text(message_text)
                logger.debug(
                    f"[API] Backtest: msg {msg.get('msg_id')} scored {score} (text: {message_text[:50]}...)"
                )

                if score is None:
                    logger.debug(
                        f"[API] Backtest: score is None for message {msg.get('msg_id')}"
                    )
                    continue

                if score >= threshold:
                    matches.append(
                        {
                            "message_id": msg["msg_id"],
                            "chat_id": msg["chat_id"],
                            "chat_title": msg["chat_title"],
                            "sender_name": msg["sender_name"],
                            "score": round(score, 3),
                            "original_score": round(msg.get("score", 0.0), 2),
                            "text_preview": message_text[:100]
                            + ("..." if len(message_text) > 100 else ""),
                            "timestamp": msg["created_at"],
                        }
                    )

            # Calculate statistics
            stats = {
                "total_messages": len(messages),
                "matched_messages": len(matches),
                "match_rate": (
                    round(len(matches) / len(messages) * 100, 1)
                    if len(messages) > 0
                    else 0
                ),
                "avg_score": (
                    round(sum(m["score"] for m in matches) / len(matches), 3)
                    if len(matches) > 0
                    else 0
                ),
                "threshold": threshold,
            }

            result_data = {
                "status": "ok",
                "profile_name": profile_name,
                "test_date": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "hours_back": hours_back,
                    "max_messages": max_messages,
                },
                "matches": matches[:50],  # Limit response size
                "stats": stats,
            }

            logger.info(
                f"[API] Backtest completed for interest profile {profile_name}: {stats}"
            )
            return jsonify(result_data)

        except Exception as exc:
            logger.error(
                f"[API] Error backtesting interest profile: {exc}", exc_info=True
            )
            return (
                jsonify({"status": "error", "message": str(exc)}),
                500,
            )

    @app.route("/api/profiles/interest/test_similarity", methods=["POST"])
    def test_similarity():
        """Test semantic similarity of a text sample against an interest profile.

        This endpoint uses the embeddings model loaded in the Sentinel container
        to compute real-time similarity scores between a test message and the
        positive training samples of an interest profile.

        Request body:
            {
                "sample": "text to test",
                "profile_id": "3000" or "profile_name"
            }

        Returns:
            {
                "status": "ok",
                "score": 0.XXX,
                "interpretation": "...",
                "profile_id": "...",
                "model": "all-MiniLM-L6-v2"
            }
        """
        try:
            if not request.is_json:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Content-Type must be application/json",
                        }
                    ),
                    400,
                )

            data = request.get_json()
            sample = data.get("sample", "").strip()
            profile_id = data.get("profile_id", "").strip()

            if not sample:
                return (
                    jsonify({"status": "error", "message": "Sample text is required"}),
                    400,
                )

            if not profile_id:
                return (
                    jsonify({"status": "error", "message": "Profile ID is required"}),
                    400,
                )

            # Import semantic module
            try:
                from tgsentinel.semantic import _model

                if _model is None:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Semantic model not loaded. Ensure EMBEDDINGS_MODEL environment variable is set.",
                            }
                        ),
                        500,
                    )
            except ImportError as ie:
                logger.error(f"[API] Semantic module import failed: {ie}")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Semantic module not available in Sentinel container",
                        }
                    ),
                    500,
                )

            # Load profile to get positive samples
            if not _config:
                return (
                    jsonify(
                        {"status": "error", "message": "Configuration not initialized"}
                    ),
                    500,
                )

            # Try to find profile in loaded profiles
            profile = None
            profiles_dict = getattr(_config, "_global_profiles", {})

            if profile_id in profiles_dict:
                profile = profiles_dict[profile_id]
            else:
                # Try loading from YAML files directly
                import os

                config_dir = os.path.dirname(getattr(_config, "_config_path", "config"))
                if not config_dir:
                    config_dir = "config"

                interest_path = os.path.join(config_dir, "profiles_interest.yml")
                if os.path.exists(interest_path):
                    import yaml

                    with open(interest_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                        profiles_data = (
                            data.get("profiles", data) if "profiles" in data else data
                        )
                        if profile_id in profiles_data:
                            profile = profiles_data[profile_id]

            if not profile:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile '{profile_id}' not found",
                        }
                    ),
                    404,
                )

            # Get positive samples from profile
            positive_samples = profile.get("positive_samples", [])
            if not positive_samples:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Profile has no positive training samples",
                        }
                    ),
                    400,
                )

            # Compute embeddings for sample and positive samples
            import numpy as np

            sample_embedding = _model.encode([sample], normalize_embeddings=True)[0]
            positive_embeddings = _model.encode(
                positive_samples, normalize_embeddings=True
            )

            # Compute cosine similarity with each positive sample
            similarities = [
                float(np.dot(sample_embedding, pos_emb))
                for pos_emb in positive_embeddings
            ]

            # Use max similarity as the score (most similar to any positive sample)
            score = max(similarities) if similarities else 0.0
            score = max(0.0, min(1.0, score))  # Clamp to [0, 1]

            # Provide interpretation
            if score < 0.30:
                interpretation = "Very different from profile interests"
            elif score < 0.50:
                interpretation = "Somewhat related"
            elif score < 0.70:
                interpretation = "Moderately similar"
            elif score < 0.85:
                interpretation = "Highly similar"
            else:
                interpretation = "Very closely matches profile"

            logger.info(
                f"[API] Similarity test for profile '{profile_id}': score={score:.3f}"
            )

            return jsonify(
                {
                    "status": "ok",
                    "score": round(score, 3),
                    "interpretation": interpretation,
                    "profile_id": profile_id,
                    "model": "all-MiniLM-L6-v2",
                    "num_positive_samples": len(positive_samples),
                    "sample_length": len(sample),
                }
            )

        except Exception as exc:
            logger.error(f"[API] Error testing similarity: {exc}", exc_info=True)
            return (
                jsonify({"status": "error", "message": str(exc)}),
                500,
            )

    # ==================== UNIFIED PROFILE CRUD ENDPOINTS ====================
    @app.route("/api/profiles/<profile_type>", methods=["GET"])
    def get_profiles(profile_type):
        """Get all profiles of a specific type (alert, global, or interest).

        Args:
            profile_type: One of 'alert', 'global', 'interest'

        Returns:
            JSON response with profiles dictionary
        """
        try:
            if profile_type not in ("alert", "global", "interest"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Invalid profile type: {profile_type}. Must be 'alert', 'global', or 'interest'",
                            "data": None,
                        }
                    ),
                    400,
                )

            # Use config directory from environment or default to /app/config
            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            profiles_path = config_dir / f"profiles_{profile_type}.yml"

            if not profiles_path.exists():
                logger.info(
                    f"[API] No {profile_type} profiles file found at {profiles_path}, returning empty"
                )
                return jsonify({"status": "ok", "data": {}})

            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles = yaml.safe_load(f) or {}

            logger.info(f"[API] Loaded {len(profiles)} {profile_type} profiles")
            return jsonify({"status": "ok", "data": profiles})

        except Exception as exc:
            logger.error(
                f"[API] Error loading {profile_type} profiles: {exc}", exc_info=True
            )
            return jsonify({"status": "error", "message": str(exc), "data": None}), 500

    @app.route("/api/profiles/<profile_type>/<profile_id>", methods=["GET"])
    def get_profile(profile_type, profile_id):
        """Get a single profile by ID and type.

        Args:
            profile_type: One of 'alert', 'global', 'interest'
            profile_id: Profile identifier

        Returns:
            JSON response with profile data
        """
        try:
            if profile_type not in ("alert", "global", "interest"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Invalid profile type: {profile_type}",
                            "data": None,
                        }
                    ),
                    400,
                )

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            profiles_path = config_dir / f"profiles_{profile_type}.yml"

            if not profiles_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile not found: {profile_id}",
                            "data": None,
                        }
                    ),
                    404,
                )

            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles = yaml.safe_load(f) or {}

            if profile_id not in profiles:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile not found: {profile_id}",
                            "data": None,
                        }
                    ),
                    404,
                )

            logger.info(f"[API] Retrieved {profile_type} profile: {profile_id}")
            return jsonify({"status": "ok", "data": profiles[profile_id]})

        except Exception as exc:
            logger.error(
                f"[API] Error getting {profile_type} profile {profile_id}: {exc}",
                exc_info=True,
            )
            return jsonify({"status": "error", "message": str(exc), "data": None}), 500

    @app.route("/api/profiles/<profile_type>", methods=["POST", "PUT"])
    def save_profiles(profile_type):
        """Save all profiles of a specific type (replaces entire file).

        Args:
            profile_type: One of 'alert', 'global', 'interest'

        Request body:
            Dictionary of profiles {profile_id: profile_data}

        Returns:
            JSON response with success status
        """
        try:
            if profile_type not in ("alert", "global", "interest"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Invalid profile type: {profile_type}",
                            "data": None,
                        }
                    ),
                    400,
                )

            if not request.is_json:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Content-Type must be application/json",
                            "data": None,
                        }
                    ),
                    400,
                )

            profiles = request.get_json()
            if not isinstance(profiles, dict):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Request body must be a dictionary of profiles",
                            "data": None,
                        }
                    ),
                    400,
                )

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            config_dir.mkdir(parents=True, exist_ok=True)

            profiles_path = config_dir / f"profiles_{profile_type}.yml"
            temp_path = profiles_path.with_suffix(".yml.tmp")

            # Write to temp file first for atomicity
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    profiles, f, default_flow_style=False, allow_unicode=True
                )

            # Atomic replace
            temp_path.replace(profiles_path)

            logger.info(
                f"[API] Saved {len(profiles)} {profile_type} profiles to {profiles_path}"
            )
            return jsonify(
                {
                    "status": "ok",
                    "message": f"Saved {len(profiles)} {profile_type} profiles",
                    "data": {"count": len(profiles), "file": str(profiles_path)},
                }
            )

        except Exception as exc:
            logger.error(
                f"[API] Error saving {profile_type} profiles: {exc}", exc_info=True
            )
            # Clean up temp file if it exists
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except:
                pass
            return jsonify({"status": "error", "message": str(exc), "data": None}), 500

    @app.route("/api/profiles/<profile_type>/<profile_id>/toggle", methods=["POST"])
    def toggle_profile(profile_type, profile_id):
        """Toggle the enabled status of a profile.

        Args:
            profile_type: One of 'alert', 'global', 'interest'
            profile_id: Profile identifier

        Returns:
            JSON response with new enabled status
        """
        try:
            if profile_type not in ("alert", "global", "interest"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Invalid profile type: {profile_type}",
                            "data": None,
                        }
                    ),
                    400,
                )

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            profiles_path = config_dir / f"profiles_{profile_type}.yml"

            if not profiles_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile not found: {profile_id}",
                            "data": None,
                        }
                    ),
                    404,
                )

            # Read current profiles
            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles = yaml.safe_load(f) or {}

            if profile_id not in profiles:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile not found: {profile_id}",
                            "data": None,
                        }
                    ),
                    404,
                )

            # Toggle enabled status
            current_status = profiles[profile_id].get("enabled", False)
            new_status = not current_status
            profiles[profile_id]["enabled"] = new_status

            # Write atomically
            temp_path = profiles_path.with_suffix(".yml.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    profiles, f, default_flow_style=False, allow_unicode=True
                )
            temp_path.replace(profiles_path)

            logger.info(
                f"[API] Toggled {profile_type} profile {profile_id}: {current_status} → {new_status}"
            )
            return jsonify(
                {
                    "status": "ok",
                    "message": f"Profile {profile_id} {'enabled' if new_status else 'disabled'}",
                    "data": {"enabled": new_status},
                }
            )

        except Exception as exc:
            logger.error(
                f"[API] Error toggling {profile_type} profile {profile_id}: {exc}",
                exc_info=True,
            )
            return jsonify({"status": "error", "message": str(exc), "data": None}), 500

    @app.route("/api/profiles/<profile_type>/<profile_id>", methods=["DELETE"])
    def delete_profile(profile_type, profile_id):
        """Delete a profile by ID and type.

        Args:
            profile_type: One of 'alert', 'global', 'interest'
            profile_id: Profile identifier

        Returns:
            JSON response with success status
        """
        try:
            if profile_type not in ("alert", "global", "interest"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Invalid profile type: {profile_type}",
                            "data": None,
                        }
                    ),
                    400,
                )

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            profiles_path = config_dir / f"profiles_{profile_type}.yml"

            if not profiles_path.exists():
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile not found: {profile_id}",
                            "data": None,
                        }
                    ),
                    404,
                )

            # Read current profiles
            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles = yaml.safe_load(f) or {}

            if profile_id not in profiles:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile not found: {profile_id}",
                            "data": None,
                        }
                    ),
                    404,
                )

            # Delete profile
            del profiles[profile_id]

            # Write atomically
            temp_path = profiles_path.with_suffix(".yml.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    profiles, f, default_flow_style=False, allow_unicode=True
                )
            temp_path.replace(profiles_path)

            logger.info(f"[API] Deleted {profile_type} profile: {profile_id}")
            return jsonify(
                {
                    "status": "ok",
                    "message": f"Profile {profile_id} deleted",
                    "data": None,
                }
            )

        except Exception as exc:
            logger.error(
                f"[API] Error deleting {profile_type} profile {profile_id}: {exc}",
                exc_info=True,
            )
            return jsonify({"status": "error", "message": str(exc), "data": None}), 500

    @app.errorhandler(404)
    def not_found(e):
        """Handle 404 errors with JSON response."""
        return (
            jsonify(
                {
                    "status": "error",
                    "code": "NOT_FOUND",
                    "message": f"Endpoint not found: {request.path}",
                    "data": None,
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "The requested endpoint does not exist",
                    },
                }
            ),
            404,
        )

    @app.errorhandler(500)
    def internal_error(e):
        """Handle 500 errors with JSON response."""
        return (
            jsonify(
                {
                    "status": "error",
                    "code": "INTERNAL_ERROR",
                    "message": "Internal server error",
                    "data": None,
                    "error": {"code": "INTERNAL_ERROR", "message": str(e)},
                }
            ),
            500,
        )

    return app


def start_api_server(host: str = "0.0.0.0", port: int = 8080):
    """Start the API server in a background thread."""
    app = create_api_app()

    def run_server():
        logger.info(f"[API] Starting sentinel HTTP API on {host}:{port}")
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

    thread = threading.Thread(target=run_server, daemon=True, name="SentinelAPI")
    thread.start()
    logger.info("[API] Sentinel HTTP API server started in background thread")

    return thread
