"""Integration tests for profile binding workflow.

Tests the complete validation flow for profile bindings in channel and user updates,
ensuring validation rules are correctly enforced across the stack.
"""

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
def app_client():
    """Create Flask test client."""
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


@pytest.fixture
def mock_profile_service():
    """Mock ProfileService with test profiles."""
    service = MagicMock()
    service.list_global_profiles.return_value = [
        {"id": "security", "name": "Security Profile", "total_keywords": 10},
        {"id": "trading", "name": "Trading Profile", "total_keywords": 15},
    ]
    return service


class TestProfileBindingValidation:
    """Integration tests for profile binding validation across the stack."""

    def test_channel_with_valid_profiles_accepted(
        self, app_client, mock_profile_service
    ):
        """Test: Channel update with valid profile IDs is accepted."""
        with (
            patch("redis.Redis") as mock_redis_cls,
            patch("ui.services.profiles_service.get_profile_service") as mock_get_svc,
            patch("ui.routes.channels._fetch_sentinel_config") as mock_fetch,
            patch("ui.routes.channels._update_sentinel_config") as mock_update,
            patch("ui.routes.channels._reload_ui_config"),
        ):

            # Setup mocks
            mock_redis_instance = MagicMock()
            mock_redis_instance.set.return_value = True
            mock_redis_instance.get.return_value = "test-lock"
            mock_redis_cls.return_value = mock_redis_instance

            mock_get_svc.return_value = mock_profile_service
            mock_fetch.return_value = (
                {"channels": [{"id": 123, "name": "Test Channel", "profiles": []}]},
                None,
            )
            mock_update.return_value = None

            response = app_client.put(
                "/api/config/channels/123",
                json={"profiles": ["security", "trading"]},
                headers={"Content-Type": "application/json"},
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["status"] == "ok"

    def test_channel_with_invalid_profiles_rejected(
        self, app_client, mock_profile_service
    ):
        """Test: Channel update with invalid profile IDs is rejected with 400."""
        with patch("ui.services.profiles_service.get_profile_service") as mock_get_svc:
            mock_get_svc.return_value = mock_profile_service

            response = app_client.put(
                "/api/config/channels/123",
                json={"profiles": ["security", "nonexistent_profile"]},
                headers={"Content-Type": "application/json"},
            )

            assert response.status_code == 400
            data = response.get_json()
            assert data["status"] == "error"
            assert "nonexistent_profile" in data["message"]

    def test_user_with_valid_profiles_and_overrides_accepted(
        self, app_client, mock_profile_service
    ):
        """Test: User update with valid profiles and overrides is accepted."""
        with (
            patch("ui.services.profiles_service.get_profile_service") as mock_get_svc,
            patch("requests.get") as mock_get,
            patch("requests.post") as mock_post,
        ):

            mock_get_svc.return_value = mock_profile_service
            mock_get.return_value = MagicMock(
                ok=True,
                json=lambda: {
                    "data": {"monitored_users": [{"id": 456, "name": "Test User"}]}
                },
            )
            mock_post.return_value = MagicMock(ok=True)

            response = app_client.put(
                "/api/config/users/456",
                json={
                    "profiles": ["security"],
                    "overrides": {"min_score": 0.9},
                },
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["status"] == "ok"

    def test_user_with_invalid_min_score_rejected(
        self, app_client, mock_profile_service
    ):
        """Test: User update with invalid min_score is rejected."""
        with (
            patch("ui.services.profiles_service.get_profile_service") as mock_get_svc,
            patch("requests.get") as mock_get,
        ):

            mock_get_svc.return_value = mock_profile_service
            mock_get.return_value = MagicMock(
                ok=True,
                json=lambda: {
                    "data": {"monitored_users": [{"id": 456, "name": "Test User"}]}
                },
            )

            response = app_client.put(
                "/api/config/users/456",
                json={
                    "profiles": ["security"],
                    "overrides": {"min_score": 1.5},  # Invalid: > 1.0
                },
            )

            assert response.status_code == 400
            data = response.get_json()
            assert "min_score" in data["message"].lower()

    def test_channel_profile_unbinding(self, app_client, mock_profile_service):
        """Test: Removing all profiles from channel (empty list) is valid."""
        with (
            patch("redis.Redis") as mock_redis_cls,
            patch("ui.services.profiles_service.get_profile_service") as mock_get_svc,
            patch("ui.routes.channels._fetch_sentinel_config") as mock_fetch,
            patch("ui.routes.channels._update_sentinel_config") as mock_update,
            patch("ui.routes.channels._reload_ui_config"),
        ):

            mock_redis_instance = MagicMock()
            mock_redis_instance.set.return_value = True
            mock_redis_instance.get.return_value = "test-lock"
            mock_redis_cls.return_value = mock_redis_instance

            mock_get_svc.return_value = mock_profile_service
            mock_fetch.return_value = (
                {
                    "channels": [
                        {
                            "id": 123,
                            "name": "Test Channel",
                            "profiles": ["security", "trading"],
                        }
                    ]
                },
                None,
            )
            mock_update.return_value = None

            response = app_client.put(
                "/api/config/channels/123",
                json={"profiles": []},  # Unbind all profiles
                headers={"Content-Type": "application/json"},
            )

            assert response.status_code == 200

    def test_non_list_profiles_value_rejected(self, app_client, mock_profile_service):
        """Test: Non-list value for profiles field is rejected."""
        with patch("ui.services.profiles_service.get_profile_service") as mock_get_svc:
            mock_get_svc.return_value = mock_profile_service

            response = app_client.put(
                "/api/config/channels/123",
                json={"profiles": "security"},  # String instead of list
                headers={"Content-Type": "application/json"},
            )

            assert response.status_code == 400
            data = response.get_json()
            assert "must be a list" in data["message"]
