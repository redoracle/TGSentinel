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
    sender_id: int | None = None
    keyword_score: Optional[float] = None
    semantic_score: Optional[float] = None

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

        # Phase 1: Dual-read - query both new (feed_interest_flag) and legacy (flagged_for_interest_feed)
        # Use semantic_scores_json for ranking if available, fallback to score
        query = """
        SELECT
            chat_id, msg_id,
            keyword_score,
            score,
            semantic_scores_json,
            semantic_type,
            sender_id,
            chat_title, sender_name,
            message_text, trigger_annotations, created_at, matched_profiles
        FROM messages
        WHERE COALESCE(feed_interest_flag, flagged_for_interest_feed) = 1
          AND digest_schedule = :schedule
          AND digest_processed = 0
          AND created_at >= :since
          AND COALESCE(keyword_score, score) >= :min_score
        ORDER BY created_at DESC
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
        # For semantic matches, extract max score from semantic_scores_json for ranking
        for row in rows:
            # Determine effective score for ranking
            keyword_score_value = row.keyword_score
            effective_score = (
                keyword_score_value
                if keyword_score_value is not None
                else (row.score or 0.0)
            )

            max_semantic: Optional[float] = None
            # If this is a semantic match, use max semantic score instead
            if row.semantic_scores_json:
                try:
                    import json

                    semantic_scores = json.loads(row.semantic_scores_json)
                    if semantic_scores:
                        current_max = max(semantic_scores.values())
                        max_semantic = current_max
                        effective_score = max(effective_score, current_max)
                except (json.JSONDecodeError, ValueError):
                    pass

            msg = DigestMessage(
                chat_id=row.chat_id,
                msg_id=row.msg_id,
                score=effective_score,  # Use effective score (semantic or keyword)
                chat_title=row.chat_title or f"Chat {row.chat_id}",
                sender_name=row.sender_name or "Unknown",
                sender_id=getattr(row, "sender_id", None),
                message_text=row.message_text or "",
                trigger_annotations=row.trigger_annotations or "",
                created_at=self._normalize_datetime(row.created_at),
                matched_profiles=self._parse_matched_profiles(row.matched_profiles),
                keyword_score=keyword_score_value,
                semantic_score=max_semantic,
            )

            self._add_or_merge_message(msg)

    def collect_for_profiles(
        self,
        profile_ids: List[str],
        min_score: float = 0.0,
        manual_trigger: bool = False,
    ):
        """Collect messages matching specific profiles.

        Useful for profile-specific digest generation where you want to filter
        by matched_profiles instead of digest_schedule.

        Args:
            profile_ids: List of profile IDs to match
            min_score: Minimum score threshold (ignored if manual_trigger=True)
            manual_trigger: If True, skip score filtering and order by recency
        """
        if not profile_ids:
            log.warning(
                "[DIGEST-COLLECTOR] No profile IDs provided, skipping collection"
            )
            return

        since = datetime.now(timezone.utc) - timedelta(hours=self.since_hours)
        since_str = since.strftime("%Y-%m-%d %H:%M:%S")

        # For manual triggers: no time/score filtering, just latest messages
        # For scheduled digests: apply time window and score filters
        if manual_trigger:
            query = """
            SELECT
                chat_id, msg_id, keyword_score, score, chat_title, sender_name,
                sender_id,
                message_text, trigger_annotations, created_at, matched_profiles,
                semantic_scores_json, semantic_type
            FROM messages
            WHERE flagged_for_interest_feed = 1
              AND (
                -- Check if any of the target profiles exist in matched_profiles JSON array
                matched_profiles IS NOT NULL
                AND matched_profiles != ''
                AND matched_profiles != '[]'
            )
            ORDER BY created_at DESC
            """
            with self.engine.begin() as con:
                rows = con.execute(text(query)).fetchall()
        else:
            order_clause = "ORDER BY score DESC, created_at DESC"
            query = f"""
            SELECT
                chat_id, msg_id, keyword_score, score, chat_title, sender_name,
                sender_id,
                message_text, trigger_annotations, created_at, matched_profiles,
                semantic_scores_json, semantic_type
            FROM messages
            WHERE flagged_for_interest_feed = 1
              AND created_at >= :since
              AND digest_processed = 0
              AND (
                -- Check if any of the target profiles exist in matched_profiles JSON array
                matched_profiles IS NOT NULL
                AND matched_profiles != ''
                AND matched_profiles != '[]'
            )
            {order_clause}
            """
            with self.engine.begin() as con:
                rows = con.execute(text(query), {"since": since_str}).fetchall()

        # Filter rows to only those matching our profile IDs AND meeting min_score threshold
        # Phase 1: Check semantic_scores_json for interest messages, score for legacy
        # For manual triggers: skip min_score filtering entirely
        profile_ids_set = set(profile_ids)
        filtered_rows: List[Tuple] = []
        for row in rows:
            matched = self._parse_matched_profiles(row.matched_profiles)
            if not profile_ids_set.intersection(matched):
                continue

            # Determine effective score for ranking, allow fallback to keyword_score
            effective_score = (
                row.score
                if row.score is not None
                else row.keyword_score if row.keyword_score is not None else 0.0
            )
            semantic_score_value = None
            if row.semantic_scores_json:
                try:
                    semantic_scores = json.loads(row.semantic_scores_json)
                    # Get max score from matched profiles
                    matched_profile_scores = [
                        semantic_scores.get(str(pid))
                        for pid in matched
                        if str(pid) in semantic_scores
                    ]
                    matched_profile_scores = [
                        score for score in matched_profile_scores if score is not None
                    ]
                    if matched_profile_scores:
                        semantic_score_value = max(matched_profile_scores)
                        effective_score = max(effective_score, semantic_score_value)
                except (json.JSONDecodeError, ValueError, TypeError):
                    # Fall back to legacy score field
                    pass

            # Apply min_score threshold (skip for manual triggers)
            if manual_trigger or effective_score >= min_score:
                # Store effective_score and semantic_score for later use
                filtered_rows.append((row, effective_score, semantic_score_value))

        log.info(
            f"[DIGEST-COLLECTOR] Collected {len(filtered_rows)} messages for profiles (manual={manual_trigger})",
            extra={
                "profile_ids": profile_ids,
                "count": len(filtered_rows),
                "since_hours": self.since_hours,
                "min_score": min_score,
            },
        )

        # Add to messages dict (deduplication happens here)
        for row, effective_score, semantic_score_value in filtered_rows:
            msg = DigestMessage(
                chat_id=row.chat_id,
                msg_id=row.msg_id,
                score=effective_score,  # Use effective score (semantic or legacy)
                chat_title=row.chat_title or f"Chat {row.chat_id}",
                sender_name=row.sender_name or "Unknown",
                sender_id=getattr(row, "sender_id", None),
                message_text=row.message_text or "",
                trigger_annotations=row.trigger_annotations or "",
                created_at=self._normalize_datetime(row.created_at),
                matched_profiles=self._parse_matched_profiles(row.matched_profiles),
                keyword_score=row.keyword_score,
                semantic_score=semantic_score_value,
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

            # Keep sender_id if missing and new message provides it
            if existing.sender_id is None and msg.sender_id is not None:
                existing.sender_id = msg.sender_id

            # Keep highest keyword_score if available
            if msg.keyword_score is not None:
                if (
                    existing.keyword_score is None
                    or msg.keyword_score > existing.keyword_score
                ):
                    existing.keyword_score = msg.keyword_score

            # Keep highest semantic_score if available
            if msg.semantic_score is not None:
                if (
                    existing.semantic_score is None
                    or msg.semantic_score > existing.semantic_score
                ):
                    existing.semantic_score = msg.semantic_score

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
            profiles_json: JSON string like '["security", "critical"]' or '[1001, 3000]'

        Returns:
            List of profile IDs as strings, empty list if None or invalid JSON
        """
        if not profiles_json:
            return []

        try:
            profiles = json.loads(profiles_json)
            if isinstance(profiles, list):
                # Ensure all profile IDs are strings (JSON may contain integers)
                return [str(p) for p in profiles]
            return []
        except (json.JSONDecodeError, TypeError):
            log.warning(
                f"[DIGEST-COLLECTOR] Failed to parse matched_profiles: {profiles_json}"
            )
            return []
