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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request
from flask_cors import CORS
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

    @app.route("/api/config", methods=["GET"])
    def get_config():
        """Get current configuration from Sentinel (single source of truth)."""
        try:
            from tgsentinel.config import load_config

            cfg = load_config()

            # Serialize config to JSON-friendly format
            config_data = {
                "telegram": {
                    "session": cfg.telegram_session,
                },
                "alerts": {
                    "mode": cfg.alerts.mode,
                    "target_channel": cfg.alerts.target_channel,
                },
                "digest": {
                    "hourly": cfg.alerts.digest.hourly,
                    "daily": cfg.alerts.digest.daily,
                    "top_n": cfg.alerts.digest.top_n,
                },
                "channels": [
                    {
                        "id": ch.id,
                        "name": ch.name,
                        "vip_senders": ch.vip_senders,
                        "keywords": ch.keywords,
                        "reaction_threshold": ch.reaction_threshold,
                        "reply_threshold": ch.reply_threshold,
                        "rate_limit_per_hour": ch.rate_limit_per_hour,
                    }
                    for ch in cfg.channels
                ],
                "monitored_users": [
                    {
                        "id": user.id,
                        "name": user.name,
                        "username": user.username,
                        "enabled": user.enabled,
                    }
                    for user in cfg.monitored_users
                ],
                "interests": cfg.interests,
                "redis": cfg.redis,
                "database_uri": cfg.db_uri,
                "embeddings_model": cfg.embeddings_model,
                "similarity_threshold": cfg.similarity_threshold,
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
