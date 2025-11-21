"""Message collection and deduplication for digest generation.

This module implements the DigestCollector, which queries messages from the database,
deduplicates them across multiple profiles, and prepares them for digest delivery.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Union

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .config import DigestSchedule

log = logging.getLogger(__name__)


@dataclass
class DigestMessage:
    """A message to include in a digest."""

    chat_id: int
    msg_id: int
    score: float
    chat_title: str
    sender_name: str
    message_text: str
    trigger_annotations: str
    created_at: datetime
    matched_profiles: List[str] = field(default_factory=list)

    def dedup_key(self) -> Tuple[int, int]:
        """Unique key for deduplication.

        Returns:
            Tuple of (chat_id, msg_id) for uniqueness
        """
        return (self.chat_id, self.msg_id)


class DigestCollector:
    """Collects and deduplicates messages for a digest schedule."""

    def __init__(self, engine: Engine, schedule: DigestSchedule, since_hours: int):
        """Initialize collector for a specific schedule.

        Args:
            engine: SQLAlchemy engine for database access
            schedule: The digest schedule being collected for
            since_hours: How many hours back to collect messages
        """
        self.engine = engine
        self.schedule = schedule
        self.since_hours = since_hours
        self.messages: Dict[Tuple[int, int], DigestMessage] = {}  # dedup_key -> message

    def collect_all_for_schedule(self, min_score: float = 0.0):
        """Collect all unprocessed messages for this schedule.

        Queries messages that:
        - Match the digest_schedule field
        - Haven't been marked as digest_processed
        - Are within the time window
        - Meet the minimum score threshold

        Args:
            min_score: Minimum score threshold (default: 0.0)
        """
        since = datetime.now(timezone.utc) - timedelta(hours=self.since_hours)
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")

        query = """
        SELECT
            chat_id, msg_id, score, chat_title, sender_name,
            message_text, trigger_annotations, created_at, matched_profiles
        FROM messages
        WHERE alerted = 1
          AND digest_schedule = :schedule
          AND digest_processed = 0
          AND created_at >= :since
          AND score >= :min_score
        ORDER BY score DESC, created_at DESC
        """

        with self.engine.begin() as con:
            rows = con.execute(
                text(query),
                {
                    "schedule": self.schedule.value,
                    "since": since_str,
                    "min_score": min_score,
                },
            ).fetchall()

        log.info(
            f"[DIGEST-COLLECTOR] Collected {len(rows)} messages for {self.schedule.value}",
            extra={
                "schedule": self.schedule.value,
                "count": len(rows),
                "since_hours": self.since_hours,
                "min_score": min_score,
            },
        )

        # Add to messages dict (deduplication happens here)
        for row in rows:
            msg = DigestMessage(
                chat_id=row.chat_id,
                msg_id=row.msg_id,
                score=row.score,
                chat_title=row.chat_title or f"Chat {row.chat_id}",
                sender_name=row.sender_name or "Unknown",
                message_text=row.message_text or "",
                trigger_annotations=row.trigger_annotations or "",
                created_at=self._normalize_datetime(row.created_at),
                matched_profiles=self._parse_matched_profiles(row.matched_profiles),
            )

            self._add_or_merge_message(msg)

    def collect_for_profiles(self, profile_ids: List[str], min_score: float = 0.0):
        """Collect messages matching specific profiles.

        Useful for profile-specific digest generation where you want to filter
        by matched_profiles instead of digest_schedule.

        Args:
            profile_ids: List of profile IDs to match
            min_score: Minimum score threshold
        """
        if not profile_ids:
            log.warning(
                "[DIGEST-COLLECTOR] No profile IDs provided, skipping collection"
            )
            return

        since = datetime.now(timezone.utc) - timedelta(hours=self.since_hours)
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")

        # Use proper JSON extraction to avoid SQL injection and false positives
        # SQLite json_extract returns NULL if key not found, so we check for non-NULL match
        # For each profile, check if it exists in the JSON array
        query = """
        SELECT
            chat_id, msg_id, score, chat_title, sender_name,
            message_text, trigger_annotations, created_at, matched_profiles
        FROM messages
        WHERE alerted = 1
          AND created_at >= :since
          AND score >= :min_score
          AND digest_processed = 0
          AND (
            -- Check if any of the target profiles exist in matched_profiles JSON array
            matched_profiles IS NOT NULL
            AND matched_profiles != ''
            AND matched_profiles != '[]'
        )
        ORDER BY score DESC, created_at DESC
        """

        # Fetch all candidates and filter in Python for safety
        # This avoids SQL injection and handles edge cases properly
        with self.engine.begin() as con:
            rows = con.execute(
                text(query), {"since": since_str, "min_score": min_score}
            ).fetchall()

        # Filter rows to only those matching our profile IDs
        # This is safer than string manipulation in SQL
        profile_ids_set = set(profile_ids)
        filtered_rows = []
        for row in rows:
            matched = self._parse_matched_profiles(row.matched_profiles)
            if profile_ids_set.intersection(matched):
                filtered_rows.append(row)

        rows = filtered_rows

        log.info(
            f"[DIGEST-COLLECTOR] Collected {len(rows)} messages for profiles (filtered from candidates)",
            extra={
                "profile_ids": profile_ids,
                "count": len(rows),
                "since_hours": self.since_hours,
                "min_score": min_score,
            },
        )

        # Add to messages dict (deduplication happens here)
        for row in rows:
            msg = DigestMessage(
                chat_id=row.chat_id,
                msg_id=row.msg_id,
                score=row.score,
                chat_title=row.chat_title or f"Chat {row.chat_id}",
                sender_name=row.sender_name or "Unknown",
                message_text=row.message_text or "",
                trigger_annotations=row.trigger_annotations or "",
                created_at=self._normalize_datetime(row.created_at),
                matched_profiles=self._parse_matched_profiles(row.matched_profiles),
            )

            self._add_or_merge_message(msg)

    def _add_or_merge_message(self, msg: DigestMessage):
        """Add message to collection or merge with existing.

        Deduplication logic:
        - If message already exists (same chat_id + msg_id), merge profiles
        - Keep higher score
        - Keep most recent created_at

        Args:
            msg: Message to add or merge
        """
        msg.created_at = self._normalize_datetime(msg.created_at)
        key = msg.dedup_key()

        if key in self.messages:
            # Merge with existing message
            existing = self.messages[key]

            # Merge matched_profiles (deduplicate)
            combined_profiles = list(
                set(existing.matched_profiles + msg.matched_profiles)
            )
            existing.matched_profiles = combined_profiles

            # Keep higher score
            if msg.score > existing.score:
                existing.score = msg.score

            # Keep more recent timestamp
            if msg.created_at > existing.created_at:
                existing.created_at = msg.created_at

            log.debug(
                f"[DIGEST-COLLECTOR] Merged message {key}",
                extra={
                    "chat_id": msg.chat_id,
                    "msg_id": msg.msg_id,
                    "profiles": combined_profiles,
                    "score": existing.score,
                },
            )
        else:
            # Add new message
            self.messages[key] = msg

    def get_top_messages(self, top_n: int) -> List[DigestMessage]:
        """Get top N messages by score.

        Args:
            top_n: Maximum number of messages to return

        Returns:
            List of DigestMessage sorted by score (descending), then created_at (descending)
        """
        sorted_messages = sorted(
            self.messages.values(),
            key=lambda m: (-m.score, -m.created_at.timestamp()),
        )

        return sorted_messages[:top_n]

    def get_all_messages(self) -> List[DigestMessage]:
        """Get all collected messages sorted by score.

        Returns:
            List of all DigestMessage sorted by score (descending), then created_at (descending)
        """
        return sorted(
            self.messages.values(),
            key=lambda m: (-m.score, -m.created_at.timestamp()),
        )

    def mark_as_processed(self):
        """Mark all collected messages as digest_processed = 1.

        This prevents them from being included in future digests.
        Should be called after successfully sending the digest.
        """
        if not self.messages:
            log.info("[DIGEST-COLLECTOR] No messages to mark as processed")
            return

        message_keys = [(msg.chat_id, msg.msg_id) for msg in self.messages.values()]

        # Build update query for all messages
        # Use CASE to update multiple rows efficiently
        update_query = """
        UPDATE messages
        SET digest_processed = 1
        WHERE (chat_id, msg_id) IN (
            VALUES {placeholders}
        )
        """.format(
            placeholders=", ".join([f"({key[0]}, {key[1]})" for key in message_keys])
        )

        with self.engine.begin() as con:
            result = con.execute(text(update_query))

        log.info(
            f"[DIGEST-COLLECTOR] Marked {len(message_keys)} messages as processed",
            extra={
                "schedule": self.schedule.value,
                "count": len(message_keys),
                "rows_affected": result.rowcount,
            },
        )

    def count(self) -> int:
        """Get count of collected messages.

        Returns:
            Number of unique messages collected
        """
        return len(self.messages)

    @staticmethod
    def _normalize_datetime(value: Union[datetime, str]) -> datetime:
        """Ensure created_at is a timezone-aware datetime.

        Accepts datetime objects (adds UTC tzinfo if naive) or ISO/SQL formatted strings.
        Falls back to current UTC time if parsing fails to avoid hard crashes.
        """
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

        if isinstance(value, str):
            # Try common formats used in SQLite before falling back to fromisoformat
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                try:
                    return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

            try:
                parsed = datetime.fromisoformat(value)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                log.warning(
                    "[DIGEST-COLLECTOR] Unable to parse created_at value, using now()",
                    extra={"created_at": value},
                )

        # Last resort: current time to keep flow running
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_matched_profiles(profiles_json: Optional[str]) -> List[str]:
        """Parse matched_profiles JSON field.

        Args:
            profiles_json: JSON string like '["security", "critical"]'

        Returns:
            List of profile IDs, empty list if None or invalid JSON
        """
        if not profiles_json:
            return []

        try:
            profiles = json.loads(profiles_json)
            if isinstance(profiles, list):
                return profiles
            return []
        except (json.JSONDecodeError, TypeError):
            log.warning(
                f"[DIGEST-COLLECTOR] Failed to parse matched_profiles: {profiles_json}"
            )
            return []
