"""Unified digest worker for schedule-driven digest generation.

This module implements the unified digest worker that replaces the separate
periodic_digest() and daily_digest() workers with a schedule-aware approach.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.engine import Engine
from telethon import TelegramClient

from .config import AppCfg, DigestSchedule, ProfileDigestConfig, ScheduleConfig
from .digest_collector import DigestCollector, DigestMessage
from .digest_scheduler import DigestScheduler

log = logging.getLogger(__name__)


class UnifiedDigestWorker:
    """Single worker that handles all digest schedules."""

    def __init__(
        self,
        cfg: AppCfg,
        engine: Engine,
        scheduler: DigestScheduler,
        redis_manager=None,
    ):
        """Initialize unified digest worker.

        Args:
            cfg: Application configuration
            engine: SQLAlchemy engine for database access
            scheduler: DigestScheduler for schedule detection
            redis_manager: Optional RedisManager for persisting schedule state
        """
        self.cfg = cfg
        self.engine = engine
        self.scheduler = scheduler
        self.redis_manager = redis_manager

        # Load persisted schedule times from Redis
        if redis_manager:
            self._load_schedule_times_from_redis()

    def _load_schedule_times_from_redis(self):
        """Load last_run times from Redis into scheduler state."""
        if not self.redis_manager:
            return
        try:
            schedule_times = self.redis_manager.get_all_digest_schedule_times()
            for schedule_name, timestamp_str in schedule_times.items():
                if timestamp_str:
                    # Parse ISO timestamp back to datetime
                    timestamp = datetime.fromisoformat(timestamp_str)
                    # Ensure timezone-aware datetime (attach UTC if naive)
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    self.scheduler.last_run[schedule_name] = timestamp
                    log.info(
                        f"[UNIFIED-DIGEST] Loaded last_run for {schedule_name}",
                        extra={"schedule": schedule_name, "last_run": timestamp_str},
                    )
        except Exception as e:
            log.warning(
                f"[UNIFIED-DIGEST] Failed to load schedule times from Redis: {e}",
                exc_info=True,
            )

    async def run(
        self,
        client: TelegramClient,
        handshake_gate: asyncio.Event,
    ) -> None:
        """Run the unified digest worker loop.

        Args:
            client: Telegram client for sending digests
            handshake_gate: Event to wait for authorization
        """
        log.info("[UNIFIED-DIGEST] Starting unified digest worker")

        await handshake_gate.wait()

        # Bootstrap: Run all enabled schedules immediately on first start
        if not self.scheduler.last_run and self.redis_manager:
            # Check Redis - if no persisted times, this is truly first run
            schedule_times = self.redis_manager.get_all_digest_schedule_times()
            if not any(schedule_times.values()):
                log.info("[UNIFIED-DIGEST] First run detected, triggering bootstrap")
                await self._run_bootstrap_digests(client)

        while True:
            # Handle reconnections: wait if gate is cleared during relogin
            if not handshake_gate.is_set():
                await handshake_gate.wait()

            # Check which schedules are due
            now = datetime.now(timezone.utc)
            due_schedules = self.scheduler.get_due_schedules(now)

            if not due_schedules:
                # No digests due, sleep and check again
                sleep_interval = self.cfg.alerts.digest.check_interval_seconds
                await asyncio.sleep(sleep_interval)
                continue

            # Process each due schedule
            for schedule in due_schedules:
                try:
                    await self._process_digest_schedule(schedule, client, now)
                    self.scheduler.mark_schedule_run(schedule, now)
                    # Persist to Redis for API access
                    if self.redis_manager:
                        self.redis_manager.set_digest_schedule_time(
                            schedule.value, now.isoformat()
                        )
                except Exception as e:
                    log.error(
                        f"[UNIFIED-DIGEST] Failed to process {schedule.value}: {e}",
                        exc_info=True,
                        extra={
                            "schedule": schedule.value,
                            "timestamp": now.isoformat(),
                        },
                    )

            # Sleep until next check (configurable interval)
            sleep_interval = self.cfg.alerts.digest.check_interval_seconds
            await asyncio.sleep(sleep_interval)

    async def _run_bootstrap_digests(self, client: TelegramClient):
        """Run all enabled schedules immediately on first start.

        Args:
            client: Telegram client for sending digests
        """
        now = datetime.now(timezone.utc)
        all_schedules = [
            DigestSchedule.HOURLY,
            DigestSchedule.EVERY_4H,
            DigestSchedule.EVERY_6H,
            DigestSchedule.EVERY_12H,
            DigestSchedule.DAILY,
            DigestSchedule.WEEKLY,
        ]

        for schedule in all_schedules:
            profiles = self.scheduler.discover_profile_schedules(schedule)
            if profiles:
                log.info(f"[UNIFIED-DIGEST] Bootstrap: processing {schedule.value}")
                try:
                    await self._process_digest_schedule(schedule, client, now)
                    self.scheduler.mark_schedule_run(schedule, now)
                    if self.redis_manager:
                        self.redis_manager.set_digest_schedule_time(
                            schedule.value, now.isoformat()
                        )
                except Exception as e:
                    log.error(
                        f"[UNIFIED-DIGEST] Bootstrap failed for {schedule.value}: {e}",
                        exc_info=True,
                    )

    async def _process_digest_schedule(
        self,
        schedule: DigestSchedule,
        client: TelegramClient,
        now: datetime,
    ):
        """Process a single digest schedule.

        Args:
            schedule: The schedule to process (HOURLY, DAILY, etc.)
            client: Telegram client for sending digests
            now: Current timestamp
        """
        log.info(
            f"[UNIFIED-DIGEST] Processing {schedule.value} digest",
            extra={"schedule": schedule.value, "timestamp": now.isoformat()},
        )

        # 1. Discover all profiles with this schedule
        profiles_with_schedule = self.scheduler.discover_profile_schedules(schedule)

        if not profiles_with_schedule:
            log.info(
                f"[UNIFIED-DIGEST] No profiles configured for {schedule.value}",
                extra={"schedule": schedule.value},
            )
            return

        # 2. Determine time window for this schedule
        since_hours = self._get_schedule_window_hours(schedule)

        # 3. Aggregate min_score and top_n from all profiles
        min_score = self._aggregate_min_score(profiles_with_schedule)
        top_n = self._aggregate_top_n(profiles_with_schedule)

        # 4. Collect messages (with deduplication)
        collector = DigestCollector(self.engine, schedule, since_hours)

        # Collect all unique profile IDs (flattened from all entities)
        all_profile_ids = []
        for identifier, digest_cfg, profile_ids in profiles_with_schedule:
            all_profile_ids.extend(profile_ids)
        all_profile_ids = list(set(all_profile_ids))  # Deduplicate

        collector.collect_for_profiles(all_profile_ids, min_score)

        # 5. Get top messages
        top_messages = collector.get_top_messages(top_n)

        if not top_messages:
            log.info(
                f"[UNIFIED-DIGEST] No messages for {schedule.value} digest",
                extra={
                    "schedule": schedule.value,
                    "min_score": min_score,
                    "since_hours": since_hours,
                },
            )
            return

        # 6. Aggregate delivery config from all profiles
        mode, target_channel = self._aggregate_delivery_config(
            profiles_with_schedule, schedule
        )

        # 7. Build and send digest
        await self._send_digest(
            client=client,
            schedule=schedule,
            messages=top_messages,
            mode=mode,
            target_channel=target_channel,
            since_hours=since_hours,
        )

        # 8. Mark messages as processed
        collector.mark_as_processed()

        log.info(
            f"[UNIFIED-DIGEST] Sent {schedule.value} digest with {len(top_messages)} messages",
            extra={
                "schedule": schedule.value,
                "message_count": len(top_messages),
                "since_hours": since_hours,
                "mode": mode,
            },
        )

    def _get_schedule_window_hours(self, schedule: DigestSchedule) -> int:
        """Get the time window for a schedule in hours.

        Args:
            schedule: DigestSchedule type

        Returns:
            Number of hours to look back for messages
        """
        windows = {
            DigestSchedule.HOURLY: 1,
            DigestSchedule.EVERY_4H: 4,
            DigestSchedule.EVERY_6H: 6,
            DigestSchedule.EVERY_12H: 12,
            DigestSchedule.DAILY: 24,
            DigestSchedule.WEEKLY: 168,  # 7 days
        }
        if schedule not in windows:
            # Warn about unexpected/unknown schedule values so callers can
            # detect misconfigurations or newly added schedule types.
            sched_val = getattr(schedule, "value", schedule)
            log.warning(
                f"[UNIFIED-DIGEST] Unknown schedule '{sched_val}' for window lookup; defaulting to 1 hour"
            )
        return windows.get(schedule, 1)

    def _aggregate_min_score(
        self,
        profiles_with_schedule: List[Tuple[str, ProfileDigestConfig, List[str]]],
    ) -> float:
        """Aggregate min_score from all profiles (use minimum).

        Args:
            profiles_with_schedule: List of (identifier, ProfileDigestConfig, profile_ids) tuples

        Returns:
            Minimum score threshold across all profiles
        """
        min_scores = []
        for _, digest_cfg, _ in profiles_with_schedule:
            # Find schedules with min_score set
            for sched in digest_cfg.schedules:
                if sched.min_score is not None:
                    min_scores.append(sched.min_score)

        if min_scores:
            return min(min_scores)

        # Fallback to global default
        return 0.0

    def _aggregate_top_n(
        self,
        profiles_with_schedule: List[Tuple[str, ProfileDigestConfig, List[str]]],
    ) -> int:
        """Aggregate top_n from all profiles (use maximum).

        Args:
            profiles_with_schedule: List of (identifier, ProfileDigestConfig, profile_ids) tuples

        Returns:
            Maximum top_n across all profiles
        """
        top_ns = []
        for _, digest_cfg, _ in profiles_with_schedule:
            # Profile-level top_n (always has a value, defaults to 10)
            top_ns.append(digest_cfg.top_n)
            # Also check schedule-level top_n overrides
            for sched in digest_cfg.schedules:
                if sched.top_n is not None:
                    top_ns.append(sched.top_n)

        if top_ns:
            return max(top_ns)

        # Fallback to global default (shouldn't normally reach here)
        return self.cfg.alerts.digest.top_n

    def _aggregate_delivery_config(
        self,
        profiles_with_schedule: List[Tuple[str, ProfileDigestConfig, List[str]]],
        schedule: DigestSchedule,
    ) -> Tuple[str, Optional[str]]:
        """Aggregate delivery mode and target channel from all profiles.

        If profiles disagree on delivery mode or target channel, uses fallback logic:
        - Mode: If conflict, falls back to 'dm' (most conservative)
        - Channel: If multiple channels specified, uses first non-empty

        Args:
            profiles_with_schedule: List of (identifier, ProfileDigestConfig, profile_ids) tuples
            schedule: The schedule being processed (for logging)

        Returns:
            Tuple of (mode, target_channel)
        """
        modes = set()
        channels = set()

        for identifier, digest_cfg, _ in profiles_with_schedule:
            modes.add(digest_cfg.mode)
            if digest_cfg.target_channel:
                channels.add(digest_cfg.target_channel)

        # Determine consensus mode
        if len(modes) == 1:
            mode = modes.pop()
        else:
            # Conflict: fall back to DM (safest)
            log.warning(
                f"[UNIFIED-DIGEST] Conflicting delivery modes for {schedule.value}: {modes}. "
                f"Falling back to 'dm'. Consider standardizing delivery config across profiles."
            )
            mode = "dm"

        # Determine target channel
        if len(channels) == 0:
            target_channel = None
        elif len(channels) == 1:
            target_channel = channels.pop()
        else:
            # Multiple channels: use first (alphabetically)
            sorted_channels = sorted(channels)
            target_channel = sorted_channels[0]
            log.warning(
                f"[UNIFIED-DIGEST] Multiple target channels for {schedule.value}: {channels}. "
                f"Using: {target_channel}. Consider standardizing target_channel across profiles."
            )

        return mode, target_channel

    async def _send_digest(
        self,
        client: TelegramClient,
        schedule: DigestSchedule,
        messages: List[DigestMessage],
        mode: str,
        target_channel: Optional[str],
        since_hours: int,
    ):
        """Format and send a digest.

        Args:
            client: Telegram client for sending messages
            schedule: DigestSchedule type being sent
            messages: List of DigestMessage objects to include
            mode: Delivery mode (dm|channel|both)
            target_channel: Target channel for delivery (if mode=channel or both)
            since_hours: Time window for the digest
        """
        # Validate mode
        if mode not in ("dm", "channel", "both"):
            log.warning(
                f"[UNIFIED-DIGEST] Invalid digest mode '{mode}'. "
                f"Expected 'dm', 'channel', or 'both'. Digest not sent.",
                extra={"mode": mode, "schedule": schedule.value},
            )
            return

        # Validate channel when required
        if mode in ("channel", "both") and not target_channel:
            log.warning(
                f"[UNIFIED-DIGEST] Digest channel target missing while mode={mode}; "
                f"falling back to DM only",
                extra={"mode": mode, "schedule": schedule.value},
            )
            mode = "dm"

        # Build digest content
        digest_text = self._format_digest(schedule, messages, since_hours)

        # Split into chunks if too long (Telegram limit: 4096 chars)
        chunks = self._split_into_chunks(digest_text)

        log.info(
            f"[UNIFIED-DIGEST] Sending {schedule.value} digest "
            f"with {len(messages)} messages in {len(chunks)} part(s) (mode={mode})",
            extra={
                "schedule": schedule.value,
                "message_count": len(messages),
                "chunk_count": len(chunks),
                "mode": mode,
            },
        )

        # Send to DM if requested
        if mode in ("dm", "both"):
            try:
                for i, chunk in enumerate(chunks):
                    part_header = (
                        f"[Part {i+1}/{len(chunks)}]\n"
                        if len(chunks) > 1 and i > 0
                        else ""
                    )
                    await client.send_message(
                        "me", part_header + chunk, link_preview=False
                    )
                log.info(
                    f"[UNIFIED-DIGEST] Sent {schedule.value} digest to DM (Saved Messages)",
                    extra={"schedule": schedule.value},
                )
            except Exception as e:
                log.error(
                    f"[UNIFIED-DIGEST] Failed to send {schedule.value} digest to DM: {e}",
                    exc_info=True,
                    extra={"schedule": schedule.value},
                )

        # Send to channel if requested
        if mode in ("channel", "both") and target_channel:
            try:
                for i, chunk in enumerate(chunks):
                    part_header = (
                        f"[Part {i+1}/{len(chunks)}]\n"
                        if len(chunks) > 1 and i > 0
                        else ""
                    )
                    await client.send_message(
                        target_channel, part_header + chunk, link_preview=False
                    )
                log.info(
                    f"[UNIFIED-DIGEST] Sent {schedule.value} digest to channel {target_channel}",
                    extra={"schedule": schedule.value, "channel": target_channel},
                )
            except Exception as e:
                log.error(
                    f"[UNIFIED-DIGEST] Failed to send {schedule.value} digest to channel: {e}",
                    exc_info=True,
                    extra={"schedule": schedule.value, "channel": target_channel},
                )

    def _format_digest(
        self,
        schedule: DigestSchedule,
        messages: List[DigestMessage],
        since_hours: int,
    ) -> str:
        """Format digest messages into readable text.

        Args:
            schedule: DigestSchedule type
            messages: List of DigestMessage objects
            since_hours: Time window for the digest

        Returns:
            Formatted digest text with Markdown
        """
        # Header
        lines = [
            f"🗞️ **{schedule.value.title()} Digest — Top {len(messages)} highlights** "
            f"(last {since_hours}h)\n"
        ]

        # Format each message
        for idx, msg in enumerate(messages, 1):
            # Create Telegram link
            if str(msg.chat_id).startswith("-100"):
                # Private channel/supergroup - remove -100 prefix
                clean_id = str(msg.chat_id)[4:]
                msg_link = f"https://t.me/c/{clean_id}/{msg.msg_id}"
            else:
                # Regular chat or group
                msg_link = (
                    f"tg://openmessage?chat_id={msg.chat_id}&message_id={msg.msg_id}"
                )

            # Truncate message text if too long
            msg_text = msg.message_text or "[No content]"
            if len(msg_text) > 150:
                msg_text = msg_text[:150] + "..."
            msg_text = msg_text.replace("\n", " ")

            # Format trigger annotations
            trigger_line = self._format_triggers(msg.trigger_annotations)

            # Format matched profiles as badges
            profile_badges = self._format_profile_badges(msg.matched_profiles)

            # Format timestamp
            if isinstance(msg.created_at, datetime):
                msg_time = msg.created_at.strftime("%H:%M")
            else:
                log.warning(
                    f"[UNIFIED-DIGEST] Message has non-datetime created_at",
                    extra={
                        "chat_id": msg.chat_id,
                        "msg_id": msg.msg_id,
                        "created_at_type": type(msg.created_at).__name__,
                        "created_at_value": str(msg.created_at),
                    },
                )
                msg_time = "??:??"

            # Build entry
            lines.append(
                f"**{idx}. [{msg.chat_title or f'Chat {msg.chat_id}'}]({msg_link})** — Score: {msg.score:.2f}\n"
                f"👤 {msg.sender_name or 'Unknown'} • 🕐 {msg_time}\n"
                f"💬 _{msg_text}_{trigger_line}{profile_badges}"
            )

        return "\n".join(lines)

    def _format_triggers(self, trigger_annotations_json: str) -> str:
        """Format trigger annotations for display.

        Args:
            trigger_annotations_json: JSON string with category -> [keywords] mapping

        Returns:
            Formatted string like "\n🎯 🔒 security: CVE • ⚡ urgency: critical"
        """
        if not trigger_annotations_json:
            return ""

        try:
            annotations = json.loads(trigger_annotations_json)
        except (json.JSONDecodeError, TypeError) as e:
            log.warning(
                f"[UNIFIED-DIGEST] Failed to parse trigger_annotations",
                extra={
                    "trigger_annotations": trigger_annotations_json[:200],  # Truncate
                    "error": str(e),
                },
            )
            return ""

        if not annotations or not isinstance(annotations, dict):
            return ""

        # Category emoji mapping
        category_icons = {
            "security": "🔒",
            "urgency": "⚡",
            "action": "✅",
            "decision": "🗳️",
            "release": "📦",
            "risk": "⚠️",
            "opportunity": "💎",
            "importance": "❗",
            "keywords": "🔍",
        }

        parts = []
        for category, keywords in annotations.items():
            if not keywords:
                continue

            icon = category_icons.get(category, "•")
            # Limit keywords shown (max 3). Make an explicit copy of the slice
            # so we don't mutate the original `keywords` list/sequence when
            # appending the "+N more" indicator.
            shown_keywords = list(keywords[:3])
            if len(keywords) > 3:
                shown_keywords.append(f"+{len(keywords) - 3} more")

            keywords_str = ", ".join(shown_keywords)
            parts.append(f"{icon} {category}: {keywords_str}")

        return f"\n🎯 {' • '.join(parts)}" if parts else ""

    def _format_profile_badges(self, matched_profiles: List[str]) -> str:
        """Format matched profiles as inline badges.

        Args:
            matched_profiles: List of profile IDs that matched this message

        Returns:
            Formatted string like "\n📋 `security` `critical_updates`"
        """
        if not matched_profiles:
            return ""

        badges = " ".join(f"`{profile}`" for profile in matched_profiles)
        return f"\n📋 {badges}"

    def _split_into_chunks(self, text: str, max_length: int = 4000) -> List[str]:
        """Split digest text into chunks for Telegram message limit.

        Args:
            text: Full digest text
            max_length: Maximum characters per chunk (default: 4000, leaving margin)

        Returns:
            List of text chunks
        """
        if len(text) <= max_length:
            return [text]

        # Split by lines to avoid breaking messages mid-line
        lines = text.split("\n")

        # If there are no line breaks, just split by character chunks
        if len(lines) == 1:
            chunks = []
            for i in range(0, len(text), max_length):
                chunks.append(text[i : i + max_length])
            return chunks

        # Split by message entries (preserving line boundaries)
        chunks = []
        current_chunk = lines[0]  # Start with header

        for line in lines[1:]:
            if len(current_chunk) + len(line) + 1 > max_length:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += "\n" + line

        if current_chunk:
            chunks.append(current_chunk)

        return chunks
