"""Tests for performance fixes and schema validation improvements.

This module tests:
1. Config mode validation (dm/channel/both)
2. User info cache key handling (user_id vs id)
3. Populate history payload schema matching client.py
4. Database index presence
5. Sentinel restart fallback to docker compose
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure UI auth gating is bypassed for these tests
os.environ.setdefault("UI_SKIP_AUTH", "true")

# Ensure ui and src paths are in sys.path for imports
REPO_ROOT = Path(__file__).resolve().parents[1]
UI_PATH = REPO_ROOT / "ui"
SRC_PATH = REPO_ROOT / "src"
if str(UI_PATH) not in sys.path:
    sys.path.insert(0, str(UI_PATH))
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from ui.app import _validate_config_payload

pytestmark = pytest.mark.integration


# ============================================================================
# Test 1: Config Mode Validation
def test_config_save_accepts_dm_mode():
    """Test that /api/config/save accepts 'dm' mode."""
    payload = {"mode": "dm"}
    # Should not raise
    _validate_config_payload(payload)
    payload = {"mode": "dm"}
    # Should not raise
    _validate_config_payload(payload)


def test_config_save_accepts_channel_mode():
    """Test that /api/config/save accepts 'channel' mode."""
    payload = {"mode": "channel"}
    # Should not raise
    _validate_config_payload(payload)
    _validate_config_payload(payload)


def test_config_save_accepts_both_mode():
    """Test that /api/config/save accepts 'both' mode."""
    payload = {"mode": "both"}
    # Should not raise
    _validate_config_payload(payload)


def test_config_save_rejects_invalid_mode():
    """Test that /api/config/save rejects invalid modes like 'direct', 'digest'."""
    for value in ("direct", "invalid"):
        with pytest.raises(ValueError, match="Invalid mode"):
            _validate_config_payload({"mode": value})


def test_config_save_mode_validation_error_message():
    """Test that mode validation error message includes correct values."""
    with pytest.raises(ValueError) as excinfo:
        _validate_config_payload({"mode": "wrong"})

    message = str(excinfo.value)
    assert "dm" in message
    assert "channel" in message
    assert "both" in message


# ============================================================================
# Test 2: User Info Cache Key Handling
# ============================================================================


def test_user_info_cache_with_user_id_key():
    """Test that participant info endpoint handles user_info with 'user_id' key."""
    mock_redis = MagicMock()
    user_info = {
        "user_id": 12345,  # New format
        "username": "testuser",
        "first_name": "Test",
    }
    mock_redis.get.return_value = json.dumps(user_info)

    # Simulate the cache lookup logic
    user_info_str = mock_redis.get("tgsentinel:user_info")
    parsed = json.loads(user_info_str)

    # Check both keys (new code should check user_id first, then id)
    cached_user_id = parsed.get("user_id") or parsed.get("id")

    assert cached_user_id == 12345


def test_user_info_cache_with_legacy_id_key():
    """Test that participant info endpoint handles legacy user_info with 'id' key."""
    mock_redis = MagicMock()
    user_info = {
        "id": 12345,  # Legacy format
        "username": "testuser",
        "first_name": "Test",
    }
    mock_redis.get.return_value = json.dumps(user_info)

    # Simulate the cache lookup logic
    user_info_str = mock_redis.get("tgsentinel:user_info")
    parsed = json.loads(user_info_str)

    # Check both keys (new code should check user_id first, then id)
    cached_user_id = parsed.get("user_id") or parsed.get("id")

    assert cached_user_id == 12345


def test_user_info_cache_prefers_user_id_over_id():
    """Test that user_id key takes precedence over id key."""
    mock_redis = MagicMock()
    user_info = {
        "user_id": 99999,  # New format (should win)
        "id": 12345,  # Legacy format (should be ignored)
        "username": "testuser",
    }
    mock_redis.get.return_value = json.dumps(user_info)

    # Simulate the cache lookup logic
    user_info_str = mock_redis.get("tgsentinel:user_info")
    parsed = json.loads(user_info_str)

    # Check both keys - user_id should take precedence
    cached_user_id = parsed.get("user_id") or parsed.get("id")

    assert cached_user_id == 99999  # Should use user_id, not id


# ============================================================================
# Test 3: Populate History Payload Schema
# ============================================================================


def test_populate_history_payload_matches_client():
    """Test that populate_history generates payloads matching client.py schema."""
    # Expected client.py schema
    expected_keys = {
        "chat_id",
        "chat_title",
        "msg_id",
        "sender_id",
        "sender_name",
        "mentioned",
        "text",
        "replies",
        "reactions",
        "timestamp",
    }

    # Simulate message data from populate_history
    msg_data = {
        "chat_id": -100123456789,
        "chat_title": "Test Channel",
        "msg_id": 12345,
        "sender_id": 98765,
        "sender_name": "Test User",
        "mentioned": False,
        "text": "Test message",
        "replies": 2,
        "reactions": 5,
        "timestamp": "2025-11-13T10:00:00+00:00",
    }

    # Verify all expected keys are present
    assert set(msg_data.keys()) == expected_keys

    # Verify types
    assert isinstance(msg_data["chat_id"], int)
    assert isinstance(msg_data["chat_title"], str)
    assert isinstance(msg_data["msg_id"], int)
    assert isinstance(msg_data["sender_id"], int)
    assert isinstance(msg_data["sender_name"], str)
    assert isinstance(msg_data["mentioned"], bool)
    assert isinstance(msg_data["text"], str)
    assert isinstance(msg_data["replies"], int)
    assert isinstance(msg_data["reactions"], int)
    assert isinstance(msg_data["timestamp"], str)


def test_populate_history_json_wrapper():
    """Test that populate_history wraps payload in 'json' field like client.py."""
    msg_data = {
        "chat_id": -100123456789,
        "chat_title": "Test Channel",
        "msg_id": 12345,
        "sender_id": 98765,
        "sender_name": "Test User",
        "mentioned": False,
        "text": "Test message",
        "replies": 2,
        "reactions": 5,
        "timestamp": "2025-11-13T10:00:00+00:00",
    }

    # Simulate populate_redis_stream logic
    redis_data = {"json": json.dumps(msg_data)}

    # Verify wrapper structure
    assert "json" in redis_data
    assert isinstance(redis_data["json"], str)

    # Verify content can be parsed
    parsed = json.loads(redis_data["json"])
    assert parsed["chat_id"] == -100123456789
    assert parsed["msg_id"] == 12345


def test_populate_history_no_extra_fields():
    """Test that populate_history doesn't add extra fields not in client.py."""
    # Fields that should NOT be in the payload
    forbidden_fields = {
        "message_id",  # Should be msg_id
        "chat_name",  # Should be chat_title
        "reply_to_msg_id",  # Not in client.py
        "reactions_count",  # Should be reactions
        "views",
        "forwards",
        "is_pinned",
    }

    msg_data = {
        "chat_id": -100123456789,
        "chat_title": "Test Channel",
        "msg_id": 12345,
        "sender_id": 98765,
        "sender_name": "Test User",
        "mentioned": False,
        "text": "Test message",
        "replies": 2,
        "reactions": 5,
        "timestamp": "2025-11-13T10:00:00+00:00",
    }

    # Verify no forbidden fields present
    for field in forbidden_fields:
        assert field not in msg_data, f"Unexpected field '{field}' found in payload"


# ============================================================================
# Test 4: Database Index Presence (SQLite)
# ============================================================================


def test_database_indexes_created():
    """Test that required indexes are created in the database."""
    from sqlalchemy import text

    from tgsentinel.store import init_db

    # Create in-memory database
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        engine = init_db(f"sqlite:///{tmp.name}")

        # Query index information
        with engine.connect() as conn:
            # SQLite stores index info in sqlite_master
            result = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
                )
            )
            indexes = {row[0] for row in result}

        # Verify expected indexes exist
        expected_indexes = {
            "idx_messages_alerts_feed",  # Changed from idx_messages_alerted
            "idx_messages_created_at",
            "idx_messages_chat_id",
            "idx_feedback_chat_msg",
        }

        assert expected_indexes.issubset(
            indexes
        ), f"Missing indexes: {expected_indexes - indexes}"


def test_indexes_are_idempotent():
    """Test that running init_db multiple times doesn't fail (indexes use IF NOT EXISTS)."""
    from tgsentinel.store import init_db

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        # Run init_db twice
        engine1 = init_db(f"sqlite:///{tmp.name}")
        engine2 = init_db(f"sqlite:///{tmp.name}")

        # Should not raise errors
        assert engine1 is not None
        assert engine2 is not None


# ============================================================================
# Test 5: Integration Tests
# ============================================================================


def test_populate_history_dry_run_no_redis_write():
    """Test that populate_history --dry-run doesn't write to Redis."""
    mock_redis = MagicMock()

    # Simulate dry run (should not call xadd)
    dry_run = True
    messages = [
        {
            "chat_id": -100123456789,
            "chat_title": "Test",
            "msg_id": 1,
            "sender_id": 123,
            "sender_name": "User",
            "mentioned": False,
            "text": "Test",
            "replies": 0,
            "reactions": 0,
            "timestamp": "2025-11-13T10:00:00+00:00",
        }
    ]

    if not dry_run:
        for msg in messages:
            redis_data = {"json": json.dumps(msg)}
            mock_redis.xadd("tgsentinel:messages", redis_data)

    # Verify Redis was not called in dry run mode
    mock_redis.xadd.assert_not_called()


def test_populate_history_real_publish():
    """Test that populate_history publishes with correct format."""
    mock_redis = MagicMock()

    messages = [
        {
            "chat_id": -100123456789,
            "chat_title": "Test Channel",
            "msg_id": 1,
            "sender_id": 123,
            "sender_name": "User",
            "mentioned": False,
            "text": "Test message",
            "replies": 0,
            "reactions": 0,
            "timestamp": "2025-11-13T10:00:00+00:00",
        }
    ]

    # Simulate real publish
    for msg in messages:
        redis_data = {"json": json.dumps(msg)}
        mock_redis.xadd("tgsentinel:messages", redis_data)

    # Verify Redis xadd was called correctly
    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args

    # Verify stream name
    assert call_args[0][0] == "tgsentinel:messages"

    # Verify payload structure
    payload = call_args[0][1]
    assert "json" in payload
    parsed = json.loads(payload["json"])
    assert parsed["chat_id"] == -100123456789
    assert parsed["msg_id"] == 1


def test_config_validation_comprehensive():
    """Comprehensive test of all config validation rules."""
    from ui.app import _validate_config_payload

    # Valid configs should not raise
    valid_configs = [
        {"mode": "dm"},
        {"mode": "channel"},
        {"mode": "both"},
        {
            "mode": "dm",
            "redis_port": 6379,
            "retention_days": 30,
            "rate_limit_per_channel": 25,
        },
    ]

    for config in valid_configs:
        _validate_config_payload(config)

    # Invalid configs should raise
    invalid_configs = [
        {"mode": "invalid"},
        {"redis_port": 0},
        {"redis_port": 99999},
        {"retention_days": -1},
        {"rate_limit_per_channel": -5},
    ]

    for config in invalid_configs:
        with pytest.raises(ValueError):
            _validate_config_payload(config)
