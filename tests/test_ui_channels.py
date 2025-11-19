"""Tests for UI channel management endpoints."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg


pytestmark = pytest.mark.integration


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
        redis={"host": "redis", "port": 6379, "stream": "test"},
        db_uri="sqlite:///test.db",
        embeddings_model="all-MiniLM-L6-v2",
        similarity_threshold=0.42,
    )


@pytest.fixture
def app_client(mock_config):
    """Create a Flask test client with mocked dependencies."""
    ui_path = Path(__file__).parent.parent / "ui"
    if str(ui_path) not in sys.path:
        sys.path.insert(0, str(ui_path))

    # Set test environment variables
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"
    os.environ["UI_SECRET_KEY"] = "test-secret-key"

    # Remove cached modules to force fresh import
    for mod in list(sys.modules.keys()):
        if mod.startswith(("app", "ui.")):
            del sys.modules[mod]

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

            # Initialize app which registers all blueprints
            flask_app.init_app()

            with flask_app.app.test_client() as client:
                yield client

    # Cleanup
    if "UI_DB_URI" in os.environ:
        del os.environ["UI_DB_URI"]
    if "UI_SECRET_KEY" in os.environ:
        del os.environ["UI_SECRET_KEY"]


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    config_data = {
        "telegram": {"session": "/tmp/test.session"},
        "channels": [
            {
                "id": -1001234567890,
                "name": "Existing Channel",
                "vip_senders": [],
                "keywords": [],
                "reaction_threshold": 5,
                "reply_threshold": 3,
                "rate_limit_per_hour": 10,
            }
        ],
        "interests": [],
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yml") as tmp_file:
        yaml.dump(config_data, tmp_file)
        temp_path = tmp_file.name

    yield temp_path

    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


class TestTelegramChatsEndpoint:
    """Tests for /api/telegram/chats endpoint."""

    def test_get_telegram_chats_success(self, app_client, mock_config):
        """Test successful retrieval of Telegram chats via Redis delegation."""
        with app_client.session_transaction() as sess:
            sess["telegram_authenticated"] = True

        # Mock the module-level redis_client in telegram_routes (injected via init_blueprint)
        with patch("ui.api.telegram_routes.redis_client") as mock_redis:
            response_data = json.dumps(
                {
                    "status": "ok",
                    "chats": [
                        {
                            "id": -1001111111111,
                            "name": "Test Channel",
                            "type": "channel",
                            "username": "testchannel",
                        }
                    ],
                }
            )

            # Mock get() to return response immediately (sentinel responded)
            mock_redis.get.return_value = response_data
            mock_redis.setex.return_value = True
            mock_redis.delete.return_value = True

            response = app_client.get("/api/telegram/chats")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "chats" in data
            assert len(data["chats"]) == 1
            assert data["chats"][0]["id"] == -1001111111111
            assert data["chats"][0]["name"] == "Test Channel"
            assert isinstance(data["chats"], list)

    def test_get_telegram_chats_redis_unavailable(self, app_client, mock_config):
        """Test error when Redis is not available."""
        with app_client.session_transaction() as sess:
            sess["telegram_authenticated"] = True

        # Set module-level redis_client to None
        with patch("ui.api.telegram_routes.redis_client", None):
            response = app_client.get("/api/telegram/chats")

            # When redis is None, returns 503
            assert response.status_code == 503
            data = json.loads(response.data)
            assert data["status"] == "error"
            assert "Redis not available" in data["message"]

    def test_get_telegram_chats_sentinel_timeout(self, app_client, mock_config):
        """Test timeout when sentinel does not respond."""
        with app_client.session_transaction() as sess:
            sess["telegram_authenticated"] = True

        with patch("ui.api.telegram_routes.redis_client") as mock_redis:
            # Mock no response from sentinel (timeout scenario)
            mock_redis.get.return_value = None
            mock_redis.setex.return_value = True
            mock_redis.delete.return_value = True

            response = app_client.get("/api/telegram/chats")

            # When sentinel doesn't respond within 30s, we get 504
            assert response.status_code == 504
            data = json.loads(response.data)
            assert data["status"] == "error"
            assert "did not respond in time" in data["message"].lower()

    def test_get_telegram_chats_session_not_found(self, app_client, mock_config):
        """Test error response from sentinel."""
        with app_client.session_transaction() as sess:
            sess["telegram_authenticated"] = True

        with patch("ui.api.telegram_routes.redis_client") as mock_redis:
            # Mock error response from sentinel
            error_data = json.dumps(
                {"status": "error", "error": "Failed to fetch dialogs"}
            )

            mock_redis.get.return_value = error_data
            mock_redis.setex.return_value = True
            mock_redis.delete.return_value = True

            response = app_client.get("/api/telegram/chats")

            # Sentinel errors return 500
            assert response.status_code == 500
            data = json.loads(response.data)
            assert data["status"] == "error"
            assert "Failed to fetch" in data["message"]

    def test_get_telegram_chats_multiple_types(self, app_client, mock_config):
        """Test retrieval of different chat types via Redis delegation."""
        with app_client.session_transaction() as sess:
            sess["telegram_authenticated"] = True

        with patch("ui.api.telegram_routes.redis_client") as mock_redis:
            # Mock Redis response with multiple chat types
            response_data = json.dumps(
                {
                    "status": "ok",
                    "chats": [
                        {
                            "id": -1001111111111,
                            "name": "Broadcast Channel",
                            "type": "channel",
                            "username": None,
                        },
                        {
                            "id": -1002222222222,
                            "name": "Supergroup",
                            "type": "supergroup",
                            "username": "testsupergroup",
                        },
                    ],
                }
            )

            mock_redis.get.return_value = response_data
            mock_redis.setex.return_value = True
            mock_redis.delete.return_value = True

            response = app_client.get("/api/telegram/chats")

            assert response.status_code == 200
            data = json.loads(response.data)
            assert len(data["chats"]) == 2
            # Both are channels but with different properties
            assert all(
                chat["type"] in ["channel", "supergroup", "group"]
                for chat in data["chats"]
            )


class TestAddChannelsEndpoint:
    """Tests for /api/config/channels/add endpoint."""

    def test_add_channels_success(self, app_client, mock_config, monkeypatch):
        """Test successfully adding channels to config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config" / "tgsentinel.yml"
            config_path.parent.mkdir(parents=True)
            initial_config = {
                "channels": [
                    {
                        "id": -1001234567890,
                        "name": "Existing Channel",
                        "vip_senders": [],
                        "keywords": [],
                    }
                ]
            }
            config_path.write_text(yaml.dump(initial_config))

            # Patch config path
            monkeypatch.chdir(tmpdir)

            payload = {
                "channels": [
                    {"id": -1009876543210, "name": "New Channel 1"},
                    {"id": -1005555555555, "name": "New Channel 2"},
                ]
            }

            response = app_client.post(
                "/api/config/channels/add",
                data=json.dumps(payload),
                content_type="application/json",
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "ok"
            assert data["added"] == 2

            # Verify config was updated
            updated_config = yaml.safe_load(config_path.read_text())
            assert len(updated_config["channels"]) == 3

            # Check new channels have default values
            new_channels = [
                ch for ch in updated_config["channels"] if ch["id"] != -1001234567890
            ]
            assert len(new_channels) == 2
            for channel in new_channels:
                assert channel["reaction_threshold"] == 5
                assert channel["reply_threshold"] == 3
                assert channel["rate_limit_per_hour"] == 10
                assert channel["vip_senders"] == []
                assert channel["keywords"] == []

    def test_add_channels_skip_duplicates(self, app_client, mock_config, monkeypatch):
        """Test that duplicate channels are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config" / "tgsentinel.yml"
            config_path.parent.mkdir(parents=True)
            initial_config = {
                "channels": [
                    {
                        "id": -1001234567890,
                        "name": "Existing Channel",
                        "vip_senders": [],
                        "keywords": [],
                    }
                ]
            }
            config_path.write_text(yaml.dump(initial_config))

            monkeypatch.chdir(tmpdir)

            payload = {
                "channels": [
                    {"id": -1001234567890, "name": "Duplicate Channel"},
                    {"id": -1009876543210, "name": "New Channel"},
                ]
            }

            response = app_client.post(
                "/api/config/channels/add",
                data=json.dumps(payload),
                content_type="application/json",
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "ok"
            assert data["added"] == 1  # Only 1 new channel added

            # Verify only 2 channels in config (not 3)
            updated_config = yaml.safe_load(config_path.read_text())
            assert len(updated_config["channels"]) == 2

    def test_add_channels_invalid_json(self, app_client, mock_config):
        """Test error with invalid JSON."""
        response = app_client.post(
            "/api/config/channels/add",
            data="not json",
            content_type="application/json",
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["status"] == "error"

    def test_add_channels_missing_channels_array(self, app_client, mock_config):
        """Test error when channels array is missing."""
        response = app_client.post(
            "/api/config/channels/add",
            data=json.dumps({"wrong_key": []}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "channels array is required" in data["message"]

    def test_add_channels_empty_array(self, app_client, mock_config):
        """Test error when channels array is empty."""
        response = app_client.post(
            "/api/config/channels/add",
            data=json.dumps({"channels": []}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["status"] == "error"

    def test_add_channels_config_not_found(self, app_client, mock_config, monkeypatch):
        """Test that config file is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)

            payload = {"channels": [{"id": -1009876543210, "name": "New Channel"}]}

            response = app_client.post(
                "/api/config/channels/add",
                data=json.dumps(payload),
                content_type="application/json",
            )

            # The endpoint creates the config file if it doesn't exist
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "ok"
            assert data["added"] == 1

            # Verify config was created
            config_path = Path(tmpdir) / "config" / "tgsentinel.yml"
            assert config_path.exists()

    def test_add_channels_preserves_existing_config(
        self, app_client, mock_config, monkeypatch
    ):
        """Test that adding channels preserves other config sections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config" / "tgsentinel.yml"
            config_path.parent.mkdir(parents=True)
            initial_config = {
                "telegram": {"session": "/tmp/test.session"},
                "alerts": {"mode": "dm"},
                "channels": [],
                "interests": ["blockchain", "security"],
            }
            config_path.write_text(yaml.dump(initial_config))

            monkeypatch.chdir(tmpdir)

            payload = {"channels": [{"id": -1009876543210, "name": "New Channel"}]}

            response = app_client.post(
                "/api/config/channels/add",
                data=json.dumps(payload),
                content_type="application/json",
            )

            assert response.status_code == 200

            # Verify other sections are preserved
            updated_config = yaml.safe_load(config_path.read_text())
            assert updated_config["telegram"]["session"] == "/tmp/test.session"
            assert updated_config["alerts"]["mode"] == "dm"
            assert updated_config["interests"] == ["blockchain", "security"]
            assert len(updated_config["channels"]) == 1
