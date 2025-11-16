"""Sentinel HTTP API Server

This module provides a minimal HTTP/JSON API for the sentinel worker to:
1. Accept session file uploads from the UI
2. Provide health and status information
3. Expose alerts and stats to the UI

The API runs in a separate thread alongside the main sentinel worker.
"""

import base64
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request
from flask_cors import CORS

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
        """Get recent alerts (placeholder - can be extended)."""
        return (
            jsonify(
                {"status": "ok", "data": {"alerts": [], "count": 0}, "error": None}
            ),
            200,
        )

    @app.route("/api/stats", methods=["GET"])
    def stats():
        """Get worker statistics (placeholder - can be extended)."""
        return (
            jsonify(
                {
                    "status": "ok",
                    "data": {
                        "messages_processed": 0,
                        "alerts_sent": 0,
                        "uptime_seconds": 0,
                    },
                    "error": None,
                }
            ),
            200,
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
