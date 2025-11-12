"""Tests for channel deletion endpoint."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg


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
        interests=["test interest 1", "test interest 2"],
        redis={"host": "redis", "port": 6379, "stream": "test"},
        db_uri="sqlite:///test.db",
        embeddings_model="all-MiniLM-L6-v2",
        similarity_threshold=0.42,
    )


@pytest.fixture
def app_client(mock_config):
    """Create a Flask test client with mocked dependencies."""
    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    with patch("redis.Redis") as mock_redis:
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis_instance.xlen.return_value = 0
        mock_redis.return_value = mock_redis_instance

        with patch("app.load_config", return_value=mock_config):
            import app as flask_app  # type: ignore[import-not-found]

            flask_app.init_app()
            with flask_app.app.test_client() as client:
                yield client


class TestDeleteChannelEndpoint:
    """Test suite for DELETE /api/config/channels/<chat_id> endpoint."""

    def test_delete_channel_route_exists(self, app_client):
        """Test that the DELETE route is registered and accessible."""
        # This will fail with 404 if route doesn't exist, or 500 if config file doesn't exist
        response = app_client.delete("/api/config/channels/123")
        # Should return 404 (config not found) or 500, not 404 "route not found"
        assert response.status_code in [404, 500]
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
        # Should return JSON, not HTML 404
        assert response.content_type == "application/json"
        data = response.get_json()
        assert "status" in data
        # Could be "ok" (deleted), "error" (not found), or config file issue
        assert data["status"] in ["ok", "error"]
