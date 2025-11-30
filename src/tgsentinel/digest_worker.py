"""Unified digest worker for schedule-driven digest generation.

This module implements the unified digest worker that replaces the separate
periodic_digest() and daily_digest() workers with a schedule-aware approach.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.engine import Engine
from telethon import TelegramClient

from .config import (
    AppCfg,
    DigestSchedule,
    ProfileDigestConfig,
    normalize_delivery_mode,
)
from .digest_collector import DigestCollector, DigestMessage
from .digest_execution import DigestExecutionStore, create_execution_record
from .digest_scheduler import DigestScheduler
from .message_formats import render_digest_entry, render_digest_header
from .notifier import _resolve_target

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
        self._execution_store: Optional[DigestExecutionStore] = None

        # Load persisted schedule times from Redis
        if redis_manager:
            self._load_schedule_times_from_redis()
            # Initialize execution store
            if redis_manager.redis:
                self._execution_store = DigestExecutionStore(redis_manager.redis)

    @property
    def execution_store(self) -> Optional[DigestExecutionStore]:
        """Get the execution store for recording digest runs."""
        return self._execution_store

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
        manual_trigger: bool = False,
    ):
        """Process a single digest schedule.

        Args:
            schedule: The schedule to process (HOURLY, DAILY, etc.)
            client: Telegram client for sending digests
            now: Current timestamp
            manual_trigger: If True, skip score filtering and use latest messages
        """
        log.info(
            f"[UNIFIED-DIGEST] Processing {schedule.value} digest (manual={manual_trigger})",
            extra={
                "schedule": schedule.value,
                "timestamp": now.isoformat(),
                "manual": manual_trigger,
            },
        )

        # Create execution record for tracking
        execution_record = None
        all_profile_ids: List[str] = []
        message_count = 0

        try:
            # 1. For manual triggers: collect from ALL profiles of the appropriate type
            #    For scheduled digests: only profiles with this specific schedule
            if manual_trigger:
                # Manual trigger: collect from ALL interest or alert profiles
                if schedule == DigestSchedule.HOURLY:
                    all_profile_ids = []
                    for profile_id in self.cfg.global_profiles.keys():
                        pid_int = int(profile_id) if profile_id.isdigit() else 0
                        if pid_int >= 3000:
                            all_profile_ids.append(profile_id)
                        elif 1000 <= pid_int < 2000:
                            all_profile_ids.append(profile_id)

                    if not all_profile_ids:
                        log.info(
                            "[UNIFIED-DIGEST] No interest/alert profiles found for manual trigger",
                            extra={"schedule": schedule.value},
                        )
                        return

                    profiles_with_schedule = [("manual:all", None, all_profile_ids)]
                else:
                    profiles_with_schedule = self.scheduler.discover_profile_schedules(
                        schedule
                    )
                    if not profiles_with_schedule:
                        log.info(
                            f"[UNIFIED-DIGEST] No profiles configured for {schedule.value}",
                            extra={"schedule": schedule.value},
                        )
                        return
                    all_profile_ids = []
                    for _, _, profile_ids in profiles_with_schedule:
                        all_profile_ids.extend(profile_ids)
                    all_profile_ids = list(set(all_profile_ids))
            else:
                profiles_with_schedule = self.scheduler.discover_profile_schedules(
                    schedule
                )
                if not profiles_with_schedule:
                    log.info(
                        f"[UNIFIED-DIGEST] No profiles configured for {schedule.value}",
                        extra={"schedule": schedule.value},
                    )
                    return

                all_profile_ids = []
                for _, _, profile_ids in profiles_with_schedule:
                    all_profile_ids.extend(profile_ids)
                all_profile_ids = list(set(all_profile_ids))

            # 2. Determine time window for this schedule
            # Manual triggers: ignored, will fetch latest messages
            if manual_trigger:
                since_hours = 1  # Placeholder, ignored
                top_n = 5  # Manual triggers: always top 5 messages
            else:
                since_hours = self._get_schedule_window_hours(schedule)
                top_n = self._aggregate_top_n(profiles_with_schedule)

            # 3. Aggregate min_score from all profiles
            min_score = (
                self._aggregate_min_score(profiles_with_schedule)
                if not manual_trigger
                else 0.0
            )

            # 4. Collect messages (with deduplication)
            collector = DigestCollector(self.engine, schedule, since_hours)

            log.info(
                f"[UNIFIED-DIGEST] Collecting messages for profiles: {all_profile_ids}",
                extra={
                    "profile_ids": all_profile_ids,
                    "manual": manual_trigger,
                    "schedule": schedule.value,
                },
            )
            collector.collect_for_profiles(all_profile_ids, min_score, manual_trigger)

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

            # Create execution record now that we have profile info
            if self._execution_store and all_profile_ids:
                profile_type = (
                    "interests"
                    if any(
                        pid.isdigit() and int(pid) >= 3000 for pid in all_profile_ids
                    )
                    else "alerts"
                )
                execution_record = create_execution_record(
                    schedule=schedule.value,
                    profile_id=",".join(all_profile_ids[:3]),
                    profile_type=profile_type,
                    delivery_mode=mode,
                    target=target_channel,
                )
                self._execution_store.save(execution_record)

            # 7. Build and send digest
            await self._send_digest(
                client=client,
                schedule=schedule,
                messages=top_messages,
                mode=mode,
                target_channel=target_channel,
                since_hours=since_hours,
                manual_trigger=manual_trigger,
                profile_ids=all_profile_ids,
            )

            # 8. Mark messages as processed
            collector.mark_as_processed()
            message_count = len(top_messages)

            # Record successful execution
            if execution_record and self._execution_store:
                execution_record.mark_success(message_count)
                self._execution_store.save(execution_record)

            log.info(
                f"[UNIFIED-DIGEST] Sent {schedule.value} digest with {len(top_messages)} messages",
                extra={
                    "schedule": schedule.value,
                    "message_count": len(top_messages),
                    "since_hours": since_hours,
                    "mode": mode,
                },
            )

        except Exception as e:
            # Record failed execution
            if execution_record and self._execution_store:
                execution_record.mark_failed(str(e))
                self._execution_store.save(execution_record)
            raise

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
        profiles_with_schedule: Sequence[
            Tuple[str, Optional[ProfileDigestConfig], List[str]]
        ],
    ) -> float:
        """Aggregate min_score from all profiles (use minimum).

        Args:
            profiles_with_schedule: List of (identifier, ProfileDigestConfig, profile_ids) tuples

        Returns:
            Minimum score threshold across all profiles
        """
        min_scores = []
        for _, digest_cfg, _ in profiles_with_schedule:
            # Skip dummy entries (from manual triggers with digest_cfg=None)
            if digest_cfg is None:
                continue
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
        profiles_with_schedule: Sequence[
            Tuple[str, Optional[ProfileDigestConfig], List[str]]
        ],
    ) -> int:
        """Aggregate top_n from all profiles (use maximum).

        Args:
            profiles_with_schedule: List of (identifier, ProfileDigestConfig, profile_ids) tuples

        Returns:
            Maximum top_n across all profiles
        """
        top_ns = []
        for _, digest_cfg, _ in profiles_with_schedule:
            # Skip dummy entries (from manual triggers with digest_cfg=None)
            if digest_cfg is None:
                continue
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
        profiles_with_schedule: Sequence[
            Tuple[str, Optional[ProfileDigestConfig], List[str]]
        ],
        schedule: DigestSchedule,
    ) -> Tuple[str, Optional[str]]:
        """Aggregate delivery mode and target channel from schedule-level configs.

        Each schedule can now have its own mode and target_channel.
        If profiles disagree, uses fallback logic:
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
            # Skip dummy entries (from manual triggers with digest_cfg=None)
            if digest_cfg is None:
                continue

            # Find the specific schedule config for this schedule type
            for sched_cfg in digest_cfg.schedules:
                if sched_cfg.schedule == schedule:
                    # Get mode and target_channel from schedule config
                    sched_mode = sched_cfg.mode or "dm"
                    sched_channel = sched_cfg.target_channel

                    modes.add(sched_mode)
                    if sched_channel:
                        channels.add(sched_channel)
                    break

        # Determine consensus mode
        # When NOTIFICATION_CHANNEL is set, prefer sending to channel even with conflicts
        default_channel = os.getenv("NOTIFICATION_CHANNEL", "")

        if len(modes) == 0:
            # No modes specified: use 'both' if channel configured, else 'dm'
            mode = "both" if default_channel else "dm"
        elif len(modes) == 1:
            mode = modes.pop()
        else:
            # Conflict: if NOTIFICATION_CHANNEL is set, use 'both' to send everywhere
            # Otherwise fall back to 'dm' (safest)
            if default_channel:
                log.warning(
                    f"[UNIFIED-DIGEST] Conflicting delivery modes for {schedule.value}: {modes}. "
                    f"Using 'both' mode since NOTIFICATION_CHANNEL is configured. "
                    f"Consider standardizing delivery config across profiles."
                )
                mode = "both"
            else:
                log.warning(
                    f"[UNIFIED-DIGEST] Conflicting delivery modes for {schedule.value}: {modes}. "
                    f"Falling back to 'dm'. Consider standardizing delivery config across profiles."
                )
                mode = "dm"

        # Determine target channel
        # Use NOTIFICATION_CHANNEL env var as fallback if no channels specified in profiles
        if len(channels) == 0:
            target_channel = default_channel if default_channel else None
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
        manual_trigger: bool = False,
        profile_ids: Optional[List[str]] = None,
    ):
        """Format and send a digest.

        Args:
            client: Telegram client for sending messages
            schedule: DigestSchedule type being sent
            messages: List of DigestMessage objects to include
            mode: Delivery mode (none|dm|digest|both)
            target_channel: Target channel/user for delivery
            since_hours: Time window for the digest
            manual_trigger: If True, use simpler format without schedule/time info
            profile_ids: List of profile IDs used for collection (for determining digest type)

        Delivery mode semantics:
            - none: No digest sent (save only)
            - dm: No digest sent (instant alerts only)
            - digest: Send digest to target
            - both: Send digest to target
        """
        # Normalize delivery mode (handles deprecated 'channel' → 'dm')
        try:
            mode = normalize_delivery_mode(mode) or "dm"
        except ValueError:
            log.warning(
                f"[UNIFIED-DIGEST] Invalid digest mode '{mode}' for {schedule.value}",
                extra={"mode": mode, "schedule": schedule.value},
            )
            return

        # Only 'digest' and 'both' modes send digests
        if mode not in ("digest", "both"):
            log.debug(
                f"[UNIFIED-DIGEST] Skipping digest send for mode '{mode}' "
                f"(only 'digest' and 'both' modes send digests)",
                extra={"mode": mode, "schedule": schedule.value},
            )
            return

        # Resolve target (supports username, ID, or defaults to "me")
        target = _resolve_target(target_channel)

        # Build digest content
        digest_text = self._format_digest(
            schedule, messages, since_hours, manual_trigger, profile_ids
        )

        # Split into chunks if too long (Telegram limit: 4096 chars)
        chunks = self._split_into_chunks(digest_text)

        # Determine targets based on mode
        targets = []
        if mode in ("dm", "both"):
            targets.append("me")  # Saved Messages
        if mode in ("digest", "both") and target:
            if target != "me":  # Don't duplicate if target is already "me"
                targets.append(target)

        if not targets:
            log.warning(
                f"[UNIFIED-DIGEST] No valid targets for {schedule.value} digest (mode={mode})",
                extra={"mode": mode, "schedule": schedule.value},
            )
            return

        log.info(
            f"[UNIFIED-DIGEST] Sending {schedule.value} digest "
            f"with {len(messages)} messages in {len(chunks)} part(s) to {len(targets)} target(s): {targets}",
            extra={
                "schedule": schedule.value,
                "message_count": len(messages),
                "chunk_count": len(chunks),
                "mode": mode,
                "targets": targets,
            },
        )

        try:
            for target_dest in targets:
                for i, chunk in enumerate(chunks):
                    part_header = (
                        f"[Part {i+1}/{len(chunks)}]\n"
                        if len(chunks) > 1 and i > 0
                        else ""
                    )
                    await client.send_message(
                        target_dest, part_header + chunk, link_preview=False
                    )
                log.info(
                    f"[UNIFIED-DIGEST] Sent {schedule.value} digest to {target_dest}",
                    extra={"schedule": schedule.value, "target": target_dest},
                )
        except Exception as e:
            log.error(
                f"[UNIFIED-DIGEST] Failed to send {schedule.value} digest: {e}",
                exc_info=True,
                extra={"schedule": schedule.value, "targets": targets},
            )

    def _format_digest(
        self,
        schedule: DigestSchedule,
        messages: List[DigestMessage],
        since_hours: int,
        manual_trigger: bool = False,
        profile_ids: Optional[List[str]] = None,
    ) -> str:
        """Format digest messages into readable text.

        Args:
            schedule: DigestSchedule type
            messages: List of DigestMessage objects
            since_hours: Time window for the digest
            manual_trigger: If True, use simpler format without schedule/time info
            profile_ids: List of profile IDs used for collection (for determining digest type)

        Returns:
            Formatted digest text with Markdown
        """
        # Count unique channels
        unique_chat_ids = {msg.chat_id for msg in messages}
        channel_count = len(unique_chat_ids)

        # Build header using global message format renderer
        if manual_trigger:
            # Manual trigger: simple format without schedule/time
            # Determine digest type based on feed type (inferred from profiles)
            # If we have interest profiles (3000+), it's an interests digest
            # If we have alert profiles (1000-1999), it's an alerts digest
            if profile_ids:
                has_interest_profiles = any(
                    pid.isdigit() and int(pid) >= 3000 for pid in profile_ids
                )
                has_alert_profiles = any(
                    pid.isdigit() and 1000 <= int(pid) < 2000 for pid in profile_ids
                )

                if has_interest_profiles:
                    digest_type = "Interests Digest"
                elif has_alert_profiles:
                    digest_type = "Alerts Digest"
                else:
                    digest_type = "Test Digest"
            else:
                digest_type = "Test Digest"

            schedule_str = "Manual"
        else:
            # Scheduled digest: include schedule and time range
            digest_type = f"{schedule.value.title()} Digest"
            schedule_str = f"last {since_hours}h"

        header = render_digest_header(
            top_n=len(messages),
            channel_count=channel_count,
            schedule=schedule_str,
            digest_type=digest_type,
        )
        lines = [header, ""]

        # Format each message using render_digest_entry for consistency
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

            # Parse trigger annotations for render_digest_entry
            parsed_triggers = []
            if msg.trigger_annotations:
                try:
                    annotations = json.loads(msg.trigger_annotations)
                    for category, keywords in annotations.items():
                        # Skip non-list fields (like semantic_scores dict)
                        if isinstance(keywords, list):
                            # Only take first 3 keywords per category to keep it concise
                            for kw in keywords[:3]:
                                # Filter out profile IDs (numeric strings like '1001', '3000')
                                # Only include actual keyword strings
                                kw_str = str(kw)
                                if not kw_str.isdigit():
                                    parsed_triggers.append((category, kw_str))
                except (json.JSONDecodeError, TypeError) as e:
                    log.warning(
                        "[UNIFIED-DIGEST] Failed to parse trigger_annotations",
                        extra={
                            "trigger_annotations": msg.trigger_annotations[:200],
                            "error": str(e),
                        },
                    )

            # Determine profile name for entry from matched_profiles
            # Look up actual profile names from config based on matched_profiles IDs
            entry_profile_name = None
            if msg.matched_profiles:
                # Get profile names from config for the first matched profile
                # (typically messages match 1-2 profiles, show the first one)
                profile_names = []
                for prof_id in msg.matched_profiles[:2]:  # Show up to 2 profiles
                    # Ensure prof_id is a string for lookup
                    prof_id_str = str(prof_id)
                    if prof_id_str in self.cfg.global_profiles:
                        profile = self.cfg.global_profiles[prof_id_str]
                        # ProfileDefinition is a dataclass, access name attribute
                        name = profile.name or prof_id_str
                        profile_names.append(name)
                    else:
                        available_keys = list(self.cfg.global_profiles.keys())[:5]
                        log.debug(
                            f"[UNIFIED-DIGEST] Profile ID {prof_id_str} not found "
                            f"in global_profiles (available: {available_keys})"
                        )
                if profile_names:
                    entry_profile_name = ", ".join(profile_names)
                elif msg.matched_profiles:
                    # Fallback to raw profile ID if name not configured
                    entry_profile_name = str(msg.matched_profiles[0])

            # Determine VIP status for this message
            is_vip = self._is_vip_sender(
                chat_id=msg.chat_id,
                sender_id=msg.sender_id,
                matched_profiles=msg.matched_profiles,
            )

            # Use render_digest_entry for consistent formatting
            entry = render_digest_entry(
                rank=idx,
                chat_title=f"[{msg.chat_title or f'Chat {msg.chat_id}'}]({msg_link})",
                message_text=msg.message_text or "[No content]",
                sender_name=msg.sender_name or "Unknown",
                score=msg.score,
                triggers=parsed_triggers if parsed_triggers else None,
                max_preview_length=150,
                timestamp=(
                    msg.created_at.isoformat()
                    if isinstance(msg.created_at, datetime)
                    else str(msg.created_at)
                ),
                message_link=msg_link,
                chat_id=msg.chat_id,
                msg_id=msg.msg_id,
                sender_id=msg.sender_id,
                profile_name=entry_profile_name,
                keyword_score=msg.keyword_score,
                semantic_score=msg.semantic_score,
                is_vip=is_vip,
            )

            lines.append(entry)

        return "\n".join(lines)

    def _is_vip_sender(
        self,
        chat_id: int,
        sender_id: int | None,
        matched_profiles: List[str] | None,
    ) -> bool:
        """Check configured VIP lists to mark VIP senders."""
        if not sender_id:
            return False

        for rule in self.cfg.channels or []:
            if rule.id == chat_id and sender_id in rule.vip_senders:
                return True

        if not matched_profiles:
            return False

        for profile_id in matched_profiles:
            profile = self.cfg.global_profiles.get(profile_id)
            if profile and sender_id in profile.vip_senders:
                return True

        return False

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
                "[UNIFIED-DIGEST] Failed to parse trigger_annotations",
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

            # Skip non-keyword categories (like semantic_scores, semantic_type, etc.)
            if not isinstance(keywords, (list, tuple)):
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
