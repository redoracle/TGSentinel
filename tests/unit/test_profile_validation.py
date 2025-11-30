"""Unit tests for profile validation in channel and user update endpoints."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add ui directory to path
ui_path = Path(__file__).parent.parent.parent / "ui"
if str(ui_path) not in sys.path:
    sys.path.insert(0, str(ui_path))


@pytest.fixture
def mock_profile_service():
    """Mock ProfileService with test profiles."""
    service = MagicMock()
    service.list_global_profiles.return_value = [
        {"id": "security", "name": "Security Profile", "total_keywords": 10},
        {"id": "trading", "name": "Trading Profile", "total_keywords": 15},
        {"id": "development", "name": "Development Profile", "total_keywords": 8},
    ]
    return service


@pytest.fixture
def app_client():
    """Create a Flask test client."""
    os.environ["UI_SECRET_KEY"] = "test-secret"
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"

    # Remove cached modules
    for mod in list(sys.modules.keys()):
        if mod.startswith("ui."):
            sys.modules.pop(mod, None)

    from ui.app import app

    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client


class TestChannelProfileValidation:
    """Tests for profile ID validation in channel update endpoint."""

    @patch("redis.Redis")  # Mock redis module imported inside function
    @patch("ui.services.profiles_service.get_profile_service")
    @patch("ui.routes.channels._fetch_sentinel_config")
    @patch("ui.routes.channels._update_sentinel_config")
    @patch("ui.routes.channels._reload_ui_config")
    def test_valid_profile_ids_accepted(
        self,
        mock_reload,
        mock_update,
        mock_fetch,
        mock_get_svc,
        mock_redis_cls,
        app_client,
        mock_profile_service,
    ):
        """Test that valid profile IDs are accepted."""
        # Setup Redis mock for distributed locking
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.return_value = True  # Lock acquired successfully
        mock_redis_instance.get.return_value = "ui-test-lock"
        mock_redis_cls.return_value = mock_redis_instance

        mock_get_svc.return_value = mock_profile_service
        mock_fetch.return_value = (
            {
                "channels": [
                    {"id": 123, "name": "Test Channel", "profiles": [], "overrides": {}}
                ]
            },
            None,
        )
        mock_update.return_value = None

        response = app_client.put(
            "/api/config/channels/123",
            json={"profiles": ["security", "trading"], "name": "Updated Channel"},
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"

    @patch("ui.services.profiles_service.get_profile_service")
    @patch("ui.routes.channels._fetch_sentinel_config")
    def test_invalid_profile_ids_rejected(
        self, mock_fetch, mock_get_svc, app_client, mock_profile_service
    ):
        """Test that invalid profile IDs are rejected with 400."""
        mock_get_svc.return_value = mock_profile_service
        mock_fetch.return_value = (
            {"channels": [{"id": 123, "name": "Test Channel"}]},
            None,
        )

        response = app_client.put(
            "/api/config/channels/123",
            json={"profiles": ["security", "invalid_profile", "another_bad_id"]},
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "invalid_profile" in data["message"]
        assert "another_bad_id" in data["message"]

    @patch("ui.services.profiles_service.get_profile_service")
    @patch("ui.routes.channels._fetch_sentinel_config")
    def test_profiles_not_list_rejected(
        self, mock_fetch, mock_get_svc, app_client, mock_profile_service
    ):
        """Test that non-list profiles value is rejected."""
        mock_get_svc.return_value = mock_profile_service
        mock_fetch.return_value = (
            {"channels": [{"id": 123, "name": "Test Channel"}]},
            None,
        )

        response = app_client.put(
            "/api/config/channels/123",
            json={"profiles": "security"},  # String instead of list
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "must be a list" in data["message"]

    @patch("redis.Redis")
    @patch("ui.services.profiles_service.get_profile_service")
    @patch("ui.routes.channels._fetch_sentinel_config")
    @patch("ui.routes.channels._update_sentinel_config")
    @patch("ui.routes.channels._reload_ui_config")
    def test_empty_profiles_list_accepted(
        self,
        mock_reload,
        mock_update,
        mock_fetch,
        mock_get_svc,
        mock_redis_cls,
        app_client,
        mock_profile_service,
    ):
        """Test that empty profiles list is valid (unbinding all)."""
        # Setup Redis mock
        mock_redis_instance = MagicMock()
        mock_redis_instance.set.return_value = True
        mock_redis_instance.get.return_value = "ui-test-lock"
        mock_redis_cls.return_value = mock_redis_instance
        mock_get_svc.return_value = mock_profile_service
        mock_fetch.return_value = (
            {
                "channels": [
                    {"id": 123, "name": "Test Channel", "profiles": ["security"]}
                ]
            },
            None,
        )
        mock_update.return_value = None

        response = app_client.put(
            "/api/config/channels/123",
            json={"profiles": []},
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert data["channel"]["profiles"] == []


class TestUserProfileValidation:
    """Test profile validation in user update endpoint."""

    @patch("ui.services.profiles_service.get_profile_service")
    @patch("requests.get")
    @patch("requests.post")
    def test_valid_profile_ids_accepted(
        self, mock_post, mock_get, mock_get_svc, app_client, mock_profile_service
    ):
        """Test that valid profile IDs are accepted for users."""
        mock_get_svc.return_value = mock_profile_service

        # Mock Sentinel config fetch
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {
                "data": {
                    "monitored_users": [
                        {
                            "id": 456,
                            "name": "Test User",
                            "profiles": [],
                            "overrides": {},
                        }
                    ]
                }
            },
        )

        # Mock Sentinel config update
        mock_post.return_value = MagicMock(ok=True)

        response = app_client.put(
            "/api/config/users/456",
            json={"profiles": ["security", "trading"], "name": "Updated User"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "security" in data["user"]["profiles"]
        assert "trading" in data["user"]["profiles"]

    @patch("ui.services.profiles_service.get_profile_service")
    @patch("requests.get")
    def test_invalid_profile_ids_rejected(
        self, mock_get, mock_get_svc, app_client, mock_profile_service
    ):
        """Test that invalid profile IDs are rejected with 400."""
        mock_get_svc.return_value = mock_profile_service
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {
                "data": {"monitored_users": [{"id": 456, "name": "Test User"}]}
            },
        )

        response = app_client.put(
            "/api/config/users/456",
            json={"profiles": ["nonexistent_profile"]},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "nonexistent_profile" in data["message"]

    @patch("ui.services.profiles_service.get_profile_service")
    @patch("requests.get")
    def test_overrides_not_dict_rejected(
        self, mock_get, mock_get_svc, app_client, mock_profile_service
    ):
        """Test that non-dict overrides value is rejected."""
        mock_get_svc.return_value = mock_profile_service
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {
                "data": {"monitored_users": [{"id": 456, "name": "Test User"}]}
            },
        )

        response = app_client.put(
            "/api/config/users/456",
            json={"overrides": "invalid"},  # String instead of dict
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "must be an object" in data["message"]

    @patch("ui.services.profiles_service.get_profile_service")
    @patch("requests.get")
    def test_min_score_out_of_range_rejected(
        self, mock_get, mock_get_svc, app_client, mock_profile_service
    ):
        """Test that min_score outside 0-1 range is rejected."""
        mock_get_svc.return_value = mock_profile_service
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {
                "data": {"monitored_users": [{"id": 456, "name": "Test User"}]}
            },
        )

        # Test value > 1
        response = app_client.put(
            "/api/config/users/456",
            json={"overrides": {"min_score": 1.5}},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "min_score" in data["message"]

        # Test negative value
        response = app_client.put(
            "/api/config/users/456",
            json={"overrides": {"min_score": -0.1}},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "min_score" in data["message"]

    @patch("ui.services.profiles_service.get_profile_service")
    @patch("requests.get")
    @patch("requests.post")
    def test_valid_min_score_accepted(
        self, mock_post, mock_get, mock_get_svc, app_client, mock_profile_service
    ):
        """Test that valid min_score values are accepted."""
        mock_get_svc.return_value = mock_profile_service
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {
                "data": {
                    "monitored_users": [
                        {"id": 456, "name": "Test User", "overrides": {}}
                    ]
                }
            },
        )
        mock_post.return_value = MagicMock(ok=True)

        # Test edge values
        for score in [0.0, 0.5, 1.0]:
            response = app_client.put(
                "/api/config/users/456",
                json={"overrides": {"min_score": score}},
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data["status"] == "ok"
            assert data["user"]["overrides"]["min_score"] == score


class TestInputSanitization:
    """Test input sanitization and type checking."""

    @patch("ui.routes.channels._fetch_sentinel_config")
    def test_non_json_request_rejected(self, mock_fetch, app_client):
        """Test that non-JSON requests are rejected."""
        response = app_client.put(
            "/api/config/channels/123",
            data="not json",
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "application/json" in data["message"]

    @patch("ui.routes.channels._fetch_sentinel_config")
    def test_invalid_json_rejected(self, mock_fetch, app_client):
        """Test that invalid JSON is rejected."""
        response = app_client.put(
            "/api/config/channels/123",
            data="{invalid json}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["status"] == "error"
        assert "Invalid JSON" in data["message"]
