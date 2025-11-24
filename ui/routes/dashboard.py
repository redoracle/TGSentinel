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
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
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


@dashboard_bp.route("/api/sentinel/stats", methods=["GET"])
def sentinel_stats_proxy():
    """Proxy for Sentinel /api/stats endpoint."""
    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        # Forward query parameters if any
        hours = min(max(request.args.get("hours", default=24, type=int), 1), 168)
        response = requests.get(
            f"{sentinel_api_url}/stats", params={"hours": hours}, timeout=5
        )

        if not response.ok:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Sentinel API error: {response.status_code}",
                    }
                ),
                response.status_code,
            )

        # Return the response from Sentinel, but guard against non-JSON or invalid JSON bodies
        content_type = response.headers.get("Content-Type", "")
        media_type = content_type.split(";")[0].strip().lower()
        if media_type == "application/json" or media_type.endswith("+json"):
            try:
                data = response.json()
                return jsonify(data)
            except Exception as parse_exc:  # catch JSONDecodeError or ValueError
                logger.error(
                    "Failed to parse JSON from Sentinel stats response: %s", parse_exc
                )
                # Fall back to raw text body with original status and content-type
                raw = response.text
                resp = make_response(raw, response.status_code)
                resp.headers["Content-Type"] = content_type or "text/plain"
                return resp
        else:
            # Non-JSON response: return raw body preserving status and content-type
            raw = response.text
            resp = make_response(raw, response.status_code)
            resp.headers["Content-Type"] = content_type or "text/plain"
            return resp

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch stats from Sentinel: {e}")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Failed to communicate with Sentinel: {str(e)}",
                }
            ),
            503,
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
                error_data = {}
                if media_type == "application/json" or media_type.endswith("+json"):
                    try:
                        error_data = response.json() or {}
                    except Exception as parse_exc:
                        logger.debug(
                            "Failed to parse JSON from Sentinel error response: %s",
                            parse_exc,
                        )
                        error_data = {}
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

            # Safely parse JSON from Sentinel
            config_data = {}
            content_type = response.headers.get("Content-Type", "")
            media_type = content_type.split(";")[0].strip().lower()
            if media_type == "application/json" or media_type.endswith("+json"):
                try:
                    parsed = response.json()
                    if isinstance(parsed, dict):
                        # parsed should be a dict with a top-level "data" key
                        config_data = parsed.get("data", {}) or {}
                    else:
                        logger.debug(
                            "Sentinel config response JSON was not an object, type=%s",
                            type(parsed),
                        )
                        config_data = {}
                except Exception as parse_exc:
                    logger.debug(
                        "Failed to parse JSON from Sentinel config response: %s",
                        parse_exc,
                    )
                    config_data = {}

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
    """Get real-time analytics metrics.

    Proxies performance metrics to Sentinel API which owns the database.
    """
    try:
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )
        hours = request.args.get("hours", default=2, type=int)
        interval_minutes = request.args.get("interval_minutes", default=2, type=int)

        response = requests.get(
            f"{sentinel_api_url}/analytics/metrics",
            params={"hours": hours, "interval_minutes": interval_minutes},
            timeout=5,
        )

        if response.ok:
            try:
                data = response.json()
                if data.get("status") == "ok":
                    metrics = data.get("data", {}).get("metrics", [])
                    return jsonify({"metrics": metrics})
            except (json.JSONDecodeError, ValueError) as parse_error:
                logger.error(
                    f"Failed to parse JSON from Sentinel metrics API: {parse_error}. "
                    f"Response content: {response.text[:200]}"
                )
                return jsonify({"metrics": []})

        logger.warning(f"Sentinel metrics API returned status {response.status_code}")
        return jsonify({"metrics": []})

    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to fetch performance metrics from Sentinel: {exc}")
        return jsonify({"metrics": []})


@dashboard_bp.route("/analytics/keywords", methods=["GET"])
def analytics_keywords():
    """Return keyword match counts from recent messages.

    Proxies request to Sentinel API which owns the database.
    """
    try:
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )
        hours = request.args.get("hours", default=24, type=int)

        response = requests.get(
            f"{sentinel_api_url}/analytics/keywords",
            params={"hours": hours},
            timeout=5,
        )

        if response.ok:
            try:
                data = response.json()
                if data.get("status") == "ok":
                    keywords = data.get("data", {}).get("keywords", [])
                    return jsonify({"keywords": keywords})
            except (json.JSONDecodeError, ValueError) as json_exc:
                logger.error(
                    f"Failed to parse JSON from Sentinel keywords API: {json_exc}. "
                    f"Response content: {response.text[:200]}"
                )
                return jsonify({"keywords": []})

        logger.warning(f"Sentinel keywords API returned status {response.status_code}")
        return jsonify({"keywords": []})

    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to fetch keyword analytics from Sentinel: {exc}")
        return jsonify({"keywords": []})


@dashboard_bp.route("/analytics/channels", methods=["GET"])
def analytics_channels():
    """Return alert counts per channel from the last 24 hours.

    Proxies request to Sentinel API which owns the database.
    """
    try:
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )
        hours = request.args.get("hours", default=24, type=int)

        response = requests.get(
            f"{sentinel_api_url}/analytics/channels",
            params={"hours": hours},
            timeout=5,
        )

        if response.ok:
            try:
                data = response.json()
                if data.get("status") == "ok":
                    channels = data.get("data", {}).get("channels", [])
                    # Normalize format for UI
                    return jsonify(
                        {
                            "channels": [
                                {"channel": ch["channel"], "count": ch["alerts"]}
                                for ch in channels
                            ]
                        }
                    )
            except (json.JSONDecodeError, ValueError) as parse_error:
                logger.error(
                    f"Failed to parse JSON from Sentinel channels API: {parse_error}. "
                    f"Response content: {response.text[:200]}"
                )
                return jsonify({"channels": []})

        logger.warning(f"Sentinel channels API returned status {response.status_code}")
        return jsonify({"channels": []})

    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to fetch channel analytics from Sentinel: {exc}")
        return jsonify({"channels": []})


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
