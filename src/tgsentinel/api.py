"""Sentinel HTTP API Server

This module provides a minimal HTTP/JSON API for the sentinel worker to:
1. Accept session file uploads from the UI
2. Provide health and status information
3. Expose alerts and stats to the UI

The API runs in a separate thread alongside the main sentinel worker.
"""

import asyncio
import base64
import fcntl
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time as time_module
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml
from flask import Flask, Response, jsonify, request, send_file
from flask_cors import CORS
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from tgsentinel.alert_feedback_aggregator import get_alert_feedback_aggregator
from tgsentinel.config import DigestSchedule
from tgsentinel.feedback_aggregator import get_feedback_aggregator
from tgsentinel.heuristics import run_heuristics
from tgsentinel.profile_tuner import ProfileTuner
from tgsentinel.timestamp_utils import format_db_timestamp

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
_shutdown_coordinator: Any = None
_client_getter: Callable[[], Any] | None = None
_unified_digest_worker: Any = None
_main_loop: Any = None  # Main asyncio event loop for scheduling coroutines

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


def set_shutdown_coordinator(coordinator: Any):
    """Set shutdown coordinator for graceful restart."""
    global _shutdown_coordinator
    _shutdown_coordinator = coordinator


def set_telegram_client_getter(getter: Callable[[], Any]):
    """Set the callable that returns the active Telegram client."""
    global _client_getter
    _client_getter = getter


def set_unified_digest_worker(worker: Any):
    """Register the running UnifiedDigestWorker for manual triggers."""
    global _unified_digest_worker
    _unified_digest_worker = worker


def set_main_event_loop(loop: Any):
    """Set the main asyncio event loop for scheduling digest coroutines."""
    global _main_loop
    _main_loop = loop


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

    def _webhooks_path() -> Path:
        """Return the path to webhooks.yml, ensuring config dir exists."""
        config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "webhooks.yml"

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

    @app.route("/api/ready", methods=["GET"])
    def ready():
        """Readiness check endpoint for login UI.

        Returns whether the auth worker is ready to accept authentication requests.
        This prevents premature login attempts during service initialization.
        """
        auth_worker_ready = _sentinel_state.get("auth_worker_ready", False)
        return (
            jsonify(
                {
                    "status": "ok",
                    "ready": auth_worker_ready,
                    "message": (
                        "Auth worker initialized"
                        if auth_worker_ready
                        else "Initializing service..."
                    ),
                }
            ),
            200,
        )

    @app.route("/api/health/semantic", methods=["GET"])
    def health_semantic():
        """Semantic scoring health check endpoint.

        Returns detailed status of the semantic scoring system including:
        - Model loading status
        - Profile embeddings status
        - Configuration details
        """
        try:
            from tgsentinel.semantic import get_model_status

            model_status = get_model_status()

            # Determine overall health
            is_healthy = model_status["model_loaded"]
            status_msg = "healthy" if is_healthy else "degraded"

            if not model_status["model_loaded"]:
                status_msg = "model_not_loaded"
            elif model_status["profile_count"] == 0:
                status_msg = "no_profiles_loaded"

            return (
                jsonify(
                    {
                        "status": "ok" if is_healthy else "warning",
                        "semantic_scoring": {
                            "enabled": model_status["model_loaded"],
                            "status": status_msg,
                            "model_name": model_status["model_name"],
                            "profiles_loaded": model_status["profile_count"],
                            "profile_ids": model_status["profiles"],
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                200,
            )
        except Exception as e:
            logger.error(f"Failed to get semantic health status: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "semantic_scoring": {
                            "enabled": False,
                            "status": "error",
                            "error": str(e),
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                500,
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

    @app.route("/api/session/download", methods=["GET"])
    @require_admin_auth
    def session_download():
        """Download the active Telethon session file.

        SECURITY: Only serves tgsentinel.session from the canonical path.
        No fallback logic or directory traversal to prevent unintended file access.
        """
        # Use explicit session path from config, or canonical default
        session_path = None
        if _config:
            session_path = getattr(_config, "telegram_session", None)

        # Only allow tgsentinel.session from canonical paths
        if session_path:
            # Handle both absolute and relative paths
            session_file = Path(session_path)
            # If relative, make it relative to /app (not current file location)
            if not session_file.is_absolute():
                session_file = Path("/app") / session_file
        else:
            # Default: /app/data/tgsentinel.session (Docker) or data/tgsentinel.session (local)
            session_file = Path("/app/data/tgsentinel.session")
            if not session_file.exists():
                session_file = Path("data/tgsentinel.session").resolve()

        # Validate filename is exactly tgsentinel.session (no other .session files)
        if session_file.name != "tgsentinel.session":
            logger.warning(
                f"[SESSION-DOWNLOAD] Rejected download attempt for non-standard session file: {session_file.name}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Only tgsentinel.session can be downloaded",
                    }
                ),
                403,
            )

        if not session_file.exists() or not session_file.is_file():
            logger.warning(
                f"[SESSION-DOWNLOAD] Session file not found at: {session_file}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Session file not found",
                    }
                ),
                404,
            )

        try:
            logger.info(f"[SESSION-DOWNLOAD] Serving session file from: {session_file}")
            return send_file(
                session_file,
                as_attachment=True,
                download_name="tgsentinel.session",
                mimetype="application/octet-stream",
            )
        except Exception as e:
            logger.error(f"Failed to serve session download: {e}", exc_info=True)
            return (
                jsonify({"status": "error", "message": "Failed to download session"}),
                500,
            )

    @app.route("/api/alerts", methods=["GET"])
    def alerts():
        """Get recent alerts from sentinel.db messages table (Alert profile matches only)."""
        try:
            # Get query parameters
            limit = request.args.get("limit", default=100, type=int)
            offset = request.args.get("offset", default=0, type=int)

            # Validate limit
            if limit > 1000:
                limit = 1000
            if limit < 1:
                limit = 100

            # Query Alert profile matched messages from sentinel.db
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
                    created_at,
                    matched_profiles,
                    trigger_annotations
                FROM messages
                WHERE flagged_for_alerts_feed = 1
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """

            count_query = """
                SELECT COUNT(*) as total
                FROM messages
                WHERE flagged_for_alerts_feed = 1
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

                # Parse matched_profiles and semantic scores
                matched_profiles = []
                semantic_scores = {}
                try:
                    if row.matched_profiles:
                        import json

                        matched_profiles = json.loads(row.matched_profiles)
                    if row.trigger_annotations:
                        import json

                        annotations = json.loads(row.trigger_annotations)
                        semantic_scores = annotations.get("semantic_scores", {})
                except (json.JSONDecodeError, AttributeError):
                    pass

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
                        "read": False,
                        "dismissed": False,
                        "matched_profiles": matched_profiles,
                        "semantic_scores": semantic_scores,
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

    @app.route("/api/interests", methods=["GET"])
    def get_interests():
        """Get Interest Profile matches (semantic scores above threshold).

        Query Parameters:
            limit: Number of matches to return (default: 100)
            profile_id: Filter by specific interest profile (optional)

        Returns:
            JSON with list of interest matches
        """
        if _engine is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Database not initialized",
                    }
                ),
                500,
            )

        try:
            limit = request.args.get("limit", default=100, type=int)
            profile_id = request.args.get("profile_id", type=int)

            # Load interest profile definitions to get thresholds
            profile_defs = {}
            profiles_path = os.path.join(
                os.getenv("CONFIG_DIR", "/app/config"), "profiles_interest.yml"
            )
            if os.path.exists(profiles_path):
                import yaml

                with open(profiles_path, "r", encoding="utf-8") as f:
                    profiles_data = yaml.safe_load(f) or {}
                    for pid, pdef in profiles_data.items():
                        if isinstance(pdef, dict):
                            profile_defs[int(pid)] = {
                                "name": pdef.get("name", f"Interest {pid}"),
                                "threshold": float(pdef.get("threshold", 0.25)),
                            }

            # Build WHERE clause conditions
            conditions = []
            params = {}

            # For each interest profile, add condition for semantic score >= threshold
            if profile_id:
                # Filter by specific profile
                if profile_id in profile_defs:
                    threshold = profile_defs[profile_id]["threshold"]
                    json_path = f'$.semantic_scores."{profile_id}"'
                    conditions.append(
                        f"CAST(json_extract(trigger_annotations, '{json_path}') AS REAL) >= :threshold_{profile_id}"
                    )
                    params[f"threshold_{profile_id}"] = threshold
                else:
                    # Profile not found
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "data": None,
                                "error": f"Interest profile {profile_id} not found",
                            }
                        ),
                        404,
                    )
            else:
                # Include all interest profiles
                for pid, pdef in profile_defs.items():
                    threshold = pdef["threshold"]
                    json_path = f'$.semantic_scores."{pid}"'
                    conditions.append(
                        f"CAST(json_extract(trigger_annotations, '{json_path}') AS REAL) >= :threshold_{pid}"
                    )
                    params[f"threshold_{pid}"] = threshold

            if not conditions:
                # No interest profiles configured
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "data": {"interests": []},
                        }
                    ),
                    200,
                )

            # Query all messages with semantic scores (we'll filter in Python)
            query = text(
                """
                SELECT
                    chat_id,
                    msg_id,
                    content_hash,
                    score,
                    chat_title,
                    sender_name,
                    message_text,
                    triggers,
                    sender_id,
                    trigger_annotations,
                    matched_profiles,
                    created_at
                FROM messages
                WHERE trigger_annotations IS NOT NULL
                  AND trigger_annotations != ''
                ORDER BY created_at DESC
                """
            )

            with _engine.connect() as conn:
                result = conn.execute(query)
                rows = result.fetchall()

            interests = []
            for row in rows:
                row_dict = dict(row._mapping)

                # Parse trigger_annotations to extract semantic scores
                trigger_annotations = {}
                semantic_scores = {}
                if row_dict.get("trigger_annotations"):
                    try:
                        trigger_annotations = json.loads(
                            row_dict["trigger_annotations"]
                        )
                        semantic_scores = trigger_annotations.get("semantic_scores", {})
                    except json.JSONDecodeError:
                        continue

                # Find the interest profile(s) that matched
                matched_interest_profiles = []
                has_match = False
                for pid, pdef in profile_defs.items():
                    pid_str = str(pid)
                    if pid_str in semantic_scores:
                        score_val = semantic_scores[pid_str]
                        if score_val >= pdef["threshold"]:
                            has_match = True
                            matched_interest_profiles.append(
                                {
                                    "profile_id": pid,
                                    "profile_name": pdef["name"],
                                    "semantic_score": round(score_val, 3),
                                    "threshold": pdef["threshold"],
                                }
                            )

                # Only include if at least one profile matched
                if not has_match:
                    continue

                # If filtering by specific profile_id, check if it matched
                if profile_id and not any(
                    mp["profile_id"] == profile_id for mp in matched_interest_profiles
                ):
                    continue

                interests.append(
                    {
                        "chat_id": row_dict["chat_id"],
                        "message_id": row_dict["msg_id"],
                        "chat_title": row_dict.get("chat_title"),
                        "sender_name": row_dict.get("sender_name"),
                        "message_text": row_dict.get("message_text"),
                        "triggers": row_dict.get("triggers"),
                        "timestamp": row_dict["created_at"],
                        "matched_interest_profiles": matched_interest_profiles,
                        "semantic_scores": semantic_scores,
                    }
                )

                # Stop once we have enough results
                if len(interests) >= limit:
                    break

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {"interests": interests},
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch interests: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch interests: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/feed/alerts", methods=["GET"])
    def feed_alerts():
        """Get alert feed (heuristic/keyword-based matches only).

        New taxonomy-compliant endpoint enforcing "Alerts = heuristic + keyword scoring".
        Uses feed_alert_flag (new naming) with fallback to flagged_for_alerts_feed (legacy).

        Query Parameters:
            limit: Number of alerts to return (default: 100, max: 1000)
            offset: Pagination offset (default: 0)

        Returns:
            JSON with alert feed items, sorted by created_at DESC
        """
        try:
            limit = request.args.get("limit", default=100, type=int)
            offset = request.args.get("offset", default=0, type=int)

            if limit > 1000:
                limit = 1000
            if limit < 1:
                limit = 100

            # Phase 1: Dual-read (new column with fallback to legacy)
            query = """
                SELECT
                    chat_id,
                    msg_id,
                    chat_title,
                    sender_name,
                    message_text,
                    keyword_score,
                    score,
                    triggers,
                    sender_id,
                    created_at,
                    matched_profiles,
                    trigger_annotations,
                    semantic_type,
                    delivery_mode_used,
                    delivery_target_used
                FROM messages
                WHERE COALESCE(feed_alert_flag, flagged_for_alerts_feed) = 1
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """

            count_query = """
                SELECT COUNT(*) as total
                FROM messages
                WHERE COALESCE(feed_alert_flag, flagged_for_alerts_feed) = 1
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

            # Format feed items
            feed_items = []
            for row in rows:
                # Use keyword_score if available, fallback to score
                keyword_score = (
                    row.keyword_score
                    if row.keyword_score is not None
                    else (row.score or 0.0)
                )

                message_text = row.message_text or ""
                if len(message_text) > 300:
                    message_text = message_text[:300] + "..."

                # Parse JSON fields
                matched_profiles = []
                trigger_annotations = {}
                try:
                    if row.matched_profiles:
                        matched_profiles = json.loads(row.matched_profiles)
                    if row.trigger_annotations:
                        trigger_annotations = json.loads(row.trigger_annotations)
                except (json.JSONDecodeError, AttributeError):
                    pass

                feed_items.append(
                    {
                        "feed_item_id": f"{row.chat_id}_{row.msg_id}",
                        "semantic_type": row.semantic_type or "alert_keyword",
                        "chat_id": row.chat_id,
                        "message_id": row.msg_id,
                        "chat_title": row.chat_title or f"Chat {row.chat_id}",
                        "sender_name": row.sender_name or "Unknown",
                        "message_text": message_text,
                        "keyword_score": float(keyword_score),
                        "triggers": row.triggers or "",
                        "sender_id": row.sender_id or 0,
                        "timestamp": row.created_at,
                        "matched_profiles": matched_profiles,
                        "trigger_annotations": trigger_annotations,
                        "delivery_mode_used": row.delivery_mode_used,
                        "delivery_target_used": row.delivery_target_used,
                    }
                )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "feed_items": feed_items,
                            "count": len(feed_items),
                            "total": total_count,
                            "limit": limit,
                            "offset": offset,
                            "semantic_type": "alert_keyword",
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch alert feed: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch alert feed: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/feed/interests", methods=["GET"])
    def feed_interests():
        """Get interest feed (semantic/embedding-based matches only).

        New taxonomy-compliant endpoint enforcing "Interests = semantic + semantic scoring".
        Uses feed_interest_flag (new naming) with fallback to flagged_for_interest_feed (legacy).

        Query Parameters:
            limit: Number of interests to return (default: 100, max: 1000)
            offset: Pagination offset (default: 0)
            profile_id: Filter by specific interest profile (optional)

        Returns:
            JSON with interest feed items, sorted by max semantic score DESC
        """
        try:
            limit = request.args.get("limit", default=100, type=int)
            offset = request.args.get("offset", default=0, type=int)
            profile_id = request.args.get("profile_id", type=str)

            if limit > 1000:
                limit = 1000
            if limit < 1:
                limit = 100

            # Phase 1: Dual-read (new column with fallback to legacy)
            query = """
                SELECT
                    chat_id,
                    msg_id,
                    chat_title,
                    sender_name,
                    message_text,
                    semantic_scores_json,
                    triggers,
                    sender_id,
                    created_at,
                    matched_profiles,
                    trigger_annotations,
                    semantic_type,
                    delivery_mode_used,
                    delivery_target_used
                FROM messages
                WHERE COALESCE(feed_interest_flag, flagged_for_interest_feed) = 1
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """

            count_query = """
                SELECT COUNT(*) as total
                FROM messages
                WHERE COALESCE(feed_interest_flag, flagged_for_interest_feed) = 1
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

            # Format feed items
            feed_items = []
            for row in rows:
                message_text = row.message_text or ""
                if len(message_text) > 300:
                    message_text = message_text[:300] + "..."

                # Parse JSON fields
                matched_profiles = []
                trigger_annotations = {}
                semantic_scores = {}

                try:
                    if row.matched_profiles:
                        matched_profiles = json.loads(row.matched_profiles)
                    if row.trigger_annotations:
                        trigger_annotations = json.loads(row.trigger_annotations)
                        # Fallback: extract semantic scores from annotations if not in dedicated column
                        if not row.semantic_scores_json:
                            semantic_scores = trigger_annotations.get(
                                "semantic_scores", {}
                            )
                    if row.semantic_scores_json:
                        semantic_scores = json.loads(row.semantic_scores_json)
                except (json.JSONDecodeError, AttributeError):
                    pass

                # Filter by profile_id if specified
                if profile_id and profile_id not in semantic_scores:
                    continue

                # Calculate max semantic score for sorting/display
                max_semantic_score = (
                    max(semantic_scores.values()) if semantic_scores else 0.0
                )

                feed_items.append(
                    {
                        "feed_item_id": f"{row.chat_id}_{row.msg_id}",
                        "semantic_type": row.semantic_type or "interest_semantic",
                        "chat_id": row.chat_id,
                        "message_id": row.msg_id,
                        "chat_title": row.chat_title or f"Chat {row.chat_id}",
                        "sender_name": row.sender_name or "Unknown",
                        "message_text": message_text,
                        "semantic_scores": semantic_scores,
                        "max_semantic_score": float(max_semantic_score),
                        "triggers": row.triggers or "",
                        "sender_id": row.sender_id or 0,
                        "timestamp": row.created_at,
                        "matched_profiles": matched_profiles,
                        "trigger_annotations": trigger_annotations,
                        "delivery_mode_used": row.delivery_mode_used,
                        "delivery_target_used": row.delivery_target_used,
                    }
                )

            # Sort by max semantic score descending (within the result set)
            feed_items.sort(key=lambda x: x["max_semantic_score"], reverse=True)

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "feed_items": feed_items,
                            "count": len(feed_items),
                            "total": total_count,
                            "limit": limit,
                            "offset": offset,
                            "semantic_type": "interest_semantic",
                            "profile_id_filter": profile_id,
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch interest feed: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch interest feed: {str(e)}",
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
                        WHERE flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1
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

    @app.route("/api/digests/trigger", methods=["POST"])
    @require_admin_auth
    def trigger_digest():
        """Manually trigger alerts or interest digest runs."""
        if _config is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Configuration not available",
                    }
                ),
                503,
            )

        payload = request.get_json(silent=True) or {}
        digest_type = (payload.get("type") or "alerts").strip().lower()
        schedule_override = (payload.get("schedule") or "").strip().lower()
        since_hours_override = payload.get("since_hours")

        if _engine is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Database not ready for manual digest",
                    }
                ),
                503,
            )

        client_getter = _client_getter
        if not client_getter:
            logger.warning(
                "Manual digest trigger called before client getter initialized"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Telegram client not ready for manual digest",
                    }
                ),
                503,
            )

        if digest_type == "alerts":
            alert_cfg = _config.alerts

            if schedule_override and schedule_override not in ("hourly", "daily"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Unsupported alerts digest schedule: {schedule_override}",
                        }
                    ),
                    400,
                )

            if schedule_override in ("daily",):
                default_schedule = "daily"
            else:
                default_schedule = "hourly" if alert_cfg.digest.hourly else "daily"

            schedule_choice = schedule_override or default_schedule
            # Manual triggers: ignored, will use no time filtering
            # Scheduled digests: use 1h for hourly, 24h for daily
            since_hours = 1  # Placeholder, ignored for manual triggers
            if since_hours_override is not None:
                try:
                    override_val = int(since_hours_override)
                    if override_val > 0:
                        since_hours = override_val
                except (TypeError, ValueError):
                    pass

            # Schedule digest in main event loop to avoid client binding issues
            if _main_loop is None:
                logger.error("Manual alerts digest aborted: Main event loop not set")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Main event loop not initialized",
                        }
                    ),
                    503,
                )

            client = client_getter()
            if not client:
                logger.warning(
                    "Manual alerts digest trigger aborted: Telegram client unavailable"
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Telegram client not available",
                        }
                    ),
                    503,
                )

            # Import here to avoid circular imports
            from tgsentinel.digest import send_digest

            # Schedule coroutine in main event loop
            future = asyncio.run_coroutine_threadsafe(
                send_digest(
                    _engine,
                    client,
                    since_hours=since_hours,
                    top_n=5,  # Manual trigger: always last 5 messages
                    mode=alert_cfg.mode,
                    channel=alert_cfg.target_channel,
                    channels_config=_config.channels,
                    min_score=alert_cfg.min_score,
                    feed_type="alerts",
                    manual_trigger=True,  # Manual trigger: no filtering
                    global_profiles=_config.global_profiles,
                ),
                _main_loop,
            )

            # Don't wait for result - return immediately
            def _log_result():
                try:
                    future.result(
                        timeout=120
                    )  # Wait up to 120s (digest can take time fetching messages from Telegram)
                    logger.info("[DIGEST] Manual alerts digest completed successfully")
                except TimeoutError:
                    logger.warning(
                        "[DIGEST] Manual alerts digest exceeded 120s timeout - may still be processing in background"
                    )
                except Exception as exc:
                    logger.error(
                        f"[DIGEST] Manual alerts digest failed: {exc}", exc_info=True
                    )

            thread = threading.Thread(
                target=_log_result,
                daemon=True,
                name="ManualAlertsDigestWaiter",
            )
            thread.start()
            logger.info(
                "[DIGEST] Alerts digest manually triggered (schedule=%s)",
                schedule_choice,
            )
            return (
                jsonify(
                    {
                        "status": "ok",
                        "message": f"Alerts digest scheduled ({schedule_choice})",
                    }
                ),
                200,
            )

        if digest_type == "interests":
            if _unified_digest_worker is None:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Digest worker not initialized yet",
                        }
                    ),
                    503,
                )

            if schedule_override:
                try:
                    schedule_enum = DigestSchedule(schedule_override)
                except ValueError:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": f"Unknown digest schedule: {schedule_override}",
                            }
                        ),
                        400,
                    )
            else:
                schedule_enum = DigestSchedule.HOURLY

            if schedule_enum == DigestSchedule.NONE:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Digest schedule 'none' cannot be triggered manually",
                        }
                    ),
                    400,
                )

            # Schedule digest in main event loop to avoid client binding issues
            if _main_loop is None:
                logger.error("Manual interest digest aborted: Main event loop not set")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Main event loop not initialized",
                        }
                    ),
                    503,
                )

            client = client_getter()
            if not client:
                logger.warning(
                    "Manual interest digest trigger aborted: Telegram client unavailable"
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Telegram client not available",
                        }
                    ),
                    503,
                )

            now = datetime.now(timezone.utc)

            # Schedule coroutine in main event loop
            future = asyncio.run_coroutine_threadsafe(
                _unified_digest_worker._process_digest_schedule(
                    schedule_enum, client, now, manual_trigger=True
                ),
                _main_loop,
            )

            # Don't wait for result - return immediately
            def _log_result():
                try:
                    future.result(
                        timeout=120
                    )  # Wait up to 120s (digest can take time fetching messages from Telegram)
                    logger.info(
                        f"[DIGEST] Manual interest digest completed successfully ({schedule_enum.value})"
                    )
                except TimeoutError:
                    logger.warning(
                        f"[DIGEST] Manual interest digest for {schedule_enum.value} "
                        "exceeded 120s timeout - may still be processing in background"
                    )
                except Exception as exc:
                    logger.error(
                        f"[DIGEST] Manual interest digest failed for {schedule_enum.value}: {exc}",
                        exc_info=True,
                    )

            thread = threading.Thread(
                target=_log_result,
                daemon=True,
                name="ManualInterestDigestWaiter",
            )
            thread.start()
            logger.info(
                "[DIGEST] Interest digest manually triggered (schedule=%s)",
                schedule_enum.value,
            )
            return (
                jsonify(
                    {
                        "status": "ok",
                        "message": f"Interest digest scheduled ({schedule_enum.value})",
                    }
                ),
                200,
            )

        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Invalid digest type, must be 'alerts' or 'interests'",
                }
            ),
            400,
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
            schedule_map = {}

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

    @app.route("/api/digest/executions", methods=["GET"])
    def get_digest_executions():
        """Get digest execution history.

        Query Parameters:
            profile_id: Optional filter by profile
            limit: Maximum number of records (default: 20, max: 100)

        Returns:
            JSON with execution history
        """
        if not _redis_client:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Redis not available",
                    }
                ),
                503,
            )

        try:
            from .digest_execution import DigestExecutionStore

            store = DigestExecutionStore(_redis_client)

            profile_id = request.args.get("profile_id")
            limit = min(int(request.args.get("limit", 20)), 100)

            if profile_id:
                # Get history for specific profile
                history = store.get_history(profile_id, limit=limit)
                stats = store.get_stats(profile_id)
                return jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "profile_id": profile_id,
                            "executions": [r.to_dict() for r in history],
                            "stats": stats,
                        },
                        "error": None,
                    }
                )
            else:
                # Get global history
                history = store.get_global_history(limit=limit)
                return jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "executions": history,
                            "count": len(history),
                        },
                        "error": None,
                    }
                )

        except Exception as e:
            logger.error(f"[API] Failed to fetch digest executions: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch digest executions: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/digest/executions/<profile_id>/latest", methods=["GET"])
    def get_latest_digest_execution(profile_id: str):
        """Get the latest execution record for a profile.

        Path Parameters:
            profile_id: Profile identifier

        Returns:
            JSON with latest execution record
        """
        if not _redis_client:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Redis not available",
                    }
                ),
                503,
            )

        try:
            from .digest_execution import DigestExecutionStore

            store = DigestExecutionStore(_redis_client)
            record = store.get_latest(profile_id)

            if not record:
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "data": None,
                            "error": None,
                        }
                    ),
                    200,
                )

            return jsonify(
                {
                    "status": "ok",
                    "data": record.to_dict(),
                    "error": None,
                }
            )

        except Exception as e:
            logger.error(
                f"[API] Failed to fetch latest execution for {profile_id}: {e}",
                exc_info=True,
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch latest execution: {str(e)}",
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
                if sched_cfg.mode is not None:
                    sched_data["mode"] = sched_cfg.mode
                if sched_cfg.target_channel is not None:
                    sched_data["target_channel"] = sched_cfg.target_channel

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

        # 3. No duplicate schedule types (NEW)
        schedule_types = [s.get("schedule") for s in schedules if s.get("schedule")]
        if len(schedule_types) != len(set(schedule_types)):
            duplicates = [
                sched_type
                for sched_type in set(schedule_types)
                if schedule_types.count(sched_type) > 1
            ]
            return (
                False,
                f"Duplicate schedule types not allowed: {', '.join(duplicates)}. "
                "Each schedule type can only appear once.",
            )

        # 4. Valid mode (dm, digest, both - 'channel' deprecated)
        mode = data.get("mode", "dm")
        if mode not in {"dm", "digest", "both"}:
            return False, f"Invalid mode: {mode}. Must be 'dm', 'digest', or 'both'"

        # 5. min_score range
        min_score = data.get("min_score", 0.0)
        if not (0.0 <= min_score <= 10.0):
            return False, f"min_score must be between 0.0 and 10.0, got {min_score}"

        # 6. Hour/day ranges
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

        # 7. target_channel required if mode=digest or both
        if mode in ("digest", "both") and not data.get("target_channel"):
            return (
                False,
                'target_channel required when mode is "digest" or "both"',
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

                # Alerts sent (flagged for alerts or interest feed)
                alerts_result = con.execute(
                    text(
                        """
                        SELECT COUNT(*) as count
                        FROM messages
                        WHERE (flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1)
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
                                WHERE (flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1)
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
                            WHERE (flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1)
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

            # Get database file size
            db_size_bytes = 0
            try:
                db_path = _sentinel_state.get("db_path")
                if not db_path and _engine:
                    # Try to extract from engine URL
                    db_url = str(_engine.url)
                    if db_url.startswith("sqlite:///"):
                        db_path = db_url.replace("sqlite:///", "")

                if db_path and Path(db_path).exists():
                    db_size_bytes = Path(db_path).stat().st_size
            except Exception as db_exc:
                logger.debug(f"Could not get database size: {db_exc}")

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "messages_ingested": int(messages_ingested),
                            "alerts_sent": int(alerts_sent),
                            "avg_importance": round(float(avg_importance), 2),
                            "feedback_accuracy": round(feedback_accuracy, 1),
                            "database_size_bytes": db_size_bytes,
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

    @app.route("/api/webhooks/history", methods=["GET"])
    def webhooks_history():
        """Get recent webhook delivery history.

        Query Parameters:
            limit: Maximum number of records to return (default: 10, max: 100)

        Returns:
            JSON with recent webhook deliveries including status, timing, and errors
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
            limit = request.args.get("limit", default=10, type=int)
            # Cap limit to prevent excessive queries
            limit = min(limit, 100)

            from tgsentinel.store import get_recent_webhook_deliveries

            deliveries = get_recent_webhook_deliveries(_engine, limit=limit)

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "deliveries": deliveries,
                            "count": len(deliveries),
                        },
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch webhook history: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch webhook history: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/webhooks", methods=["GET"])
    def webhooks_list():
        """List all configured webhooks (secrets masked)."""
        path = _webhooks_path()
        if not path.exists():
            return jsonify({"webhooks": [], "enabled": True})

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            webhooks = data.get("webhooks", [])
            for wh in webhooks:
                if "secret" in wh:
                    wh["secret"] = "••••••"
            return jsonify({"webhooks": webhooks, "enabled": True})
        except Exception as exc:
            logger.error(f"Failed to list webhooks: {exc}", exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/webhooks", methods=["POST"])
    def webhooks_create():
        """Create a new webhook entry and persist to config/webhooks.yml."""
        payload = request.get_json(force=True, silent=True) or {}
        service = (payload.get("service") or "").strip()
        url = (payload.get("url") or "").strip()
        secret = payload.get("secret", "")
        enabled = bool(payload.get("enabled", True))

        if not service or not url:
            return (
                jsonify({"status": "error", "message": "service and url are required"}),
                400,
            )

        path = _webhooks_path()
        data = {}
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            webhooks = data.get("webhooks", [])
            if any(wh.get("service") == service for wh in webhooks):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Webhook '{service}' already exists",
                        }
                    ),
                    409,
                )
            webhooks.append(
                {
                    "service": service,
                    "url": url,
                    "secret": secret,
                    "enabled": enabled,
                }
            )
            data["webhooks"] = webhooks

            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, dir=path.parent, suffix=".tmp"
            ) as tmp:
                yaml.safe_dump(data, tmp, default_flow_style=False, sort_keys=False)
                tmp_path = tmp.name
            shutil.move(tmp_path, path)

            return jsonify({"status": "ok", "service": service}), 201
        except Exception as exc:
            logger.error(f"Failed to create webhook: {exc}", exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/webhooks/<service_name>", methods=["PATCH"])
    def webhooks_update(service_name: str):
        """Update an existing webhook entry."""
        payload = request.get_json(force=True, silent=True) or {}
        path = _webhooks_path()
        if not path.exists():
            return (
                jsonify({"status": "error", "message": "No webhooks configured"}),
                404,
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            webhooks = data.get("webhooks", [])
            target = next(
                (wh for wh in webhooks if wh.get("service") == service_name), None
            )
            if not target:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Webhook '{service_name}' not found",
                        }
                    ),
                    404,
                )

            for key in ["url", "secret", "enabled"]:
                if key in payload:
                    target[key] = payload[key]

            data["webhooks"] = webhooks
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, dir=path.parent, suffix=".tmp"
            ) as tmp:
                yaml.safe_dump(data, tmp, default_flow_style=False, sort_keys=False)
                tmp_path = tmp.name
            shutil.move(tmp_path, path)

            return jsonify({"status": "ok", "service": service_name})
        except Exception as exc:
            logger.error(f"Failed to update webhook: {exc}", exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/webhooks/<service_name>", methods=["DELETE"])
    def webhooks_delete(service_name: str):
        """Delete a webhook entry by service name."""
        path = _webhooks_path()
        if not path.exists():
            return (
                jsonify({"status": "error", "message": "No webhooks configured"}),
                404,
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            webhooks = data.get("webhooks", [])
            new_webhooks = [wh for wh in webhooks if wh.get("service") != service_name]
            if len(new_webhooks) == len(webhooks):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Webhook '{service_name}' not found",
                        }
                    ),
                    404,
                )

            data["webhooks"] = new_webhooks
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, dir=path.parent, suffix=".tmp"
            ) as tmp:
                yaml.safe_dump(data, tmp, default_flow_style=False, sort_keys=False)
                tmp_path = tmp.name
            shutil.move(tmp_path, path)

            return jsonify({"status": "ok", "service": service_name})
        except Exception as exc:
            logger.error(f"Failed to delete webhook: {exc}", exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/analytics/keywords", methods=["GET"])
    def analytics_keywords():
        """Get keyword analytics from alerted messages.

        Query Parameters:
            hours: Number of hours to look back (default: 24)

        Returns:
            JSON with keyword counts and metadata
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

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            with _engine.begin() as con:
                # Fetch all triggers and aggregate in Python for accurate counts
                result = con.execute(
                    text(
                        """
                        SELECT triggers
                        FROM messages
                        WHERE flagged_for_alerts_feed = 1
                          AND triggers IS NOT NULL
                          AND triggers != ''
                          AND datetime(created_at) >= datetime(:cutoff)
                    """
                    ),
                    {"cutoff": cutoff},
                )

                # Count individual keywords across all messages
                from collections import Counter

                keyword_counts = Counter()

                for row in result:
                    try:
                        triggers_list = json.loads(row[0]) if row[0] else []
                        for trigger in triggers_list:
                            keyword_counts[str(trigger)] += 1
                    except (
                        json.JSONDecodeError,
                        ValueError,
                        TypeError,
                        AttributeError,
                    ):
                        if row[0]:
                            keyword_counts[str(row[0])] += 1

                # Get top 20 keywords
                keywords = [
                    {"keyword": keyword, "count": count}
                    for keyword, count in keyword_counts.most_common(20)
                ]

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {"keywords": keywords[:20]},  # Top 20
                        "error": None,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch keyword analytics: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch keyword analytics: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/analytics/channels", methods=["GET"])
    def analytics_channels():
        """Get channel impact analytics from alerted messages.

        Query Parameters:
            hours: Number of hours to look back (default: 24)

        Returns:
            JSON with channel alert counts
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

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            with _engine.begin() as con:
                result = con.execute(
                    text(
                        """
                        SELECT chat_title, COUNT(*) as alert_count
                        FROM messages
                        WHERE (flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1)
                          AND chat_title IS NOT NULL
                          AND datetime(created_at) >= datetime(:cutoff)
                        GROUP BY chat_title
                        ORDER BY alert_count DESC
                        LIMIT 20
                    """
                    ),
                    {"cutoff": cutoff},
                )

                channels = [
                    {"channel": row[0], "alerts": row[1]} for row in result.fetchall()
                ]

            return (
                jsonify(
                    {"status": "ok", "data": {"channels": channels}, "error": None}
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch channel analytics: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch channel analytics: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/analytics/metrics", methods=["GET"])
    def analytics_metrics():
        """Get time-series performance metrics.

        Query Parameters:
            hours: Number of hours to look back (default: 2, max: 168)
            interval_minutes: Time bucket interval in minutes (default: 2, min: 1, max: 60)
                             Timestamps are floored to multiples of this interval for aggregation.

        Returns:
            JSON with time-series data (timestamps, alert_counts, avg_scores)
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
            hours = request.args.get("hours", default=2, type=int)
            interval_minutes = request.args.get("interval_minutes", default=2, type=int)

            if hours < 1 or hours > 168:  # Max 1 week
                hours = 2
            if interval_minutes < 1 or interval_minutes > 60:
                interval_minutes = 2

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            interval_seconds = interval_minutes * 60

            with _engine.begin() as con:
                # Get time-bucketed metrics with dynamic interval
                # Floor each timestamp to multiples of interval_seconds
                result = con.execute(
                    text(
                        """
                        SELECT
                            datetime(
                                (CAST(strftime('%s', created_at) AS INTEGER) / :interval_seconds) * :interval_seconds,
                                'unixepoch'
                            ) as time_bucket,
                            COUNT(*) as alert_count,
                            AVG(score) as avg_score
                        FROM messages
                        WHERE (flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1)
                          AND datetime(created_at) >= datetime(:cutoff)
                        GROUP BY time_bucket
                        ORDER BY time_bucket ASC
                    """
                    ),
                    {"cutoff": cutoff, "interval_seconds": interval_seconds},
                )

                metrics = []
                for row in result.fetchall():
                    metrics.append(
                        {
                            "timestamp": row[0],
                            "alert_count": row[1],
                            "avg_score": round(row[2], 2) if row[2] else 0.0,
                        }
                    )

            return (
                jsonify({"status": "ok", "data": {"metrics": metrics}, "error": None}),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to fetch performance metrics: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to fetch performance metrics: {str(e)}",
                    }
                ),
                500,
            )

    @app.route("/api/feedback", methods=["POST"])
    def submit_feedback():
        """Record user feedback (thumbs up/down) for feed items.

        Request JSON:
            chat_id: Chat ID (integer)
            msg_id: Message ID (integer)
            label: "up" or "down"
            semantic_type: "alert_keyword" | "interest_semantic" (optional)
            profile_ids: List of profile identifiers involved in the match (optional)

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
            semantic_type = payload.get("semantic_type", "alert_keyword")
            profile_ids = payload.get("profile_ids", [])

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

            # Validate semantic_type
            allowed_semantic_types = {"alert_keyword", "interest_semantic"}
            if (
                not isinstance(semantic_type, str)
                or semantic_type not in allowed_semantic_types
            ):
                semantic_type = "alert_keyword"

            # Normalize profile IDs list
            normalized_profile_ids: list[str] = []
            if isinstance(profile_ids, list):
                for raw_pid in profile_ids:
                    if raw_pid is None:
                        continue
                    pid_str = str(raw_pid).strip()
                    if pid_str:
                        normalized_profile_ids.append(pid_str)

            with _engine.begin() as con:
                con.execute(
                    text(
                        """
                        INSERT INTO feedback(chat_id, msg_id, label, semantic_type, updated_at)
                        VALUES(:c, :m, :l, :t, CURRENT_TIMESTAMP)
                        ON CONFLICT(chat_id, msg_id) DO UPDATE
                            SET label=excluded.label,
                                semantic_type=excluded.semantic_type,
                                updated_at=CURRENT_TIMESTAMP
                    """
                    ),
                    {
                        "c": chat_id_int,
                        "m": msg_id_int,
                        "l": label_value,
                        "t": semantic_type,
                    },
                )

                # Persist profile mappings for downstream learning loops
                con.execute(
                    text(
                        "DELETE FROM feedback_profiles WHERE chat_id=:c AND msg_id=:m"
                    ),
                    {"c": chat_id_int, "m": msg_id_int},
                )

                if normalized_profile_ids:
                    # Deduplicate profile_ids to avoid UNIQUE constraint violations
                    # while preserving order
                    seen = set()
                    deduplicated_profile_ids = []
                    for pid in normalized_profile_ids:
                        if pid not in seen:
                            seen.add(pid)
                            deduplicated_profile_ids.append(pid)

                    con.execute(
                        text(
                            """
                            INSERT INTO feedback_profiles(chat_id, msg_id, profile_id)
                            VALUES(:c, :m, :p)
                            """
                        ),
                        [
                            {"c": chat_id_int, "m": msg_id_int, "p": pid}
                            for pid in deduplicated_profile_ids
                        ],
                    )

            logger.info(
                "Feedback recorded",
                extra={
                    "chat_id": chat_id_int,
                    "msg_id": msg_id_int,
                    "label": label,
                    "semantic_type": semantic_type,
                    "profile_ids": normalized_profile_ids,
                },
            )

            # Phase 1: Feedback Learning Integration
            if _config and getattr(_config.feedback_learning, "enabled", False):
                try:
                    # Process each profile that was involved in the match
                    for profile_id in normalized_profile_ids:
                        # Determine profile type from ID or semantic_type
                        try:
                            pid_int = int(profile_id)
                            if 3000 <= pid_int < 4000:
                                profile_type = "interest"
                            elif 1000 <= pid_int < 2000:
                                profile_type = "alert"
                            else:
                                continue
                        except (ValueError, TypeError):
                            # Fallback to semantic_type
                            if semantic_type == "interest_semantic":
                                profile_type = "interest"
                            elif semantic_type == "alert_keyword":
                                profile_type = "alert"
                            else:
                                continue

                        # Route to appropriate aggregator
                        if profile_type == "alert":
                            _process_alert_feedback(
                                profile_id,
                                label,
                                chat_id_int,
                                msg_id_int,
                                payload,
                            )
                        elif profile_type == "interest":
                            _process_interest_feedback(
                                profile_id,
                                label,
                                chat_id_int,
                                msg_id_int,
                                payload,
                            )

                except Exception as e:
                    logger.error(
                        f"[FEEDBACK] Feedback learning processing failed: {e}",
                        exc_info=True,
                    )

            return (
                jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "chat_id": chat_id_int,
                            "msg_id": msg_id_int,
                            "label": label,
                            "semantic_type": semantic_type,
                            "profile_ids": normalized_profile_ids,
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

    def _get_profile_threshold(profile_id: str, profile_type: str) -> float:
        """Helper to get current threshold from YAML.

        Handles both flat and nested YAML structures, normalizes keys to strings.
        """
        try:
            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            if profile_type == "interest":
                profiles_path = config_dir / "profiles_interest.yml"
                field_name = "threshold"
            else:
                profiles_path = config_dir / "profiles_alert.yml"
                field_name = "min_score"

            if not profiles_path.exists():
                return 0.75 if profile_type == "interest" else 1.0

            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles = yaml.safe_load(f) or {}

            # Detect and use nested "profiles" mapping if present
            profiles = profiles.get("profiles", profiles)

            # Normalize keys to strings (handles integer YAML keys like 3000:)
            normalized_profiles = {str(k): v for k, v in profiles.items()}

            # Look up using normalized string key
            if str(profile_id) not in normalized_profiles:
                return 0.75 if profile_type == "interest" else 1.0

            profile_data = normalized_profiles[str(profile_id)]
            if not isinstance(profile_data, dict):
                return 0.75 if profile_type == "interest" else 1.0

            return profile_data.get(
                field_name, 0.75 if profile_type == "interest" else 1.0
            )
        except Exception as e:
            logger.error(f"[FEEDBACK] Failed to get threshold for {profile_id}: {e}")
            return 0.75 if profile_type == "interest" else 1.0

    def _process_alert_feedback(
        profile_id: str,
        label: str,
        chat_id: int,
        msg_id: int,
        payload: dict,
    ):
        """Process feedback for alert profiles (min_score adjustments only)."""
        alert_aggregator = get_alert_feedback_aggregator()

        # Get current min_score
        min_score = _get_profile_threshold(profile_id, "alert")

        # Alert profiles only care about negative feedback (false positives)
        if label == "down":
            # Record negative feedback
            recommendation = alert_aggregator.record_feedback(
                profile_id=profile_id,
                label="down",
                min_score=min_score,
            )

            logger.info(
                f"[ALERT-FEEDBACK] Aggregator recommendation for {profile_id}: {recommendation.get('action', 'none')}",
                extra={
                    "profile_id": profile_id,
                    "action": recommendation.get("action"),
                    "reason": recommendation.get("reason"),
                    "negative_count": (
                        recommendation["current_stats"]["negative_feedback"]
                        if "current_stats" in recommendation
                        else 0
                    ),
                },
            )

            # If action recommended, apply it
            if recommendation.get("action") == "raise_min_score":
                config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
                tuner = ProfileTuner(_engine, config_dir)

                stats = recommendation.get("current_stats", {})
                negative_count = stats.get("negative_feedback", 1)

                adjustment = tuner.apply_alert_min_score_adjustment(
                    profile_id=profile_id,
                    delta=recommendation["delta"],
                    reason="negative_feedback",
                    feedback_count=negative_count,
                    trigger_chat_id=chat_id,
                    trigger_msg_id=msg_id,
                    dry_run=False,
                )

                if adjustment:
                    # Update cumulative drift tracker
                    delta_applied = adjustment.new_value - adjustment.old_value
                    alert_aggregator.update_cumulative_delta(profile_id, delta_applied)

                    logger.info(
                        f"[ALERT-FEEDBACK] ✓ Applied min_score adjustment: {profile_id} "
                        f"{adjustment.old_value} → {adjustment.new_value}"
                    )
        else:
            # Thumbs up - just log it (no auto-adjustment for alerts)
            alert_aggregator.record_feedback(
                profile_id=profile_id,
                label="up",
                min_score=min_score,
            )
            logger.info(
                f"[ALERT-FEEDBACK] Positive feedback logged for {profile_id} (no auto-adjustment)"
            )

    def _process_interest_feedback(
        profile_id: str,
        label: str,
        chat_id: int,
        msg_id: int,
        payload: dict,
    ):
        """Process feedback for interest profiles (threshold + sample augmentation)."""
        interest_aggregator = get_feedback_aggregator()

        # Get semantic score from feedback payload (if provided)
        semantic_score = payload.get("semantic_score")

        if semantic_score is None:
            # Query score from database
            with _engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT score, semantic_scores_json
                        FROM messages
                        WHERE chat_id = :chat_id AND msg_id = :msg_id
                    """
                    ),
                    {"chat_id": chat_id, "msg_id": msg_id},
                )
                row = result.fetchone()
                if row:
                    if row[1]:
                        # Parse semantic_scores_json
                        semantic_scores = json.loads(row[1])
                        semantic_score = semantic_scores.get(profile_id, 0.0)
                    else:
                        semantic_score = row[0] or 0.0

        if semantic_score is None:
            logger.warning(
                f"[FEEDBACK] Could not determine score for {profile_id}, skipping aggregation"
            )
            return

        # Get profile threshold
        threshold = _get_profile_threshold(profile_id, "interest")

        # Record feedback and check for recommended action
        recommendation = interest_aggregator.record_feedback(
            profile_id=profile_id,
            label=label,
            semantic_score=semantic_score,
            threshold=threshold,
        )

        logger.info(
            f"[FEEDBACK] Aggregator recommendation for {profile_id}: {recommendation['action']}",
            extra={
                "profile_id": profile_id,
                "action": recommendation["action"],
                "reason": recommendation["reason"],
                "borderline_fp": (
                    recommendation["stats"].borderline_fp
                    if recommendation["stats"]
                    else 0
                ),
            },
        )

        # If action recommended, apply it
        if recommendation["action"] == "raise_threshold":
            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            tuner = ProfileTuner(_engine, config_dir)

            adjustment = tuner.apply_threshold_adjustment(
                profile_id=profile_id,
                profile_type="interest",
                delta=recommendation["delta"],
                reason="negative_feedback",
                feedback_count=recommendation["stats"].borderline_fp,
                trigger_chat_id=chat_id,
                trigger_msg_id=msg_id,
                dry_run=False,
            )

            if adjustment:
                # Reset aggregator counters
                interest_aggregator.reset_stats(profile_id, "raise_threshold")

                # Phase 3: Schedule for batch recomputation
                try:
                    from tgsentinel.feedback_processor import get_batch_processor

                    processor = get_batch_processor()
                    processor.schedule_recompute(profile_id)
                    logger.debug(
                        f"[FEEDBACK] Scheduled {profile_id} for batch recomputation"
                    )
                except Exception as batch_err:
                    logger.warning(
                        f"[FEEDBACK] Failed to schedule batch recompute: {batch_err}"
                    )

                logger.info(
                    f"[FEEDBACK] ✓ Applied threshold adjustment: {profile_id} "
                    f"{adjustment.old_value} → {adjustment.new_value}"
                )

        # Phase 2: Handle sample addition actions
        elif recommendation["action"] == "add_negative_sample":
            # Get message text for sample
            with _engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT message_text
                        FROM messages
                        WHERE chat_id = :chat_id AND msg_id = :msg_id
                    """
                    ),
                    {"chat_id": chat_id, "msg_id": msg_id},
                )
                row = result.fetchone()
                if row and row[0]:
                    message_text = row[0]

                    config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
                    tuner = ProfileTuner(_engine, config_dir)

                    success = tuner.add_to_pending_samples(
                        profile_id=profile_id,
                        profile_type="interest",
                        sample_category="negative",
                        sample_text=message_text,
                        semantic_score=semantic_score,
                        feedback_chat_id=chat_id,
                        feedback_msg_id=msg_id,
                        sample_weight=0.4,
                    )

                    if success:
                        interest_aggregator.reset_stats(
                            profile_id, "add_negative_sample"
                        )
                        logger.info(
                            f"[FEEDBACK] ✓ Added to pending negative samples: {profile_id}"
                        )

        elif recommendation["action"] == "add_positive_sample":
            # Get message text for sample
            with _engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT message_text
                        FROM messages
                        WHERE chat_id = :chat_id AND msg_id = :msg_id
                    """
                    ),
                    {"chat_id": chat_id, "msg_id": msg_id},
                )
                row = result.fetchone()
                if row and row[0]:
                    message_text = row[0]

                    config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
                    tuner = ProfileTuner(_engine, config_dir)

                    success = tuner.add_to_pending_samples(
                        profile_id=profile_id,
                        profile_type="interest",
                        sample_category="positive",
                        sample_text=message_text,
                        semantic_score=semantic_score,
                        feedback_chat_id=chat_id,
                        feedback_msg_id=msg_id,
                        sample_weight=0.4,
                    )

                    if success:
                        interest_aggregator.reset_stats(
                            profile_id, "add_positive_sample"
                        )
                        logger.info(
                            f"[FEEDBACK] ✓ Added to pending positive samples: {profile_id}"
                        )

    @app.route("/api/profiles/interest/<profile_id>/feedback-stats", methods=["GET"])
    def get_interest_profile_feedback_stats(profile_id: str):
        """Get aggregated feedback stats for an interest profile."""
        try:
            aggregator = get_feedback_aggregator()
            stats = aggregator.get_stats(profile_id)

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            tuner = ProfileTuner(_engine, config_dir)
            history = tuner.get_adjustment_history(profile_id, limit=5)

            if stats:
                return jsonify(
                    {
                        "status": "ok",
                        "stats": {
                            "profile_id": stats.profile_id,
                            "borderline_fp": stats.borderline_fp,
                            "severe_fp": stats.severe_fp,
                            "strong_tp": stats.strong_tp,
                            "marginal_tp": stats.marginal_tp,
                            "cumulative_threshold_delta": stats.cumulative_threshold_delta,
                            "cumulative_negative_weight_delta": stats.cumulative_negative_weight_delta,
                        },
                        "history": history,
                    }
                )
            else:
                return jsonify(
                    {
                        "status": "ok",
                        "stats": {
                            "profile_id": profile_id,
                            "borderline_fp": 0,
                            "severe_fp": 0,
                            "strong_tp": 0,
                            "marginal_tp": 0,
                            "cumulative_threshold_delta": 0.0,
                            "cumulative_negative_weight_delta": 0.0,
                        },
                        "history": [],
                    }
                )
        except Exception as e:
            logger.error(f"[FEEDBACK] Failed to get feedback stats: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/profiles/interest/<profile_id>/pending-samples", methods=["GET"])
    def get_pending_samples(profile_id: str):
        """Get pending samples for review."""
        try:
            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            tuner = ProfileTuner(_engine, config_dir)

            samples = tuner.get_pending_samples(profile_id, "interest")

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "profile_id": profile_id,
                        "positive": samples["positive"],
                        "negative": samples["negative"],
                    },
                }
            )
        except Exception as e:
            logger.error(
                f"[FEEDBACK] Failed to get pending samples: {e}", exc_info=True
            )
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route(
        "/api/profiles/interest/<profile_id>/pending-samples/commit", methods=["POST"]
    )
    def commit_pending_samples_endpoint(profile_id: str):
        """Commit pending samples to feedback samples."""
        try:
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

            payload = request.get_json(silent=True)
            if payload is None:
                return (
                    jsonify({"status": "error", "error": "Invalid JSON payload"}),
                    400,
                )

            category = payload.get("category")
            if category not in ("positive", "negative"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "error": "category must be 'positive' or 'negative'",
                        }
                    ),
                    400,
                )

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            tuner = ProfileTuner(_engine, config_dir)

            committed_count = tuner.commit_pending_samples(
                profile_id, "interest", category
            )

            if committed_count > 0:
                # Phase 3: Schedule for batch recomputation instead of immediate clear
                try:
                    from tgsentinel.feedback_processor import get_batch_processor

                    processor = get_batch_processor()
                    processor.schedule_recompute(profile_id)
                    logger.info(
                        f"[FEEDBACK] Committed {committed_count} {category} samples for {profile_id}, "
                        f"scheduled for batch recomputation"
                    )
                except Exception as batch_err:
                    logger.warning(
                        f"[FEEDBACK] Failed to schedule batch recompute: {batch_err}"
                    )
                    # Fallback to immediate clear
                    from tgsentinel.semantic import clear_profile_cache

                    clear_profile_cache(profile_id)
                    logger.info(
                        f"[FEEDBACK] Fallback: cleared cache immediately for {profile_id}"
                    )

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "profile_id": profile_id,
                        "category": category,
                        "committed_count": committed_count,
                    },
                }
            )
        except Exception as e:
            logger.error(
                f"[FEEDBACK] Failed to commit pending samples: {e}", exc_info=True
            )
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route(
        "/api/profiles/interest/<profile_id>/pending-samples/rollback", methods=["POST"]
    )
    def rollback_pending_samples_endpoint(profile_id: str):
        """Rollback pending samples without committing."""
        try:
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

            payload = request.get_json(silent=True)
            if payload is None:
                return (
                    jsonify({"status": "error", "error": "Invalid JSON payload"}),
                    400,
                )

            category = payload.get("category")
            if category not in ("positive", "negative"):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "error": "category must be 'positive' or 'negative'",
                        }
                    ),
                    400,
                )

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            tuner = ProfileTuner(_engine, config_dir)

            rolled_back_count = tuner.rollback_pending_samples(
                profile_id, "interest", category
            )

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "profile_id": profile_id,
                        "category": category,
                        "rolled_back_count": rolled_back_count,
                    },
                }
            )
        except Exception as e:
            logger.error(
                f"[FEEDBACK] Failed to rollback pending samples: {e}", exc_info=True
            )
            return jsonify({"status": "error", "message": str(e)}), 500

    # Phase 3: Monitoring endpoints for feedback learning system

    @app.route("/api/feedback-learning/status", methods=["GET"])
    def get_feedback_learning_status():
        """
        Get status of feedback learning system.

        Returns overall system status including:
        - Batch processor queue status
        - Feedback aggregator statistics
        - Background task health
        - Configuration values

        Returns:
            JSON with system status
        """
        try:
            from tgsentinel.feedback_aggregator import get_feedback_aggregator
            from tgsentinel.feedback_processor import get_batch_processor

            aggregator = get_feedback_aggregator()

            # Get queue status - handle case where processor might not be initialized
            try:
                processor = get_batch_processor()
                queue_status = processor.get_queue_status()
            except ValueError:
                # Processor not initialized yet
                queue_status = {
                    "pending_count": 0,
                    "pending_profiles": [],
                    "last_batch_time": datetime.now().isoformat(),
                    "seconds_since_last_batch": 0,
                }

            # Build profile breakdown from DATABASE (persistent across restarts)
            breakdown = {
                "interest_profiles_count": 0,
                "interest_pending_samples": 0,
                "interest_adjusted_count": 0,
                "interest_near_cap": 0,
                "alert_profiles_count": 0,
                "alert_avg_negative_rate": 0.0,
                "alert_adjusted_count": 0,
                "alert_near_cap": 0,
            }

            # Query database for actual feedback stats
            try:
                with _engine.connect() as con:
                    # Get interest profiles with feedback (profile_id >= 3000)
                    interest_result = con.execute(
                        text(
                            """
                            SELECT
                                profile_id,
                                COUNT(*) as total_feedback,
                                SUM(CASE WHEN label = 0 THEN 1 ELSE 0 END) as negative_count
                            FROM feedback f
                            JOIN feedback_profiles fp ON f.chat_id = fp.chat_id AND f.msg_id = fp.msg_id
                            WHERE CAST(fp.profile_id AS INTEGER) >= 3000 AND CAST(fp.profile_id AS INTEGER) < 4000
                            GROUP BY profile_id
                        """
                        )
                    ).fetchall()

                    breakdown["interest_profiles_count"] = len(interest_result)

                    # Get alert profiles with feedback (profile_id >= 1000 and < 2000)
                    alert_result = con.execute(
                        text(
                            """
                            SELECT
                                profile_id,
                                COUNT(*) as total_feedback,
                                SUM(CASE WHEN label = 0 THEN 1 ELSE 0 END) as negative_count
                            FROM feedback f
                            JOIN feedback_profiles fp ON f.chat_id = fp.chat_id AND f.msg_id = fp.msg_id
                            WHERE CAST(fp.profile_id AS INTEGER) >= 1000 AND CAST(fp.profile_id AS INTEGER) < 2000
                            GROUP BY profile_id
                        """
                        )
                    ).fetchall()

                    breakdown["alert_profiles_count"] = len(alert_result)

                    # Calculate average negative rate for alerts
                    if alert_result:
                        total_negative = sum(row.negative_count for row in alert_result)
                        total_feedback = sum(row.total_feedback for row in alert_result)
                        breakdown["alert_avg_negative_rate"] = (
                            total_negative / total_feedback
                            if total_feedback > 0
                            else 0.0
                        )

                    # Count pending samples for interest profiles
                    pending_samples_result = con.execute(
                        text(
                            """
                            SELECT COUNT(DISTINCT profile_id) as pending_count
                            FROM profile_sample_additions
                            WHERE profile_type = 'interest' AND sample_status = 'pending'
                        """
                        )
                    ).fetchone()

                    if pending_samples_result and pending_samples_result.pending_count:
                        breakdown["interest_pending_samples"] = (
                            pending_samples_result.pending_count
                        )

                    # Get adjustment history counts (profiles that have been adjusted)
                    adjustment_history = con.execute(
                        text(
                            """
                            SELECT
                                profile_id,
                                profile_type,
                                SUM(new_value - old_value) as cumulative_delta
                            FROM profile_adjustments
                            GROUP BY profile_id, profile_type
                        """
                        )
                    ).fetchall()

                    for row in adjustment_history:
                        if row.profile_type == "interest":
                            # Count any profile with adjustments (non-zero delta)
                            if row.cumulative_delta != 0:
                                breakdown["interest_adjusted_count"] += 1
                            # Check if near drift cap (0.8 * 0.25 = 0.20)
                            if abs(row.cumulative_delta) >= 0.20:
                                breakdown["interest_near_cap"] += 1
                        elif row.profile_type == "alert":
                            # Count any profile with adjustments (non-zero delta)
                            if row.cumulative_delta != 0:
                                breakdown["alert_adjusted_count"] += 1
                            # Check if near drift cap (0.8 * 0.50 = 0.40)
                            if abs(row.cumulative_delta) >= 0.40:
                                breakdown["alert_near_cap"] += 1

            except Exception as db_err:
                logger.warning(
                    f"[FEEDBACK] Failed to query database for breakdown stats: {db_err}",
                    exc_info=True,
                )

            # Calculate stats_summary from breakdown (database-backed, persistent)
            stats_summary = {
                "total_profiles": breakdown["alert_profiles_count"]
                + breakdown["interest_profiles_count"],
                "profiles_with_feedback": breakdown["alert_profiles_count"]
                + breakdown["interest_profiles_count"],
                "profiles_near_drift_cap": breakdown["alert_near_cap"]
                + breakdown["interest_near_cap"],
            }

            # Check if background tasks are running
            batch_processor_running = False
            decay_task_running = False

            try:
                processor = get_batch_processor()
                batch_processor_running = processor._running
            except (ValueError, AttributeError):
                pass

            decay_task_running = aggregator._decay_running

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "aggregator": {
                            "decay_running": decay_task_running,
                            "last_decay": aggregator._last_decay.isoformat(),
                            "stats_summary": stats_summary,
                            "breakdown": breakdown,
                        },
                        "batch_processor": {
                            "running": batch_processor_running,
                            "pending_count": queue_status["pending_count"],
                            "pending_profiles": queue_status["pending_profiles"],
                            "last_batch_time": queue_status["last_batch_time"],
                            "seconds_since_last_batch": queue_status[
                                "seconds_since_last_batch"
                            ],
                        },
                        "config": {
                            "batch_interval_seconds": (
                                processor.BATCH_INTERVAL_SECONDS
                                if batch_processor_running
                                else 600
                            ),
                            "batch_size_threshold": (
                                processor.BATCH_SIZE_THRESHOLD
                                if batch_processor_running
                                else 5
                            ),
                            "decay_interval_hours": aggregator.DECAY_INTERVAL_HOURS,
                            "feedback_window_days": aggregator.FEEDBACK_WINDOW_DAYS,
                        },
                    },
                }
            )
        except Exception as e:
            logger.error(
                f"[FEEDBACK] Failed to get feedback learning status: {e}",
                exc_info=True,
            )
            return jsonify({"status": "error", "message": str(e)}), 500

    # Alert feedback endpoints

    @app.route("/api/profiles/alert/<profile_id>/adjust", methods=["POST"])
    @require_admin_auth
    def adjust_alert_min_score(profile_id: str):
        """
        Manually adjust min_score for an alert profile.

        **REQUIRES ADMIN AUTHENTICATION** via X-Admin-Token header.

        Request JSON:
            {
                "delta": 0.5,  // Amount to adjust min_score (positive = stricter)
                "reason": "manual_adjustment",  // Optional reason
                "dry_run": false  // Optional, defaults to false
            }

        Returns:
            JSON with adjustment result
        """
        try:
            data = request.get_json()
            if not data or "delta" not in data:
                return (
                    jsonify({"status": "error", "message": "delta field required"}),
                    400,
                )

            delta = float(data["delta"])
            reason = data.get("reason", "manual_adjustment")
            dry_run = data.get("dry_run", False)

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            tuner = ProfileTuner(_engine, config_dir)

            result = tuner.apply_alert_min_score_adjustment(
                profile_id=profile_id,
                delta=delta,
                reason=reason,
                dry_run=dry_run,
            )

            if result is None:
                return (
                    jsonify(
                        {"status": "error", "message": "Failed to apply adjustment"}
                    ),
                    500,
                )

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "profile_id": result.profile_id,
                        "old_value": result.old_value,
                        "new_value": result.new_value,
                        "delta": result.new_value - result.old_value,
                        "reason": result.adjustment_reason,
                        "adjustment_type": result.adjustment_type,
                        "dry_run": dry_run,
                    },
                }
            )
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        except Exception as e:
            logger.error(
                f"[ALERT-FEEDBACK] Failed to adjust min_score for {profile_id}: {e}",
                exc_info=True,
            )
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/profiles/alert/<profile_id>/feedback-stats", methods=["GET"])
    def get_alert_feedback_stats(profile_id: str):
        """
        Get aggregated feedback statistics for an alert profile.

        Query params:
            days (int): Number of days to look back (default: 30)

        Returns:
            JSON with feedback stats and recommendations
        """
        try:
            days = int(request.args.get("days", 30))

            from tgsentinel.alert_feedback_aggregator import (
                get_alert_feedback_aggregator,
            )

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            aggregator = get_alert_feedback_aggregator()
            tuner = ProfileTuner(_engine, config_dir)

            # Get stats from aggregator
            agg_stats = aggregator.get_stats(profile_id)

            # Get DB stats from tuner
            db_stats = tuner.get_alert_feedback_stats(profile_id, days=days)

            if agg_stats is None and db_stats is None:
                return jsonify(
                    {
                        "status": "ok",
                        "data": {
                            "profile_id": profile_id,
                            "message": "No feedback recorded yet",
                        },
                    }
                )

            response_data = {
                "profile_id": profile_id,
                "aggregator_stats": agg_stats,  # Already a dict from get_stats()
                "db_stats": db_stats,
                "recommendation": None,
            }

            # Check if adjustment recommended
            if agg_stats:
                total = agg_stats.get("total_feedback", 0)
                negative = agg_stats.get("negative_feedback", 0)
                if total > 0:
                    negative_rate = negative / total
                    if (
                        negative >= aggregator.MIN_NEGATIVE_FEEDBACK
                        and negative_rate >= aggregator.NEGATIVE_RATE_THRESHOLD
                    ):
                        response_data["recommendation"] = {
                            "action": "raise_min_score",
                            "delta": aggregator.MIN_SCORE_DELTA,
                            "reason": f"{negative} false positives ({negative_rate*100:.1f}%)",
                        }

            return jsonify({"status": "ok", "data": response_data})
        except Exception as e:
            logger.error(
                f"[ALERT-FEEDBACK] Failed to get feedback stats for {profile_id}: {e}",
                exc_info=True,
            )
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/profiles/alert/<profile_id>/adjustment-history", methods=["GET"])
    def get_alert_adjustment_history(profile_id: str):
        """
        Get historical min_score adjustments for an alert profile.

        Query params:
            days (int): Number of days to look back (default: 30)
            limit (int): Max number of records (default: 100)

        Returns:
            JSON with adjustment history
        """
        try:
            # Note: days parameter kept for API compatibility but not used
            # get_adjustment_history only accepts limit parameter
            limit = int(request.args.get("limit", 100))

            config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
            tuner = ProfileTuner(_engine, config_dir)

            history = tuner.get_adjustment_history(profile_id=profile_id, limit=limit)

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "profile_id": profile_id,
                        "profile_type": "alert",
                        "adjustments": history,  # Already formatted as dicts
                    },
                }
            )
        except Exception as e:
            logger.error(
                f"[ALERT-FEEDBACK] Failed to get adjustment history for {profile_id}: {e}",
                exc_info=True,
            )
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/feedback-learning/batch-history", methods=["GET"])
    def get_batch_history():
        """
        Get batch processing history.

        Query parameters:
            limit (int): Maximum number of records to return (default: 50, max: 200)

        Returns:
            JSON with batch history records
        """
        try:
            limit = min(int(request.args.get("limit", 50)), 200)

            with _engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT
                            id,
                            started_at,
                            completed_at,
                            profiles_processed,
                            profile_ids,
                            elapsed_seconds,
                            trigger_type,
                            status
                        FROM batch_history
                        ORDER BY started_at DESC
                        LIMIT :limit
                        """
                    ),
                    {"limit": limit},
                ).fetchall()

                history = [
                    {
                        "id": row.id,
                        "started_at": row.started_at,
                        "completed_at": row.completed_at,
                        "profiles_processed": row.profiles_processed,
                        "profile_ids": (
                            row.profile_ids.split(",") if row.profile_ids else []
                        ),
                        "elapsed_seconds": row.elapsed_seconds,
                        "trigger_type": row.trigger_type,
                        "status": row.status,
                    }
                    for row in result
                ]

            return jsonify({"status": "ok", "data": {"history": history}})

        except Exception as e:
            logger.error(f"[FEEDBACK] Failed to get batch history: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/feedback-learning/trigger-batch", methods=["POST"])
    @require_admin_auth
    def trigger_batch_processing():
        """
        Manually trigger batch processing (for testing/admin).

        **REQUIRES ADMIN AUTHENTICATION** via X-Admin-Token header.

        Useful for:
        - Testing batch processing without waiting for interval
        - Forcing immediate cache clear after adjustments
        - Debugging batch processor behavior

        Returns:
            JSON with status
        """
        try:
            import asyncio
            import threading

            from tgsentinel.feedback_processor import get_batch_processor

            processor = get_batch_processor()

            # Run batch processing in background thread to avoid blocking the API
            def _run_batch():
                """Run batch processing in a new event loop."""
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(
                        processor.process_batch(trigger_type="manual")
                    )
                    loop.close()
                    logger.info("[FEEDBACK] Manual batch processing completed")
                except Exception as e:
                    logger.error(
                        f"[FEEDBACK] Batch processing failed: {e}", exc_info=True
                    )

            # Start batch in background thread
            thread = threading.Thread(target=_run_batch, daemon=True)
            thread.start()

            logger.info("[FEEDBACK] Manual batch processing triggered via API")

            return jsonify({"status": "ok", "message": "Batch processing triggered"})

        except ValueError:
            # Processor not initialized
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Batch processor not initialized (feedback learning may be disabled)",
                    }
                ),
                503,
            )
        except Exception as e:
            logger.error(f"[FEEDBACK] Failed to trigger batch: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/restart", methods=["POST"])
    @require_admin_auth
    def restart_sentinel_api():
        """Trigger graceful restart of Sentinel container.

        **REQUIRES ADMIN AUTHENTICATION** via X-Admin-Token header.

        This endpoint triggers a graceful shutdown by setting the shutdown event.
        Docker's restart policy (restart: on-failure) will automatically restart
        the container after process exit.

        Returns:
            JSON with status and message
        """
        if _shutdown_coordinator is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": "Shutdown coordinator not available",
                    }
                ),
                503,
            )

        try:
            logger.info("[API-RESTART] Restart requested via API endpoint")
            # Trigger shutdown event - Docker will restart the container
            _shutdown_coordinator.shutdown_event.set()

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "message": "Sentinel restarting. Container will restart automatically."
                    },
                    "error": None,
                }
            )
        except Exception as exc:
            logger.error(
                f"[API-RESTART] Failed to trigger restart: {exc}", exc_info=True
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": f"Failed to trigger restart: {exc}",
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
                        "error": (
                            "VACUUM requires maintenance_window=true parameter to acknowledge "
                            "exclusive DB access and potential downtime"
                        ),
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
                            "error": (
                                f"VACUUM already in progress or lock held. TTL: {ttl}s. "
                                "Wait or manually clear lock if stale."
                            ),
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
                            f"[VACUUM-JOB-{job_id}] VACUUM completed in {duration:.2f}s, "
                            f"reclaimed {reclaimed_mb:.2f} MB"
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
                        _config.system.database, "preserve_flagged_multiplier", 2
                    )
                except (AttributeError, KeyError):
                    pass

            # Run cleanup
            cleanup_stats = cleanup_old_messages(
                _engine,
                retention_days=days,
                max_messages=max_messages,
                preserve_flagged_multiplier=preserve_multiplier,
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

    @app.route("/api/database/purge", methods=["POST"])
    def purge_database_endpoint():
        """Delete all messages, feedback, and webhook deliveries from the Sentinel database.

        Safely handles cases where tables may not exist yet.
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
            from sqlalchemy import inspect

            deleted_counts = {}

            with _engine.begin() as con:
                inspector = inspect(_engine)
                existing_tables = inspector.get_table_names()

                # Only delete from tables that exist
                tables_to_purge = [
                    ("messages", "messages"),
                    ("feedback", "feedback"),
                    ("webhook_deliveries", "webhook_deliveries"),
                ]

                for table_name, display_name in tables_to_purge:
                    if table_name in existing_tables:
                        try:
                            # Get count before deleting
                            result = con.execute(
                                text(f"SELECT COUNT(*) FROM {table_name}")
                            )
                            count = result.scalar()

                            # Delete all rows
                            con.execute(text(f"DELETE FROM {table_name}"))
                            deleted_counts[display_name] = count
                            logger.info(f"Purged {count} rows from {table_name}")
                        except Exception as e:
                            logger.warning(f"Failed to purge {table_name}: {e}")
                            deleted_counts[display_name] = 0
                    else:
                        logger.debug(f"Table {table_name} does not exist, skipping")
                        deleted_counts[display_name] = 0

            total_deleted = sum(deleted_counts.values())

            return (
                jsonify(
                    {
                        "status": "ok",
                        "message": f"Database purged: {total_deleted} total rows deleted",
                        "deleted": total_deleted,
                        "details": deleted_counts,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Failed to purge database: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Failed to purge database: {str(e)}",
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
                        "excluded_users": getattr(ch, "excluded_users", []),
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
                "redis": {
                    "host": safe_getattr(cfg, "system.redis.host", "redis"),
                    "port": safe_getattr(cfg, "system.redis.port", 6379),
                    "stream": safe_getattr(
                        cfg, "system.redis.stream", "tgsentinel:messages"
                    ),
                    "group": safe_getattr(
                        cfg, "system.redis.group", "sentinel-workers"
                    ),
                    "consumer": safe_getattr(cfg, "system.redis.consumer", "worker-1"),
                },
                "database_uri": safe_getattr(
                    cfg, "system.database_uri", "sqlite:////app/data/sentinel.db"
                ),
                "embeddings_model": os.getenv(
                    "EMBEDDINGS_MODEL",
                    getattr(cfg, "embeddings_model", "all-MiniLM-L6-v2"),
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
                                "details": (
                                    "The updated configuration contains invalid values "
                                    "or missing required fields"
                                ),
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
                from tgsentinel.semantic import (
                    _model,
                    load_profile_embeddings,
                    score_text_for_profile,
                )

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

                # Validate profile has positive samples
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
            cutoff_str = format_db_timestamp(cutoff)  # Format for SQLite comparison
            query = text(
                """
                SELECT msg_id, chat_id, chat_title, sender_name, message_text,
                       score, created_at, sender_id
                FROM messages
                WHERE created_at >= :cutoff
                ORDER BY created_at DESC
                LIMIT :limit
                """
            )

            with _engine.connect() as conn:
                result = conn.execute(
                    query, {"cutoff": cutoff_str, "limit": max_messages}
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
                        "sender_id": row[7],
                    }
                    for row in result
                ]

            # Get VIP and excluded users from profile
            vip_senders = set(profile.get("vip_senders", []))
            excluded_users = set(profile.get("excluded_users", []))

            # Get coefficients from profile
            positive_weight = profile.get("positive_weight", 1.0)
            negative_weight = profile.get("negative_weight", 0.15)

            # Load profile embeddings temporarily for backtest
            profile_id_temp = f"backtest_{profile_name}"
            load_profile_embeddings(
                profile_id_temp,
                profile.get("positive_samples", []),
                profile.get("negative_samples", []),
                profile.get("threshold", 0.42),
                positive_weight,
                negative_weight,
            )

            # Score messages using semantic model - SHOW ALL MESSAGES with reasons
            all_results = []
            threshold = profile.get("threshold", 0.42)

            logger.info(
                f"[API] Backtest: scoring {len(messages)} messages with threshold {threshold}, "
                f"pos_weight={positive_weight}, neg_weight={negative_weight}, "
                f"VIP senders: {len(vip_senders)}, excluded users: {len(excluded_users)}"
            )

            for msg in messages:
                sender_id = msg.get("sender_id", 0)
                message_text = msg.get("message_text", "")

                # Initialize result for this message
                result = {
                    "message_id": msg["msg_id"],
                    "chat_id": msg["chat_id"],
                    "chat_title": msg["chat_title"],
                    "sender_name": msg["sender_name"],
                    "sender_id": sender_id,
                    "text_preview": message_text[:100]
                    + ("..." if len(message_text) > 100 else ""),
                    "timestamp": msg["created_at"],
                    "is_vip": sender_id in vip_senders,
                    "matched": False,
                    "reason": None,
                    "semantic_score": None,
                    "threshold": threshold,
                    "positive_weight": positive_weight,
                    "negative_weight": negative_weight,
                }

                # Check exclusions first
                if sender_id in excluded_users:
                    result["reason"] = f"Excluded user (ID: {sender_id})"
                    result["matched"] = False
                    all_results.append(result)
                    logger.debug(
                        f"[API] Backtest: skipping message {msg.get('msg_id')} from excluded user {sender_id}"
                    )
                    continue

                if not message_text:
                    result["reason"] = "No text content"
                    result["matched"] = False
                    all_results.append(result)
                    logger.debug(
                        f"[API] Backtest: skipping message {msg.get('msg_id')} - no text"
                    )
                    continue

                # Score text with profile-specific semantic model
                score = score_text_for_profile(message_text, profile_id_temp)
                result["semantic_score"] = (
                    round(score, 3) if score is not None else None
                )

                logger.debug(
                    f"[API] Backtest: msg {msg.get('msg_id')} scored {score} (text: {message_text[:50]}...)"
                )

                if score is None:
                    result["reason"] = "Scoring failed (model error)"
                    result["matched"] = False
                    all_results.append(result)
                    logger.debug(
                        f"[API] Backtest: score is None for message {msg.get('msg_id')}"
                    )
                    continue

                # VIP senders: lower the threshold for matching
                effective_threshold = (
                    threshold * 0.7 if sender_id in vip_senders else threshold
                )

                # Check if message matches
                if score >= effective_threshold:
                    result["matched"] = True
                    result["reason"] = (
                        f"Semantic score {round(score, 3)} >= threshold {round(effective_threshold, 3)}"
                    )
                    if sender_id in vip_senders:
                        result["reason"] += " (VIP threshold 0.7x)"
                else:
                    result["matched"] = False
                    result["reason"] = (
                        f"Semantic score {round(score, 3)} < threshold {round(effective_threshold, 3)}"
                    )
                    if sender_id in vip_senders:
                        result["reason"] += " (VIP threshold 0.7x)"
                    # Add coefficient info to help tuning
                    result[
                        "reason"
                    ] += f" | Coefficients: pos_weight={positive_weight}, neg_weight={negative_weight}"

                all_results.append(result)

            # Separate matched and unmatched for statistics
            matches = [r for r in all_results if r["matched"]]
            unmatched = [r for r in all_results if not r["matched"]]

            # Calculate statistics
            vip_matches = sum(1 for m in matches if m.get("is_vip", False))
            avg_matched_score = (
                round(
                    sum(m["semantic_score"] for m in matches if m["semantic_score"])
                    / len(matches),
                    3,
                )
                if len(matches) > 0
                else 0
            )
            avg_unmatched_score = (
                round(
                    sum(m["semantic_score"] for m in unmatched if m["semantic_score"])
                    / len(unmatched),
                    3,
                )
                if len(unmatched) > 0
                else 0
            )

            stats = {
                "total_messages": len(all_results),
                "matched_messages": len(matches),
                "unmatched_messages": len(unmatched),
                "vip_matches": vip_matches,
                "excluded_count": len(
                    [
                        r
                        for r in all_results
                        if "Excluded user" in (r.get("reason") or "")
                    ]
                ),
                "match_rate": (
                    round(len(matches) / len(all_results) * 100, 1)
                    if len(all_results) > 0
                    else 0
                ),
                "avg_matched_score": avg_matched_score,
                "avg_unmatched_score": avg_unmatched_score,
                "threshold": threshold,
                "positive_weight": positive_weight,
                "negative_weight": negative_weight,
            }

            # Generate recommendations based on results
            recommendations = []
            if stats["match_rate"] < 5:
                recommendations.append("⚠️ Very low match rate (<5%). Consider:")
                recommendations.append(
                    f"  • Lowering threshold (current: {threshold:.2f})"
                )
                recommendations.append(
                    f"  • Lowering negative_weight (current: {negative_weight:.2f} → "
                    f"try {max(0.05, negative_weight * 0.5):.2f})"
                )
                recommendations.append("  • Adding more diverse positive samples")
            elif stats["match_rate"] > 50:
                recommendations.append("⚠️ Very high match rate (>50%). Consider:")
                recommendations.append(
                    f"  • Raising threshold (current: {threshold:.2f})"
                )
                recommendations.append(
                    f"  • Raising negative_weight (current: {negative_weight:.2f} → "
                    f"try {min(0.5, negative_weight * 1.5):.2f})"
                )
                recommendations.append(
                    "  • Adding more negative samples to filter noise"
                )
            else:
                recommendations.append(
                    f"✅ Match rate looks good ({stats['match_rate']}%)"
                )
                if avg_matched_score and avg_unmatched_score:
                    score_gap = avg_matched_score - avg_unmatched_score
                    if score_gap < 0.1:
                        recommendations.append(
                            f"  ⚠️ Small score gap ({score_gap:.3f}). "
                            "Consider increasing negative_weight for better discrimination."
                        )

            result_data = {
                "status": "ok",
                "profile_name": profile_name,
                "profile_id": profile.get("id"),
                "test_date": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "hours_back": hours_back,
                    "max_messages": max_messages,
                    "positive_weight": positive_weight,
                    "negative_weight": negative_weight,
                },
                "matches": all_results[
                    :20
                ],  # Limit to 20 most recent messages for better UX
                "stats": stats,
                "recommendations": recommendations,
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

    @app.route("/api/profiles/alert/backtest", methods=["POST"])
    def backtest_alert_profile():
        """Backtest an alert profile using stored message history.

        The Sentinel service owns the message store and heuristic scoring pipeline,
        so the UI delegates backtesting here to mirror production alert decisions.
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

            payload = request.get_json()
            profile_id_raw = (
                payload.get("profile_id")
                if payload.get("profile_id") is not None
                else payload.get("id")
            )

            if profile_id_raw is None or str(profile_id_raw).strip() == "":
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Profile ID is required",
                        }
                    ),
                    400,
                )

            profile_id = str(profile_id_raw).strip()

            try:
                hours_back = int(payload.get("hours_back", 24))
            except (TypeError, ValueError):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "hours_back must be an integer",
                        }
                    ),
                    400,
                )

            try:
                max_messages = int(payload.get("max_messages", 100))
            except (TypeError, ValueError):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "max_messages must be an integer",
                        }
                    ),
                    400,
                )

            if not (0 <= hours_back <= 168):
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

            channel_filter_raw = payload.get("channel_filter")
            channel_filter = None
            if channel_filter_raw not in (None, ""):
                try:
                    channel_filter = int(channel_filter_raw)
                except (TypeError, ValueError):
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "channel_filter must be an integer",
                            }
                        ),
                        400,
                    )

            profile_payload = payload.get("profile")
            if profile_payload is not None and not isinstance(profile_payload, dict):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "profile must be an object when provided",
                        }
                    ),
                    400,
                )

            profile_data: Dict[str, Any] = {}
            if profile_payload is None:
                config_dir = Path(os.getenv("CONFIG_DIR", "/app/config"))
                profiles_path = config_dir / "profiles_alert.yml"

                if not profiles_path.exists():
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Alert profiles file not found",
                            }
                        ),
                        404,
                    )

                with open(profiles_path, "r", encoding="utf-8") as fp:
                    loaded_profiles = yaml.safe_load(fp) or {}

                base_profiles: Dict[str, Any] = {}
                if isinstance(loaded_profiles, dict):
                    nested_profiles = loaded_profiles.get("profiles")
                    if isinstance(nested_profiles, dict):
                        base_profiles = dict(nested_profiles)
                    else:
                        base_profiles = dict(loaded_profiles)

                candidate_profile = base_profiles.get(profile_id) or base_profiles.get(
                    str(profile_id)
                )

                if not isinstance(candidate_profile, dict):
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": f"Profile {profile_id} not found",
                            }
                        ),
                        404,
                    )

                profile_data = dict(candidate_profile)
            else:
                profile_data = dict(profile_payload)

            profile_name = str(profile_data.get("name", profile_id))

            def _as_list(value: Any) -> list[str]:
                if isinstance(value, list):
                    return [str(item) for item in value if isinstance(item, str)]
                if isinstance(value, str):
                    value = value.strip()
                    return [value] if value else []
                return []

            def _collect(keys: list[str]) -> list[str]:
                combined: list[str] = []
                for key in keys:
                    combined.extend(_as_list(profile_data.get(key, [])))
                dedup: list[str] = []
                seen: set[str] = set()
                for keyword in combined:
                    if keyword not in seen:
                        seen.add(keyword)
                        dedup.append(keyword)
                return dedup

            keywords = _collect(
                [
                    "keywords",
                    "general_keywords",
                    "technical_keywords",
                    "community_keywords",
                ]
            )
            urgency_keywords = _collect(["urgency_keywords", "critical_keywords"])
            importance_keywords = _collect(["importance_keywords", "project_keywords"])
            opportunity_keywords = _collect(
                ["opportunity_keywords", "financial_keywords"]
            )
            security_keywords = _collect(["security_keywords"])
            risk_keywords = _collect(["risk_keywords"])
            release_keywords = _collect(["release_keywords"])
            action_keywords = _collect(["action_keywords"])
            decision_keywords = _collect(["decision_keywords"])

            has_profile_keywords = any(
                [
                    keywords,
                    action_keywords,
                    decision_keywords,
                    urgency_keywords,
                    importance_keywords,
                    release_keywords,
                    security_keywords,
                    risk_keywords,
                    opportunity_keywords,
                ]
            )

            vip_senders = set()
            vip_raw = _as_list(profile_data.get("vip_senders", []))
            for sender in vip_raw:
                try:
                    sender_id = int(sender)
                    if sender_id <= 0:
                        logger.warning(
                            f"[PROFILE] Skipping non-positive VIP sender ID: {sender}"
                        )
                        continue
                    vip_senders.add(sender_id)
                except (TypeError, ValueError):
                    logger.warning(
                        f"[PROFILE] Skipping invalid VIP sender ID: {sender}"
                    )
                    continue

            # Enforce max count limit
            if len(vip_senders) > 100:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "error": f"Too many VIP senders ({len(vip_senders)}). Maximum allowed is 100.",
                        }
                    ),
                    400,
                )

            excluded_users = set()
            excluded_raw = _as_list(profile_data.get("excluded_users", []))
            for user in excluded_raw:
                try:
                    user_id = int(user)
                    if user_id <= 0:
                        logger.warning(
                            f"[PROFILE] Skipping non-positive excluded user ID: {user}"
                        )
                        continue
                    excluded_users.add(user_id)
                except (TypeError, ValueError):
                    logger.warning(
                        f"[PROFILE] Skipping invalid excluded user ID: {user}"
                    )
                    continue

            # Enforce max count limit
            if len(excluded_users) > 100:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "error": f"Too many excluded users ({len(excluded_users)}). Maximum allowed is 100.",
                        }
                    ),
                    400,
                )

            reaction_threshold = 0
            reply_threshold = 0
            try:
                reaction_threshold = int(profile_data.get("reaction_threshold", 0) or 0)
            except (TypeError, ValueError):
                reaction_threshold = 0
            try:
                reply_threshold = int(profile_data.get("reply_threshold", 0) or 0)
            except (TypeError, ValueError):
                reply_threshold = 0

            # Detection flags: opt-in model (default False)
            detect_codes = bool(profile_data.get("detect_codes", False))
            detect_documents = bool(profile_data.get("detect_documents", False))
            prioritize_pinned = bool(profile_data.get("prioritize_pinned", False))
            prioritize_admin = bool(profile_data.get("prioritize_admin", False))
            detect_polls = bool(profile_data.get("detect_polls", False))

            if not _engine:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Database not initialized",
                        }
                    ),
                    500,
                )

            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
            cutoff_str = format_db_timestamp(cutoff)

            base_query = """
                SELECT chat_id, msg_id, chat_title, sender_name, message_text,
                       score, triggers, flagged_for_alerts_feed, sender_id, created_at
                FROM messages
                WHERE created_at >= :cutoff
            """

            params: Dict[str, Any] = {"cutoff": cutoff_str, "limit": max_messages}
            if channel_filter is not None:
                base_query += " AND chat_id = :channel_id"
                params["channel_id"] = channel_filter

            base_query += " ORDER BY created_at DESC LIMIT :limit"

            with _engine.connect() as conn:
                result = conn.execute(text(base_query), params)
                rows = result.fetchall()

            messages = []
            for row in rows:
                data_row = row._mapping
                messages.append(
                    {
                        "chat_id": data_row.get("chat_id"),
                        "msg_id": data_row.get("msg_id"),
                        "chat_title": data_row.get("chat_title"),
                        "sender_name": data_row.get("sender_name"),
                        "message_text": data_row.get("message_text", ""),
                        "score": data_row.get("score", 0.0) or 0.0,
                        "triggers": data_row.get("triggers", ""),
                        "flagged_for_alerts_feed": bool(
                            data_row.get("flagged_for_alerts_feed", 0)
                        ),
                        "sender_id": data_row.get("sender_id") or 0,
                        "created_at": data_row.get("created_at"),
                    }
                )

            total_messages = len(messages)

            default_threshold = 5.0
            if _config and getattr(_config, "alerts", None):
                default_threshold = getattr(_config.alerts, "min_score", 5.0) or 5.0

            threshold_value = profile_data.get("min_score")
            alert_threshold = default_threshold
            if threshold_value is not None:
                try:
                    alert_threshold = float(threshold_value)
                except (TypeError, ValueError):
                    alert_threshold = default_threshold

            matches = []
            matched_messages = 0
            true_positives = 0
            false_positives = 0
            false_negatives = 0

            for msg in messages:
                sender_id = int(msg.get("sender_id", 0) or 0)

                # Skip messages from excluded users (blacklist)
                if sender_id in excluded_users:
                    continue

                text_content = msg.get("message_text", "") or ""

                heuristics = run_heuristics(
                    text=text_content,
                    sender_id=sender_id,
                    mentioned=False,
                    reactions=0,
                    replies=0,
                    vip=vip_senders,
                    keywords=keywords,
                    react_thr=reaction_threshold,
                    reply_thr=reply_threshold,
                    is_private=bool(msg.get("chat_id", 0) and msg.get("chat_id") > 0),
                    is_reply_to_user=False,
                    has_media=False,
                    media_type=None,
                    is_pinned=False,
                    is_poll=False,
                    sender_is_admin=False,
                    has_forward=False,
                    action_keywords=action_keywords,
                    decision_keywords=decision_keywords,
                    urgency_keywords=urgency_keywords,
                    importance_keywords=importance_keywords,
                    release_keywords=release_keywords,
                    security_keywords=security_keywords,
                    risk_keywords=risk_keywords,
                    opportunity_keywords=opportunity_keywords,
                    detect_codes=detect_codes,
                    detect_documents=detect_documents,
                    prioritize_pinned=prioritize_pinned,
                    prioritize_admin=prioritize_admin,
                    detect_polls=detect_polls,
                )

                score = heuristics.pre_score
                # Note: For alert profiles, we use keyword-based scoring only
                # Semantic scoring is reserved for interest profiles (IDs 3000+)

                meets_threshold = score >= alert_threshold

                # Alert profile logic: keyword score must meet threshold AND have important triggers
                would_alert = (
                    has_profile_keywords and meets_threshold and heuristics.important
                )

                actually_flagged_for_alerts = bool(
                    msg.get("flagged_for_alerts_feed", False)
                )

                if would_alert:
                    matched_messages += 1
                    if actually_flagged_for_alerts:
                        true_positives += 1
                    else:
                        false_positives += 1
                elif actually_flagged_for_alerts:
                    false_negatives += 1

                if would_alert or actually_flagged_for_alerts:
                    original_triggers_raw = msg.get("triggers", "") or ""
                    original_triggers = [
                        trig.strip()
                        for trig in original_triggers_raw.split(",")
                        if trig.strip()
                    ]

                    match_entry = {
                        "message_id": msg.get("msg_id"),
                        "chat_id": msg.get("chat_id"),
                        "chat_title": msg.get("chat_title"),
                        "sender_name": msg.get("sender_name"),
                        "score": round(score, 2),
                        "original_score": round(msg.get("score", 0.0), 2),
                        "triggers": heuristics.reasons,
                        "original_triggers": original_triggers,
                        "trigger_annotations": heuristics.trigger_annotations,
                        "text_preview": text_content[:100]
                        + ("..." if len(text_content) > 100 else ""),
                        "timestamp": msg.get("created_at"),
                        "would_alert": would_alert,
                        "actually_flagged": actually_flagged_for_alerts,
                    }
                    matches.append(match_entry)

            avg_score = 0.0
            if matches:
                scored_matches = [
                    m["score"] for m in matches if isinstance(m["score"], (int, float))
                ]
                if scored_matches:
                    avg_score = round(sum(scored_matches) / len(scored_matches), 2)

            match_rate = 0.0
            if total_messages > 0:
                match_rate = round(matched_messages / total_messages * 100, 1)

            precision = 0.0
            if (true_positives + false_positives) > 0:
                precision = round(
                    true_positives / (true_positives + false_positives) * 100, 1
                )

            stats = {
                "total_messages": total_messages,
                "matched_messages": matched_messages,
                "match_rate": match_rate,
                "avg_score": avg_score,
                "true_positives": true_positives,
                "false_positives": false_positives,
                "false_negatives": false_negatives,
                "precision": precision,
                "threshold": alert_threshold,
            }

            recommendations = []
            if stats["false_positives"] > stats["true_positives"]:
                recommendations.append(
                    "⚠️ High false positive rate - consider tightening keyword matches"
                )
            if stats["match_rate"] > 50:
                recommendations.append(
                    "📊 Very high match rate - profile may be too broad"
                )
            if stats["match_rate"] < 5:
                recommendations.append(
                    "📉 Low match rate - consider adding more keywords or lowering thresholds"
                )
            if stats["precision"] < 70:
                recommendations.append("🎯 Low precision - review keyword relevance")

            result = {
                "status": "ok",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "test_date": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "hours_back": hours_back,
                    "max_messages": max_messages,
                    "channel_filter": channel_filter,
                },
                "matches": matches[:50],
                "stats": stats,
                "recommendations": recommendations,
            }

            logger.info(
                f"[API] Backtest completed for alert profile {profile_id}: {stats}"
            )
            return jsonify(result)

        except Exception as exc:
            logger.error(f"[API] Error backtesting alert profile: {exc}", exc_info=True)
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
                "model": "<actual model name from EMBEDDINGS_MODEL>"
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
                                "message": (
                                    "Semantic model not loaded. Ensure EMBEDDINGS_MODEL "
                                    "environment variable is set."
                                ),
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

            # Try to find profile in loaded profiles using public attribute
            profile = None
            profiles_dict = _config.global_profiles

            if profile_id in profiles_dict:
                profile = profiles_dict[profile_id]
            else:
                # Try loading from YAML files directly as fallback
                config_dir = _config.get_config_dir()

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

            # Get positive samples from profile (handle both dict and ProfileDefinition)
            if isinstance(profile, dict):
                positive_samples = profile.get("positive_samples", [])
            else:
                # ProfileDefinition object
                positive_samples = getattr(profile, "positive_samples", [])

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

            # Get negative samples from profile (optional)
            if isinstance(profile, dict):
                negative_samples = profile.get("negative_samples", [])
                threshold = profile.get("threshold", 0.4)
                positive_weight = profile.get("positive_weight", 1.0)
                negative_weight = profile.get("negative_weight", 0.15)
            else:
                negative_samples = getattr(profile, "negative_samples", [])
                threshold = getattr(profile, "threshold", 0.4)
                positive_weight = getattr(profile, "positive_weight", 1.0)
                negative_weight = getattr(profile, "negative_weight", 0.15)

            # Load profile embeddings (ensures vectors are computed for this profile)
            # This is needed because profiles might not be loaded at startup
            from tgsentinel.semantic import (
                compute_max_sample_similarity,
                load_profile_embeddings,
                score_text_for_profile,
            )

            load_profile_embeddings(
                profile_id=profile_id,
                positive_samples=positive_samples,
                negative_samples=negative_samples,
                threshold=threshold,
                positive_weight=positive_weight,
                negative_weight=negative_weight,
            )

            # Use the same scoring method as the worker (averaged embeddings + negative penalty)
            # This ensures test results match production behavior
            score = score_text_for_profile(sample, profile_id)

            if score is None:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Failed to calculate semantic score. Model may not be loaded.",
                        }
                    ),
                    500,
                )

            # Provide interpretation relative to threshold (already extracted above)
            if score < threshold * 0.5:
                interpretation = (
                    f"Very different from profile (< {threshold * 0.5:.2f})"
                )
            elif score < threshold * 0.8:
                interpretation = f"Somewhat related (< {threshold * 0.8:.2f})"
            elif score < threshold:
                interpretation = f"Close but below threshold (< {threshold:.2f})"
            elif score < threshold * 1.2:
                interpretation = f"Matches profile (≥ {threshold:.2f})"
            else:
                interpretation = f"Strongly matches profile (≥ {threshold * 1.2:.2f})"

            logger.info(
                f"[API] Similarity test for profile '{profile_id}': score={score:.3f}, threshold={threshold:.2f}"
            )

            # Also compute max individual sample similarity (useful for exact match detection)
            max_sample_sim = compute_max_sample_similarity(sample, positive_samples)

            # Get actual model name from environment or fallback to default
            model_name = os.getenv("EMBEDDINGS_MODEL", "all-MiniLM-L6-v2")

            return jsonify(
                {
                    "status": "ok",
                    "score": round(score, 3),
                    "max_sample_similarity": (
                        round(max_sample_sim, 3) if max_sample_sim is not None else None
                    ),
                    "interpretation": interpretation,
                    "profile_id": profile_id,
                    "model": model_name,
                    "num_positive_samples": len(positive_samples),
                    "sample_length": len(sample),
                    "threshold": threshold,
                    "will_match": score >= threshold,
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
                            "message": (
                                f"Invalid profile type: {profile_type}. "
                                "Must be 'alert', 'global', or 'interest'"
                            ),
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
            except Exception:
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

    # =========================================================================
    # Message Formats API
    # =========================================================================

    @app.route("/api/message-formats", methods=["GET"])
    def get_message_formats():
        """Get current message format templates.

        Returns all format templates with metadata (description, variables).
        """
        try:
            from tgsentinel.message_formats import (
                DEFAULT_FORMATS,
                SAMPLE_DATA,
                load_message_formats,
            )

            formats = load_message_formats()

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "formats": formats,
                        "defaults": DEFAULT_FORMATS,
                        "sample_data": SAMPLE_DATA,
                    },
                    "error": None,
                }
            )
        except Exception as e:
            logger.error(f"[API] Error loading message formats: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "LOAD_ERROR", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/message-formats", methods=["PUT"])
    def update_message_formats():
        """Update message format templates.

        Validates and saves the provided format templates.
        Creates a backup of the previous configuration.
        """
        try:
            from tgsentinel.message_formats import (
                save_message_formats,
                validate_formats,
            )

            data = request.get_json()
            if not data or "formats" not in data:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "Missing 'formats' field",
                            },
                        }
                    ),
                    400,
                )

            formats = data["formats"]

            # Validate formats
            is_valid, errors = validate_formats(formats)
            if not is_valid:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": {"validation_errors": errors},
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "Format validation failed",
                            },
                        }
                    ),
                    400,
                )

            # Save formats
            success, error_msg = save_message_formats(formats)
            if not success:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {"code": "SAVE_ERROR", "message": error_msg},
                        }
                    ),
                    500,
                )

            logger.info("[API] Message formats updated successfully")
            return jsonify(
                {
                    "status": "ok",
                    "data": {"message": "Message formats saved successfully"},
                    "error": None,
                }
            )

        except json.JSONDecodeError as e:
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "INVALID_JSON", "message": str(e)},
                    }
                ),
                400,
            )
        except Exception as e:
            logger.error(f"[API] Error saving message formats: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "INTERNAL_ERROR", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/message-formats/preview", methods=["POST"])
    def preview_message_format():
        """Preview a rendered message format.

        Renders a template with sample data for preview.

        Request body:
            {
                "format_type": "dm_alerts" | "saved_messages" | "digest.header" | etc.,
                "template": "optional custom template",
                "sample_data": {optional custom sample data}
            }
        """
        try:
            from tgsentinel.message_formats import (
                FormatterContext,
                get_format,
                render_template,
            )

            data = request.get_json()
            if not data:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "Request body required",
                            },
                        }
                    ),
                    400,
                )

            format_type = data.get("format_type", "dm_alerts")
            custom_template = data.get("template")
            custom_sample = data.get("sample_data")

            # Parse format_type for nested types (e.g., "digest.header")
            if "." in format_type:
                parts = format_type.split(".", 1)
                main_type, subtype = parts[0], parts[1]
                template = custom_template or get_format(main_type, subtype)
            else:
                template = custom_template or get_format(format_type)

            if not template:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "NOT_FOUND",
                                "message": f"Format not found: {format_type}",
                            },
                        }
                    ),
                    404,
                )

            # Build context using FormatterContext
            ctx = FormatterContext.from_sample(format_type, custom_sample)
            sample = ctx.build()

            # Render template
            rendered = render_template(template, sample, safe=True)

            return jsonify(
                {
                    "status": "ok",
                    "data": {
                        "rendered": rendered,
                        "template": template,
                        "sample_data": sample,
                    },
                    "error": None,
                }
            )

        except Exception as e:
            logger.error(f"[API] Error previewing message format: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "RENDER_ERROR", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/message-formats/test", methods=["POST"])
    def test_message_format():
        """Test send a formatted message.

        Sends a test message using the specified format to the user's Saved Messages.
        Uses the request/response pattern with a Redis key that the async handler monitors.

        Request body:
            {
                "format_type": "dm_alerts" | "saved_messages" | "digest.header" | etc.,
                "template": "optional custom template"
            }
        """
        try:
            from tgsentinel.message_formats import (
                FormatterContext,
                get_format,
                render_template,
            )

            data = request.get_json()
            if not data:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "Request body required",
                            },
                        }
                    ),
                    400,
                )

            format_type = data.get("format_type", "dm_alerts")
            custom_template = data.get("template")

            # Parse format_type for nested types
            if "." in format_type:
                parts = format_type.split(".", 1)
                main_type, subtype = parts[0], parts[1]
                template = custom_template or get_format(main_type, subtype)
            else:
                template = custom_template or get_format(format_type)

            if not template:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "NOT_FOUND",
                                "message": f"Format not found: {format_type}",
                            },
                        }
                    ),
                    404,
                )

            # Build context using FormatterContext
            ctx = FormatterContext.from_sample(format_type)
            sample = ctx.build()

            # Render template
            rendered = render_template(template, sample, safe=True)

            # Queue test message for async delivery via Redis request/response pattern
            request_id = str(uuid.uuid4())

            if not _redis_client:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "NO_REDIS",
                                "message": "Redis client not available",
                            },
                        }
                    ),
                    503,
                )

            # Create request for async handler (uses same pattern as dialog/users handlers)
            request_key = f"tgsentinel:request:send_test_message:{request_id}"
            response_key = f"tgsentinel:response:send_test_message:{request_id}"

            _redis_client.setex(
                request_key,
                60,  # 1 minute TTL
                json.dumps(
                    {
                        "request_id": request_id,
                        "message": rendered,
                        "format_type": format_type,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )

            # Wait for response (with timeout)
            max_wait = 20  # seconds (increased to allow handler time to process)
            poll_interval = 0.2  # seconds
            waited = 0

            while waited < max_wait:
                response_data = _redis_client.get(response_key)
                if response_data:
                    try:
                        result = json.loads(response_data)
                        _redis_client.delete(response_key)

                        if result.get("status") == "ok":
                            logger.info(
                                "[API] Test message sent successfully: %s", request_id
                            )
                            return jsonify(
                                {
                                    "status": "ok",
                                    "data": {
                                        "message": "Test message sent to Saved Messages",
                                        "preview": rendered,
                                    },
                                    "error": None,
                                }
                            )
                        else:
                            error_msg = result.get("error", "Unknown error")
                            logger.warning(
                                "[API] Test message failed: %s - %s",
                                request_id,
                                error_msg,
                            )
                            return (
                                jsonify(
                                    {
                                        "status": "error",
                                        "data": {"preview": rendered},
                                        "error": {
                                            "code": "SEND_FAILED",
                                            "message": error_msg,
                                        },
                                    }
                                ),
                                500,
                            )
                    except json.JSONDecodeError:
                        pass

                import time

                time.sleep(poll_interval)
                waited += poll_interval

            # Timeout - clean up request key
            _redis_client.delete(request_key)
            logger.warning("[API] Test message request timed out: %s", request_id)

            return (
                jsonify(
                    {
                        "status": "error",
                        "data": {"preview": rendered},
                        "error": {
                            "code": "TIMEOUT",
                            "message": "Test message delivery timed out. "
                            "Ensure Sentinel is authorized and connected to Telegram.",
                        },
                    }
                ),
                504,
            )

        except Exception as e:
            logger.error(f"[API] Error sending test message: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "SEND_ERROR", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/message-formats/reset", methods=["POST"])
    def reset_message_formats():
        """Reset message formats to defaults.

        Accessed through authenticated UI routes.
        Resets to hardcoded defaults defined in defaults.py.
        """
        try:
            from tgsentinel.message_formats.loader import reset_to_defaults

            success, error_msg = reset_to_defaults()
            if not success:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {"code": "RESET_ERROR", "message": error_msg},
                        }
                    ),
                    500,
                )

            logger.info("[API] Message formats reset to defaults")
            return jsonify(
                {
                    "status": "ok",
                    "data": {"message": "Message formats reset to defaults"},
                    "error": None,
                }
            )

        except Exception as e:
            logger.error(f"[API] Error resetting message formats: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "INTERNAL_ERROR", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/message-formats/export", methods=["GET"])
    def export_message_formats():
        """Export message formats as downloadable YAML file."""
        try:
            from tgsentinel.message_formats import load_message_formats

            formats = load_message_formats()

            # Generate YAML content
            yaml_content = yaml.safe_dump(
                formats,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )

            # Return as downloadable file
            return Response(
                yaml_content,
                mimetype="application/x-yaml",
                headers={
                    "Content-Disposition": "attachment; filename=message_formats.yml"
                },
            )

        except Exception as e:
            logger.error(f"[API] Error exporting message formats: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "EXPORT_ERROR", "message": str(e)},
                    }
                ),
                500,
            )

    @app.route("/api/message-formats/import", methods=["POST"])
    def import_message_formats():
        """Import message formats from uploaded YAML file.

        Validates the imported formats before saving.
        Creates a backup of the current configuration.
        """
        try:
            from tgsentinel.message_formats import (
                save_message_formats,
                validate_formats,
            )

            # Check for file upload
            if "file" not in request.files:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {"code": "NO_FILE", "message": "No file uploaded"},
                        }
                    ),
                    400,
                )

            file = request.files["file"]
            if not file.filename:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {"code": "NO_FILE", "message": "No file selected"},
                        }
                    ),
                    400,
                )

            # Read and parse YAML
            try:
                content = file.read().decode("utf-8")
                formats = yaml.safe_load(content)
            except (yaml.YAMLError, UnicodeDecodeError) as e:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "PARSE_ERROR",
                                "message": f"Invalid YAML: {e}",
                            },
                        }
                    ),
                    400,
                )

            if not isinstance(formats, dict):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {
                                "code": "INVALID_FORMAT",
                                "message": "Formats must be a dictionary",
                            },
                        }
                    ),
                    400,
                )

            # Validate formats
            is_valid, errors = validate_formats(formats)
            if not is_valid:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": {"validation_errors": errors},
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "Format validation failed",
                            },
                        }
                    ),
                    400,
                )

            # Save formats
            success, error_msg = save_message_formats(formats)
            if not success:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "data": None,
                            "error": {"code": "SAVE_ERROR", "message": error_msg},
                        }
                    ),
                    500,
                )

            logger.info("[API] Message formats imported successfully")
            return jsonify(
                {
                    "status": "ok",
                    "data": {"message": "Message formats imported successfully"},
                    "error": None,
                }
            )

        except Exception as e:
            logger.error(f"[API] Error importing message formats: {e}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "data": None,
                        "error": {"code": "INTERNAL_ERROR", "message": str(e)},
                    }
                ),
                500,
            )

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
