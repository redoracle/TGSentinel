"""Admin and management API routes for TG Sentinel UI.

This blueprint handles administrative operations:
- Config testing and validation
- Statistics reset
- Config export
- Database cleanup
- Sentinel container restart
- Alert feedback
"""

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request, send_file

logger = logging.getLogger(__name__)

# Blueprint setup
admin_bp = Blueprint("admin", __name__)

# Global dependencies (injected via init function)
_redis_client = None
_execute_fn = None
_serialize_channels_fn = None
_get_stream_name_fn = None
_cached_summary = None
_cached_health = None


def init_admin_routes(
    redis_client=None,
    execute_fn: Callable | None = None,
    serialize_channels_fn: Callable | None = None,
    get_stream_name_fn: Callable | None = None,
):
    """Initialize admin blueprint with dependencies.

    Args:
        redis_client: Redis client instance
        execute_fn: Database execute function
        serialize_channels_fn: Function to serialize channels
        get_stream_name_fn: Function to get Redis stream name
    """
    global _redis_client, _execute_fn, _serialize_channels_fn, _get_stream_name_fn
    _redis_client = redis_client
    _execute_fn = execute_fn
    _serialize_channels_fn = serialize_channels_fn
    _get_stream_name_fn = get_stream_name_fn
    logger.info("Admin routes initialized")


# ═══════════════════════════════════════════════════════════════════
# Configuration Testing and Validation
# ═══════════════════════════════════════════════════════════════════


@admin_bp.post("/api/config/rules/test")
def test_config_rules():
    """Run a lightweight rule test over configured channels.

    Body (optional): {"channel_ids": [..], "text": "sample"}
    Returns a summary with matched_rules (empty if none) and diagnostics.
    This endpoint is designed for quick UI feedback and does not alter state.
    """
    try:
        payload = request.get_json(silent=True) or {}
        only_ids = set(map(int, payload.get("channel_ids", []) or []))
        sample_text = str(payload.get("text", "")).strip()

        channels = _serialize_channels_fn() if _serialize_channels_fn else []
        if only_ids:
            channels = [c for c in channels if int(c.get("id", 0)) in only_ids]

        results = []
        for ch in channels:
            # Basic keyword probing over provided sample_text if any
            keywords = [str(k).lower() for k in (ch.get("keywords") or [])]
            matches = []
            if sample_text and keywords:
                low = sample_text.lower()
                matches = [kw for kw in keywords if kw and kw in low]
            results.append(
                {
                    "channel_id": ch.get("id"),
                    "channel_name": ch.get("name"),
                    "matched_rules": matches,
                    "diagnostics": {
                        "keywords": keywords,
                        "vip_senders": ch.get("vip_senders") or [],
                        "reaction_threshold": ch.get("reaction_threshold", 0),
                        "reply_threshold": ch.get("reply_threshold", 0),
                    },
                }
            )

        return jsonify({"status": "ok", "tested": len(results), "results": results})
    except Exception as exc:
        logger.error("Rule test failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@admin_bp.post("/api/config/stats/reset")
def reset_stats():
    """Reset per-channel transient counters (best-effort).

    Clears any known Redis keys used for rate limiting or stats. Safe no-op if Redis unavailable.
    """
    cleared = 0
    try:
        if _redis_client is not None:
            try:
                # Scan and delete likely keys (best-effort, ignores missing)
                patterns = [
                    "tgsentinel:rate:*",
                    "tgsentinel:stats:*",
                ]
                for pat in patterns:
                    # Use scan_iter if available; fall back to keys()
                    keys = []
                    try:
                        scan_iter = getattr(_redis_client, "scan_iter", None)
                        if callable(scan_iter):
                            # scan_iter returns a generator, convert to list
                            try:
                                keys = [k for k in scan_iter(match=pat)]  # type: ignore
                            except Exception:
                                keys = []
                        else:
                            result = _redis_client.keys(pat)  # type: ignore
                            keys = list(result) if result else []
                    except Exception:
                        keys = []
                    if keys:
                        try:
                            cleared += int(_redis_client.delete(*keys) or 0)
                        except Exception:
                            pass
            except Exception:
                pass
        # Optionally, clear any in-memory caches we maintain
        global _cached_summary, _cached_health
        _cached_summary = None
        _cached_health = None
        return jsonify({"status": "ok", "cleared_keys": cleared})
    except Exception as exc:
        logger.error("Reset stats failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@admin_bp.get("/api/config/export")
def export_config():
    """Export the current YAML configuration as a downloadable file."""
    try:
        cfg_path = Path(
            os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml")
        ).resolve()
        if not cfg_path.exists():
            return (
                jsonify({"status": "error", "message": "Configuration file not found"}),
                404,
            )

        return send_file(
            str(cfg_path),
            mimetype="application/x-yaml",
            as_attachment=True,
            download_name=f"tgsentinel_config_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.yml",
        )
    except Exception as exc:
        logger.error("Export config failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
# Database Cleanup
# ═══════════════════════════════════════════════════════════════════


@admin_bp.post("/api/config/clean-db")
def clean_database():
    """Clean all data from the UI database and Redis stream, leaving a fresh environment.

    This endpoint permanently deletes:
    - All cached alerts from UI database
    - All digest runs history
    - All audit log entries
    - All messages from Redis stream
    - Cached participant info
    - User info cache
    - User and chat avatars

    NOTE: This only cleans UI database (ui.db), NOT sentinel database (sentinel.db).
    Sentinel's message/feedback data is managed by the sentinel worker.

    Returns the count of deleted records.
    """
    try:
        from ui.database import _ui_db

        # Check if UI database was successfully initialized
        if _ui_db is None:
            logger.warning(
                "Clean DB: UI database not initialized, skipping database clean"
            )
            return (
                jsonify({"status": "error", "message": "UI database not available"}),
                503,
            )

        conn = _ui_db.connect()
        cursor = conn.cursor()

        deleted_count = 0
        redis_deleted = 0

        # Count and delete UI database records
        cursor.execute("SELECT COUNT(*) FROM alerts")
        alerts_count = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM digest_runs")
        digest_count = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM audit_log")
        audit_count = cursor.fetchone()[0] or 0

        deleted_count = alerts_count + digest_count + audit_count

        # Delete all data from UI tables
        cursor.execute("DELETE FROM alerts")
        logger.info("Deleted %d records from alerts table", alerts_count)

        cursor.execute("DELETE FROM digest_runs")
        logger.info("Deleted %d records from digest_runs table", digest_count)

        cursor.execute("DELETE FROM audit_log")
        logger.info("Deleted %d records from audit_log table", audit_count)

        # Reset auto-increment counters for SQLite
        try:
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='alerts'")
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='digest_runs'")
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='audit_log'")
        except Exception:
            # No sequence table, that's okay
            pass

        conn.commit()

        # Clear Redis stream and caches
        if _redis_client:
            try:
                stream_name = (
                    _get_stream_name_fn()
                    if _get_stream_name_fn
                    else "tgsentinel:stream"
                )

                # Get count of messages in stream before deletion
                stream_len = _redis_client.xlen(stream_name)

                # Delete the entire stream
                _redis_client.delete(stream_name)
                logger.info(
                    "Deleted %d messages from Redis stream '%s'",
                    stream_len,
                    stream_name,
                )
                redis_deleted += stream_len

                # Clear cached user info and participant data using scan_iter (non-blocking)
                # Use batched deletion to avoid long argument lists and blocking
                batch_size = 100
                participant_keys = []
                for key in _redis_client.scan_iter(
                    match="tgsentinel:participant:*", count=100
                ):
                    participant_keys.append(key)
                    if len(participant_keys) >= batch_size:
                        # Delete batch using pipeline for efficiency
                        pipe = _redis_client.pipeline()
                        for k in participant_keys:
                            pipe.unlink(
                                k
                            )  # unlink is non-blocking alternative to delete
                        pipe.execute()
                        deleted_batch = len(participant_keys)
                        redis_deleted += deleted_batch
                        logger.info(
                            "Deleted batch of %d participant info keys from Redis",
                            deleted_batch,
                        )
                        participant_keys = []

                # Delete remaining keys in final batch
                if participant_keys:
                    pipe = _redis_client.pipeline()
                    for k in participant_keys:
                        pipe.unlink(k)
                    pipe.execute()
                    deleted_batch = len(participant_keys)
                    redis_deleted += deleted_batch
                    logger.info(
                        "Deleted final batch of %d participant info keys from Redis",
                        deleted_batch,
                    )

                # Clear user info cache
                if _redis_client.delete("tgsentinel:user_info"):
                    redis_deleted += 1
                    logger.info("Deleted user info cache from Redis")

                # Clear avatars using scan_iter (non-blocking)
                avatar_keys = []
                for key in _redis_client.scan_iter(
                    match="tgsentinel:*_avatar:*", count=100
                ):
                    avatar_keys.append(key)
                    if len(avatar_keys) >= batch_size:
                        # Delete batch using pipeline
                        pipe = _redis_client.pipeline()
                        for k in avatar_keys:
                            pipe.unlink(k)
                        pipe.execute()
                        deleted_batch = len(avatar_keys)
                        redis_deleted += deleted_batch
                        logger.info(
                            "Deleted batch of %d avatar keys from Redis", deleted_batch
                        )
                        avatar_keys = []

                # Delete remaining avatar keys in final batch
                if avatar_keys:
                    pipe = _redis_client.pipeline()
                    for k in avatar_keys:
                        pipe.unlink(k)
                    pipe.execute()
                    deleted_batch = len(avatar_keys)
                    redis_deleted += deleted_batch
                    logger.info(
                        "Deleted final batch of %d avatar keys from Redis",
                        deleted_batch,
                    )

            except Exception as redis_exc:
                logger.warning("Redis cleanup failed: %s", redis_exc)

        logger.info(
            "Database cleanup completed: %d DB records, %d Redis items",
            deleted_count,
            redis_deleted,
        )

        return jsonify(
            {
                "status": "ok",
                "deleted": {
                    "database": deleted_count,
                    "redis": redis_deleted,
                    "total": deleted_count + redis_deleted,
                },
                "details": {
                    "alerts": alerts_count,
                    "digest_runs": digest_count,
                    "audit_log": audit_count,
                },
            }
        )

    except Exception as exc:
        logger.error("Database cleanup failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
# Alert Feedback
# ═══════════════════════════════════════════════════════════════════


@admin_bp.post("/api/alerts/feedback")
def submit_alert_feedback():
    """Submit feedback (thumbs up/down) for an alert.

    Forwards the request to Sentinel API to ensure feedback is stored
    in the correct database (sentinel.db, not ui.db).
    """
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

    chat_id = payload.get("chat_id")
    msg_id = payload.get("msg_id")
    label = payload.get("label")  # "up" or "down"

    if not chat_id or not msg_id or label not in ("up", "down"):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing or invalid parameters: chat_id, msg_id, label",
                }
            ),
            400,
        )

    # Validate that chat_id and msg_id can be converted to integers
    try:
        chat_id_int = int(chat_id)
        msg_id_int = int(msg_id)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Invalid chat_id or msg_id: must be valid integers",
                }
            ),
            400,
        )

    try:
        # Forward to Sentinel API (single source of truth for feedback)
        import requests

        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )

        response = requests.post(
            f"{sentinel_api_url}/feedback",
            json={"chat_id": chat_id_int, "msg_id": msg_id_int, "label": label},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "ok":
            logger.info(
                "Feedback forwarded to Sentinel: chat_id=%s, msg_id=%s, label=%s",
                chat_id_int,
                msg_id_int,
                label,
            )
            return jsonify({"status": "ok"})
        else:
            logger.error("Sentinel API returned error: %s", data.get("error"))
            return (
                jsonify(
                    {"status": "error", "message": data.get("error", "Unknown error")}
                ),
                500,
            )

    except requests.RequestException as e:
        logger.error("Failed to forward feedback to Sentinel API: %s", e)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to contact Sentinel: {str(e)}"}
            ),
            500,
        )
    except Exception as exc:
        logger.error("Failed to record feedback: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
# Sentinel Container Control
# ═══════════════════════════════════════════════════════════════════


@admin_bp.post("/api/sentinel/restart")
def restart_sentinel():
    """Restart the sentinel container to apply configuration changes."""
    try:
        # Try to restart using docker-compose
        result = subprocess.run(
            ["docker-compose", "restart", "sentinel"],
            cwd="/app",  # Docker container working directory
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info("Sentinel container restart initiated successfully")
            return jsonify({"status": "ok", "message": "Sentinel restarting"})
        else:
            logger.error("Failed to restart sentinel: %s", result.stderr)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Restart command failed",
                        "details": result.stderr,
                    }
                ),
                500,
            )
    except subprocess.TimeoutExpired:
        logger.error("Sentinel restart timed out")
        return jsonify({"status": "error", "message": "Restart timed out"}), 500
    except FileNotFoundError:
        logger.error("docker-compose not found")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "docker-compose not available in this environment",
                }
            ),
            500,
        )
    except Exception as exc:
        logger.error("Unexpected error restarting sentinel: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
