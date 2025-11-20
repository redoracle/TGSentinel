"""Test digest trigger annotations formatting."""

import json

import pytest

from tgsentinel.digest import format_alert_triggers


def test_format_alert_triggers_empty():
    """Test formatting with empty annotations."""
    assert format_alert_triggers("") == ""
    assert format_alert_triggers("{}") == ""
    assert format_alert_triggers("null") == ""


def test_format_alert_triggers_invalid_json():
    """Test formatting with invalid JSON."""
    assert format_alert_triggers("not json") == ""
    assert format_alert_triggers("{invalid}") == ""


def test_format_alert_triggers_single_category():
    """Test formatting with single category."""
    annotations = {"security": ["CVE", "vulnerability"]}
    result = format_alert_triggers(json.dumps(annotations))

    assert "🔒 security" in result
    assert "CVE" in result
    assert "vulnerability" in result


def test_format_alert_triggers_multiple_categories():
    """Test formatting with multiple categories."""
    annotations = {
        "security": ["CVE-2024-1234"],
        "urgency": ["critical", "immediate"],
        "action": ["update now"],
    }
    result = format_alert_triggers(json.dumps(annotations))

    assert "🔒 security" in result
    assert "⚡ urgency" in result
    assert "✅ action" in result
    assert "CVE-2024-1234" in result
    assert "critical" in result


def test_format_alert_triggers_max_keywords():
    """Test keyword truncation."""
    annotations = {"keywords": ["one", "two", "three", "four", "five"]}
    result = format_alert_triggers(json.dumps(annotations), max_keywords=3)

    assert "one" in result
    assert "two" in result
    assert "three" in result
    assert "+2 more" in result
    assert "four" not in result
    assert "five" not in result


def test_format_alert_triggers_all_categories():
    """Test all category icons."""
    annotations = {
        "security": ["sec"],
        "urgency": ["urg"],
        "action": ["act"],
        "decision": ["dec"],
        "release": ["rel"],
        "risk": ["risk"],
        "opportunity": ["opp"],
        "importance": ["imp"],
        "keywords": ["key"],
    }
    result = format_alert_triggers(json.dumps(annotations))

    # Check all icons are present
    assert "🔒" in result  # security
    assert "⚡" in result  # urgency
    assert "✅" in result  # action
    assert "🗳️" in result  # decision
    assert "📦" in result  # release
    assert "⚠️" in result  # risk
    assert "💎" in result  # opportunity
    assert "❗" in result  # importance
    assert "🔍" in result  # keywords


def test_format_alert_triggers_separator():
    """Test categories are separated by bullet."""
    annotations = {
        "security": ["CVE"],
        "urgency": ["critical"],
    }
    result = format_alert_triggers(json.dumps(annotations))

    assert " • " in result  # Bullet separator between categories


def test_format_alert_triggers_empty_category():
    """Test empty category arrays are skipped."""
    annotations = {
        "security": ["CVE"],
        "urgency": [],  # Empty
        "action": ["update"],
    }
    result = format_alert_triggers(json.dumps(annotations))

    assert "security" in result
    assert "action" in result
    assert "urgency" not in result


def test_format_alert_triggers_real_world_example():
    """Test with realistic trigger annotations."""
    annotations = {
        "security": ["CVE", "vulnerability", "exploit"],
        "urgency": ["critical", "urgent"],
        "keywords": ["Algorand"],
    }
    result = format_alert_triggers(json.dumps(annotations), max_keywords=2)

    # Should show first 2 keywords per category + "+N more" for overflow
    assert "🔒 security: CVE, vulnerability" in result
    assert "+1 more" in result
    assert "⚡ urgency: critical, urgent" in result
    assert "🔍 keywords: Algorand" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
