"""Dashboard and analytics routes for TG Sentinel UI.

This blueprint handles:
- Dashboard summary and activity feed
- System health monitoring
- Recent alerts retrieval
- Alert digests
- Analytics metrics (messages/min, latency, resource usage)
- Keyword analysis
"""

from __future__ import annotations

import csv
import io
import logging
import os
import requests
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, jsonify, make_response, request

# Import dependency container
try:
    from ui.core import get_deps
except ImportError:
    from core import get_deps  # type: ignore

# Create blueprint
dashboard_bp = Blueprint("dashboard", __name__)

logger = logging.getLogger(__name__)

# Import psutil for health metrics
try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

try:
    import redis
except ImportError:
    redis = None  # type: ignore


def _load_alerts_via_app(limit: int) -> list[dict[str, Any]]:
    """Load alerts via dependency injection.

    Uses explicit dependency injection pattern:
    1. Try deps.alert_loader(limit) if available
    2. Fall back to deps.data_service.load_alerts(limit=limit)
    3. Return empty list if no loader available

    Args:
        limit: Maximum number of alerts to load

    Returns:
        List of alert dictionaries
    """
    deps = get_deps()

    # Try injected alert loader first (for tests and custom implementations)
    if deps.alert_loader and callable(deps.alert_loader):
        alerts = deps.alert_loader(limit)
        if alerts is None:
            return []
        # Ensure we return a list
        if isinstance(alerts, list):
            return alerts
        return []

    # Fall back to data service
    if deps.data_service:
        return deps.data_service.load_alerts(limit=limit)

    return []


@dashboard_bp.route("/dashboard/summary", methods=["GET"])
def dashboard_summary():
    """Get dashboard summary statistics."""
    deps = get_deps()
    return jsonify(deps.data_service.compute_summary() if deps.data_service else {})


@dashboard_bp.route("/dashboard/activity", methods=["GET"])
def dashboard_activity():
    """Get recent activity feed."""
    deps = get_deps()
    limit = min(int(request.args.get("limit", 10)), 100)
    return jsonify(
        {
            "entries": (
                deps.data_service.load_live_feed(limit=limit)
                if deps.data_service
                else []
            )
        }
    )


@dashboard_bp.route("/system/health", methods=["GET"])
def system_health():
    """Get system health metrics."""
    deps = get_deps()
    return jsonify(
        deps.data_service.compute_health(psutil=psutil, redis_module=redis)
        if deps.data_service
        else {}
    )


@dashboard_bp.route("/alerts/recent", methods=["GET"])
def recent_alerts():
    """Get recent alerts."""
    deps = get_deps()
    limit = min(int(request.args.get("limit", 100)), 250)
    return jsonify(
        {
            "alerts": (
                deps.data_service.load_alerts(limit=limit) if deps.data_service else []
            )
        }
    )


@dashboard_bp.route("/alerts/digests", methods=["GET"])
def alert_digests():
    """Get alert digests."""
    deps = get_deps()
    limit = min(int(request.args.get("limit", 20)), 100)
    return jsonify(
        {
            "digests": (
                deps.data_service.load_digests(limit=limit) if deps.data_service else []
            )
        }
    )


@dashboard_bp.route("/config/threshold", methods=["GET", "POST"])
def config_threshold():
    """Proxy for alert threshold (min_score) to/from Sentinel API."""
    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    if request.method == "POST":
        # Forward POST request to Sentinel
        data = request.get_json() or {}
        threshold = data.get("threshold")

        if threshold is None:
            return jsonify({"status": "error", "message": "threshold is required"}), 400

        try:
            threshold_value = float(threshold)
            if not (0.0 <= threshold_value <= 10.0):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "threshold must be between 0.0 and 10.0",
                        }
                    ),
                    400,
                )

            # Update config on Sentinel (single source of truth)
            response = requests.post(
                f"{sentinel_api_url}/config",
                json={"alerts": {"min_score": threshold_value}},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if not response.ok:
                # Normalize Content-Type by extracting media type (ignore charset and other parameters)
                content_type = response.headers.get("content-type", "")
                media_type = content_type.split(";")[0].strip().lower()
                error_data = response.json() if media_type == "application/json" else {}
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": error_data.get(
                                "message", f"Sentinel API error: {response.status_code}"
                            ),
                        }
                    ),
                    response.status_code,
                )

            return jsonify(
                {
                    "status": "ok",
                    "threshold": threshold_value,
                    "message": "Threshold saved successfully",
                }
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to update threshold on Sentinel: {e}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Failed to communicate with Sentinel: {str(e)}",
                    }
                ),
                503,
            )
        except (ValueError, TypeError) as e:
            return (
                jsonify(
                    {"status": "error", "message": f"Invalid threshold value: {e}"}
                ),
                400,
            )
    else:
        # GET: Fetch current threshold from Sentinel
        try:
            response = requests.get(f"{sentinel_api_url}/config", timeout=5)
            if not response.ok:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to fetch config from Sentinel: {response.status_code}",
                        }
                    ),
                    response.status_code,
                )

            config_data = response.json().get("data", {})
            min_score = config_data.get("alerts", {}).get("min_score", 5.0)

            return jsonify({"status": "ok", "threshold": float(min_score)})

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch config from Sentinel: {e}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Failed to communicate with Sentinel: {str(e)}",
                    }
                ),
                503,
            )


@dashboard_bp.route("/analytics/metrics", methods=["GET"])
def analytics_metrics():
    """Get real-time analytics metrics."""
    deps = get_deps()
    from ui.core import query_one

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    result = (
        query_one(
            deps.engine,
            "SELECT COUNT(*) AS total_count FROM messages WHERE datetime(created_at) >= :cutoff",
            {"cutoff": cutoff},
        )
        if deps.engine
        else None
    )
    processed = result.get("total_count", 0) if result else 0
    # Calculate actual messages per minute with decimal precision
    messages_per_min = round(int(processed) / 60.0, 2)

    latency = round(float(os.getenv("SEMANTIC_LATENCY_MS", "120")) / 1000, 3)
    health = (
        deps.data_service.compute_health(psutil=psutil, redis_module=redis)
        if deps.data_service
        else {}
    )

    # Get CPU and memory values with proper fallbacks
    cpu_val = health.get("cpu_percent")
    memory_val = health.get("memory_mb")

    return jsonify(
        {
            "messages_per_min": messages_per_min,
            "semantic_latency": latency,
            "cpu": cpu_val if cpu_val is not None else "N/A",
            "memory": memory_val if memory_val is not None else "N/A",
            "redis_stream_depth": health.get("redis_stream_depth", 0),
        }
    )


@dashboard_bp.route("/analytics/keywords", methods=["GET"])
def analytics_keywords():
    """Return keyword match counts from recent messages in the database.

    Uses the union of configured channel keywords and counts case-insensitive
    occurrences in the `messages.message_text` field within the last 24 hours.
    """
    deps = get_deps()
    from ui.core import query_one

    # Fetch keywords from Sentinel API
    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")
    kw_set: set[str] = set()

    try:
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if response.ok:
            config_data = response.json().get("data", {})
            channels = config_data.get("channels", [])
            for channel in channels:
                for kw in channel.get("keywords", []) or []:
                    if isinstance(kw, str) and kw.strip():
                        kw_set.add(kw.strip())
        else:
            # Fallback to local config
            from ui.utils.serializers import serialize_channels

            for channel in serialize_channels(deps.config):
                for kw in channel.get("keywords", []) or []:
                    if isinstance(kw, str) and kw.strip():
                        kw_set.add(kw.strip())
    except requests.exceptions.RequestException:
        # Fallback to local config
        from ui.utils.serializers import serialize_channels

        for channel in serialize_channels(deps.config):
            for kw in channel.get("keywords", []) or []:
                if isinstance(kw, str) and kw.strip():
                    kw_set.add(kw.strip())

    if not kw_set:
        return jsonify({"keywords": []})

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Count matches per keyword using SQL LIKE (case-insensitive via lower())
    results: list[dict[str, int | str]] = []
    for kw in sorted(kw_set):
        try:
            result = (
                query_one(
                    deps.engine,
                    """
                SELECT COUNT(*) AS keyword_count FROM messages
                WHERE datetime(created_at) >= :cutoff
                  AND lower(COALESCE(message_text, '')) LIKE '%' || lower(:kw) || '%'
                """,
                    {"cutoff": cutoff, "kw": kw},
                )
                if deps.engine
                else None
            )
            count = result.get("keyword_count", 0) if result else 0
        except Exception:
            count = 0
        results.append({"keyword": kw, "count": int(count or 0)})

    # Sort descending by count
    results.sort(key=lambda x: x["count"], reverse=True)
    return jsonify({"keywords": results})


@dashboard_bp.get("/export_alerts")
def export_alerts():
    """Export alerts as CSV file."""
    try:
        limit = max(1, min(request.args.get("limit", 1000, type=int), 10000))
        format_type = request.args.get("format", "human", type=str).lower()

        alerts = _load_alerts_via_app(limit)
        format_type = request.args.get("format", "human", type=str).lower()

        alerts = _load_alerts_via_app(limit)

        output = io.StringIO()
        writer = csv.writer(output)

        if format_type == "machine":
            writer.writerow(
                [
                    "chat_name",
                    "sender",
                    "excerpt",
                    "score",
                    "trigger",
                    "sent_to",
                    "created_at",
                ]
            )
        else:
            writer.writerow(
                [
                    "Channel",
                    "Sender",
                    "Excerpt",
                    "Score",
                    "Trigger",
                    "Destination",
                    "Timestamp",
                ]
            )

        for alert in alerts:
            writer.writerow(
                [
                    alert.get("chat_name", ""),
                    alert.get("sender", ""),
                    alert.get("excerpt", ""),
                    alert.get("score", 0.0),
                    alert.get("trigger", ""),
                    alert.get("sent_to", ""),
                    alert.get("created_at", ""),
                ]
            )

        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="tgsentinel_alerts_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.csv"'
        )
        return response

    except Exception as exc:
        logger.error("Failed to export alerts: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500
