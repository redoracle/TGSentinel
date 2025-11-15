"""Tests for the Telegram users API endpoint."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis import Redis


class TestTelegramUsersAPI:
    """Test the /api/telegram/users endpoint."""

    @pytest.fixture
    def app_client(self):
        """Create a Flask test client with mocked dependencies."""
        ui_path = Path(__file__).parent.parent / "ui"
        sys.path.insert(0, str(ui_path))

        from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg

        mock_config = AppCfg(
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
            redis={"host": "redis", "port": 6379, "stream": "test"},
            db_uri="sqlite:///test.db",
            embeddings_model="all-MiniLM-L6-v2",
            similarity_threshold=0.42,
        )

        with patch("app.load_config", return_value=mock_config):
            import app as flask_app

            flask_app.app.config["TESTING"] = True
            flask_app.app.config["TGSENTINEL_CONFIG"] = mock_config

            yield flask_app.app.test_client()

    @patch("app.redis_client")
    def test_users_api_with_redis_success(self, mock_redis, client):
        """Test successful retrieval with Redis response."""
        # Mock successful response from sentinel
        users_response = [
            {
                "id": 123456,
                "name": "Test User",
                "username": "testuser",
                "phone": "+1234567890",
            }
        ]

        response_data = json.dumps({"status": "success", "users": users_response})

        # Mock Redis calls in sequence:
        # 1. Cache check (returns None - cache miss)
        # 2. First poll of response key (returns response immediately)
        mock_redis.get.side_effect = [None, response_data]
        mock_redis.setex.return_value = True
        mock_redis.delete.return_value = True

        response = client.get("/api/telegram/users")

        assert response.status_code == 200
        data = response.get_json()
        assert "users" in data
        assert len(data["users"]) == 1
        assert data["users"][0]["name"] == "Test User"

    def test_users_api_with_cache_hit(self, client):
        """Test retrieval from Redis cache."""
        with patch("app.redis.Redis") as mock_redis_class:
            mock_redis = MagicMock(spec=Redis)
            mock_redis_class.return_value = mock_redis

            # Mock cache hit
            cached_users = [
                {
                    "id": 111111,
                    "name": "Cached User",
                    "username": "cached",
                    "phone": None,
                }
            ]
            mock_redis.get.return_value = json.dumps(cached_users)

            response = client.get("/api/telegram/users")

            assert response.status_code == 200
            data = response.get_json()
            assert len(data["users"]) == 1
            assert data["users"][0]["name"] == "Cached User"

    def test_users_api_timeout_fallback_empty(self, client):
        """Test timeout fallback when no monitored users configured."""
        with patch("app.redis_client") as mock_redis:
            # Mock cache miss and no response (timeout)
            mock_redis.get.return_value = None
            mock_redis.delete.return_value = True

            response = client.get("/api/telegram/users")

            assert response.status_code == 200
            data = response.get_json()
            assert "users" in data
            assert len(data["users"]) == 0  # Empty fallback

    def test_users_api_timeout_fallback_with_monitored_users(self, client):
        """Test timeout fallback returns configured monitored users."""
        ui_path = Path(__file__).parent.parent / "ui"
        sys.path.insert(0, str(ui_path))

        from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg, MonitoredUser

        # Create config with monitored users
        monitored_users = [
            MonitoredUser(id=555555, name="Monitored User 1", username="monitored1"),
            MonitoredUser(id=666666, name="Monitored User 2", username="monitored2"),
        ]

        mock_config = AppCfg(
            telegram_session="/tmp/test.session",
            api_id=12345,
            api_hash="test_hash",
            alerts=AlertsCfg(
                mode="both",
                target_channel="@test_bot",
                digest=DigestCfg(hourly=True, daily=True, top_n=10),
            ),
            channels=[],
            monitored_users=monitored_users,
            interests=["test interest"],
            redis={"host": "redis", "port": 6379, "stream": "test"},
            db_uri="sqlite:///test.db",
            embeddings_model="all-MiniLM-L6-v2",
            similarity_threshold=0.42,
        )

        with patch("app.load_config", return_value=mock_config):
            with patch("app.redis_client") as mock_redis:
                # Mock cache miss and no response (timeout)
                mock_redis.get.return_value = None
                mock_redis.delete.return_value = True

                response = client.get("/api/telegram/users")

                assert response.status_code == 200
                data = response.get_json()
                assert len(data["users"]) == 2
                assert data["users"][0]["id"] == 555555
                assert data["users"][0]["name"] == "Monitored User 1"
                assert data["source"] == "config"

    def test_users_api_redis_error(self, client):
        """Test Redis connection error - returns empty list gracefully."""
        with patch("app.redis_client", None):  # Simulate no Redis
            response = client.get("/api/telegram/users")

            # Updated: API now returns 200 with empty list instead of 503
            assert response.status_code == 200
            data = response.get_json()
            assert data["users"] == []

    def test_users_api_malformed_response(self, client):
        """Test malformed response from sentinel."""
        with patch("app.redis_client") as mock_redis:
            # Mock cache miss and malformed response
            mock_redis.get.side_effect = [None, "invalid json"]
            mock_redis.delete.return_value = True

            response = client.get("/api/telegram/users")

            assert response.status_code == 200  # Falls back to empty
            data = response.get_json()
            assert "users" in data
            assert len(data["users"]) == 0
