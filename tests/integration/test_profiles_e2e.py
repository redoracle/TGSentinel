"""Integration test for profiles architecture end-to-end flow."""

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from tgsentinel.config import (
    AppCfg,
    ChannelOverrides,
    ChannelRule,
    ProfileDefinition,
    load_config,
)
from tgsentinel.digest import format_alert_triggers
from tgsentinel.heuristics import run_heuristics
from tgsentinel.profile_resolver import ProfileResolver
from tgsentinel.store import init_db, upsert_message


@pytest.fixture
def temp_profiles_config():
    """Create temporary config files with profiles."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)

        # Create profiles.yml
        profiles_yml = config_dir / "profiles.yml"
        profiles_yml.write_text(
            """
profiles:
  security:
    name: "Security Alerts"
    security_keywords:
      - CVE
      - vulnerability
      - exploit
    urgency_keywords:
      - critical
      - urgent
    scoring_weights:
      security: 1.5
      urgency: 1.8
    detect_codes: true
    
  releases:
    name: "Release Updates"
    release_keywords:
      - v1.0
      - version
      - release
    scoring_weights:
      release: 1.0
"""
        )

        # Create tgsentinel.yml
        config_yml = config_dir / "tgsentinel.yml"
        config_yml.write_text(
            """
channels:
  - id: -1001234567890
    name: "Test Security Channel"
    vip_senders: []
    profiles:
      - security
    overrides:
      keywords_extra:
        - Algorand
      scoring_weights:
        security: 2.0

  - id: -1009876543210
    name: "Test Release Channel"
    vip_senders: []
    profiles:
      - releases
      - security

telegram:
  session: "data/test.session"

alerts:
  mode: "dm"
  min_score: 0.7
"""
        )

        yield config_dir


def test_profile_resolution_e2e(temp_profiles_config):
    """Test profile resolution from config through to heuristics."""
    # Note: This would require setting TG_API_ID and TG_API_HASH env vars
    # For unit testing, we'll manually construct the config

    global_profiles = {
        "security": ProfileDefinition(
            id="security",
            name="Security Alerts",
            security_keywords=["CVE", "vulnerability", "exploit"],
            urgency_keywords=["critical", "urgent"],
            scoring_weights={"security": 1.5, "urgency": 1.8},
        ),
        "releases": ProfileDefinition(
            id="releases",
            name="Release Updates",
            release_keywords=["v1.0", "version", "release"],
            scoring_weights={"release": 1.0},
        ),
    }

    # Create channel with profile bindings
    channel = ChannelRule(
        id=-1001234567890,
        name="Test Security Channel",
        vip_senders=[],
        profiles=["security"],
        overrides=ChannelOverrides(
            keywords_extra=["Algorand"],
            scoring_weights={"security": 2.0},
        ),
    )

    # Resolve profiles
    resolver = ProfileResolver(global_profiles)
    resolved = resolver.resolve_for_channel(channel)

    # Verify resolution
    assert "CVE" in resolved.security_keywords
    assert "vulnerability" in resolved.security_keywords
    assert "critical" in resolved.urgency_keywords
    assert "Algorand" in resolved.keywords  # From override
    assert resolved.scoring_weights["security"] == 2.0  # Overridden

    # Test with actual message text
    message_text = "CRITICAL: CVE-2024-1234 vulnerability found in Algorand. Urgent patch required!"

    hr = run_heuristics(
        text=message_text,
        sender_id=123456,
        mentioned=False,
        reactions=0,
        replies=0,
        vip=set(),
        keywords=resolved.keywords,
        react_thr=5,
        reply_thr=3,
        security_keywords=resolved.security_keywords,
        urgency_keywords=resolved.urgency_keywords,
        detect_codes=resolved.detect_codes,
    )

    # Verify heuristics detected triggers
    assert hr.important
    assert "security" in hr.reasons
    assert "urgent" in hr.reasons

    # Verify trigger annotations
    assert "security" in hr.trigger_annotations
    assert "CVE" in hr.trigger_annotations["security"]
    assert "vulnerability" in hr.trigger_annotations["security"]

    assert "urgency" in hr.trigger_annotations
    urgency_keywords = [kw.lower() for kw in hr.trigger_annotations["urgency"]]
    assert "critical" in urgency_keywords or "urgent" in urgency_keywords

    # Test formatting for digest
    annotations_json = json.dumps(hr.trigger_annotations)
    formatted = format_alert_triggers(annotations_json)

    assert "🔒 security" in formatted
    assert "⚡ urgency" in formatted or "⚡ urgency" in formatted.lower()


def test_database_storage_with_annotations():
    """Test storing and retrieving trigger annotations from database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_uri = f"sqlite:///{tmp.name}"

        try:
            # Initialize database
            engine = init_db(db_uri)

            # Create trigger annotations
            trigger_annotations = {
                "security": ["CVE", "vulnerability"],
                "urgency": ["critical"],
                "keywords": ["Algorand"],
            }
            annotations_json = json.dumps(trigger_annotations)

            # Insert message with annotations
            upsert_message(
                engine=engine,
                chat_id=-1001234567890,
                msg_id=12345,
                h="testhash123",
                score=8.5,
                chat_title="Security Channel",
                sender_name="Security Bot",
                message_text="CVE-2024-1234 critical vulnerability",
                triggers="security, urgent",
                sender_id=123456,
                trigger_annotations=annotations_json,
            )

            # Retrieve and verify
            with engine.begin() as con:
                result = con.execute(
                    text(
                        "SELECT trigger_annotations, triggers, score FROM messages "
                        "WHERE chat_id = :chat_id AND msg_id = :msg_id"
                    ),
                    {"chat_id": -1001234567890, "msg_id": 12345},
                ).fetchone()

            assert result is not None
            assert result.trigger_annotations == annotations_json
            assert result.triggers == "security, urgent"
            assert result.score == 8.5

            # Parse and verify annotations
            retrieved_annotations = json.loads(result.trigger_annotations)
            assert retrieved_annotations["security"] == ["CVE", "vulnerability"]
            assert retrieved_annotations["urgency"] == ["critical"]

        finally:
            # Cleanup
            Path(tmp.name).unlink(missing_ok=True)


def test_multi_profile_merge():
    """Test merging multiple profiles."""
    global_profiles = {
        "security": ProfileDefinition(
            id="security",
            security_keywords=["CVE", "vulnerability"],
            scoring_weights={"security": 1.5},
        ),
        "releases": ProfileDefinition(
            id="releases",
            release_keywords=["v1.0", "release"],
            scoring_weights={"release": 1.0},
        ),
    }

    channel = ChannelRule(
        id=-1009876543210,
        name="Multi Profile Channel",
        vip_senders=[],
        profiles=["security", "releases"],  # Multiple profiles
    )

    resolver = ProfileResolver(global_profiles)
    resolved = resolver.resolve_for_channel(channel)

    # Should have keywords from both profiles
    assert "CVE" in resolved.security_keywords
    assert "vulnerability" in resolved.security_keywords
    assert "v1.0" in resolved.release_keywords
    assert "release" in resolved.release_keywords

    # Both profiles should be listed
    assert "security" in resolved.bound_profiles
    assert "releases" in resolved.bound_profiles


def test_backward_compatibility():
    """Test that legacy keyword fields still work without profiles."""
    resolver = ProfileResolver({})  # No global profiles

    # Old-style channel with direct keywords
    channel = ChannelRule(
        id=-1001111111111,
        name="Legacy Channel",
        vip_senders=[],
        keywords=["important", "urgent"],
        security_keywords=["CVE"],
        profiles=[],  # No profiles bound
    )

    resolved = resolver.resolve_for_channel(channel)

    # Legacy keywords should still work
    assert "important" in resolved.keywords
    assert "urgent" in resolved.keywords
    assert "CVE" in resolved.security_keywords
    assert len(resolved.bound_profiles) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
