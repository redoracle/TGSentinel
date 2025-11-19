"""
Analytics API Routes Blueprint

Handles anomaly detection and diagnostic exports for the TG Sentinel UI.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from flask import Blueprint, jsonify, make_response, request

logger = logging.getLogger(__name__)

# Create blueprint
analytics_bp = Blueprint("analytics", __name__, url_prefix="/api")

# Dependencies (injected during registration)
_query_all: Callable | None = None
_compute_summary: Callable | None = None
_compute_health: Callable | None = None
_serialize_channels: Callable | None = None
_load_alerts: Callable | None = None
_load_live_feed: Callable | None = None
config = None
redis_client = None
socketio = None


def init_blueprint(
    query_all: Callable,
    compute_summary: Callable,
    compute_health: Callable,
    serialize_channels: Callable,
    load_alerts: Callable,
    load_live_feed: Callable,
    config_obj: Any,
    redis_obj: Any,
    socketio_obj: Any,
    ensure_init_decorator: Callable,
) -> None:
    """Initialize blueprint with dependencies."""
    global _query_all, _compute_summary, _compute_health, _serialize_channels
    global _load_alerts, _load_live_feed, config, redis_client, socketio

    _query_all = query_all
    _compute_summary = compute_summary
    _compute_health = compute_health
    _serialize_channels = serialize_channels
    _load_alerts = load_alerts
    _load_live_feed = load_live_feed
    config = config_obj
    redis_client = redis_obj
    socketio = socketio_obj


@analytics_bp.get("/analytics/anomalies")
def api_analytics_anomalies():
    """Detect and return anomalous patterns in channel activity."""
    try:
        # Load configurable thresholds from environment with sensible defaults
        volume_threshold = float(os.getenv("ANOMALY_VOLUME_THRESHOLD", "3.0"))
        importance_threshold = float(os.getenv("ANOMALY_IMPORTANCE_THRESHOLD", "2.0"))
        alert_rate_threshold = float(os.getenv("ANOMALY_ALERT_RATE", "0.5"))  # 50%

        # Standard deviation mode configuration
        use_stddev = os.getenv("ANOMALY_USE_STDDEV", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        stddev_multiplier = float(os.getenv("ANOMALY_STDDEV_MULTIPLIER", "2.0"))

        # Get recent activity from last 24 hours
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Query message statistics per channel (using chat_id from database)
        app_module = sys.modules.get("ui.app") or sys.modules.get("app")
        query_fn = _query_all
        if app_module and hasattr(app_module, "_query_all"):
            query_fn = getattr(app_module, "_query_all")

        if not query_fn:
            return (
                jsonify({"status": "error", "message": "Database not initialized"}),
                503,
            )

        channel_stats = query_fn(
            """
            SELECT 
                chat_id,
                COUNT(*) as msg_count,
                AVG(score) as avg_score,
                MAX(score) as max_score,
                COUNT(CASE WHEN alerted = 1 THEN 1 END) as alert_count
            FROM messages 
            WHERE datetime(created_at) >= :cutoff
            GROUP BY chat_id
            HAVING msg_count > 0
            """,
            cutoff=cutoff,
        )

        anomalies = []

        # Calculate overall statistics
        if channel_stats:
            all_counts = [s["msg_count"] for s in channel_stats]
            all_scores = [s["avg_score"] for s in channel_stats if s["avg_score"]]

            avg_msg_count = sum(all_counts) / len(all_counts) if all_counts else 0
            avg_score = sum(all_scores) / len(all_scores) if all_scores else 0

            # Calculate standard deviation if enabled
            volume_std_dev = 0
            importance_std_dev = 0
            if use_stddev and len(all_counts) > 1:
                # Calculate standard deviation for message counts
                variance_counts = sum(
                    (x - avg_msg_count) ** 2 for x in all_counts
                ) / len(all_counts)
                volume_std_dev = variance_counts**0.5

                # Calculate standard deviation for importance scores
                if len(all_scores) > 1:
                    variance_scores = sum(
                        (x - avg_score) ** 2 for x in all_scores
                    ) / len(all_scores)
                    importance_std_dev = variance_scores**0.5

            # Detect anomalies
            for stat in channel_stats:
                chat_id = stat["chat_id"]
                msg_count = stat["msg_count"]
                avg_ch_score = stat["avg_score"] or 0
                alert_count = stat["alert_count"]

                # Convert chat_id to readable name
                channel_name = f"Chat {chat_id}"

                # Anomaly 1: Unusual message volume
                if use_stddev and volume_std_dev > 0:
                    # Standard deviation mode
                    threshold = avg_msg_count + (stddev_multiplier * volume_std_dev)
                    if msg_count > threshold:
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High volume: {msg_count} messages (avg: {int(avg_msg_count)}, σ: {volume_std_dev:.1f})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "volume_spike",
                            }
                        )
                else:
                    # Multiplier mode
                    if (
                        avg_msg_count > 0
                        and msg_count > avg_msg_count * volume_threshold
                    ):
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High volume: {msg_count} messages (avg: {int(avg_msg_count)})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "volume_spike",
                            }
                        )

                # Anomaly 2: Unusual importance scores
                if use_stddev and importance_std_dev > 0:
                    # Standard deviation mode
                    threshold = avg_score + (stddev_multiplier * importance_std_dev)
                    if avg_ch_score > threshold:
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High importance: {avg_ch_score:.2f} (avg: {avg_score:.2f}, σ: {importance_std_dev:.2f})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "importance_spike",
                            }
                        )
                else:
                    # Multiplier mode
                    if (
                        avg_score > 0
                        and avg_ch_score > avg_score * importance_threshold
                    ):
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High importance: {avg_ch_score:.2f} (avg: {avg_score:.2f})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "importance_spike",
                            }
                        )

                # Anomaly 3: High alert rate
                if msg_count > 5:
                    alert_rate = alert_count / msg_count
                    if alert_rate > alert_rate_threshold:
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"Alert rate: {alert_count}/{msg_count} ({int(alert_rate*100)}%)",
                                "severity": "info",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "alert_rate",
                            }
                        )

        return jsonify({"anomalies": anomalies})

    except Exception as exc:
        logger.error(f"Failed to detect anomalies: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@analytics_bp.get("/console/diagnostics")
def api_export_diagnostics():
    """Export anonymized system diagnostics for support/debugging."""
    try:
        # Check dependencies
        if not all(
            [
                _compute_summary,
                _compute_health,
                _serialize_channels,
                _load_alerts,
                _load_live_feed,
            ]
        ):
            return (
                jsonify({"status": "error", "message": "Service not initialized"}),
                503,
            )

        # Collect diagnostic information
        diagnostics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "summary": _compute_summary(),  # type: ignore
            "health": _compute_health(),  # type: ignore
            "channels": {
                "count": len(_serialize_channels()),  # type: ignore
                "channels": [
                    {
                        "id": ch.get("id"),
                        "name": ch.get("name"),
                        "keywords_count": len(ch.get("keywords", [])),
                        "vip_count": len(ch.get("vip_senders", [])),
                        "enabled": ch.get("enabled", True),
                    }
                    for ch in _serialize_channels()  # type: ignore
                ],
            },
            "alerts": {
                "total": len(_load_alerts(limit=100)),  # type: ignore
                "recent_sample": [
                    {
                        "score": a.get("score"),
                        "trigger": a.get("trigger"),
                        "created_at": a.get("created_at"),
                    }
                    for a in _load_alerts(limit=10)  # type: ignore
                ],
            },
            "activity": {
                "recent_count": len(_load_live_feed(limit=50)),  # type: ignore
            },
            "config": {
                "has_config": config is not None,
                "has_redis": redis_client is not None,
            },
        }

        # Create JSON response with download headers
        response = make_response(json.dumps(diagnostics, indent=2))
        response.headers["Content-Type"] = "application/json"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="tgsentinel_diagnostics_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.json"'
        )
        return response

    except Exception as exc:
        logger.error(f"Failed to generate diagnostics: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500
