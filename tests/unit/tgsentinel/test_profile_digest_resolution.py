"""Unit tests for Phase 2: Profile Resolution with Digest Config.

Tests the digest configuration resolution logic added to ProfileResolver
and the helper functions for digest schedule determination.
"""

from src.tgsentinel.config import (
    ChannelOverrides,
    ChannelRule,
    DigestSchedule,
    MonitoredUser,
    ProfileDefinition,
    ProfileDigestConfig,
    ScheduleConfig,
)
from src.tgsentinel.profile_resolver import ProfileResolver


def get_primary_digest_schedule(digest_config):
    """Local copy of get_primary_digest_schedule to avoid importing worker.

    This avoids Prometheus metrics registration issues in tests.
    Implementation matches worker.get_primary_digest_schedule exactly.
    """
    if not digest_config or not digest_config.schedules:
        return ""

    priority = [
        DigestSchedule.HOURLY,
        DigestSchedule.EVERY_4H,
        DigestSchedule.EVERY_6H,
        DigestSchedule.EVERY_12H,
        DigestSchedule.DAILY,
        DigestSchedule.WEEKLY,
        DigestSchedule.NONE,
    ]

    enabled_schedules = {s.schedule for s in digest_config.schedules if s.enabled}

    for sched in priority:
        if sched in enabled_schedules:
            return sched.value

    return ""


class TestDigestConfigResolution:
    """Tests for digest config resolution in ProfileResolver."""

    def test_no_digest_config(self):
        """Test resolution when no digest config exists anywhere."""
        profile = ProfileDefinition(
            id="basic",
            name="Basic Profile",
            keywords=["test"],
        )
        resolver = ProfileResolver({"basic": profile})

        channel = ChannelRule(
            id=-1001234567890,
            name="Test Channel",
            profiles=["basic"],
        )

        resolved = resolver.resolve_for_channel(channel)
        assert resolved.digest is None
        assert resolved.matched_profile_ids == ["basic"]

    def test_profile_level_digest(self):
        """Test digest config from profile level."""
        digest_config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
            top_n=10,
        )
        profile = ProfileDefinition(
            id="security",
            name="Security",
            security_keywords=["CVE"],
            digest=digest_config,
        )
        resolver = ProfileResolver({"security": profile})

        channel = ChannelRule(
            id=-1001234567890,
            name="Security Channel",
            profiles=["security"],
        )

        resolved = resolver.resolve_for_channel(channel)
        assert resolved.digest is not None
        assert len(resolved.digest.schedules) == 1
        assert resolved.digest.schedules[0].schedule == DigestSchedule.HOURLY
        assert resolved.matched_profile_ids == ["security"]

    def test_overrides_digest_precedence(self):
        """Test that overrides.digest takes precedence over profile.digest."""
        profile_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
            top_n=10,
        )
        override_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
            top_n=5,
        )

        profile = ProfileDefinition(
            id="security",
            name="Security",
            digest=profile_digest,
        )
        resolver = ProfileResolver({"security": profile})

        channel = ChannelRule(
            id=-1001234567890,
            name="Security Channel",
            profiles=["security"],
            overrides=ChannelOverrides(digest=override_digest),
        )

        resolved = resolver.resolve_for_channel(channel)
        assert resolved.digest is not None
        assert resolved.digest.schedules[0].schedule == DigestSchedule.HOURLY
        assert resolved.digest.top_n == 5  # From override

    def test_entity_digest_highest_precedence(self):
        """Test that entity-level digest takes highest precedence."""
        profile_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
        )
        override_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
        )
        entity_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.EVERY_4H)],
        )

        profile = ProfileDefinition(
            id="trading",
            name="Trading",
            digest=profile_digest,
        )
        resolver = ProfileResolver({"trading": profile})

        channel = ChannelRule(
            id=-1001234567890,
            name="Trading Channel",
            profiles=["trading"],
            overrides=ChannelOverrides(digest=override_digest),
            digest=entity_digest,  # Highest priority
        )

        resolved = resolver.resolve_for_channel(channel)
        assert resolved.digest is not None
        assert resolved.digest.schedules[0].schedule == DigestSchedule.EVERY_4H

    def test_first_profile_wins(self):
        """Test that first bound profile's digest is used when multiple profiles exist."""
        digest1 = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
        )
        digest2 = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
        )

        profile1 = ProfileDefinition(id="security", name="Security", digest=digest1)
        profile2 = ProfileDefinition(id="urgent", name="Urgent", digest=digest2)

        resolver = ProfileResolver({"security": profile1, "urgent": profile2})

        channel = ChannelRule(
            id=-1001234567890,
            name="Test Channel",
            profiles=["security", "urgent"],  # security first
        )

        resolved = resolver.resolve_for_channel(channel)
        assert resolved.digest is not None
        assert (
            resolved.digest.schedules[0].schedule == DigestSchedule.HOURLY
        )  # From security

    def test_multiple_profiles_tracked(self):
        """Test that all bound profile IDs are tracked for digest deduplication."""
        profile1 = ProfileDefinition(id="security", name="Security")
        profile2 = ProfileDefinition(id="urgent", name="Urgent")
        profile3 = ProfileDefinition(id="critical", name="Critical")

        resolver = ProfileResolver(
            {"security": profile1, "urgent": profile2, "critical": profile3}
        )

        channel = ChannelRule(
            id=-1001234567890,
            name="Test Channel",
            profiles=["security", "urgent", "critical"],
        )

        resolved = resolver.resolve_for_channel(channel)
        assert resolved.matched_profile_ids == ["security", "urgent", "critical"]
        assert len(resolved.matched_profile_ids) == 3

    def test_monitored_user_digest_resolution(self):
        """Test digest resolution for monitored users."""
        digest_config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY, daily_hour=10)],
        )

        profile = ProfileDefinition(id="vip", name="VIP", digest=digest_config)
        resolver = ProfileResolver({"vip": profile})

        user = MonitoredUser(
            id=123456789,
            name="Important Contact",
            profiles=["vip"],
        )

        resolved = resolver.resolve_for_user(user)
        assert resolved.digest is not None
        assert resolved.digest.schedules[0].schedule == DigestSchedule.DAILY
        assert resolved.digest.schedules[0].daily_hour == 10

    def test_user_entity_digest_override(self):
        """Test entity-level digest override for users."""
        profile_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
        )
        user_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
        )

        profile = ProfileDefinition(id="vip", name="VIP", digest=profile_digest)
        resolver = ProfileResolver({"vip": profile})

        user = MonitoredUser(
            id=123456789,
            name="Important Contact",
            profiles=["vip"],
            digest=user_digest,  # Override
        )

        resolved = resolver.resolve_for_user(user)
        assert resolved.digest is not None
        assert resolved.digest.schedules[0].schedule == DigestSchedule.HOURLY


class TestGetPrimaryDigestSchedule:
    """Tests for get_primary_digest_schedule helper function."""

    def test_no_digest_config(self):
        """Test with no digest config."""
        schedule = get_primary_digest_schedule(None)
        assert schedule == ""

    def test_empty_schedules(self):
        """Test with empty schedules list."""
        config = ProfileDigestConfig(schedules=[])
        schedule = get_primary_digest_schedule(config)
        assert schedule == ""

    def test_hourly_schedule(self):
        """Test hourly schedule (highest priority)."""
        config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=True)]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == "hourly"

    def test_daily_schedule(self):
        """Test daily schedule."""
        config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY, enabled=True)]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == "daily"

    def test_multiple_schedules_priority(self):
        """Test that most frequent schedule wins when multiple exist."""
        config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.DAILY, enabled=True),
                ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=True),
                ScheduleConfig(schedule=DigestSchedule.WEEKLY, enabled=True),
            ]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == "hourly"  # Hourly has highest priority

    def test_every_4h_priority(self):
        """Test every_4h schedule priority."""
        config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.EVERY_4H, enabled=True),
                ScheduleConfig(schedule=DigestSchedule.DAILY, enabled=True),
            ]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == "every_4h"  # Higher frequency wins

    def test_disabled_schedules_ignored(self):
        """Test that disabled schedules are ignored."""
        config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=False),
                ScheduleConfig(schedule=DigestSchedule.DAILY, enabled=True),
            ]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == "daily"  # Hourly disabled, so daily wins

    def test_all_schedules_disabled(self):
        """Test when all schedules are disabled."""
        config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.HOURLY, enabled=False),
                ScheduleConfig(schedule=DigestSchedule.DAILY, enabled=False),
            ]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == ""  # No enabled schedules

    def test_weekly_schedule(self):
        """Test weekly schedule (lowest priority)."""
        config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.WEEKLY, enabled=True)]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == "weekly"

    def test_none_schedule(self):
        """Test 'none' schedule (instant alerts only)."""
        config = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.NONE, enabled=True)]
        )
        schedule = get_primary_digest_schedule(config)
        assert schedule == "none"


class TestIntegrationScenarios:
    """Integration tests for realistic digest resolution scenarios."""

    def test_security_channel_hourly_digest(self):
        """Test security channel with hourly digest from profile."""
        digest_config = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(
                    schedule=DigestSchedule.HOURLY,
                    enabled=True,
                    min_score=7.0,
                    mode="both",
                ),
                ScheduleConfig(
                    schedule=DigestSchedule.DAILY,
                    enabled=True,
                    daily_hour=8,
                    mode="both",
                ),
            ],
            top_n=20,
        )

        profile = ProfileDefinition(
            id="security",
            name="Security Alerts",
            security_keywords=["CVE", "vulnerability"],
            digest=digest_config,
        )

        resolver = ProfileResolver({"security": profile})

        channel = ChannelRule(
            id=-1001234567890,
            name="Security Channel",
            profiles=["security"],
        )

        resolved = resolver.resolve_for_channel(channel)

        # Verify digest config
        assert resolved.digest is not None
        assert len(resolved.digest.schedules) == 2
        assert resolved.digest.top_n == 20
        # Mode is now on each schedule, not on the digest config
        assert all(s.mode == "both" for s in resolved.digest.schedules)

        # Verify primary schedule
        primary = get_primary_digest_schedule(resolved.digest)
        assert primary == "hourly"  # Hourly has priority over daily

        # Verify profile tracking
        assert resolved.matched_profile_ids == ["security"]

    def test_trading_channel_custom_schedule(self):
        """Test trading channel with custom every_4h schedule override."""
        profile_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
        )
        channel_digest = ProfileDigestConfig(
            schedules=[
                ScheduleConfig(schedule=DigestSchedule.HOURLY, mode="dm"),
                ScheduleConfig(schedule=DigestSchedule.EVERY_4H, mode="dm"),
            ],
        )

        profile = ProfileDefinition(id="trading", name="Trading", digest=profile_digest)
        resolver = ProfileResolver({"trading": profile})

        channel = ChannelRule(
            id=-1001234567890,
            name="Trading Signals",
            profiles=["trading"],
            digest=channel_digest,  # Override
        )

        resolved = resolver.resolve_for_channel(channel)

        # Channel digest overrides profile digest
        assert resolved.digest is not None
        assert len(resolved.digest.schedules) == 2
        # Mode is now on each schedule, not on the digest config
        assert all(s.mode == "dm" for s in resolved.digest.schedules)

        # Primary schedule should be hourly (highest priority)
        primary = get_primary_digest_schedule(resolved.digest)
        assert primary == "hourly"

    def test_multi_profile_deduplication_setup(self):
        """Test setup for message deduplication across multiple profiles."""
        # Three profiles, each with different digest config
        security_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
        )
        critical_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.HOURLY)],
        )
        urgent_digest = ProfileDigestConfig(
            schedules=[ScheduleConfig(schedule=DigestSchedule.DAILY)],
        )

        profiles = {
            "security": ProfileDefinition(
                id="security", name="Security", digest=security_digest
            ),
            "critical": ProfileDefinition(
                id="critical", name="Critical", digest=critical_digest
            ),
            "urgent": ProfileDefinition(
                id="urgent", name="Urgent", digest=urgent_digest
            ),
        }

        resolver = ProfileResolver(profiles)

        channel = ChannelRule(
            id=-1001234567890,
            name="Multi-Profile Channel",
            profiles=["security", "critical", "urgent"],
        )

        resolved = resolver.resolve_for_channel(channel)

        # All three profiles tracked
        assert len(resolved.matched_profile_ids) == 3
        assert set(resolved.matched_profile_ids) == {"security", "critical", "urgent"}

        # First profile's digest wins (security)
        assert resolved.digest is not None
        primary = get_primary_digest_schedule(resolved.digest)
        assert primary == "hourly"

        # This setup allows digest collector to:
        # 1. Find all messages with "security", "critical", or "urgent" in matched_profiles
        # 2. Deduplicate messages that match multiple profiles
        # 3. Show profile badges on each message in the digest
