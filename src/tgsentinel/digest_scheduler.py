"""Schedule discovery and digest execution coordination.

This module implements the DigestScheduler, which determines when digests are due
and discovers all profiles/entities that have enabled each schedule type.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import AppCfg, DigestSchedule, ProfileDigestConfig, ScheduleConfig

log = logging.getLogger(__name__)


class DigestScheduler:
    """Discovers and coordinates due digests across all profiles."""

    def __init__(self, cfg: AppCfg, state_file: Optional[str] = None):
        """Initialize scheduler with application configuration.

        Args:
            cfg: Application configuration with profiles, channels, and users
            state_file: Path to state file for persisting last_run timestamps
                       (defaults to data/digest_scheduler_state.json)
        """
        self.cfg = cfg
        self._state_file = (
            None
            if state_file == ":memory:"
            else (state_file or "data/digest_scheduler_state.json")
        )
        self._lock = threading.Lock()  # Protects concurrent access to self.last_run
        self.last_run: Dict[str, datetime] = {}  # schedule -> last run timestamp

        # Load persisted state
        self._load_state()

    def _load_state(self) -> None:
        """Load persisted last_run state from disk.

        Falls back to empty dict on missing or corrupt file.
        Non-fatal errors are logged but don't block initialization.
        """
        if self._state_file is None:
            log.debug("[DIGEST-SCHEDULER] Memory-only mode; skipping state load")
            return

        try:
            state_path = Path(self._state_file)
            if not state_path.exists():
                log.debug(
                    f"[DIGEST-SCHEDULER] State file not found, starting with empty state: {self._state_file}"
                )
                return

            with open(state_path, "r") as f:
                data = json.load(f)

            # Parse ISO timestamps back to datetime objects in temporary dict
            # Do NOT hold lock during parsing and logging
            temp_state: Dict[str, datetime] = {}
            for schedule_name, timestamp_str in data.items():
                try:
                    temp_state[schedule_name] = datetime.fromisoformat(timestamp_str)
                except (ValueError, TypeError) as e:
                    log.warning(
                        f"[DIGEST-SCHEDULER] Invalid timestamp for {schedule_name}: {e}"
                    )

            # Acquire lock only to update self.last_run
            with self._lock:
                self.last_run.update(temp_state)

            log.info(
                f"[DIGEST-SCHEDULER] Loaded state with {len(temp_state)} schedule timestamps"
            )

        except json.JSONDecodeError as e:
            log.warning(f"[DIGEST-SCHEDULER] Corrupt state file, starting fresh: {e}")
        except Exception as e:
            log.error(f"[DIGEST-SCHEDULER] Failed to load state file (non-fatal): {e}")

    def _save_state(self) -> None:
        """Atomically save last_run state to disk.

        Uses atomic write (temp file + rename) to prevent corruption.
        Errors are logged but don't raise exceptions.
        """
        if self._state_file is None:
            # Memory-only mode; nothing to persist
            return

        try:
            # Serialize datetime objects to ISO strings
            with self._lock:
                data = {
                    schedule_name: timestamp.isoformat()
                    for schedule_name, timestamp in self.last_run.items()
                }

            state_path = Path(self._state_file)

            # Ensure parent directory exists
            state_path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write: write to temp file, then rename
            temp_fd, temp_path = tempfile.mkstemp(
                dir=state_path.parent,
                prefix=f".{state_path.name}.tmp.",
                suffix=".json",
                text=True,
            )

            try:
                with os.fdopen(temp_fd, "w") as f:
                    json.dump(data, f, indent=2)

                # Atomic rename
                os.replace(temp_path, state_path)

                log.debug(
                    f"[DIGEST-SCHEDULER] Saved state with {len(data)} schedule timestamps"
                )

            except Exception as write_error:
                # Clean up temp file on write failure
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
                raise write_error

        except Exception as e:
            log.error(f"[DIGEST-SCHEDULER] Failed to save state file (non-fatal): {e}")

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

        # Run if hour changed
        return now.hour != last.hour

    def _is_every_4h_due(self, now: datetime) -> bool:
        """Check if 4-hour digest is due.

        Runs at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC.

        Args:
            now: Current time

        Returns:
            True if 4-hour digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.EVERY_4H.value)

        if last is None:
            # First run: trigger if we're at one of the scheduled hours
            return now.hour in (0, 4, 8, 12, 16, 20) and now.minute < 5

        # Run at scheduled hours if hour changed
        return now.hour in (0, 4, 8, 12, 16, 20) and now.hour != last.hour

    def _is_every_6h_due(self, now: datetime) -> bool:
        """Check if 6-hour digest is due.

        Runs at 00:00, 06:00, 12:00, 18:00 UTC.

        Args:
            now: Current time

        Returns:
            True if 6-hour digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.EVERY_6H.value)

        if last is None:
            return now.hour in (0, 6, 12, 18) and now.minute < 5

        return now.hour in (0, 6, 12, 18) and now.hour != last.hour

    def _is_every_12h_due(self, now: datetime) -> bool:
        """Check if 12-hour digest is due.

        Runs at 00:00 and 12:00 UTC.

        Args:
            now: Current time

        Returns:
            True if 12-hour digest should run
        """
        with self._lock:
            last = self.last_run.get(DigestSchedule.EVERY_12H.value)

        if last is None:
            return now.hour in (0, 12) and now.minute < 5

        return now.hour in (0, 12) and now.hour != last.hour

    def _is_daily_due(self, now: datetime) -> bool:
        """Check if daily digest is due.

        Runs once per day at configured hour (default: 08:00 UTC).

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

        # Run once per day at configured hour
        return now.date() > last.date() and now.hour == daily_hour

    def _is_weekly_due(self, now: datetime) -> bool:
        """Check if weekly digest is due.

        Runs once per week on configured day+hour (default: Monday 08:00 UTC).

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

        # Run once per week at configured day+hour
        days_since_last = (now.date() - last.date()).days
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

        # Persist state to disk
        self._save_state()

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
