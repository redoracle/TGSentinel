"""Prometheus metrics for TG Sentinel.

Provides application-level metrics for monitoring:
- Message processing rates
- Alert generation
- Database operations
- API requests
- Worker health
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, Info

log = logging.getLogger(__name__)


_DEFAULT_VERSION = "dev"
_DEFAULT_BUILD_DATE = "unknown"
_DEFAULT_GIT_COMMIT = "unknown"

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_INFO_PATH = REPO_ROOT / "build_info.json"


def _load_build_info_file():
    """Read build metadata file created during container builds."""
    if not BUILD_INFO_PATH.exists():
        return {}
    try:
        return json.loads(BUILD_INFO_PATH.read_text())
    except Exception as exc:  # noqa: BLE001
        log.debug("Unable to read build info file (%s): %s", BUILD_INFO_PATH, exc)
        return {}


def _run_git_command(*args):
    """Run git command inside repository root, if available."""
    try:
        return subprocess.run(
            ("git",) + args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:  # noqa: BLE001
        log.debug("Git command failed (%s): %s", args, exc)
        return ""


def _load_git_metadata():
    """Fallback to git metadata when env/file values are missing."""
    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        return {}
    metadata = {}
    commit = _run_git_command("rev-parse", "HEAD")
    if commit:
        metadata["git_commit"] = commit
    commit_date = _run_git_command("show", "-s", "--format=%cI", "HEAD")
    if commit_date:
        metadata["build_date"] = commit_date
    return metadata


def _utc_iso_now():
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_build_metadata():
    """Consolidate env, file, and git metadata into single source."""
    metadata = {
        "version": os.getenv("APP_VERSION", _DEFAULT_VERSION),
        "build_date": os.getenv("BUILD_DATE", _DEFAULT_BUILD_DATE),
        "git_commit": os.getenv("GIT_COMMIT", _DEFAULT_GIT_COMMIT),
    }

    file_data = _load_build_info_file()
    if file_data:
        if metadata["version"] == _DEFAULT_VERSION:
            metadata["version"] = file_data.get("app_version", metadata["version"])
        if metadata["build_date"] == _DEFAULT_BUILD_DATE:
            metadata["build_date"] = file_data.get("build_date", metadata["build_date"])
        if metadata["git_commit"] == _DEFAULT_GIT_COMMIT:
            metadata["git_commit"] = file_data.get("git_commit", metadata["git_commit"])

    git_data = _load_git_metadata()
    if metadata["build_date"] == _DEFAULT_BUILD_DATE:
        metadata["build_date"] = git_data.get("build_date", _utc_iso_now())
    if metadata["git_commit"] == _DEFAULT_GIT_COMMIT:
        metadata["git_commit"] = git_data.get("git_commit", _DEFAULT_GIT_COMMIT)
    return metadata


_BUILD_METADATA = _resolve_build_metadata()

# Version and build metadata (read from environment variables set by CI/Docker build)
VERSION = _BUILD_METADATA["version"]
BUILD_DATE = _BUILD_METADATA["build_date"]
GIT_COMMIT = _BUILD_METADATA["git_commit"]

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
    [
        "channel",
        "trigger_type",
    ],  # channel=destination (dm/channel), trigger_type=keyword/vip/score
)

alerts_sent_total = Counter(
    "tgsentinel_alerts_sent_total",
    "Total number of alerts successfully sent",
    ["destination"],  # dm, digest
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
            "python_version": (
                f"{__import__('sys').version_info.major}."
                f"{__import__('sys').version_info.minor}."
                f"{__import__('sys').version_info.micro}"
            ),
        }
    )
    log.info(
        f"Initialized build info: version={VERSION}, build_date={BUILD_DATE}, commit={GIT_COMMIT}"
    )


def inc(metric_name: str, **labels) -> None:
    """Increment a counter metric by 1.

    Helper function for incrementing Prometheus counters with cleaner syntax.

    Args:
        metric_name: Name of the metric to increment (e.g., 'alerts_total', 'processed_total')
        **labels: Label key-value pairs for the metric

    Example:
        inc("alerts_total", chat="123")
        inc("processed_total", important=True)
    """
    # Map common metric names to actual counter objects
    metric_map = {
        "alerts_total": alerts_generated_total,
        "alerts_sent": alerts_sent_total,
        "processed_total": messages_processed_total,
        "errors_total": messages_processed_total,  # Use 'error' status
        "ingested_total": messages_ingested_total,
        "feedback_total": feedback_submitted_total,
        "messages_deleted": db_messages_deleted_total,
    }

    counter = metric_map.get(metric_name)
    if counter is None:
        log.warning(f"Unknown metric name: {metric_name}")
        return

    # Special handling for certain metrics
    if metric_name == "errors_total":
        labels["status"] = "error"
        counter.labels(**labels).inc()
    elif metric_name == "processed_total":
        # Convert important boolean to status
        important = labels.pop("important", False)
        labels["status"] = "success" if important else "filtered"
        counter.labels(**labels).inc()
    elif metric_name == "ingested_total":
        # ingested_total has no labels
        counter.inc()
    else:
        try:
            if labels:
                counter.labels(**labels).inc()
            else:
                counter.inc()
        except Exception as e:
            log.warning(f"Failed to increment {metric_name}: {e}")


def dump() -> str:
    """Export all Prometheus metrics in text format.

    Returns:
        String containing all metrics in Prometheus exposition format
    """
    from prometheus_client import generate_latest

    try:
        return generate_latest().decode("utf-8")
    except Exception as e:
        log.error(f"Failed to dump metrics: {e}")
        return ""
