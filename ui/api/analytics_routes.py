"""
Analytics API Routes Blueprint

Handles anomaly detection, diagnostic exports, and container health monitoring for the TG Sentinel UI.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests
from flask import Blueprint, jsonify, make_response, request
from prometheus_client.parser import text_string_to_metric_families

try:
    from docker.errors import DockerException  # type: ignore[import-not-found]

    import docker  # type: ignore[import-not-found]

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None  # type: ignore[assignment]
    DockerException = Exception  # type: ignore[misc,assignment]

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
                COUNT(CASE WHEN flagged_for_alerts_feed = 1 OR flagged_for_interest_feed = 1 THEN 1 END) as alert_count
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
                                "signal": (
                                    f"High volume: {msg_count} messages "
                                    f"(avg: {int(avg_msg_count)}, σ: {volume_std_dev:.1f})"
                                ),
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
                                "signal": (
                                    f"High importance: {avg_ch_score:.2f} "
                                    f"(avg: {avg_score:.2f}, σ: {importance_std_dev:.2f})"
                                ),
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


@analytics_bp.route("/analytics/channels", methods=["GET"])
def api_analytics_channels():
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

        logger.warning(f"Sentinel channels API returned status {response.status_code}")
        return jsonify({"channels": []})

    except requests.exceptions.RequestException as exc:
        logger.error(f"Failed to fetch channel analytics from Sentinel: {exc}")
        return jsonify({"channels": []})


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


def get_docker_client():
    """Get Docker client instance with error handling."""
    if not DOCKER_AVAILABLE:
        logger.warning(
            "Docker SDK not available - container health monitoring disabled"
        )
        return None
    try:
        return docker.from_env()  # type: ignore[union-attr]
    except DockerException as e:
        logger.error(f"Failed to connect to Docker daemon: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error connecting to Docker: {e}")
        return None


def calculate_cpu_percent(stats):
    """
    Calculate CPU percentage from Docker stats.

    Formula from Docker docs:
    cpu_delta = cpu_stats.cpu_usage.total_usage - precpu_stats.cpu_usage.total_usage
    system_cpu_delta = cpu_stats.system_cpu_usage - precpu_stats.system_cpu_usage
    cpu_percent = (cpu_delta / system_cpu_delta) * number_cpus * 100.0
    """
    try:
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        cpu_usage = cpu_stats.get("cpu_usage", {})
        precpu_usage = precpu_stats.get("cpu_usage", {})

        cpu_delta = cpu_usage.get("total_usage", 0) - precpu_usage.get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
            "system_cpu_usage", 0
        )

        online_cpus = cpu_stats.get(
            "online_cpus", len(cpu_usage.get("percpu_usage", [1]))
        )

        if system_delta > 0 and cpu_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
            return round(cpu_percent, 1)
        return 0.0
    except (KeyError, TypeError, ZeroDivisionError) as e:
        logger.warning(f"Error calculating CPU percentage: {e}")
        return 0.0


def format_uptime(started_at_str):
    """Convert container start time to human-readable uptime."""
    try:
        # Parse ISO 8601 timestamp from Docker
        started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
        now = datetime.now(started_at.tzinfo)
        uptime_seconds = (now - started_at).total_seconds()

        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    except (ValueError, AttributeError) as e:
        logger.warning(f"Error formatting uptime: {e}")
        return "N/A"


@analytics_bp.route("/analytics/containers", methods=["GET"])
def get_container_health():
    """
    Get health status for all TG Sentinel containers.

    Returns:
        JSON with container status, resource usage, uptime, and restart count
    """
    client = get_docker_client()
    if not client:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Cannot connect to Docker daemon",
                    "containers": [],
                }
            ),
            503,
        )

    try:
        # Filter containers by project name
        containers = client.containers.list(all=True, filters={"name": "tgsentinel"})

        container_data = []

        for container in containers:
            try:
                # Get real-time stats (non-streaming)
                stats = container.stats(stream=False)

                # Parse container info
                attrs = container.attrs
                state = attrs.get("State", {})

                # Calculate memory usage in MB
                memory_stats = stats.get("memory_stats", {})
                memory_usage = memory_stats.get("usage", 0)
                memory_limit = memory_stats.get("limit", 1)
                memory_mb = round(memory_usage / 1024 / 1024, 1)
                memory_limit_mb = round(memory_limit / 1024 / 1024, 1)
                memory_percent = (
                    round((memory_usage / memory_limit) * 100, 1)
                    if memory_limit > 0
                    else 0.0
                )

                # Network I/O
                networks = stats.get("networks", {})
                rx_bytes = sum(net.get("rx_bytes", 0) for net in networks.values())
                tx_bytes = sum(net.get("tx_bytes", 0) for net in networks.values())

                # Build container info
                container_info = {
                    "name": container.name,
                    "short_name": container.name.replace("tgsentinel-", "").replace(
                        "-1", ""
                    ),
                    "status": state.get("Status", "unknown"),
                    "running": state.get("Running", False),
                    "uptime_seconds": 0,
                    "uptime_display": "N/A",
                    "restarts": state.get("RestartCount", 0),
                    "cpu_percent": calculate_cpu_percent(stats),
                    "memory_mb": memory_mb,
                    "memory_limit_mb": memory_limit_mb,
                    "memory_percent": memory_percent,
                    "network_rx_bytes": rx_bytes,
                    "network_tx_bytes": tx_bytes,
                }

                # Format uptime if running
                if state.get("Running"):
                    started_at = state.get("StartedAt")
                    if started_at:
                        container_info["uptime_display"] = format_uptime(started_at)
                        try:
                            started = datetime.fromisoformat(
                                started_at.replace("Z", "+00:00")
                            )
                            now = datetime.now(started.tzinfo)
                            container_info["uptime_seconds"] = int(
                                (now - started).total_seconds()
                            )
                        except (ValueError, AttributeError):
                            pass

                container_data.append(container_info)

            except Exception as e:
                logger.error(f"Error processing container {container.name}: {e}")
                # Add minimal info for failed container
                container_data.append(
                    {
                        "name": container.name,
                        "short_name": container.name.replace("tgsentinel-", "").replace(
                            "-1", ""
                        ),
                        "status": "error",
                        "running": False,
                        "error": str(e),
                    }
                )

        # Sort by name for consistent ordering
        container_data.sort(key=lambda x: x["name"])

        return jsonify(
            {
                "status": "ok",
                "containers": container_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "count": len(container_data),
            }
        )

    except DockerException as e:
        logger.error(f"Docker API error: {e}")
        return jsonify({"status": "error", "message": str(e), "containers": []}), 500
    finally:
        try:
            client.close()
        except Exception:
            pass


@analytics_bp.route("/analytics/endpoints", methods=["GET"])
def get_endpoint_health():
    """
    Check health status of all TG Sentinel API endpoints.

    Monitors:
    - Sentinel API (port 8080): /metrics, /api/status, /api/alerts
    - UI API (port 5000): /health, /api/worker/status
    - Redis (port 6379): Connection via PING

    Returns:
        JSON with endpoint statuses, latencies, and uptime percentages
    """
    sentinel_base_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080")
    # UI listens on port 5000 internally within the container
    ui_base_url = "http://127.0.0.1:5000"

    endpoints_to_check = [
        {
            "group": "Sentinel API",
            "port": 8080,
            "base_url": sentinel_base_url,
            "paths": [
                {"path": "/metrics", "timeout": 5},
                {"path": "/api/status", "timeout": 3},
                {"path": "/api/alerts", "timeout": 5},
            ],
        },
        {
            "group": "UI API",
            "port": 5000,  # Internal container port
            "base_url": ui_base_url,
            "paths": [
                {"path": "/health", "timeout": 2, "auth_required": False},
                {"path": "/api/worker/status", "timeout": 3, "auth_required": True},
            ],
        },
    ]

    results = []

    # Check HTTP endpoints
    for group_config in endpoints_to_check:
        group_name = group_config["group"]
        base_url = group_config["base_url"]
        port = group_config["port"]

        group_online = True
        group_latencies = []
        endpoint_details = []

        for endpoint_config in group_config["paths"]:
            path = endpoint_config["path"]
            timeout = endpoint_config["timeout"]
            auth_required = endpoint_config.get("auth_required", False)
            full_url = f"{base_url}{path}"

            try:
                start_time = time.time()
                # Add session cookie if authentication is required
                if auth_required and request.cookies.get("session"):
                    session_cookie = request.cookies.get("session")
                    response = requests.get(
                        full_url,
                        timeout=timeout,
                        cookies={"session": session_cookie} if session_cookie else None,
                    )
                else:
                    response = requests.get(full_url, timeout=timeout)
                latency_ms = round((time.time() - start_time) * 1000, 1)

                online = response.status_code < 500
                group_latencies.append(latency_ms)

                endpoint_details.append(
                    {
                        "path": path,
                        "online": online,
                        "status_code": response.status_code,
                        "latency_ms": latency_ms,
                    }
                )

                if not online:
                    group_online = False

            except requests.Timeout:
                endpoint_details.append(
                    {
                        "path": path,
                        "online": False,
                        "status_code": 0,
                        "latency_ms": timeout * 1000,
                        "error": "Timeout",
                    }
                )
                group_online = False

            except requests.RequestException as e:
                endpoint_details.append(
                    {
                        "path": path,
                        "online": False,
                        "status_code": 0,
                        "latency_ms": 0,
                        "error": str(e),
                    }
                )
                group_online = False

        avg_latency = (
            round(sum(group_latencies) / len(group_latencies), 1)
            if group_latencies
            else 0
        )

        results.append(
            {
                "group": group_name,
                "port": port,
                "online": group_online,
                "avg_latency_ms": avg_latency,
                "endpoints": endpoint_details,
            }
        )

    # Check Redis connection
    redis_online = False
    redis_latency = 0

    if redis_client:
        try:
            start_time = time.time()
            redis_client.ping()
            redis_latency = round((time.time() - start_time) * 1000, 1)
            redis_online = True
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")

    results.append(
        {
            "group": "Redis",
            "port": 6379,
            "online": redis_online,
            "avg_latency_ms": redis_latency,
            "endpoints": [
                {
                    "path": "PING",
                    "online": redis_online,
                    "latency_ms": redis_latency,
                }
            ],
        }
    )

    # Calculate overall health
    total_endpoints = sum(len(r["endpoints"]) for r in results)
    online_endpoints = sum(
        sum(1 for e in r["endpoints"] if e.get("online", False)) for r in results
    )

    return jsonify(
        {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_endpoints": total_endpoints,
                "online_endpoints": online_endpoints,
                "online_percentage": (
                    round((online_endpoints / total_endpoints) * 100, 1)
                    if total_endpoints > 0
                    else 0
                ),
            },
            "groups": results,
        }
    )


@analytics_bp.route("/analytics/prometheus", methods=["GET"])
def get_prometheus_metrics():
    """
    Parse and return key Prometheus metrics from Sentinel service.

    Extracts metrics like:
    - worker_authorized
    - worker_connected
    - redis_stream_depth
    - messages_processed_total
    - api_requests_total
    - db_messages_current

    Returns:
        JSON with parsed metrics and their current values
    """
    sentinel_metrics_url = os.getenv(
        "SENTINEL_METRICS_URL", "http://sentinel:8080/metrics"
    )

    try:
        response = requests.get(sentinel_metrics_url, timeout=5)
        response.raise_for_status()

        metrics_text = response.text

        # Log for debugging
        logger.debug(
            f"Fetched {len(metrics_text)} bytes from Prometheus metrics endpoint"
        )
        if not metrics_text or len(metrics_text) < 10:
            logger.warning(
                "Prometheus metrics endpoint returned empty or very short response"
            )

        parsed_metrics = {}

        # Define metrics we want to extract (include tgsentinel_ prefix and common Python metrics)
        target_metrics = [
            "tgsentinel_",  # All TG Sentinel custom metrics
            "process_",  # Process metrics (CPU, memory, etc.)
            "python_",  # Python runtime metrics
        ]

        # Use prometheus_client parser for robust metric parsing
        try:
            for family in text_string_to_metric_families(metrics_text):
                # Check if this is a metric family we care about
                metric_name = family.name
                is_target_metric = any(
                    metric_name.startswith(target) for target in target_metrics
                )

                if not is_target_metric:
                    continue

                if metric_name not in parsed_metrics:
                    parsed_metrics[metric_name] = []

                # Extract samples from the metric family
                for sample in family.samples:
                    try:
                        metric_entry: dict[str, Any] = {"value": float(sample.value)}

                        # Add labels if present
                        if sample.labels:
                            metric_entry["labels"] = dict(sample.labels)

                        parsed_metrics[metric_name].append(metric_entry)
                    except (ValueError, AttributeError) as e:
                        logger.debug(f"Error parsing sample {sample.name}: {e}")
                        continue

        except Exception as parse_error:
            logger.error(f"Error parsing Prometheus metrics with parser: {parse_error}")
            # Fall back to empty metrics on parser failure
            parsed_metrics = {}

        # Calculate aggregates for multi-value metrics
        aggregated_metrics = {}

        for metric_name, values in parsed_metrics.items():
            if len(values) == 1 and "labels" not in values[0]:
                # Simple metric with single value
                aggregated_metrics[metric_name] = {
                    "value": values[0]["value"],
                    "type": "gauge",
                }
            else:
                # Multiple values or labeled metric
                total = sum(v["value"] for v in values)
                aggregated_metrics[metric_name] = {
                    "value": total,
                    "count": len(values),
                    "type": "counter" if "total" in metric_name else "gauge",
                    "details": values,
                }

        return jsonify(
            {
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": aggregated_metrics,
                "raw_count": len(parsed_metrics),
            }
        )

    except requests.Timeout:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Timeout connecting to Prometheus metrics endpoint",
                    "metrics": {},
                }
            ),
            504,
        )

    except requests.ConnectionError as e:
        # Connection refused typically means Sentinel is starting up
        logger.warning(
            f"Connection error fetching Prometheus metrics (Sentinel may be starting): {e}"
        )
        return (
            jsonify(
                {
                    "status": "initializing",
                    "message": "Sentinel service is starting up. Metrics will be available shortly.",
                    "metrics": {},
                }
            ),
            503,
        )

    except requests.RequestException as e:
        logger.error(f"Error fetching Prometheus metrics: {e}")
        # Check if it's a 503 status code response (service unavailable during startup)
        if (
            hasattr(e, "response")
            and e.response is not None
            and e.response.status_code == 503
        ):
            return (
                jsonify(
                    {
                        "status": "initializing",
                        "message": "Sentinel service is starting up. Metrics will be available shortly.",
                        "metrics": {},
                    }
                ),
                503,
            )
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Failed to fetch metrics: {str(e)}",
                    "metrics": {},
                }
            ),
            503,
        )


@analytics_bp.route("/analytics/system-health", methods=["GET"])
def get_system_health():
    """
    Calculate and return overall system health score (0-100).

    Health score calculation:
    - Container health: 30% (all containers running, low restarts)
    - API health: 25% (all endpoints online, low latency)
    - Resource usage: 25% (CPU/memory within thresholds)
    - Worker status: 20% (authorized, connected, processing messages)

    Returns:
        JSON with health score, component scores, and recommendations
    """
    health_components = {}
    total_score = 0.0

    # 1. Container Health (30 points)
    container_score = 0.0
    client = None
    try:
        # Reuse container health endpoint logic
        client = get_docker_client()

        if client and DOCKER_AVAILABLE:
            try:
                containers = client.containers.list(
                    all=True, filters={"name": "tgsentinel"}
                )
                if containers:
                    running_count = sum(
                        1 for c in containers if c.attrs.get("State", {}).get("Running")
                    )
                    total_count = len(containers)
                    running_ratio = (
                        running_count / total_count if total_count > 0 else 0
                    )

                    # Check restart counts
                    restart_counts = [
                        c.attrs.get("State", {}).get("RestartCount", 0)
                        for c in containers
                    ]
                    avg_restarts = (
                        sum(restart_counts) / len(restart_counts)
                        if restart_counts
                        else 0
                    )

                    # Score calculation
                    container_score = running_ratio * 30  # Up to 30 points
                    if avg_restarts > 5:
                        container_score -= 5  # Penalty for frequent restarts
                    elif avg_restarts > 2:
                        container_score -= 2

                    health_components["containers"] = {
                        "score": round(container_score, 1),
                        "max_score": 30,
                        "details": {
                            "running": running_count,
                            "total": total_count,
                            "avg_restarts": round(avg_restarts, 1),
                        },
                    }
            except Exception as e:
                logger.error(f"Error calculating container health: {e}")
                health_components["containers"] = {
                    "score": 0,
                    "max_score": 30,
                    "details": {"error": str(e)},
                }
                container_score = 0
        else:
            health_components["containers"] = {
                "score": 0,
                "max_score": 30,
                "details": {"error": "Docker unavailable"},
            }

        total_score += container_score

    except Exception as e:
        logger.error(f"Error initializing Docker client for container health: {e}")
        health_components["containers"] = {
            "score": 0,
            "max_score": 30,
            "details": {"error": str(e)},
        }
    finally:
        # Always close Docker client if it was created
        if client:
            try:
                client.close()
            except Exception as close_error:
                logger.warning(f"Error closing Docker client: {close_error}")

    # 2. API Endpoints Health (25 points)
    try:
        sentinel_base_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080")
        api_score = 0.0
        endpoints_checked = 0
        endpoints_online = 0

        # Check key endpoints
        critical_endpoints = [
            f"{sentinel_base_url}/api/status",
            f"{sentinel_base_url}/metrics",
        ]

        for endpoint in critical_endpoints:
            endpoints_checked += 1
            try:
                response = requests.get(endpoint, timeout=3)
                if response.status_code < 500:
                    endpoints_online += 1
            except Exception:
                pass

        if endpoints_checked > 0:
            api_score = (endpoints_online / endpoints_checked) * 25

        health_components["api_endpoints"] = {
            "score": round(api_score, 1),
            "max_score": 25,
            "details": {"online": endpoints_online, "total": endpoints_checked},
        }

        total_score += api_score

    except Exception as e:
        logger.error(f"Error calculating API health: {e}")
        health_components["api_endpoints"] = {
            "score": 0,
            "max_score": 25,
            "details": {"error": str(e)},
        }

    # 3. Resource Usage (25 points)
    resource_score = 25.0  # Start optimistic
    client = None
    try:
        client = get_docker_client()

        if client and DOCKER_AVAILABLE:
            try:
                containers = client.containers.list(filters={"name": "tgsentinel"})
                cpu_values = []
                memory_values = []

                for container in containers:
                    try:
                        stats = container.stats(stream=False)
                        cpu_percent = calculate_cpu_percent(stats)
                        cpu_values.append(cpu_percent)

                        memory_stats = stats.get("memory_stats", {})
                        memory_usage = memory_stats.get("usage", 0)
                        memory_limit = memory_stats.get("limit", 1)
                        memory_percent = (
                            (memory_usage / memory_limit) * 100
                            if memory_limit > 0
                            else 0
                        )
                        memory_values.append(memory_percent)

                    except Exception:
                        pass

                # Apply penalties for high resource usage
                if cpu_values:
                    avg_cpu = sum(cpu_values) / len(cpu_values)
                    if avg_cpu > 85:
                        resource_score -= 10
                    elif avg_cpu > 70:
                        resource_score -= 5

                if memory_values:
                    avg_memory = sum(memory_values) / len(memory_values)
                    if avg_memory > 90:
                        resource_score -= 10
                    elif avg_memory > 80:
                        resource_score -= 5

                health_components["resources"] = {
                    "score": round(max(0, resource_score), 1),
                    "max_score": 25,
                    "details": {
                        "avg_cpu_percent": round(avg_cpu, 1) if cpu_values else 0,
                        "avg_memory_percent": (
                            round(avg_memory, 1) if memory_values else 0
                        ),
                    },
                }

            except Exception as e:
                logger.error(f"Error calculating resource health: {e}")
                health_components["resources"] = {
                    "score": 0,
                    "max_score": 25,
                    "details": {"error": str(e)},
                }
                resource_score = 0
        else:
            health_components["resources"] = {
                "score": 0,
                "max_score": 25,
                "details": {"error": "Docker unavailable"},
            }
            resource_score = 0

        total_score += max(0, resource_score)

    except Exception as e:
        logger.error(f"Error initializing Docker client for resource health: {e}")
        health_components["resources"] = {
            "score": 0,
            "max_score": 25,
            "details": {"error": str(e)},
        }
    finally:
        # Always close Docker client if it was created
        if client:
            try:
                client.close()
            except Exception as close_error:
                logger.warning(f"Error closing Docker client: {close_error}")

    # 4. Worker Status (20 points)
    try:
        worker_score = 0.0

        # Check Redis for worker status
        if redis_client:
            worker_status = redis_client.get("tgsentinel:worker_status")
            if worker_status:
                try:
                    status_data = json.loads(worker_status)
                    if status_data.get("authorized"):
                        worker_score += 15  # Authorized worker
                    # Accept both 'authorized' and 'ready' status as healthy
                    if status_data.get("status") in ["authorized", "ready"]:
                        worker_score += 5  # Additional points for ready status

                    health_components["worker"] = {
                        "score": round(worker_score, 1),
                        "max_score": 20,
                        "details": {
                            "authorized": status_data.get("authorized", False),
                            "status": status_data.get("status", "unknown"),
                        },
                    }
                except json.JSONDecodeError:
                    pass

        if "worker" not in health_components:
            health_components["worker"] = {
                "score": 0,
                "max_score": 20,
                "details": {"error": "Worker status unavailable"},
            }

        total_score += worker_score

    except Exception as e:
        logger.error(f"Error calculating worker health: {e}")
        health_components["worker"] = {
            "score": 0,
            "max_score": 20,
            "details": {"error": str(e)},
        }

    # Calculate final score and grade
    final_score = min(100, round(total_score, 1))

    if final_score >= 90:
        grade = "excellent"
        color = "success"
    elif final_score >= 75:
        grade = "good"
        color = "success"
    elif final_score >= 60:
        grade = "fair"
        color = "warning"
    elif final_score >= 40:
        grade = "poor"
        color = "warning"
    else:
        grade = "critical"
        color = "danger"

    # Generate recommendations
    recommendations = []
    if health_components.get("containers", {}).get("score", 0) < 20:
        recommendations.append(
            "Check container status - some containers are not running"
        )
    if health_components.get("api_endpoints", {}).get("score", 0) < 15:
        recommendations.append("API endpoints are not responding properly")
    if health_components.get("resources", {}).get("score", 0) < 15:
        recommendations.append(
            "Resource usage is high - consider scaling or optimization"
        )
    if health_components.get("worker", {}).get("score", 0) < 10:
        recommendations.append("Worker authorization or connection issues detected")

    return jsonify(
        {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "health_score": final_score,
            "grade": grade,
            "color": color,
            "components": health_components,
            "recommendations": recommendations,
        }
    )
