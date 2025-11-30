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
from typing import Callable, List, Tuple

import requests
import yaml
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


@admin_bp.get("/api/config/export")
def export_config():
    """Export the current YAML configuration as a downloadable file.

    Fetches config from Sentinel API (single source of truth) and returns
    it as a downloadable YAML file.
    """
    import io

    import requests as http_requests

    try:
        # Fetch config from Sentinel API (single source of truth)
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )
        response = http_requests.get(f"{sentinel_api_url}/config", timeout=5)

        if not response.ok:
            logger.error(
                f"Failed to fetch config from Sentinel: {response.status_code}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Could not fetch config from Sentinel",
                    }
                ),
                502,
            )

        config_data = response.json().get("data", {})

        # Convert to YAML and return as downloadable file
        yaml_content = yaml.safe_dump(
            config_data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

        return send_file(
            io.BytesIO(yaml_content.encode("utf-8")),
            mimetype="application/x-yaml",
            as_attachment=True,
            download_name=f"tgsentinel_config_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.yml",
        )
    except http_requests.RequestException as e:
        logger.error("Export config failed (Sentinel unavailable): %s", e)
        return (
            jsonify({"status": "error", "message": "Sentinel service unavailable"}),
            503,
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

    NOTE: This only cleans UI cache (legacy UI DB removed), NOT sentinel database (sentinel.db).
    Sentinel's message/feedback data is managed by the sentinel worker.

    Returns the count of deleted records.
    """
    try:
        redis_deleted = 0

        # Note: UI database operations removed - legacy UI tables were never populated
        # All data is in Sentinel database, accessed via HTTP API

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
            "Cleanup completed: %d Redis items deleted",
            redis_deleted,
        )

        return jsonify(
            {
                "status": "ok",
                "deleted": {
                    "database": 0,  # legacy UI tables removed - no longer used
                    "redis": redis_deleted,
                    "total": redis_deleted,
                },
                "details": {
                    "redis_stream": redis_deleted,
                    "note": "UI database removed - all data in Sentinel DB",
                },
            }
        )

    except Exception as exc:
        logger.error("Cleanup failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


def _parse_feedback_payload() -> Tuple[int, int, str, List[str]]:
    """Validate common feedback payload fields."""

    if not request.is_json:
        raise ValueError("Content-Type must be application/json")

    payload = request.get_json(silent=True)
    if payload is None:
        raise ValueError("Invalid JSON payload")

    chat_id = payload.get("chat_id")
    msg_id = payload.get("msg_id")
    label = payload.get("label")
    profile_ids = payload.get("profile_ids", [])

    if not chat_id or not msg_id or label not in ("up", "down"):
        raise ValueError("Missing or invalid parameters: chat_id, msg_id, label")

    try:
        chat_id_int = int(chat_id)
        msg_id_int = int(msg_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid chat_id or msg_id: must be valid integers") from exc

    normalized_profiles: List[str] = []
    if isinstance(profile_ids, list):
        for raw_pid in profile_ids:
            if raw_pid is None:
                continue
            pid_str = str(raw_pid).strip()
            if pid_str:
                normalized_profiles.append(pid_str)

    return chat_id_int, msg_id_int, label, normalized_profiles


def _forward_feedback(
    chat_id: int,
    msg_id: int,
    label: str,
    semantic_type: str,
    profile_ids: List[str],
):
    """Forward validated feedback to Sentinel API."""

    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        response = requests.post(
            f"{sentinel_api_url}/feedback",
            json={
                "chat_id": chat_id,
                "msg_id": msg_id,
                "label": label,
                "semantic_type": semantic_type,
                "profile_ids": profile_ids,
            },
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "ok":
            logger.info(
                "Feedback forwarded to Sentinel",
                extra={
                    "chat_id": chat_id,
                    "msg_id": msg_id,
                    "label": label,
                    "semantic_type": semantic_type,
                    "profile_ids": profile_ids,
                },
            )
            return jsonify({"status": "ok"})

        logger.error("Sentinel API returned error: %s", data.get("error"))
        return (
            jsonify({"status": "error", "message": data.get("error", "Unknown error")}),
            500,
        )

    except requests.RequestException as exc:
        logger.error("Failed to forward feedback to Sentinel API: %s", exc)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Failed to contact Sentinel: {str(exc)}",
                }
            ),
            500,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to record feedback: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
# Alert & Interest Feedback
# ═══════════════════════════════════════════════════════════════════


@admin_bp.post("/api/alerts/feedback")
def submit_alert_feedback():
    """Submit feedback for heuristic alert matches (keyword-based)."""

    try:
        chat_id, msg_id, label, profile_ids = _parse_feedback_payload()
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    return _forward_feedback(
        chat_id,
        msg_id,
        label,
        semantic_type="alert_keyword",
        profile_ids=profile_ids,
    )


@admin_bp.post("/api/interests/feedback")
def submit_interest_feedback():
    """Submit feedback for semantic interest matches."""

    try:
        chat_id, msg_id, label, profile_ids = _parse_feedback_payload()
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    return _forward_feedback(
        chat_id,
        msg_id,
        label,
        semantic_type="interest_semantic",
        profile_ids=profile_ids,
    )


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


# ═══════════════════════════════════════════════════════════════════
# Message Formats Proxy
# ═══════════════════════════════════════════════════════════════════


@admin_bp.route("/sentinel/message-formats", methods=["GET", "PUT"])
def proxy_message_formats():
    """Proxy message formats requests to Sentinel API."""
    import requests

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        if request.method == "GET":
            response = requests.get(
                f"{sentinel_url}/message-formats",
                timeout=10,
            )
        else:  # PUT
            response = requests.put(
                f"{sentinel_url}/message-formats",
                json=request.get_json(),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

        return jsonify(response.json()), response.status_code

    except requests.exceptions.Timeout:
        logger.error("Sentinel message-formats API timeout")
        return (
            jsonify({"status": "error", "error": {"message": "Request timeout"}}),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return jsonify({"status": "error", "error": {"message": str(exc)}}), 502


@admin_bp.route("/sentinel/message-formats/preview", methods=["POST"])
def proxy_message_formats_preview():
    """Proxy message formats preview requests to Sentinel API."""
    import requests

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        response = requests.post(
            f"{sentinel_url}/message-formats/preview",
            json=request.get_json(),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return jsonify(response.json()), response.status_code

    except requests.exceptions.Timeout:
        logger.error("Sentinel message-formats preview API timeout")
        return (
            jsonify({"status": "error", "error": {"message": "Request timeout"}}),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return jsonify({"status": "error", "error": {"message": str(exc)}}), 502


@admin_bp.route("/sentinel/alerts", methods=["GET"])
def proxy_sentinel_alerts():
    """Proxy alerts requests to the Sentinel API for developer dashboards."""
    import requests

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
    limit = request.args.get("limit", "20")

    try:
        response = requests.get(
            f"{sentinel_url}/alerts",
            params={"limit": limit},
            timeout=10,
        )
        return jsonify(response.json()), response.status_code
    except requests.exceptions.Timeout:
        logger.error("Sentinel alerts API timeout")
        return (
            jsonify({"status": "error", "error": {"message": "Request timeout"}}),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return (
            jsonify({"status": "error", "error": {"message": str(exc)}}),
            502,
        )


@admin_bp.route("/sentinel/digests/trigger", methods=["POST"])
def proxy_sentinel_digests_trigger():
    """Proxy manual digest trigger requests to Sentinel API."""
    import requests

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if not admin_token:
        logger.warning("Admin digest trigger attempted without ADMIN_TOKEN")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Admin token not configured",
                }
            ),
            503,
        )

    try:
        headers = {
            "Content-Type": "application/json",
            "X-Admin-Token": admin_token,
        }
        response = requests.post(
            f"{sentinel_url}/digests/trigger",
            json=request.get_json(silent=True) or {},
            headers=headers,
            timeout=30,
        )
        return jsonify(response.json()), response.status_code

    except requests.exceptions.Timeout:
        logger.error("Sentinel digests trigger API timeout")
        return (
            jsonify({"status": "error", "error": {"message": "Request timeout"}}),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return (
            jsonify({"status": "error", "error": {"message": str(exc)}}),
            502,
        )


@admin_bp.route("/sentinel/message-formats/test", methods=["POST"])
def proxy_message_formats_test():
    """Proxy message formats test send requests to Sentinel API."""
    import requests

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        response = requests.post(
            f"{sentinel_url}/message-formats/test",
            json=request.get_json(),
            headers={"Content-Type": "application/json"},
            timeout=30,  # Increased to 30s to allow Sentinel time to process
        )
        return jsonify(response.json()), response.status_code

    except requests.exceptions.Timeout:
        logger.error("Sentinel message-formats test API timeout after 30s")
        return (
            jsonify(
                {
                    "status": "error",
                    "error": {
                        "code": "GATEWAY_TIMEOUT",
                        "message": "Request timeout - Sentinel service did not respond in time. "
                        "Ensure Sentinel is running and authorized.",
                    },
                }
            ),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return jsonify({"status": "error", "error": {"message": str(exc)}}), 502


@admin_bp.route("/sentinel/message-formats/reset", methods=["POST"])
def proxy_message_formats_reset():
    """Proxy message formats reset requests to Sentinel API."""
    import requests

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        # Forward admin token if present
        headers: dict[str, str] = {"Content-Type": "application/json"}
        admin_token = request.headers.get("X-Admin-Token")
        if admin_token:
            headers["X-Admin-Token"] = admin_token

        response = requests.post(
            f"{sentinel_url}/message-formats/reset",
            headers=headers,
            timeout=10,
        )
        return jsonify(response.json()), response.status_code

    except requests.exceptions.Timeout:
        logger.error("Sentinel message-formats reset API timeout")
        return (
            jsonify({"status": "error", "error": {"message": "Request timeout"}}),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return jsonify({"status": "error", "error": {"message": str(exc)}}), 502


@admin_bp.route("/sentinel/message-formats/export", methods=["GET"])
def proxy_message_formats_export():
    """Proxy message formats export requests to Sentinel API."""
    import requests
    from flask import Response

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        response = requests.get(
            f"{sentinel_url}/message-formats/export",
            timeout=10,
        )

        return Response(
            response.content,
            mimetype=response.headers.get("Content-Type", "application/x-yaml"),
            headers={
                "Content-Disposition": response.headers.get(
                    "Content-Disposition", "attachment; filename=message_formats.yml"
                )
            },
        )

    except requests.exceptions.Timeout:
        logger.error("Sentinel message-formats export API timeout")
        return (
            jsonify({"status": "error", "error": {"message": "Request timeout"}}),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return jsonify({"status": "error", "error": {"message": str(exc)}}), 502


@admin_bp.route("/sentinel/message-formats/import", methods=["POST"])
def proxy_message_formats_import():
    """Proxy message formats import requests to Sentinel API."""
    import requests

    sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        # Forward the file upload
        files = {}
        if "file" in request.files:
            file = request.files["file"]
            files["file"] = (file.filename, file.stream, file.content_type)

        response = requests.post(
            f"{sentinel_url}/message-formats/import",
            files=files,
            timeout=30,
        )
        return jsonify(response.json()), response.status_code

    except requests.exceptions.Timeout:
        logger.error("Sentinel message-formats import API timeout")
        return (
            jsonify({"status": "error", "error": {"message": "Request timeout"}}),
            504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Error connecting to Sentinel API: %s", exc)
        return jsonify({"status": "error", "error": {"message": str(exc)}}), 502


# ═══════════════════════════════════════════════════════════════════
# Phase 3: Feedback Learning Monitor
# ═══════════════════════════════════════════════════════════════════


@admin_bp.route("/feedback-learning-monitor", methods=["GET"])
def feedback_learning_monitor():
    """
    Render feedback learning system monitoring dashboard.

    Shows real-time status of:
    - Batch processor queue
    - Feedback aggregator stats
    - Background task health
    - Configuration values
    """
    from flask import render_template

    sentinel_api_base = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    return render_template(
        "admin/feedback_learning_monitor.html", sentinel_api_base=sentinel_api_base
    )


@admin_bp.route("/api/feedback-learning/status", methods=["GET"])
def feedback_learning_status_proxy():
    """
    Proxy feedback learning status from Sentinel API.
    This allows browser JavaScript to call the endpoint without CORS issues.
    """
    import requests

    sentinel_api_base = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        response = requests.get(
            f"{sentinel_api_base}/feedback-learning/status", timeout=10
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to fetch feedback learning status: {exc}", exc_info=True)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Failed to fetch status from Sentinel: {str(exc)}",
                }
            ),
            500,
        )


@admin_bp.route("/api/feedback-learning/batch-history", methods=["GET"])
def feedback_learning_batch_history_proxy():
    """
    Proxy batch history request to Sentinel API.
    """
    import requests

    sentinel_api_base = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
    limit = request.args.get("limit", "50")

    try:
        response = requests.get(
            f"{sentinel_api_base}/feedback-learning/batch-history",
            params={"limit": limit},
            timeout=10,
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to fetch batch history: {exc}", exc_info=True)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Failed to fetch batch history from Sentinel: {str(exc)}",
                }
            ),
            500,
        )


@admin_bp.route("/api/feedback-learning/trigger-batch", methods=["POST"])
def feedback_learning_trigger_batch_proxy():
    """
    Proxy trigger batch request to Sentinel API.
    Automatically uses the ADMIN_TOKEN from environment for authenticated UI users.
    """
    import requests

    sentinel_api_base = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    # Use the configured ADMIN_TOKEN from environment
    # UI users who are already authenticated don't need a separate admin login
    admin_token = os.getenv("ADMIN_TOKEN", "")

    if not admin_token:
        logger.warning("ADMIN_TOKEN not configured in environment")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Admin token not configured. Please set ADMIN_TOKEN environment variable.",
                }
            ),
            503,
        )

    try:
        headers = {"X-Admin-Token": admin_token}

        response = requests.post(
            f"{sentinel_api_base}/feedback-learning/trigger-batch",
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to trigger batch processing: {exc}", exc_info=True)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to trigger batch: {str(exc)}"}
            ),
            response.status_code if "response" in locals() else 500,
        )
