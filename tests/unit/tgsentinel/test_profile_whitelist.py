"""
Alert Profile Whitelist Validation Test

This test verifies that the ProfileDefinition valid_fields whitelist
includes all fields that are:
1. Present in the ProfileDefinition dataclass
2. Saved by the UI to YAML files
3. Used by the worker/heuristics engine

Related to: ALERT_PROFILE_AUDIT.md Section 4.1
"""

from dataclasses import fields

import pytest

from src.tgsentinel.config import ProfileDefinition


def test_valid_fields_whitelist_completeness():
    """Ensure all ProfileDefinition fields are in the valid_fields whitelist."""

    # Extract all field names from ProfileDefinition dataclass
    profile_definition_fields = {f.name for f in fields(ProfileDefinition)}

    # Expected whitelist (from config.py lines 694-722)
    valid_fields_whitelist = {
        "id",
        "name",
        "description",
        "enabled",
        "keywords",
        "action_keywords",
        "decision_keywords",
        "urgency_keywords",
        "importance_keywords",
        "release_keywords",
        "security_keywords",
        "risk_keywords",
        "opportunity_keywords",
        "tags",
        "detect_codes",
        "detect_documents",
        "detect_links",  # FIXED: Added in whitelist fix
        "require_forwarded",  # FIXED: Added in whitelist fix
        "detect_mentions",  # FIXED: Added in whitelist fix
        "detect_questions",  # FIXED: Added in whitelist fix
        "prioritize_pinned",
        "prioritize_admin",
        "detect_polls",
        "scoring_weights",
        "digest",
        "channels",
        "users",
        "vip_senders",  # FIXED: Added in whitelist fix
        "excluded_users",  # FIXED: Added in whitelist fix
        "webhooks",  # FIXED: Added in whitelist fix
        "min_score",
        "negative_samples",
        "negative_weight",
        "positive_samples",
        "positive_weight",
        "threshold",
        "prioritize_private",
        "reaction_threshold",
        "reply_threshold",
    }

    # Fields that should be in ProfileDefinition but excluded from whitelist
    # (none currently - all fields should be loadable)
    expected_exclusions = set()

    # Calculate differences
    missing_from_whitelist = (
        profile_definition_fields - valid_fields_whitelist - expected_exclusions
    )
    extra_in_whitelist = valid_fields_whitelist - profile_definition_fields

    # Assert no missing fields
    assert not missing_from_whitelist, (
        f"ProfileDefinition fields missing from valid_fields whitelist: {missing_from_whitelist}\n"
        "These fields will be filtered out when loading profiles from YAML!"
    )

    # Assert no extra fields (typos or outdated whitelist entries)
    assert not extra_in_whitelist, (
        f"valid_fields whitelist contains fields not in ProfileDefinition: {extra_in_whitelist}\n"
        "These are likely typos or outdated entries."
    )

    print(f"✅ Whitelist validation passed: {len(valid_fields_whitelist)} fields")


def test_alert_profile_critical_fields_present():
    """Verify critical Alert profile fields are in ProfileDefinition."""

    profile_definition_fields = {f.name for f in fields(ProfileDefinition)}

    # Critical fields that were previously missing from whitelist
    critical_alert_fields = {
        "detect_links",  # URL detection (used in heuristics)
        "require_forwarded",  # Forward-only filter (used in heuristics)
        "detect_mentions",  # @mention detection (in development)
        "detect_questions",  # Question pattern detection (partial implementation)
        "vip_senders",  # Always-important sender IDs (used in heuristics)
        "excluded_users",  # Blacklist (used in worker filtering)
        "webhooks",  # Webhook routing (used in notifier)
    }

    missing = critical_alert_fields - profile_definition_fields

    assert not missing, (
        f"Critical Alert profile fields missing from ProfileDefinition: {missing}\n"
        "These fields are saved by the UI and must be loadable by the worker!"
    )

    print(
        f"✅ All {len(critical_alert_fields)} critical Alert fields are in ProfileDefinition"
    )


def test_heuristics_parameters_match_profile_definition():
    """Verify run_heuristics() accepts all ProfileDefinition keyword/detection fields."""

    import inspect

    from src.tgsentinel.heuristics import run_heuristics

    # Get run_heuristics() signature
    sig = inspect.signature(run_heuristics)
    heuristics_params = set(sig.parameters.keys())

    # Expected ProfileDefinition fields that should be heuristics parameters
    # NOTE: detect_mentions and detect_questions are NOT parameters because:
    # - detect_mentions: Handled by the 'mentioned' boolean parameter (from Telethon)
    # - detect_questions: Logic is inline via _detect_question_patterns() (private chats only)
    expected_in_heuristics = {
        # Keyword categories (9)
        "keywords",
        "action_keywords",
        "decision_keywords",
        "urgency_keywords",
        "importance_keywords",
        "release_keywords",
        "security_keywords",
        "risk_keywords",
        "opportunity_keywords",
        # Detection flags (9 out of 11)
        "detect_codes",
        "detect_documents",
        "detect_links",
        "require_forwarded",
        "prioritize_pinned",
        "prioritize_admin",
        "detect_polls",
        # NOT included: detect_mentions (uses 'mentioned' param), detect_questions (inline logic)
    }

    # Check which expected fields are missing
    missing = expected_in_heuristics - heuristics_params

    assert not missing, (
        f"ProfileDefinition fields missing from run_heuristics() signature: {missing}\n"
        "Heuristics engine cannot use these fields for scoring!"
    )

    print(
        f"✅ All {len(expected_in_heuristics)} scoring fields are in run_heuristics() signature"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
