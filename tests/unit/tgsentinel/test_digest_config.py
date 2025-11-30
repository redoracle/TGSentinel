"""Unit tests for digest configuration data models (Phase 1).

Tests the new DigestSchedule enum, ScheduleConfig, and ProfileDigestConfig
classes added in Phase 1 of the schedule-driven digest implementation.
"""

import pytest

from src.tgsentinel.config import (
    DigestSchedule,
    ProfileDigestConfig,
    ScheduleConfig,
    _load_global_profiles,
)


class TestDigestSchedule:
    """Tests for DigestSchedule enum."""

    def test_all_schedule_types(self):
        """Test that all expected schedule types are defined."""
        assert DigestSchedule.HOURLY == "hourly"
        assert DigestSchedule.EVERY_4H == "every_4h"
        assert DigestSchedule.EVERY_6H == "every_6h"
        assert DigestSchedule.EVERY_12H == "every_12h"
        assert DigestSchedule.DAILY == "daily"
        assert DigestSchedule.WEEKLY == "weekly"
        assert DigestSchedule.NONE == "none"

    def test_enum_from_string(self):
        """Test that schedule can be created from string values."""
        assert DigestSchedule("hourly") == DigestSchedule.HOURLY
        assert DigestSchedule("daily") == DigestSchedule.DAILY
        assert DigestSchedule("none") == DigestSchedule.NONE

    def test_invalid_schedule_raises(self):
        """Test that invalid schedule string raises ValueError."""
        with pytest.raises(ValueError):
            DigestSchedule("invalid_schedule")


class TestScheduleConfig:
    """Tests for ScheduleConfig dataclass."""

    def test_default_values(self):
        """Test that ScheduleConfig has sensible defaults."""
        config = ScheduleConfig(schedule=DigestSchedule.HOURLY)
        assert config.schedule == DigestSchedule.HOURLY
        assert config.enabled is True
        assert config.top_n is None
        assert config.min_score is None
        assert config.daily_hour == 8
        assert config.weekly_day == 0
        assert config.weekly_hour == 8

    def test_with_overrides(self):
        """Test ScheduleConfig with custom values."""
        config = ScheduleConfig(
            schedule=DigestSchedule.DAILY,
            enabled=True,
            top_n=20,
            min_score=6.5,
            daily_hour=14,
        )
        assert config.schedule == DigestSchedule.DAILY
        assert config.enabled is True
        assert config.top_n == 20
        assert config.min_score == 6.5
        assert config.daily_hour == 14

    def test_string_schedule_conversion(self):
        """Test that string schedule is converted to enum."""
        config = ScheduleConfig(schedule="hourly")  # type: ignore[arg-type]
        assert config.schedule == DigestSchedule.HOURLY
        assert isinstance(config.schedule, DigestSchedule)

    def test_invalid_daily_hour_raises(self):
        """Test that invalid daily_hour raises ValueError."""
        with pytest.raises(ValueError, match="daily_hour must be 0-23"):
            ScheduleConfig(schedule=DigestSchedule.DAILY, daily_hour=25)

    def test_invalid_weekly_day_raises(self):
        """Test that invalid weekly_day raises ValueError."""
        with pytest.raises(ValueError, match="weekly_day must be 0-6"):
            ScheduleConfig(schedule=DigestSchedule.WEEKLY, weekly_day=7)

    def test_invalid_weekly_hour_raises(self):
        """Test that invalid weekly_hour raises ValueError."""
        with pytest.raises(ValueError, match="weekly_hour must be 0-23"):
            ScheduleConfig(schedule=DigestSchedule.WEEKLY, weekly_hour=24)

    def test_invalid_min_score_raises(self):
        """Test that min_score outside 0.0-10.0 raises ValueError."""
        with pytest.raises(ValueError, match="min_score must be 0.0-10.0"):
            ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=11.0)

        with pytest.raises(ValueError, match="min_score must be 0.0-10.0"):
            ScheduleConfig(schedule=DigestSchedule.HOURLY, min_score=-1.0)


class TestProfileDigestConfig:
    """Tests for ProfileDigestConfig dataclass."""

    def test_empty_config(self):
        """Test ProfileDigestConfig with no schedules."""
        config = ProfileDigestConfig()
        assert config.schedules == []
        assert config.top_n == 10
        assert config.min_score == 5.0
        # NOTE: mode and target_channel removed from ProfileDigestConfig
        # Delivery mode is now determined at the profile level (DeliveryMode enum)

    def test_single_schedule(self):
        """Test ProfileDigestConfig with one schedule."""
        config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=True, top_n=5)
            ],
            top_n=10,
        )
        assert len(config.schedules) == 1
        assert config.schedules[0].schedule == DigestSchedule.HOURLY
        assert config.schedules[0].top_n == 5

    def test_multiple_schedules(self):
        """Test ProfileDigestConfig with multiple schedules."""
        config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=True),
                ScheduleConfig(
                    schedule=DigestSchedule.DAILY, enabled=True, daily_hour=9
                ),
            ],
            top_n=15,
            # NOTE: mode removed - delivery mode is determined at profile level
        )
        assert len(config.schedules) == 2
        assert config.schedules[0].schedule == DigestSchedule.HOURLY
        assert config.schedules[1].schedule == DigestSchedule.DAILY
        assert config.schedules[1].daily_hour == 9

    def test_max_three_schedules(self):
        """Test that more than 3 schedules raises ValueError."""
        with pytest.raises(ValueError, match="Maximum 3 schedules per profile"):
            ProfileDigestConfig(
                schedules=[
                    ScheduleConfig(schedule=DigestSchedule.HOURLY),
                    ScheduleConfig(schedule=DigestSchedule.EVERY_4H),
                    ScheduleConfig(schedule=DigestSchedule.DAILY),
                    ScheduleConfig(schedule=DigestSchedule.WEEKLY),
                ]
            )

    def test_invalid_min_score_raises(self):
        """Test that invalid min_score raises ValueError."""
        with pytest.raises(ValueError, match="min_score must be 0.0-10.0"):
            ProfileDigestConfig(min_score=15.0)

    # NOTE: test_invalid_mode_raises removed - mode is no longer on ProfileDigestConfig

    def test_dict_schedule_conversion(self):
        """Test that dict schedules are converted to ScheduleConfig objects."""
        config = ProfileDigestConfig(
            schedules=[  # type: ignore[arg-type]
                {"schedule": "hourly", "enabled": True, "top_n": 5},
                {"schedule": "daily", "enabled": True, "daily_hour": 10},
            ]
        )
        assert len(config.schedules) == 2
        assert all(isinstance(s, ScheduleConfig) for s in config.schedules)
        assert config.schedules[0].schedule == DigestSchedule.HOURLY
        assert config.schedules[0].top_n == 5
        assert config.schedules[1].schedule == DigestSchedule.DAILY
        assert config.schedules[1].daily_hour == 10

    # NOTE: test_with_target_channel removed - target_channel is no longer
    # on ProfileDigestConfig. Delivery target is now determined at profile level.


# NOTE: TestLegacyDigestConversion class removed - _convert_legacy_digest
# function was removed as part of legacy code cleanup. All digest config
# should now use the new ProfileDigestConfig/ScheduleConfig directly.


class TestIntegration:
    """Integration tests for digest configuration workflow."""

    def test_profile_with_new_digest_config(self):
        """Test creating a profile with new-style digest config."""
        from src.tgsentinel.config import ProfileDefinition

        digest_config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(
                    schedule=DigestSchedule.HOURLY, enabled=True, min_score=7.0
                ),
                ScheduleConfig(
                    schedule=DigestSchedule.DAILY, enabled=True, daily_hour=9
                ),
            ],
            top_n=20,
            # NOTE: mode removed from ProfileDigestConfig
        )

        profile = ProfileDefinition(
            id="security",
            name="Security Alerts",
            security_keywords=["CVE", "vulnerability"],
            digest=digest_config,
        )

        assert profile.id == "security"
        assert profile.digest is not None
        assert len(profile.digest.schedules) == 2
        assert profile.digest.top_n == 20

    def test_channel_with_digest_override(self):
        """Test creating a channel with digest schedule override."""
        from src.tgsentinel.config import ChannelRule

        digest_config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.EVERY_4H)]
            # NOTE: mode removed from ProfileDigestConfig
        )

        channel = ChannelRule(
            id=-1001234567890,
            name="Trading Signals",
            profiles=["trading"],
            digest=digest_config,
        )

        assert channel.digest is not None
        assert len(channel.digest.schedules) == 1
        assert channel.digest.schedules[0].schedule == DigestSchedule.EVERY_4H

    def test_monitored_user_with_digest(self):
        """Test creating a monitored user with digest config."""
        from src.tgsentinel.config import MonitoredUser

        digest_config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY, daily_hour=10)]
            # NOTE: mode removed from ProfileDigestConfig
        )

        user = MonitoredUser(
            id=123456789,
            name="Important Contact",
            profiles=["vip"],
            digest=digest_config,
        )

        assert user.digest is not None
        assert user.digest.schedules[0].daily_hour == 10

    def test_load_global_profiles_with_digest_yaml(self, tmp_path):
        """Digest blocks in YAML should load correctly."""
        yaml_content = """\
profiles:
  3000:
    name: "Test Interest"
    keywords:
      - blockchain
    digest:
      schedules:
        - schedule: hourly
          enabled: true
          top_n: 7
          min_score: 6.2
      top_n: 5
      min_score: 5.5
"""
        interest_path = tmp_path / "profiles_interest.yml"
        interest_path.write_text(yaml_content)

        profiles = _load_global_profiles(str(tmp_path))
        profile = profiles.get("3000")

        assert profile is not None
        digest = profile.digest
        assert digest is not None
        # NOTE: mode and target_channel removed from ProfileDigestConfig
        assert digest.top_n == 5
        assert pytest.approx(5.5) == digest.min_score
        assert len(digest.schedules) == 1

        schedule = digest.schedules[0]
        assert schedule.schedule == DigestSchedule.HOURLY
        assert schedule.enabled is True
        assert schedule.top_n == 7
        assert pytest.approx(6.2) == schedule.min_score
