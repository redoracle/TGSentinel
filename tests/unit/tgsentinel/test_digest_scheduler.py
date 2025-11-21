"""Unit tests for Phase 3: DigestScheduler.

Tests the schedule discovery and due schedule detection logic.
"""

from datetime import datetime, timezone

import pytest

from src.tgsentinel.config import (
    AlertsCfg,
    AppCfg,
    ChannelOverrides,
    ChannelRule,
    DigestSchedule,
    MonitoredUser,
    ProfileDefinition,
    ProfileDigestConfig,
    ScheduleConfig,
    SystemCfg,
)
from src.tgsentinel.digest_scheduler import DigestScheduler


def create_test_app_cfg(
    global_profiles=None, channels=None, monitored_users=None
) -> AppCfg:
    """Helper to create test AppCfg with minimal required fields."""
    return AppCfg(
        telegram_session="test.session",
        api_id=12345,
        api_hash="test",
        alerts=AlertsCfg(),
        channels=channels or [],
        monitored_users=monitored_users or [],
        interests=[],
        system=SystemCfg(),
        embeddings_model=None,
        global_profiles=global_profiles or {},
        similarity_threshold=0.42,
    )


class TestScheduleDueDetection:
    """Tests for due schedule detection methods."""

    def test_hourly_due_first_run(self):
        """Test hourly digest is due on first run if minute < 5."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # First run at 14:02 should be due
        now = datetime(2025, 11, 20, 14, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_hourly_due(now) is True

        # First run at 14:07 should NOT be due (too late in the hour)
        now = datetime(2025, 11, 20, 14, 7, 0, tzinfo=timezone.utc)
        assert scheduler._is_hourly_due(now) is False

    def test_hourly_due_after_previous_run(self):
        """Test hourly digest runs when hour changes."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # Mark previous run at 14:00
        prev_run = datetime(2025, 11, 20, 14, 0, 0, tzinfo=timezone.utc)
        scheduler.mark_schedule_run(DigestSchedule.HOURLY, prev_run)

        # Same hour: not due
        now = datetime(2025, 11, 20, 14, 30, 0, tzinfo=timezone.utc)
        assert scheduler._is_hourly_due(now) is False

        # Next hour: due
        now = datetime(2025, 11, 20, 15, 1, 0, tzinfo=timezone.utc)
        assert scheduler._is_hourly_due(now) is True

    def test_every_4h_due_detection(self):
        """Test 4-hour digest runs at correct times."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # First run at scheduled hour
        now = datetime(2025, 11, 20, 8, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_every_4h_due(now) is True

        # Mark as run
        scheduler.mark_schedule_run(DigestSchedule.EVERY_4H, now)

        # Same hour: not due
        now = datetime(2025, 11, 20, 8, 30, 0, tzinfo=timezone.utc)
        assert scheduler._is_every_4h_due(now) is False

        # Non-scheduled hour: not due
        now = datetime(2025, 11, 20, 10, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_every_4h_due(now) is False

        # Next scheduled hour: due
        now = datetime(2025, 11, 20, 12, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_every_4h_due(now) is True

    def test_every_6h_due_detection(self):
        """Test 6-hour digest runs at 0, 6, 12, 18."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # Scheduled hours
        for hour in (0, 6, 12, 18):
            now = datetime(2025, 11, 20, hour, 2, 0, tzinfo=timezone.utc)
            assert scheduler._is_every_6h_due(now) is True

        # Mark as run at 6:00
        scheduler.mark_schedule_run(
            DigestSchedule.EVERY_6H,
            datetime(2025, 11, 20, 6, 0, 0, tzinfo=timezone.utc),
        )

        # Non-scheduled hours
        for hour in (1, 3, 9, 15):
            now = datetime(2025, 11, 20, hour, 2, 0, tzinfo=timezone.utc)
            assert scheduler._is_every_6h_due(now) is False

    def test_every_12h_due_detection(self):
        """Test 12-hour digest runs at 0 and 12."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # Scheduled hours
        now = datetime(2025, 11, 20, 0, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_every_12h_due(now) is True

        now = datetime(2025, 11, 20, 12, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_every_12h_due(now) is True

        # Mark as run at 0:00
        scheduler.mark_schedule_run(
            DigestSchedule.EVERY_12H,
            datetime(2025, 11, 20, 0, 0, 0, tzinfo=timezone.utc),
        )

        # Non-scheduled hours
        now = datetime(2025, 11, 20, 6, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_every_12h_due(now) is False

    def test_daily_due_detection(self):
        """Test daily digest runs once per day at configured hour."""
        profile = ProfileDefinition(
            id="test",
            name="Test",
            digest=ProfileDigestConfig(
                schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY, daily_hour=9)],
            ),
        )

        cfg = create_test_app_cfg(
            global_profiles={"test": profile},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # First run at configured hour
        now = datetime(2025, 11, 20, 9, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_daily_due(now) is True

        # Mark as run
        scheduler.mark_schedule_run(DigestSchedule.DAILY, now)

        # Same day, same hour: not due
        now = datetime(2025, 11, 20, 9, 30, 0, tzinfo=timezone.utc)
        assert scheduler._is_daily_due(now) is False

        # Next day, before configured hour: not due
        now = datetime(2025, 11, 21, 8, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_daily_due(now) is False

        # Next day, at configured hour: due
        now = datetime(2025, 11, 21, 9, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_daily_due(now) is True

    def test_weekly_due_detection(self):
        """Test weekly digest runs once per week on configured day+hour."""
        profile = ProfileDefinition(
            id="test",
            name="Test",
            digest=ProfileDigestConfig(
                schedules=[
                    ScheduleConfig(
                        schedule=DigestSchedule.WEEKLY,
                        weekly_day=0,  # Monday
                        weekly_hour=10,
                    )
                ],
            ),
        )

        cfg = create_test_app_cfg(
            global_profiles={"test": profile},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # First run on Monday at 10:02
        # Nov 18, 2025 is a Tuesday, so Nov 17 is Monday
        now = datetime(2025, 11, 17, 10, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_weekly_due(now) is True

        # Mark as run
        scheduler.mark_schedule_run(DigestSchedule.WEEKLY, now)

        # Same week, same day: not due
        now = datetime(2025, 11, 17, 14, 0, 0, tzinfo=timezone.utc)
        assert scheduler._is_weekly_due(now) is False

        # Next Monday, at configured hour: due
        now = datetime(2025, 11, 24, 10, 2, 0, tzinfo=timezone.utc)
        assert scheduler._is_weekly_due(now) is True


class TestGetDueSchedules:
    """Tests for get_due_schedules method."""

    def test_no_schedules_due(self):
        """Test when no schedules are due."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # Mark all schedules as recently run
        now = datetime(2025, 11, 20, 14, 30, 0, tzinfo=timezone.utc)
        scheduler.mark_schedule_run(DigestSchedule.HOURLY, now)
        scheduler.mark_schedule_run(DigestSchedule.EVERY_4H, now)
        scheduler.mark_schedule_run(DigestSchedule.EVERY_6H, now)
        scheduler.mark_schedule_run(DigestSchedule.EVERY_12H, now)
        scheduler.mark_schedule_run(DigestSchedule.DAILY, now)
        scheduler.mark_schedule_run(DigestSchedule.WEEKLY, now)

        # Check 10 minutes later: nothing due (not on the hour)
        check_time = datetime(2025, 11, 20, 14, 40, 0, tzinfo=timezone.utc)
        due = scheduler.get_due_schedules(check_time)
        assert due == []

    def test_hourly_due(self):
        """Test hourly schedule detection."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # Mark hourly as run at 14:00
        scheduler.mark_schedule_run(
            DigestSchedule.HOURLY,
            datetime(2025, 11, 20, 14, 0, 0, tzinfo=timezone.utc),
        )

        # Check at 15:02: hourly should be due
        now = datetime(2025, 11, 20, 15, 2, 0, tzinfo=timezone.utc)
        due = scheduler.get_due_schedules(now)
        assert DigestSchedule.HOURLY in due

    def test_multiple_schedules_due(self):
        """Test multiple schedules can be due simultaneously."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        # At 12:02, hourly, every_4h, every_6h, and every_12h could all be due
        now = datetime(2025, 11, 20, 12, 2, 0, tzinfo=timezone.utc)
        due = scheduler.get_due_schedules(now)

        # Should include multiple schedules
        assert DigestSchedule.HOURLY in due
        assert DigestSchedule.EVERY_4H in due
        assert DigestSchedule.EVERY_6H in due
        assert DigestSchedule.EVERY_12H in due


class TestDiscoverProfileSchedules:
    """Tests for discover_profile_schedules method."""

    def test_no_profiles_with_schedule(self):
        """Test when no profiles have the requested schedule."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        results = scheduler.discover_profile_schedules(DigestSchedule.HOURLY)
        assert results == []

    def test_discover_global_profile(self):
        """Test discovering schedule from global profile."""
        profile = ProfileDefinition(
            id="security",
            name="Security",
            digest=ProfileDigestConfig(
                schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
            ),
        )

        cfg = create_test_app_cfg(
            global_profiles={"security": profile},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        results = scheduler.discover_profile_schedules(DigestSchedule.HOURLY)
        assert len(results) == 1
        assert results[0][0] == "profile:security"
        assert results[0][1] == profile.digest

    def test_discover_channel_digest(self):
        """Test discovering schedule from channel-level digest."""
        channel = ChannelRule(
            id=-1001234567890,
            name="Test Channel",
            profiles=["basic"],
            digest=ProfileDigestConfig(
                schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
            ),
        )

        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[channel],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        results = scheduler.discover_profile_schedules(DigestSchedule.DAILY)
        assert len(results) == 1
        assert results[0][0] == "channel:-1001234567890"
        assert results[0][1] == channel.digest

    def test_discover_user_digest(self):
        """Test discovering schedule from user-level digest."""
        user = MonitoredUser(
            id=123456789,
            name="VIP User",
            profiles=["vip"],
            digest=ProfileDigestConfig(
                schedules=[ScheduleConfig(schedule=DigestSchedule.WEEKLY)],
            ),
        )

        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[user],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        results = scheduler.discover_profile_schedules(DigestSchedule.WEEKLY)
        assert len(results) == 1
        assert results[0][0] == "user:123456789"
        assert results[0][1] == user.digest

    def test_discover_multiple_sources(self):
        """Test discovering schedules from multiple sources."""
        profile = ProfileDefinition(
            id="security",
            name="Security",
            digest=ProfileDigestConfig(
                schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
            ),
        )

        channel = ChannelRule(
            id=-1001234567890,
            name="Trading",
            profiles=["trading"],
            digest=ProfileDigestConfig(
                schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
            ),
        )

        user = MonitoredUser(
            id=123456789,
            name="VIP",
            profiles=["vip"],
            digest=ProfileDigestConfig(
                schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
            ),
        )

        cfg = create_test_app_cfg(
            global_profiles={"security": profile},
            channels=[channel],
            monitored_users=[user],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        results = scheduler.discover_profile_schedules(DigestSchedule.HOURLY)
        assert len(results) == 3
        identifiers = [r[0] for r in results]
        assert "profile:security" in identifiers
        assert "channel:-1001234567890" in identifiers
        assert "user:123456789" in identifiers

    def test_disabled_schedule_not_discovered(self):
        """Test that disabled schedules are not discovered."""
        profile = ProfileDefinition(
            id="security",
            name="Security",
            digest=ProfileDigestConfig(
                schedules=[
                    ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=False)
                ],
            ),
        )

        cfg = create_test_app_cfg(
            global_profiles={"security": profile},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        results = scheduler.discover_profile_schedules(DigestSchedule.HOURLY)
        assert results == []


class TestScheduleConfigRetrieval:
    """Tests for get_schedule_config method."""

    def test_get_existing_schedule_config(self):
        """Test retrieving existing schedule config."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        digest_config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=7.0, top_n=5),
                ScheduleConfig(schedule=DigestSchedule.DAILY, daily_hour=9),
            ],
        )

        sched_cfg = scheduler.get_schedule_config(digest_config, DigestSchedule.HOURLY)
        assert sched_cfg is not None
        assert sched_cfg.schedule == DigestSchedule.HOURLY
        assert sched_cfg.min_score == 7.0
        assert sched_cfg.top_n == 5

    def test_get_nonexistent_schedule_config(self):
        """Test retrieving non-existent schedule config."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        digest_config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
        )

        sched_cfg = scheduler.get_schedule_config(digest_config, DigestSchedule.WEEKLY)
        assert sched_cfg is None

    def test_disabled_schedule_returns_none(self):
        """Test that disabled schedule config returns None."""
        cfg = create_test_app_cfg(
            global_profiles={},
            channels=[],
            monitored_users=[],
        )
        scheduler = DigestScheduler(cfg, state_file=":memory:")

        digest_config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=False)],
        )

        sched_cfg = scheduler.get_schedule_config(digest_config, DigestSchedule.HOURLY)
        assert sched_cfg is None
