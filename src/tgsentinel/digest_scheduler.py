"""Schedule discovery and digest execution coordination.

This module implements the DigestScheduler, which determines when digests are due
and discovers all profiles/entities that have enabled each schedule type.

State Persistence:
    All state is persisted to Redis. File-based persistence has been removed
    in favor of Redis-only storage for consistency and simplicity.
    Redis keys: tgsentinel:digest:last_run:{schedule}
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .config import AppCfg, DigestSchedule, ProfileDigestConfig, ScheduleConfig
from .redis_operations import RedisManager

log = logging.getLogger(__name__)


class DigestScheduler:
    """Discovers and coordinates due digests across all profiles.

    State Persistence:
        All schedule timestamps are persisted to Redis only.
        The state_file parameter is deprecated and ignored.
    """

    def __init__(
        self,
        cfg: AppCfg,
        state_file: Optional[str] = None,
        redis_manager: Optional[RedisManager] = None,
    ):
        """Initialize scheduler with application configuration.

        Args:
            cfg: Application configuration with profiles, channels, and users
            state_file: DEPRECATED - ignored, kept for API compatibility
            redis_manager: Redis manager for state persistence (required for persistence)
        """
        self.cfg = cfg
        # state_file is deprecated - all persistence is via Redis
        self._lock = threading.Lock()  # Protects concurrent access to self.last_run
        self.last_run: Dict[str, datetime] = {}  # schedule -> last run timestamp
        self.redis_manager = redis_manager

        # Load persisted state from Redis
        self._load_state_from_redis()

    def _load_state_from_redis(self) -> None:
        """Load schedule timestamps from Redis.

        This is the primary (and only) source of persisted state.
        Falls back to empty dict if Redis is unavailable.
        """
        if not self.redis_manager:
            log.debug("[DIGEST-SCHEDULER] No Redis manager; starting with empty state")
            return

        try:
            schedule_times = self.redis_manager.get_all_digest_schedule_times()
        except Exception as exc:
            log.warning("[DIGEST-SCHEDULER] Failed to load state from Redis: %s", exc)
            return

        parsed: Dict[str, datetime] = {}
        for name, timestamp in schedule_times.items():
            if not timestamp:
                continue
            try:
                dt = datetime.fromisoformat(timestamp)
                # Ensure timezone-aware
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                parsed[name] = dt
            except ValueError as e:
                log.warning(f"[DIGEST-SCHEDULER] Invalid timestamp for {name}: {e}")
                continue

        if not parsed:
            log.debug("[DIGEST-SCHEDULER] No schedule timestamps found in Redis")
            return

        with self._lock:
            self.last_run.update(parsed)

        log.info(
            "[DIGEST-SCHEDULER] Loaded %d schedule timestamps from Redis",
            len(parsed),
            extra={"source": "redis"},
        )

    def _save_state(self) -> None:
        """Save last_run state to Redis.

        Persists all schedule timestamps to Redis for recovery after restarts.
        Errors are logged but don't raise exceptions.
        """
        if not self.redis_manager:
            log.debug("[DIGEST-SCHEDULER] No Redis manager; skipping state save")
            return

        try:
            with self._lock:
                for schedule_name, timestamp in self.last_run.items():
                    self.redis_manager.set_digest_schedule_time(
                        schedule_name, timestamp.isoformat()
                    )
            log.debug(
                "[DIGEST-SCHEDULER] Saved %d schedule timestamps to Redis",
                len(self.last_run),
            )
        except Exception as e:
            log.error("[DIGEST-SCHEDULER] Failed to save state to Redis: %s", e)

    def get_due_schedules(self, now: Optional[datetime] = None) -> List[DigestSchedule]:
        """Determine which schedules are due to run now.

        Args:
            now: Current time (defaults to datetime.now(timezone.utc))

        Returns:
            List of DigestSchedule types that are due to run
        """
        if now is None:
            now = datetime.now(timezone.utc)

        due = []

        # Check each schedule type
        if self._is_hourly_due(now):
            due.append(DigestSchedule.HOURLY)

        if self._is_every_4h_due(now):
            due.append(DigestSchedule.EVERY_4H)

        if self._is_every_6h_due(now):
            due.append(DigestSchedule.EVERY_6H)

        if self._is_every_12h_due(now):
            due.append(DigestSchedule.EVERY_12H)

        if self._is_daily_due(now):
            due.append(DigestSchedule.DAILY)

        if self._is_weekly_due(now):
            due.append(DigestSchedule.WEEKLY)

        return due

    def _is_hourly_due(self, now: datetime) -> bool:
        """Check if hourly digest is due.

        Runs every hour, on the hour (e.g., 14:00, 15:00, 16:00).
        Catch-up: If last run was more than 1 hour ago, triggers immediately.

        Args:
            now: Current time

        Returns:
            True if hourly digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.HOURLY.value)

        if last is None:
            # First run: trigger if we're at minute 0-5
            return now.minute < 5

        # Catch-up: If more than 1 hour since last run, trigger immediately
        hours_since_last = (now - last).total_seconds() / 3600
        if hours_since_last >= 1.0:
            return True

        return False

    def _is_every_4h_due(self, now: datetime) -> bool:
        """Check if 4-hour digest is due.

        Runs at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC.
        Catch-up: If last run was more than 4 hours ago AND we're at a scheduled hour.

        Args:
            now: Current time

        Returns:
            True if 4-hour digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.EVERY_4H.value)

        scheduled_hours = (0, 4, 8, 12, 16, 20)

        if last is None:
            # First run: trigger if we're at one of the scheduled hours
            return now.hour in scheduled_hours and now.minute < 5

        # Must be at a scheduled hour
        if now.hour not in scheduled_hours:
            return False

        # Catch-up: If more than 4 hours since last run, trigger
        hours_since_last = (now - last).total_seconds() / 3600
        if hours_since_last >= 4.0:
            return True

        # Normal schedule: run at designated hours if hour changed
        return now.hour != last.hour

    def _is_every_6h_due(self, now: datetime) -> bool:
        """Check if 6-hour digest is due.

        Runs at 00:00, 06:00, 12:00, 18:00 UTC.
        Catch-up: If last run was more than 6 hours ago AND we're at a scheduled hour.

        Args:
            now: Current time

        Returns:
            True if 6-hour digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.EVERY_6H.value)

        scheduled_hours = (0, 6, 12, 18)

        if last is None:
            return now.hour in scheduled_hours and now.minute < 5

        # Must be at a scheduled hour
        if now.hour not in scheduled_hours:
            return False

        # Catch-up: If more than 6 hours since last run, trigger
        hours_since_last = (now - last).total_seconds() / 3600
        if hours_since_last >= 6.0:
            return True

        # Normal schedule: run at designated hours if hour changed
        return now.hour != last.hour

    def _is_every_12h_due(self, now: datetime) -> bool:
        """Check if 12-hour digest is due.

        Runs at 00:00 and 12:00 UTC.
        Catch-up: If last run was more than 12 hours ago AND we're at a scheduled hour.

        Args:
            now: Current time

        Returns:
            True if 12-hour digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.EVERY_12H.value)

        scheduled_hours = (0, 12)

        if last is None:
            return now.hour in scheduled_hours and now.minute < 5

        # Must be at a scheduled hour
        if now.hour not in scheduled_hours:
            return False

        # Catch-up: If more than 12 hours since last run, trigger
        hours_since_last = (now - last).total_seconds() / 3600
        if hours_since_last >= 12.0:
            return True

        # Normal schedule: run at designated hours if hour changed
        return now.hour != last.hour

    def _is_daily_due(self, now: datetime) -> bool:
        """Check if daily digest is due.

        Runs once per day at configured hour (default: 08:00 UTC).
        Catch-up: If last run was more than 24 hours ago, triggers immediately.

        Args:
            now: Current time

        Returns:
            True if daily digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.DAILY.value)
        daily_hour = self._get_daily_hour()

        if last is None:
            # First run: trigger if we're at the configured hour
            return now.hour == daily_hour and now.minute < 5

        # Catch-up: If more than 24 hours since last run, trigger immediately
        hours_since_last = (now - last).total_seconds() / 3600
        if hours_since_last >= 24.0:
            return True

        # Normal schedule: run once per day at configured hour
        return now.date() > last.date() and now.hour == daily_hour

    def _is_weekly_due(self, now: datetime) -> bool:
        """Check if weekly digest is due.

        Runs once per week on configured day+hour (default: Monday 08:00 UTC).
        Catch-up: If last run was more than 7 days ago, triggers immediately.

        Args:
            now: Current time

        Returns:
            True if weekly digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.WEEKLY.value)
        weekly_day, weekly_hour = self._get_weekly_schedule()

        if last is None:
            # First run: trigger if we're at the configured day+hour
            return (
                now.weekday() == weekly_day
                and now.hour == weekly_hour
                and now.minute < 5
            )

        # Catch-up: If more than 7 days since last run, trigger immediately
        days_since_last = (now.date() - last.date()).days
        if days_since_last >= 7:
            return True

        # Normal schedule: run once per week at configured day+hour
        return (
            days_since_last >= 7
            and now.weekday() == weekly_day
            and now.hour == weekly_hour
        )

    def _get_daily_hour(self) -> int:
        """Get daily digest hour respecting precedence hierarchy.

        Precedence (highest to lowest):
        1. channel.digest / user.digest (entity-level)
        2. channel.overrides.digest / user.overrides.digest
        3. profile.digest (global profile)
        4. Default (8)

        If multiple entities at same level disagree, use consensus + warn.

        Returns:
            Hour in UTC (0-23), defaults to 8
        """
        # Priority 1: Entity-level digest configs
        entity_hours = []
        for channel in self.cfg.channels:
            if channel.digest:
                for sched_cfg in channel.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.DAILY:
                        entity_hours.append(sched_cfg.daily_hour)

        for user in self.cfg.monitored_users:
            if user.digest:
                for sched_cfg in user.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.DAILY:
                        entity_hours.append(sched_cfg.daily_hour)

        if entity_hours:
            return self._select_hour_with_consensus(entity_hours, level="entity-level")

        # Priority 2: Override-level digest configs
        override_hours = []
        for channel in self.cfg.channels:
            if channel.overrides and channel.overrides.digest:
                for sched_cfg in channel.overrides.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.DAILY:
                        override_hours.append(sched_cfg.daily_hour)

        for user in self.cfg.monitored_users:
            if user.overrides and user.overrides.digest:
                for sched_cfg in user.overrides.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.DAILY:
                        override_hours.append(sched_cfg.daily_hour)

        if override_hours:
            return self._select_hour_with_consensus(
                override_hours, level="override-level"
            )

        # Priority 3: Global profile configs
        profile_hours = []
        for profile in self.cfg.global_profiles.values():
            if profile.digest:
                for sched_cfg in profile.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.DAILY:
                        profile_hours.append(sched_cfg.daily_hour)

        if profile_hours:
            return self._select_hour_with_consensus(
                profile_hours, level="global-profile"
            )

        # Default
        return 8

    def _select_hour_with_consensus(self, hours: List[int], level: str) -> int:
        """Select hour using consensus, warn if disagreement.

        Args:
            hours: List of hour values to choose from
            level: Description of precedence level for logging

        Returns:
            Most common hour value
        """
        from collections import Counter

        hour_counts = Counter(hours)
        consensus_hour = hour_counts.most_common(1)[0][0]

        if len(set(hours)) > 1:
            log.warning(
                f"[DIGEST-SCHEDULER] Multiple daily_hour values at {level}: {set(hours)}. "
                f"Using consensus: {consensus_hour}"
            )

        return consensus_hour

    def _get_weekly_schedule(self) -> Tuple[int, int]:
        """Get weekly digest schedule respecting precedence hierarchy.

        Precedence (highest to lowest):
        1. channel.digest / user.digest (entity-level)
        2. channel.overrides.digest / user.overrides.digest
        3. profile.digest (global profile)
        4. Default (Monday 08:00)

        If multiple entities at same level disagree, use consensus + warn.

        Returns:
            Tuple of (weekday, hour) where weekday is 0-6 (Mon-Sun)
        """
        # Priority 1: Entity-level digest configs
        entity_schedules = []
        for channel in self.cfg.channels:
            if channel.digest:
                for sched_cfg in channel.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.WEEKLY:
                        entity_schedules.append(
                            (sched_cfg.weekly_day, sched_cfg.weekly_hour)
                        )

        for user in self.cfg.monitored_users:
            if user.digest:
                for sched_cfg in user.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.WEEKLY:
                        entity_schedules.append(
                            (sched_cfg.weekly_day, sched_cfg.weekly_hour)
                        )

        if entity_schedules:
            return self._select_schedule_with_consensus(
                entity_schedules, level="entity-level"
            )

        # Priority 2: Override-level digest configs
        override_schedules = []
        for channel in self.cfg.channels:
            if channel.overrides and channel.overrides.digest:
                for sched_cfg in channel.overrides.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.WEEKLY:
                        override_schedules.append(
                            (sched_cfg.weekly_day, sched_cfg.weekly_hour)
                        )

        for user in self.cfg.monitored_users:
            if user.overrides and user.overrides.digest:
                for sched_cfg in user.overrides.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.WEEKLY:
                        override_schedules.append(
                            (sched_cfg.weekly_day, sched_cfg.weekly_hour)
                        )

        if override_schedules:
            return self._select_schedule_with_consensus(
                override_schedules, level="override-level"
            )

        # Priority 3: Global profile configs
        profile_schedules = []
        for profile in self.cfg.global_profiles.values():
            if profile.digest:
                for sched_cfg in profile.digest.schedules:
                    if sched_cfg.schedule == DigestSchedule.WEEKLY:
                        profile_schedules.append(
                            (sched_cfg.weekly_day, sched_cfg.weekly_hour)
                        )

        if profile_schedules:
            return self._select_schedule_with_consensus(
                profile_schedules, level="global-profile"
            )

        # Default
        return (0, 8)  # Monday 08:00 UTC

    def _select_schedule_with_consensus(
        self, schedules: List[Tuple[int, int]], level: str
    ) -> Tuple[int, int]:
        """Select weekly schedule using consensus, warn if disagreement.

        Args:
            schedules: List of (day, hour) tuples
            level: Description of precedence level for logging

        Returns:
            Most common (day, hour) tuple
        """
        from collections import Counter

        schedule_counts = Counter(schedules)
        consensus_schedule = schedule_counts.most_common(1)[0][0]

        if len(set(schedules)) > 1:
            log.warning(
                f"[DIGEST-SCHEDULER] Multiple weekly schedules at {level}: {set(schedules)}. "
                f"Using consensus: {consensus_schedule}"
            )

        return consensus_schedule

    def discover_profile_schedules(
        self, schedule: DigestSchedule
    ) -> List[Tuple[str, ProfileDigestConfig, List[str]]]:
        """Discover all profiles/entities with a specific schedule enabled.

        Args:
            schedule: The schedule type to discover (HOURLY, DAILY, etc.)

        Returns:
            List of (identifier, digest_config, profile_ids) tuples where:
            - identifier: String like "profile:security", "channel:-1001234" (for logging)
            - digest_config: ProfileDigestConfig for that entity
            - profile_ids: Actual profile IDs to match in DB (e.g., ["security", "tech"])

        Example:
            [("profile:security", security_digest_cfg, ["security"]),
             ("channel:-1001234567890", channel_digest_cfg, ["security", "tech"])]
        """
        results = []

        # 1. Scan global profiles
        for profile_id, profile in self.cfg.global_profiles.items():
            if profile.digest:
                for sched_cfg in profile.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append(
                            (
                                f"profile:{profile_id}",
                                profile.digest,
                                [profile_id],  # Plain profile ID for DB matching
                            )
                        )
                        break  # Only add once per profile

        # 2. Scan channel-level digest configs
        for channel in self.cfg.channels:
            # Check channel.digest (highest precedence)
            if channel.digest:
                for sched_cfg in channel.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append(
                            (
                                f"channel:{channel.id}",
                                channel.digest,
                                channel.profiles,  # Bound profile IDs
                            )
                        )
                        break

            # Check channel.overrides.digest (if no channel.digest)
            elif channel.overrides and channel.overrides.digest:
                for sched_cfg in channel.overrides.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append(
                            (
                                f"channel:{channel.id}_override",
                                channel.overrides.digest,
                                channel.profiles,  # Bound profile IDs
                            )
                        )
                        break

        # 3. Scan user-level digest configs
        for user in self.cfg.monitored_users:
            # Check user.digest
            if user.digest:
                for sched_cfg in user.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append(
                            (
                                f"user:{user.id}",
                                user.digest,
                                user.profiles if hasattr(user, "profiles") else [],
                            )
                        )
                        break

            # Check user.overrides.digest (if no user.digest)
            elif user.overrides and user.overrides.digest:
                for sched_cfg in user.overrides.digest.schedules:
                    if sched_cfg.schedule == schedule and sched_cfg.enabled:
                        results.append(
                            (
                                f"user:{user.id}_override",
                                user.overrides.digest,
                                user.profiles if hasattr(user, "profiles") else [],
                            )
                        )
                        break

        return results

    def get_schedule_config(
        self, digest_config: ProfileDigestConfig, schedule: DigestSchedule
    ) -> Optional[ScheduleConfig]:
        """Get the specific schedule config for a given schedule type.

        Args:
            digest_config: The digest configuration to search
            schedule: The schedule type to find

        Returns:
            ScheduleConfig if found, None otherwise
        """
        for sched_cfg in digest_config.schedules:
            if sched_cfg.schedule == schedule and sched_cfg.enabled:
                return sched_cfg
        return None

    def mark_schedule_run(
        self, schedule: DigestSchedule, run_time: Optional[datetime] = None
    ):
        """Mark that a schedule has been executed.

        Args:
            schedule: The schedule that was executed
            run_time: Time of execution (defaults to now)
        """
        if run_time is None:
            run_time = datetime.now(timezone.utc)

        with self._lock:
            self.last_run[schedule.value] = run_time

        log.info(
            f"[DIGEST-SCHEDULER] Marked {schedule.value} as executed",
            extra={"schedule": schedule.value, "timestamp": run_time.isoformat()},
        )

        # Persist individual schedule to Redis immediately
        if self.redis_manager:
            try:
                self.redis_manager.set_digest_schedule_time(
                    schedule.value, run_time.isoformat()
                )
            except Exception as exc:
                log.warning(
                    "[DIGEST-SCHEDULER] Failed to persist %s to Redis: %s",
                    schedule.value,
                    exc,
                )

    def get_schedule_last_run(self, schedule: DigestSchedule) -> Optional[datetime]:
        """Get the last run time for a schedule.

        Args:
            schedule: The schedule to query

        Returns:
            Last run datetime or None if never run
        """
        with self._lock:
            return self.last_run.get(schedule.value)

    def get_all_schedule_times(self) -> Dict[str, Optional[str]]:
        """Get last run times for all schedules.

        Returns:
            Dictionary mapping schedule name to ISO timestamp (or None)
        """
        with self._lock:
            return {
                sched.value: (
                    self.last_run[sched.value].isoformat()
                    if sched.value in self.last_run
                    else None
                )
                for sched in DigestSchedule
                if sched != DigestSchedule.NONE
            }
