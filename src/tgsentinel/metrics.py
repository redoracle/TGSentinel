"""Prometheus metrics for TG Sentinel.

Provides application-level metrics for monitoring:
- Message processing rates
- Alert generation
- Database operations
- API requests
- Worker health
"""

import logging
import os
from prometheus_client import Counter, Gauge, Histogram, Info

log = logging.getLogger(__name__)

# Version and build metadata (read from environment variables set by CI/Docker build)
VERSION = os.getenv("APP_VERSION", "dev")
BUILD_DATE = os.getenv("BUILD_DATE", "unknown")
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")

# Message processing metrics
messages_ingested_total = Counter(
    "tgsentinel_messages_ingested_total",
    "Total number of messages ingested from Telegram",
)

messages_processed_total = Counter(
    "tgsentinel_messages_processed_total",
    "Total number of messages processed through scoring pipeline",
    ["status"],  # success, error, filtered
)

# Alert metrics
alerts_generated_total = Counter(
    "tgsentinel_alerts_generated_total",
    "Total number of alerts generated",
    ["channel", "trigger_type"],  # dm, channel, both / keyword, vip, score
)

alerts_sent_total = Counter(
    "tgsentinel_alerts_sent_total",
    "Total number of alerts successfully sent",
    ["destination"],  # dm, channel
)

# Scoring metrics
message_score_histogram = Histogram(
    "tgsentinel_message_score",
    "Distribution of message importance scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

semantic_inference_duration = Histogram(
    "tgsentinel_semantic_inference_seconds",
    "Time spent on semantic model inference",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# Database metrics
db_messages_current = Gauge(
    "tgsentinel_db_messages_current",
    "Current number of messages in database",
)

db_cleanup_duration = Histogram(
    "tgsentinel_db_cleanup_seconds",
    "Time spent on database cleanup operations",
)

db_vacuum_duration = Histogram(
    "tgsentinel_db_vacuum_seconds",
    "Time spent on VACUUM operations",
)

db_messages_deleted_total = Counter(
    "tgsentinel_db_messages_deleted_total",
    "Total number of messages deleted by cleanup",
    ["reason"],  # age, count_limit
)

# API metrics
api_requests_total = Counter(
    "tgsentinel_api_requests_total",
    "Total number of API requests",
    ["method", "endpoint", "status_code"],
)

api_request_duration = Histogram(
    "tgsentinel_api_request_seconds",
    "API request duration",
    ["method", "endpoint"],
)

# Worker health metrics
worker_authorized = Gauge(
    "tgsentinel_worker_authorized",
    "Whether the Telegram worker is authorized (1=yes, 0=no)",
)

worker_connected = Gauge(
    "tgsentinel_worker_connected",
    "Whether the Telegram worker is connected (1=yes, 0=no)",
)

redis_stream_depth = Gauge(
    "tgsentinel_redis_stream_depth",
    "Current depth of Redis message stream",
)

# Feedback metrics
feedback_submitted_total = Counter(
    "tgsentinel_feedback_submitted_total",
    "Total feedback submissions",
    ["label"],  # 1 (positive), 0 (negative)
)

# System info
sentinel_info = Info(
    "tgsentinel_build",
    "TG Sentinel build and version information",
)


def initialize_build_info():
    """Initialize build information metric.

    Should be called once at application startup to populate the sentinel_info
    metric with version, build date, and commit information.
    """
    sentinel_info.info(
        {
            "version": VERSION,
            "build_date": BUILD_DATE,
            "git_commit": GIT_COMMIT,
            "python_version": f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}.{__import__('sys').version_info.micro}",
        }
    )
    log.info(
        f"Initialized build info: version={VERSION}, build_date={BUILD_DATE}, commit={GIT_COMMIT}"
    )


# Legacy compatibility: maintain simple inc/dump interface
def inc(name: str, **labels):
    """Increment a counter metric (legacy interface).

    Maps to appropriate Prometheus metric based on name.

    Note: chat_id and chat_name labels are no longer supported for messages_ingested
    to avoid high-cardinality issues. Per-chat tracking should be done outside Prometheus.
    """
    # Map legacy metric names to Prometheus metrics
    if name == "messages_ingested":
        # Increment global counter without per-chat labels
        messages_ingested_total.inc()
    elif name == "messages_processed":
        status = labels.get("status", "success")
        messages_processed_total.labels(status=status).inc()
    elif name == "alerts_generated":
        channel = labels.get("channel", "unknown")
        trigger_type = labels.get("trigger_type", "score")
        alerts_generated_total.labels(channel=channel, trigger_type=trigger_type).inc()
    elif name == "alerts_sent":
        destination = labels.get("destination", "dm")
        alerts_sent_total.labels(destination=destination).inc()
    elif name == "feedback_submitted":
        label = labels.get("label", "1")
        feedback_submitted_total.labels(label=str(label)).inc()
    else:
        # Unknown metric, log for debugging
        log.debug(f"Unknown metric: {name} with labels {labels}")


def dump():
    """Dump metrics to logs (legacy interface, now no-op).

    Prometheus metrics are automatically exposed via /metrics endpoint.
    """
    log.debug("Metrics dump called (Prometheus metrics auto-exported via /metrics)")
