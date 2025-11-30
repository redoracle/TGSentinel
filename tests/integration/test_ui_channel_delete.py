"""Tests for channel deletion endpoint."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg, RedisCfg, SystemCfg


@pytest.fixture
def mock_config():
    """Create a mock configuration object."""
    return AppCfg(
        telegram_session="/tmp/test.session",
        api_id=12345,
        api_hash="test_hash",
        alerts=AlertsCfg(
            mode="both",
            target_channel="@test_bot",
            digest=DigestCfg(hourly=True, daily=True, top_n=10),
        ),
        channels=[],
        monitored_users=[],
        interests=["test interest 1", "test interest 2"],
        system=SystemCfg(
            redis=RedisCfg(host="redis", port=6379, stream="test"),
            database_uri="sqlite:///test.db",
        ),
        embeddings_model="all-MiniLM-L6-v2",
        similarity_threshold=0.42,
    )


@pytest.fixture
def app_client(mock_config):
    """Create a Flask test client with mocked dependencies."""
    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    # Set test environment variables
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"
    os.environ["UI_SECRET_KEY"] = "test-secret-key"

    # Remove cached app module to force fresh import
    if "app" in sys.modules:
        del sys.modules["app"]

    with patch("redis.Redis") as mock_redis:
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis_instance.xlen.return_value = 0
        mock_redis.return_value = mock_redis_instance

        with patch("ui.app.load_config", return_value=mock_config):
            import ui.app as flask_app

            # Reset state before initialization to prevent route conflicts
            flask_app.reset_for_testing()

            flask_app.app.config["TESTING"] = True
            flask_app.app.config["TGSENTINEL_CONFIG"] = mock_config

            flask_app.init_app()
            with flask_app.app.test_client() as client:
                yield client

    # Cleanup
    if "UI_DB_URI" in os.environ:
        del os.environ["UI_DB_URI"]
    if "UI_SECRET_KEY" in os.environ:
        del os.environ["UI_SECRET_KEY"]


class TestDeleteChannelEndpoint:
    """Test suite for DELETE /api/config/channels/<chat_id> endpoint."""

    def test_delete_channel_route_exists(self, app_client):
        """Test that the DELETE route is registered and accessible."""
        # This will fail with 404 if route doesn't exist, or 500 if config file doesn't exist
        response = app_client.delete("/api/config/channels/123")
        # Should return 404 (config not found), 500, or 503 when Sentinel is unavailable
        assert response.status_code in [404, 500, 503]
        # Ensure it's returning JSON, not HTML
        assert response.content_type == "application/json"

    def test_delete_channel_returns_json(self, app_client):
        """Test that endpoint returns JSON even on error."""
        response = app_client.delete("/api/config/channels/999")
        assert response.content_type == "application/json"
        data = response.get_json()
        assert "status" in data
        assert data["status"] == "error"

    def test_delete_channel_with_large_negative_id(self, app_client):
        """Test that endpoint handles large negative Telegram chat IDs."""
        # Telegram group IDs can be very large negative numbers like -1002222333444
        response = app_client.delete("/api/config/channels/-1002222333444")
        # Should return JSON and never raise a Method Not Allowed response
        assert response.status_code != 405
        assert response.content_type == "application/json"
        data = response.get_json()
        assert "status" in data
        # Could be "ok" (deleted), "error" (not found), or config file issue
        assert data["status"] in ["ok", "error"]

    def test_get_channel_with_negative_id(self, app_client):
        """Regression test ensuring GET uses the signed-int route."""
        response = app_client.get("/api/config/channels/-1001234567890")
        assert response.status_code != 405
        assert response.content_type == "application/json"
