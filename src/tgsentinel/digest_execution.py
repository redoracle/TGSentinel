"""
Digest execution records for tracking digest runs.

This module provides data structures and persistence for digest execution history,
enabling visibility into digest health, troubleshooting, and operational metrics.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, cast

from redis import Redis

log = logging.getLogger(__name__)

# Redis key patterns
EXECUTION_LIST_KEY = "tgsentinel:digest:executions:{profile_id}"
EXECUTION_LATEST_KEY = "tgsentinel:digest:executions:latest:{profile_id}"
EXECUTION_HISTORY_KEY = "tgsentinel:digest:executions:history"

# Default TTL for execution records (7 days)
EXECUTION_TTL_SECONDS = 7 * 24 * 60 * 60

# Maximum history entries per profile
MAX_HISTORY_PER_PROFILE = 50


class ExecutionStatus(str, Enum):
    """Status of a digest execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"  # Some messages delivered, some failed
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DigestExecutionRecord:
    """Record of a single digest execution.

    Attributes:
        execution_id: Unique identifier for this execution
        schedule: Schedule type (hourly, daily, etc.)
        profile_id: Profile identifier
        profile_type: Type of profile (alerts, interests)
        profile_name: Human-readable profile name
        started_at: Execution start time
        completed_at: Execution completion time
        status: Execution status
        message_count: Number of messages included
        delivery_mode: Delivery mode (dm, saved, channel)
        target: Delivery target (user ID, channel ID)
        error: Error message if failed
        duration_seconds: Execution duration
        metadata: Additional execution metadata
    """

    execution_id: str
    schedule: str
    profile_id: str
    profile_type: str
    started_at: str
    status: str = ExecutionStatus.PENDING.value
    profile_name: Optional[str] = None
    completed_at: Optional[str] = None
    message_count: int = 0
    delivery_mode: str = "dm"
    target: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DigestExecutionRecord":
        """Create from dictionary."""
        # Handle metadata if it's a string (from JSON)
        if "metadata" in data and isinstance(data["metadata"], str):
            try:
                data["metadata"] = json.loads(data["metadata"])
            except json.JSONDecodeError:
                data["metadata"] = {}

        return cls(
            execution_id=data.get("execution_id", ""),
            schedule=data.get("schedule", ""),
            profile_id=data.get("profile_id", ""),
            profile_type=data.get("profile_type", ""),
            profile_name=data.get("profile_name"),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at"),
            status=data.get("status", ExecutionStatus.PENDING.value),
            message_count=data.get("message_count", 0),
            delivery_mode=data.get("delivery_mode", "dm"),
            target=data.get("target"),
            error=data.get("error"),
            duration_seconds=data.get("duration_seconds"),
            metadata=data.get("metadata", {}),
        )

    def mark_running(self) -> None:
        """Mark execution as running."""
        self.status = ExecutionStatus.RUNNING.value

    def mark_success(
        self,
        message_count: int,
        completed_at: Optional[str] = None,
    ) -> None:
        """Mark execution as successful.

        Args:
            message_count: Number of messages delivered
            completed_at: Completion timestamp (defaults to now)
        """
        now = completed_at or datetime.now(timezone.utc).isoformat()
        self.status = ExecutionStatus.SUCCESS.value
        self.message_count = message_count
        self.completed_at = now
        self._calculate_duration()

    def mark_failed(
        self,
        error: str,
        completed_at: Optional[str] = None,
    ) -> None:
        """Mark execution as failed.

        Args:
            error: Error message
            completed_at: Completion timestamp (defaults to now)
        """
        now = completed_at or datetime.now(timezone.utc).isoformat()
        self.status = ExecutionStatus.FAILED.value
        self.error = error
        self.completed_at = now
        self._calculate_duration()

    def mark_partial(
        self,
        message_count: int,
        error: str,
        completed_at: Optional[str] = None,
    ) -> None:
        """Mark execution as partially successful.

        Args:
            message_count: Number of messages that were delivered
            error: Description of what failed
            completed_at: Completion timestamp
        """
        now = completed_at or datetime.now(timezone.utc).isoformat()
        self.status = ExecutionStatus.PARTIAL.value
        self.message_count = message_count
        self.error = error
        self.completed_at = now
        self._calculate_duration()

    def _calculate_duration(self) -> None:
        """Calculate duration from started_at to completed_at."""
        if self.started_at and self.completed_at:
            try:
                start = datetime.fromisoformat(self.started_at)
                end = datetime.fromisoformat(self.completed_at)
                self.duration_seconds = (end - start).total_seconds()
            except ValueError:
                pass


class DigestExecutionStore:
    """Storage backend for digest execution records.

    Persists execution history to Redis with TTL for automatic cleanup.
    """

    def __init__(
        self,
        redis_client: Redis,
        ttl_seconds: int = EXECUTION_TTL_SECONDS,
        max_history: int = MAX_HISTORY_PER_PROFILE,
    ):
        """Initialize execution store.

        Args:
            redis_client: Redis client instance
            ttl_seconds: TTL for execution records
            max_history: Maximum history entries per profile
        """
        self.redis = redis_client
        self.ttl = ttl_seconds
        self.max_history = max_history

    def save(self, record: DigestExecutionRecord) -> None:
        """Save an execution record.

        Args:
            record: Execution record to save
        """
        try:
            record_json = json.dumps(record.to_dict())
            profile_key = EXECUTION_LIST_KEY.format(profile_id=record.profile_id)
            latest_key = EXECUTION_LATEST_KEY.format(profile_id=record.profile_id)

            # Store as latest for quick access
            self.redis.setex(latest_key, self.ttl, record_json)

            # Append to history list (left push, trim to max)
            self.redis.lpush(profile_key, record_json)
            self.redis.ltrim(profile_key, 0, self.max_history - 1)
            self.redis.expire(profile_key, self.ttl)

            # Also add to global history (for cross-profile queries)
            global_entry = json.dumps(
                {
                    "profile_id": record.profile_id,
                    "execution_id": record.execution_id,
                    "schedule": record.schedule,
                    "status": record.status,
                    "started_at": record.started_at,
                    "completed_at": record.completed_at,
                }
            )
            self.redis.lpush(EXECUTION_HISTORY_KEY, global_entry)
            self.redis.ltrim(EXECUTION_HISTORY_KEY, 0, self.max_history * 10 - 1)
            self.redis.expire(EXECUTION_HISTORY_KEY, self.ttl)

            log.debug(
                "[EXECUTION-STORE] Saved execution record",
                extra={
                    "execution_id": record.execution_id,
                    "profile_id": record.profile_id,
                    "status": record.status,
                },
            )
        except Exception as e:
            log.warning(f"[EXECUTION-STORE] Failed to save execution record: {e}")

    def get_latest(self, profile_id: str) -> Optional[DigestExecutionRecord]:
        """Get the latest execution record for a profile.

        Args:
            profile_id: Profile identifier

        Returns:
            Latest execution record or None
        """
        try:
            latest_key = EXECUTION_LATEST_KEY.format(profile_id=profile_id)
            raw_data = self.redis.get(latest_key)
            if raw_data:
                data: str = (
                    raw_data.decode() if isinstance(raw_data, bytes) else str(raw_data)
                )
                record_dict = json.loads(data)
                return DigestExecutionRecord.from_dict(record_dict)
            return None
        except Exception as e:
            log.warning(f"[EXECUTION-STORE] Failed to get latest execution: {e}")
            return None

    def get_history(
        self,
        profile_id: str,
        limit: int = 10,
    ) -> List[DigestExecutionRecord]:
        """Get execution history for a profile.

        Args:
            profile_id: Profile identifier
            limit: Maximum number of records to return

        Returns:
            List of execution records (newest first)
        """
        try:
            profile_key = EXECUTION_LIST_KEY.format(profile_id=profile_id)
            raw_records = cast(
                List[bytes], self.redis.lrange(profile_key, 0, limit - 1)
            )
            return [
                DigestExecutionRecord.from_dict(
                    json.loads(r.decode() if isinstance(r, bytes) else str(r))
                )
                for r in raw_records
                if r
            ]
        except Exception as e:
            log.warning(f"[EXECUTION-STORE] Failed to get execution history: {e}")
            return []

    def get_all_latest(
        self, profile_ids: List[str]
    ) -> Dict[str, DigestExecutionRecord]:
        """Get latest execution for multiple profiles.

        Args:
            profile_ids: List of profile identifiers

        Returns:
            Dict mapping profile_id to latest execution record
        """
        result = {}
        for profile_id in profile_ids:
            record = self.get_latest(profile_id)
            if record:
                result[profile_id] = record
        return result

    def get_global_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get global execution history across all profiles.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of execution summaries (newest first)
        """
        try:
            raw_records = cast(
                List[bytes], self.redis.lrange(EXECUTION_HISTORY_KEY, 0, limit - 1)
            )
            return [
                json.loads(r.decode() if isinstance(r, bytes) else str(r))
                for r in raw_records
                if r
            ]
        except Exception as e:
            log.warning(f"[EXECUTION-STORE] Failed to get global history: {e}")
            return []

    def get_stats(self, profile_id: str) -> Dict[str, Any]:
        """Get execution statistics for a profile.

        Args:
            profile_id: Profile identifier

        Returns:
            Dict with execution statistics
        """
        history = self.get_history(profile_id, limit=self.max_history)
        if not history:
            return {
                "total_executions": 0,
                "success_count": 0,
                "failure_count": 0,
                "success_rate": 0.0,
                "avg_message_count": 0.0,
                "avg_duration_seconds": 0.0,
            }

        total = len(history)
        successes = [r for r in history if r.status == ExecutionStatus.SUCCESS.value]
        failures = [r for r in history if r.status == ExecutionStatus.FAILED.value]

        msg_counts = [r.message_count for r in successes if r.message_count > 0]
        durations = [
            r.duration_seconds for r in history if r.duration_seconds is not None
        ]

        return {
            "total_executions": total,
            "success_count": len(successes),
            "failure_count": len(failures),
            "success_rate": len(successes) / total if total > 0 else 0.0,
            "avg_message_count": (
                sum(msg_counts) / len(msg_counts) if msg_counts else 0.0
            ),
            "avg_duration_seconds": (
                sum(durations) / len(durations) if durations else 0.0
            ),
            "last_success": successes[0].completed_at if successes else None,
            "last_failure": failures[0].completed_at if failures else None,
        }


def create_execution_record(
    schedule: str,
    profile_id: str,
    profile_type: str,
    profile_name: Optional[str] = None,
    delivery_mode: str = "dm",
    target: Optional[str] = None,
) -> DigestExecutionRecord:
    """Factory function to create a new execution record.

    Args:
        schedule: Schedule type (hourly, daily, etc.)
        profile_id: Profile identifier
        profile_type: Profile type (alerts, interests)
        profile_name: Human-readable profile name
        delivery_mode: Delivery mode
        target: Delivery target

    Returns:
        New DigestExecutionRecord in RUNNING state
    """
    import uuid

    record = DigestExecutionRecord(
        execution_id=str(uuid.uuid4()),
        schedule=schedule,
        profile_id=profile_id,
        profile_type=profile_type,
        profile_name=profile_name,
        started_at=datetime.now(timezone.utc).isoformat(),
        status=ExecutionStatus.RUNNING.value,
        delivery_mode=delivery_mode,
        target=target,
    )
    return record
