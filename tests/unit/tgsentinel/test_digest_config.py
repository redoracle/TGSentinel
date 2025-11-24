"""Unit tests for digest configuration data models (Phase 1).

Tests the new DigestSchedule enum, ScheduleConfig, and ProfileDigestConfig
classes added in Phase 1 of the schedule-driven digest implementation.
"""

import pytest
import yaml

from src.tgsentinel.config import (
    DigestCfg,
    DigestSchedule,
    ProfileDigestConfig,
    ScheduleConfig,
    _convert_legacy_digest,
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
        assert config.mode == "dm"
        assert config.target_channel is None

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
            mode="both",
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

    def test_invalid_mode_raises(self):
        """Test that invalid mode raises ValueError."""
        with pytest.raises(ValueError, match="mode must be dm|channel|both"):
            ProfileDigestConfig(mode="invalid")

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

    def test_with_target_channel(self):
        """Test ProfileDigestConfig with target channel override."""
        config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
            mode="channel",
            target_channel="@alerts_channel",
        )
        assert config.mode == "channel"
        assert config.target_channel == "@alerts_channel"


class TestLegacyDigestConversion:
    """Tests for _convert_legacy_digest backward compatibility function."""

    def test_hourly_only(self):
        """Test conversion of legacy hourly-only config."""
        legacy = DigestCfg(hourly=True, daily=False, top_n=10)
        converted = _convert_legacy_digest(legacy)

        assert len(converted.schedules) == 1
        assert converted.schedules[0].schedule == DigestSchedule.HOURLY
        assert converted.schedules[0].enabled is True
        assert converted.schedules[0].top_n == 10
        assert converted.top_n == 10
        assert converted.mode == "dm"

    def test_daily_only(self):
        """Test conversion of legacy daily-only config."""
        legacy = DigestCfg(hourly=False, daily=True, top_n=15)
        converted = _convert_legacy_digest(legacy)

        assert len(converted.schedules) == 1
        assert converted.schedules[0].schedule == DigestSchedule.DAILY
        assert converted.schedules[0].enabled is True
        assert converted.schedules[0].top_n == 15
        assert converted.schedules[0].daily_hour == 8  # Default

    def test_both_enabled(self):
        """Test conversion with both hourly and daily enabled."""
        legacy = DigestCfg(hourly=True, daily=True, top_n=12)
        converted = _convert_legacy_digest(legacy)

        assert len(converted.schedules) == 2
        assert converted.schedules[0].schedule == DigestSchedule.HOURLY
        assert converted.schedules[1].schedule == DigestSchedule.DAILY
        assert all(s.enabled for s in converted.schedules)

    def test_both_disabled(self):
        """Test conversion with both disabled (instant alerts only)."""
        legacy = DigestCfg(hourly=False, daily=False, top_n=10)
        converted = _convert_legacy_digest(legacy)

        assert len(converted.schedules) == 0
        assert converted.top_n == 10

    def test_with_custom_mode_and_channel(self):
        """Test conversion with custom mode and target channel."""
        legacy = DigestCfg(hourly=True, daily=False, top_n=8)
        converted = _convert_legacy_digest(
            legacy, mode="channel", target_channel="@security_alerts"
        )

        assert converted.mode == "channel"
        assert converted.target_channel == "@security_alerts"
        assert len(converted.schedules) == 1


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
            mode="both",
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
            schedules=[ScheduleConfig(schedule=DigestSchedule.EVERY_4H)], mode="dm"
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
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY, daily_hour=10)],
            mode="dm",
        )

        user = MonitoredUser(
            id=123456789,
            name="Important Contact",
            profiles=["vip"],
            digest=digest_config,
        )

        assert user.digest is not None
        assert user.digest.schedules[0].daily_hour == 10

    def test_load_global_profiles_with_legacy_digest_yaml(self, tmp_path):
        """Legacy digest blocks in YAML should load via compatibility shim."""

        yaml_content = {
            "profiles": {
                "3000": {
                    "name": "Legacy Interest",
                    "keywords": ["blockchain"],
                    "digest": {
                        "hourly": {"enabled": True, "top_n": 7, "min_score": 6.2},
                        "daily": {"enabled": False},
                        "top_n": 5,
                        "mode": "channel",
                        "target_channel": "@legacy_alerts",
                        "min_score": 5.5,
                    },
                }
            }
        }

        interest_path = tmp_path / "profiles_interest.yml"
        interest_path.write_text(yaml.safe_dump(yaml_content, sort_keys=False))

        profiles = _load_global_profiles(str(tmp_path))
        profile = profiles.get("3000")

        assert profile is not None
        digest = profile.digest
        assert digest is not None
        assert digest.mode == "channel"
        assert digest.target_channel == "@legacy_alerts"
        assert digest.top_n == 5
        assert pytest.approx(5.5) == digest.min_score
        assert len(digest.schedules) == 1

        schedule = digest.schedules[0]
        assert schedule.schedule == DigestSchedule.HOURLY
        assert schedule.enabled is True
        assert schedule.top_n == 7
        assert pytest.approx(6.2) == schedule.min_score
