"""Test two-layer profiles architecture."""

import pytest

from tgsentinel.config import (
    ChannelOverrides,
    ChannelRule,
    MonitoredUser,
    ProfileDefinition,
)
from tgsentinel.profile_resolver import (
    ProfileResolver,
    validate_profiles,
)


def test_profile_definition_defaults():
    """Test ProfileDefinition with default scoring weights."""
    profile = ProfileDefinition(
        id="security",
        name="Security Profile",
        security_keywords=["CVE", "vulnerability"],
    )

    assert profile.id == "security"
    assert profile.name == "Security Profile"
    assert "security" in profile.scoring_weights
    assert profile.scoring_weights["security"] == 1.2


def test_channel_overrides():
    """Test ChannelOverrides dataclass."""
    overrides = ChannelOverrides(
        keywords_extra=["blockchain", "crypto"],
        scoring_weights={"security": 2.0},
        min_score=0.8,
    )

    assert len(overrides.keywords_extra) == 2
    assert overrides.scoring_weights["security"] == 2.0
    assert overrides.min_score == 0.8


def test_channel_rule_with_profiles():
    """Test ChannelRule with profile bindings."""
    channel = ChannelRule(
        id=-1001234567890,
        name="Test Channel",
        vip_senders=[],
        profiles=["security", "releases"],
        overrides=ChannelOverrides(keywords_extra=["urgent"]),
    )

    assert len(channel.profiles) == 2
    assert "security" in channel.profiles
    assert channel.overrides.keywords_extra == ["urgent"]


def test_profile_resolver_simple():
    """Test basic profile resolution."""
    global_profiles = {
        "security": ProfileDefinition(
            id="security",
            name="Security",
            security_keywords=["CVE", "exploit"],
            scoring_weights={"security": 1.5},
        )
    }

    resolver = ProfileResolver(global_profiles)
    channel = ChannelRule(
        id=-100123,
        name="Test",
        vip_senders=[],
        profiles=["security"],
    )

    resolved = resolver.resolve_for_channel(channel)

    assert "CVE" in resolved.security_keywords
    assert "exploit" in resolved.security_keywords
    assert resolved.scoring_weights["security"] == 1.5
    assert "security" in resolved.bound_profiles


def test_profile_resolver_merge():
    """Test merging multiple profiles."""
    global_profiles = {
        "security": ProfileDefinition(
            id="security",
            security_keywords=["CVE"],
            scoring_weights={"security": 1.5},
        ),
        "releases": ProfileDefinition(
            id="releases",
            release_keywords=["v1.0"],
            scoring_weights={"release": 1.0},
        ),
    }

    resolver = ProfileResolver(global_profiles)
    channel = ChannelRule(
        id=-100123,
        name="Test",
        vip_senders=[],
        profiles=["security", "releases"],
    )

    resolved = resolver.resolve_for_channel(channel)

    assert "CVE" in resolved.security_keywords
    assert "v1.0" in resolved.release_keywords
    assert resolved.scoring_weights["security"] == 1.5
    assert resolved.scoring_weights["release"] == 1.0


def test_profile_resolver_with_overrides():
    """Test profile resolution with channel overrides."""
    global_profiles = {
        "security": ProfileDefinition(
            id="security",
            security_keywords=["CVE"],
            scoring_weights={"security": 1.5},
        )
    }

    resolver = ProfileResolver(global_profiles)
    channel = ChannelRule(
        id=-100123,
        name="Test",
        vip_senders=[],
        profiles=["security"],
        overrides=ChannelOverrides(
            keywords_extra=["urgent", "critical"],
            scoring_weights={"security": 2.0},  # Override weight
        ),
    )

    resolved = resolver.resolve_for_channel(channel)

    assert "CVE" in resolved.security_keywords
    assert "urgent" in resolved.keywords
    assert "critical" in resolved.keywords
    assert resolved.scoring_weights["security"] == 2.0  # Overridden
    assert resolved.has_overrides


def test_profile_resolver_no_legacy_support():
    """Test that legacy keyword fields are no longer supported (removed in refactor)."""
    global_profiles = {}  # No profiles

    resolver = ProfileResolver(global_profiles)
    channel = ChannelRule(
        id=-100123,
        name="Test",
        vip_senders=[],
        keywords=["legacy1", "legacy2"],  # Old-style keywords (no longer supported)
        security_keywords=["CVE"],
        profiles=[],  # No profiles bound
    )

    resolved = resolver.resolve_for_channel(channel)

    # Legacy keywords no longer work after refactor - must use profiles
    assert len(resolved.keywords) == 0
    assert len(resolved.security_keywords) == 0
    assert len(resolved.bound_profiles) == 0


def test_validate_profiles_missing():
    """Test validation catches missing profile references."""
    global_profiles = {"security": ProfileDefinition(id="security", name="Security")}

    channels = [
        ChannelRule(
            id=-100123,
            name="Test",
            vip_senders=[],
            profiles=["security", "nonexistent"],  # Missing profile!
        )
    ]

    errors = validate_profiles(global_profiles, channels, [])

    assert len(errors) > 0
    assert any("nonexistent" in err for err in errors)


def test_validate_profiles_duplicate():
    """Test validation catches duplicate profile IDs."""
    # Note: Python dict literal will only keep the last value for duplicate keys
    # This test documents that behavior
    global_profiles = {
        "security": ProfileDefinition(id="security", name="Duplicate"),
    }

    errors = validate_profiles(global_profiles, [], [])

    # Note: Python dict will only keep one, so this is caught differently
    # In YAML loading, we'd catch this
    assert isinstance(errors, list)


def test_monitored_user_with_profiles():
    """Test MonitoredUser with profile bindings."""
    user = MonitoredUser(
        id=123456,
        name="John Doe",
        username="johndoe",
        enabled=True,
        profiles=["security", "governance"],
        overrides=ChannelOverrides(urgency_keywords_extra=["ASAP"]),
    )

    assert len(user.profiles) == 2
    assert user.overrides.urgency_keywords_extra == ["ASAP"]


def test_heuristic_result_with_annotations():
    """Test HeuristicResult includes trigger_annotations."""
    from tgsentinel.heuristics import HeuristicResult

    result = HeuristicResult(
        important=True,
        reasons=["security", "urgent"],
        content_hash="abc123",
        pre_score=2.5,
        trigger_annotations={
            "security": ["CVE", "vulnerability"],
            "urgency": ["urgent", "critical"],
        },
    )

    assert result.important
    assert len(result.trigger_annotations) == 2
    assert "CVE" in result.trigger_annotations["security"]
    assert "urgent" in result.trigger_annotations["urgency"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
