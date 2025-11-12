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
        """Test successful retrieval of Telegram chats."""
        # Mock environment variables
        with patch.dict(os.environ, {"TG_API_ID": "12345", "TG_API_HASH": "test_hash"}):
            # Mock session path exists
            with patch("pathlib.Path.exists", return_value=True):
                # Mock the entire get_dialogs functionality
                from telethon.tl.types import Channel

                channel_entity = MagicMock(spec=Channel)
                channel_entity.id = -1001111111111
                channel_entity.title = "Test Channel"
                channel_entity.broadcast = True
                channel_entity.megagroup = False
                channel_entity.username = "testchannel"

                mock_dialog = MagicMock()
                mock_dialog.entity = channel_entity

                with patch("telethon.TelegramClient") as mock_tg:
                    mock_client_instance = MagicMock()

                    # Mock async methods
                    async def mock_connect():
                        return None

                    async def mock_is_authorized():
                        return True

                    async def mock_get_dialogs():
                        return [mock_dialog]

                    mock_client_instance.connect = mock_connect
                    mock_client_instance.is_user_authorized = mock_is_authorized
                    mock_client_instance.get_dialogs = mock_get_dialogs
                    mock_client_instance.disconnect = MagicMock()

                    mock_tg.return_value = mock_client_instance

                    response = app_client.get("/api/telegram/chats")

                    assert response.status_code == 200
                    data = json.loads(response.data)
                    assert "chats" in data
                    assert len(data["chats"]) == 1
                    assert data["chats"][0]["id"] == -1001111111111
                    assert data["chats"][0]["name"] == "Test Channel"
                    assert isinstance(data["chats"], list)

    def test_get_telegram_chats_missing_api_credentials(self, app_client, mock_config):
        """Test error when API credentials are missing."""
        with patch.dict(os.environ, {"TG_API_ID": "", "TG_API_HASH": ""}, clear=True):
            response = app_client.get("/api/telegram/chats")

            assert response.status_code == 400
            data = json.loads(response.data)
            assert data["status"] == "error"
            assert "TG_API_ID and TG_API_HASH are required" in data["message"]

    def test_get_telegram_chats_invalid_api_id(self, app_client, mock_config):
        """Test error when API ID is not numeric."""
        with patch.dict(
            os.environ, {"TG_API_ID": "not_a_number", "TG_API_HASH": "test_hash"}
        ):
            response = app_client.get("/api/telegram/chats")

            assert response.status_code == 400
            data = json.loads(response.data)
            assert data["status"] == "error"
            assert "must be numeric" in data["message"]

    def test_get_telegram_chats_session_not_found(self, app_client, mock_config):
        """Test error when session file doesn't exist."""
        with patch.dict(os.environ, {"TG_API_ID": "12345", "TG_API_HASH": "test_hash"}):
            with patch("pathlib.Path.exists", return_value=False):
                response = app_client.get("/api/telegram/chats")

                assert response.status_code == 404
                data = json.loads(response.data)
                assert data["status"] == "error"
                assert "session not found" in data["message"].lower()

    def test_get_telegram_chats_multiple_types(self, app_client, mock_config):
        """Test retrieval of different chat types."""
        with patch.dict(os.environ, {"TG_API_ID": "12345", "TG_API_HASH": "test_hash"}):
            with patch("pathlib.Path.exists", return_value=True):
                # Create mocks for different chat types
                from telethon.tl.types import Channel

                channel_entity = MagicMock(spec=Channel)
                channel_entity.id = -1001111111111
                channel_entity.title = "Broadcast Channel"
                channel_entity.broadcast = True
                channel_entity.megagroup = False
                channel_entity.username = None

                supergroup_entity = MagicMock(spec=Channel)
                supergroup_entity.id = -1002222222222
                supergroup_entity.title = "Supergroup"
                supergroup_entity.broadcast = False
                supergroup_entity.megagroup = True
                supergroup_entity.username = "testsupergroup"

                mock_dialogs = []
                for entity in [channel_entity, supergroup_entity]:
                    dialog = MagicMock()
                    dialog.entity = entity
                    mock_dialogs.append(dialog)

                with patch("telethon.TelegramClient") as mock_tg:
                    mock_client_instance = MagicMock()

                    # Mock async methods
                    async def mock_connect():
                        return None

                    async def mock_is_authorized():
                        return True

                    async def mock_get_dialogs():
                        return mock_dialogs

                    mock_client_instance.connect = mock_connect
                    mock_client_instance.is_user_authorized = mock_is_authorized
                    mock_client_instance.get_dialogs = mock_get_dialogs
                    mock_client_instance.disconnect = MagicMock()

                    mock_tg.return_value = mock_client_instance

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
        """Test error when config file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)

            payload = {"channels": [{"id": -1009876543210, "name": "New Channel"}]}

            response = app_client.post(
                "/api/config/channels/add",
                data=json.dumps(payload),
                content_type="application/json",
            )

            assert response.status_code == 404
            data = json.loads(response.data)
            assert data["status"] == "error"
            assert "not found" in data["message"].lower()

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
