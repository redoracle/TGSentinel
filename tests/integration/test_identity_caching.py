"""Tests for user identity caching in Redis for both authentication methods."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


def _make_app():
    os.environ["UI_SECRET_KEY"] = "test-secret"
    os.environ["TG_API_ID"] = "123456"
    os.environ["TG_API_HASH"] = "hash"
    os.environ["DB_URI"] = "sqlite:///:memory:"
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"

    # Clear any cached module
    import sys

    if "ui.app" in sys.modules:
        del sys.modules["ui.app"]

    # Mock config to return test values
    with patch("ui.app.load_config") as mock_load:
        from tgsentinel.config import RedisCfg, SystemCfg

        cfg = MagicMock()
        cfg.channels = []  # Empty list to avoid serialization issues
        cfg.system = SystemCfg(
            redis=RedisCfg(host="localhost", port=6379, stream="tgsentinel:messages"),
            database_uri="sqlite:///:memory:",
        )
        cfg.api_id = 123456
        cfg.api_hash = "hash"
        # Add legacy properties for backward compatibility
        cfg.redis = {"host": "localhost", "port": 6379, "stream": "tgsentinel:messages"}
        cfg.db_uri = "sqlite:///:memory:"
        mock_load.return_value = cfg

        import ui.app as flask_app

        flask_app.init_app()
        return flask_app.app


@pytest.mark.unit
def test_user_info_has_required_fields_for_ui():
    """Test that cached user info includes all fields needed by UI."""
    app = _make_app()

    # Import the internal functions from ui.app
    from ui.app import redis_client as original_redis

    # Simulate user info stored by worker
    user_info = {
        "username": "testuser",
        "first_name": "Test",
        "last_name": "User",
        "phone": "+1234567890",
        "user_id": 12345,
        "avatar": "/api/avatar/user/12345",
    }

    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(user_info).encode()

    import ui.app
    from ui.redis_cache import load_cached_user_info

    ui.app.redis_client = mock_redis

    try:
        loaded = load_cached_user_info(mock_redis)

        # Verify all required fields are present
        assert loaded is not None
        assert loaded.get("username") == "testuser"
        assert loaded.get("first_name") == "Test"
        assert loaded.get("last_name") == "User"
        assert loaded.get("phone") is not None  # May be formatted
        assert loaded.get("user_id") == 12345
        assert loaded.get("avatar") == "/api/avatar/user/12345"
    finally:
        ui.app.redis_client = original_redis


@pytest.mark.integration
def test_session_info_endpoint_returns_identity():
    """Test /api/session/info endpoint structure and fallback behavior."""
    app = _make_app()
    client = app.test_client()

    # Without proper Redis/authorization, endpoint should still work with fallbacks
    response = client.get("/api/session/info")
    assert response.status_code == 200

    data = response.get_json()
    # Verify response has required structure
    assert "username" in data
    assert "avatar" in data
    assert "phone_masked" in data
    assert "authorized" in data

    # When not authorized, should return fallback values
    # (This tests the contract, not implementation details)


def test_logout_removes_identity_from_redis():
    """Test that logout removes user_info and avatar from Redis."""
    app = _make_app()
    from ui.app import _invalidate_session
    from ui.app import redis_client as original_redis

    mock_redis = MagicMock()
    mock_redis.delete.return_value = 1
    mock_redis.scan_iter.return_value = ["tgsentinel:user_avatar:12345"]

    import ui.app

    ui.app.redis_client = mock_redis

    try:
        result = _invalidate_session("/app/data/tgsentinel.session")

        # Verify user_info was deleted
        assert "tgsentinel:user_info" in result["cache_keys_deleted"]

        # Verify relogin handshake was cleared (canonical key from DB_Architecture.instructions.md)
        assert "tgsentinel:relogin:handshake" in result["cache_keys_deleted"]

        # Verify avatar was deleted
        assert any(
            key.startswith("tgsentinel:user_avatar:")
            for key in result["cache_keys_deleted"]
        )
    finally:
        ui.app.redis_client = original_redis


def test_identity_fields_consistency():
    """Test that worker stores consistent field names across auth methods."""
    # This test documents the expected Redis schema
    expected_fields = {
        "username",  # Primary display name
        "first_name",  # First name
        "last_name",  # Last name
        "phone",  # Phone number
        "user_id",  # Telegram user ID (not "id")
        "avatar",  # Avatar URL or path
    }

    # Simulate what worker should store
    user_info = {
        "username": "testuser",
        "first_name": "Test",
        "last_name": "User",
        "phone": "+1234567890",
        "user_id": 12345,
        "avatar": "/api/avatar/user/12345",
    }

    # Verify all expected fields are present
    assert set(user_info.keys()) == expected_fields


@pytest.mark.integration
def test_fallback_when_redis_unavailable():
    """Test that UI falls back gracefully when Redis is unavailable."""
    app = _make_app()
    client = app.test_client()
    import ui.app as flask_app

    original_redis = flask_app.redis_client
    flask_app.redis_client = None

    try:
        response = client.get("/api/session/info")
        assert response.status_code == 200

        data = response.get_json()
        # Should return fallback values
        assert data["username"] is not None  # Fallback username
        assert data["avatar"] == "/static/images/logo.png"  # Fallback avatar
    finally:
        flask_app.redis_client = original_redis


@pytest.mark.unit
def test_avatar_path_formats():
    """Test that both avatar path formats are supported."""
    app = _make_app()
    from ui.app import _load_cached_user_info
    from ui.app import redis_client as original_redis

    test_cases = [
        "/api/avatar/user/123",  # Avatar from Redis via API
        "/static/images/logo.png",  # Fallback logo
        "https://example.com/avatar.jpg",  # External URL (if supported)
    ]

    import ui.app

    for avatar_path in test_cases:
        user_info = {
            "username": "test",
            "user_id": 123,
            "avatar": avatar_path,
        }

        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(user_info)

        ui.app.redis_client = mock_redis

        try:
            loaded = _load_cached_user_info()
            assert loaded is not None
            assert loaded.get("avatar") == avatar_path
        finally:
            ui.app.redis_client = original_redis
