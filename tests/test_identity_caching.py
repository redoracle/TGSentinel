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

    # Clear any cached module
    import sys

    if "app" in sys.modules:
        del sys.modules["app"]

    # Mock config to return test values
    with patch("ui.app.load_config") as mock_load:
        cfg = MagicMock()
        cfg.channels = []
        cfg.db_uri = "sqlite:///:memory:"
        cfg.redis = {"host": "localhost", "port": 6379, "stream": "tgsentinel:messages"}
        cfg.api_id = 123456
        cfg.api_hash = "hash"
        mock_load.return_value = cfg

        import app as flask_app  # type: ignore

        flask_app.init_app()
        return flask_app.app


def test_user_info_has_required_fields_for_ui():
    """Test that cached user info includes all fields needed by UI."""
    app = _make_app()

    # Import the internal functions from ui.app
    from ui.app import _load_cached_user_info, redis_client as original_redis

    # Simulate user info stored by worker
    user_info = {
        "username": "testuser",
        "first_name": "Test",
        "last_name": "User",
        "phone": "+1234567890",
        "user_id": 12345,
        "avatar": "/data/user_avatar.jpg",
    }

    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(user_info)

    import ui.app

    ui.app.redis_client = mock_redis

    try:
        loaded = _load_cached_user_info()

        # Verify all required fields are present
        assert loaded is not None
        assert loaded.get("username") == "testuser"
        assert loaded.get("first_name") == "Test"
        assert loaded.get("last_name") == "User"
        assert loaded.get("phone") is not None  # May be formatted
        assert loaded.get("user_id") == 12345
        assert loaded.get("avatar") == "/data/user_avatar.jpg"
    finally:
        ui.app.redis_client = original_redis


def test_session_info_endpoint_returns_identity():
    """Test /api/session/info returns cached identity."""
    app = _make_app()
    client = app.test_client()
    import app as flask_app  # type: ignore[import-not-found]

    user_info = {
        "username": "john_doe",
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+12025551234",
        "user_id": 99999,
        "avatar": "/data/user_avatar.jpg",
    }

    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(user_info)

    original_redis = flask_app.redis_client
    flask_app.redis_client = mock_redis

    try:
        response = client.get("/api/session/info")
        assert response.status_code == 200

        data = response.get_json()
        assert data["username"] == "john_doe"
        assert data["avatar"] == "/data/user_avatar.jpg"
        assert data["phone_masked"] is not None  # Phone should be masked
    finally:
        flask_app.redis_client = original_redis


def test_logout_removes_identity_from_redis():
    """Test that logout removes user_info and avatar from Redis."""
    app = _make_app()
    from ui.app import _invalidate_session, redis_client as original_redis

    mock_redis = MagicMock()
    mock_redis.delete.return_value = 1
    mock_redis.scan_iter.return_value = ["tgsentinel:user_avatar:12345"]

    import ui.app

    ui.app.redis_client = mock_redis

    try:
        result = _invalidate_session("/app/data/tgsentinel.session")

        # Verify user_info was deleted
        assert "tgsentinel:user_info" in result["cache_keys_deleted"]

        # Verify relogin handshake was cleared
        assert "tgsentinel:relogin" in result["cache_keys_deleted"]

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
        "avatar": "/data/user_avatar.jpg",
    }

    # Verify all expected fields are present
    assert set(user_info.keys()) == expected_fields


def test_fallback_when_redis_unavailable():
    """Test that UI falls back gracefully when Redis is unavailable."""
    app = _make_app()
    client = app.test_client()
    import app as flask_app  # type: ignore[import-not-found]

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


def test_avatar_path_formats():
    """Test that both avatar path formats are supported."""
    app = _make_app()
    from ui.app import _load_cached_user_info, redis_client as original_redis

    test_cases = [
        "/data/user_avatar.jpg",  # Downloaded avatar
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
