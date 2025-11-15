"""Test configuration and shared fixtures for TG Sentinel tests."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis import Redis
from sqlalchemy import create_engine


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_config_file(temp_dir):
    """Create a temporary config file for testing."""
    config_content = """
telegram:
  session: "data/test.session"

alerts:
  mode: "dm"
  target_channel: ""
  digest:
    hourly: true
    daily: true
    top_n: 10

channels:
  - id: -100123456789
    name: "Test Channel"
    vip_senders: [11111, 22222]
    keywords: ["test", "important"]
    reaction_threshold: 5
    reply_threshold: 3
    rate_limit_per_hour: 10

interests:
  - "test topic"
  - "important subject"
"""
    config_file = temp_dir / "test_config.yml"
    config_file.write_text(config_content)
    return str(config_file)


@pytest.fixture
def test_env_vars(monkeypatch):
    """Set up test environment variables."""
    monkeypatch.setenv("TG_API_ID", "123456")
    monkeypatch.setenv("TG_API_HASH", "test_hash_123")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("DB_URI", "sqlite:///:memory:")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "")
    monkeypatch.setenv("SIMILARITY_THRESHOLD", "0.42")


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis_mock = MagicMock(spec=Redis)
    redis_mock.xadd.return_value = b"1234567890-0"
    redis_mock.xreadgroup.return_value = []
    redis_mock.xgroup_create.return_value = True
    redis_mock.xack.return_value = 1
    return redis_mock


@pytest.fixture
def mock_telegram_client():
    """Create a mock Telegram client."""
    client = AsyncMock()
    client.send_message = AsyncMock()
    client.start = AsyncMock()
    client.run_until_disconnected = AsyncMock()
    return client


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite database for testing."""
    from tgsentinel.store import init_db

    engine = init_db("sqlite:///:memory:")
    return engine


@pytest.fixture
def sample_message_payload():
    """Create a sample message payload for testing."""
    return {
        "chat_id": -100123456789,
        "chat_title": "Test Channel",
        "msg_id": 12345,
        "sender_id": 11111,
        "mentioned": False,
        "text": "This is a test message with important keyword",
        "replies": 2,
        "reactions": 5,
    }


@pytest.fixture
def sample_telegram_message():
    """Create a sample Telegram message object."""
    msg = MagicMock()
    msg.id = 12345
    msg.sender_id = 11111
    msg.mentioned = False
    msg.message = "Test message"
    msg.replies = MagicMock()
    msg.replies.replies = 2
    msg.reactions = MagicMock()
    msg.reactions.results = [MagicMock(count=3), MagicMock(count=2)]
    return msg


@pytest.fixture
def sample_telegram_event():
    """Create a sample Telegram event."""
    event = MagicMock()
    event.chat_id = -100123456789

    # Mock chat
    chat = MagicMock()
    chat.title = "Test Channel"
    event.chat = chat

    # Mock async get_chat() method
    async def mock_get_chat():
        return chat

    event.get_chat = mock_get_chat

    # Mock sender with all required attributes (id, first_name, last_name, username)
    sender = MagicMock()
    sender.id = 11111
    sender.first_name = "John"
    sender.last_name = "Doe"
    sender.username = "johndoe"
    event.sender = sender

    # Mock async get_sender() method
    async def mock_get_sender():
        return sender

    event.get_sender = mock_get_sender

    event.message = MagicMock()
    event.message.id = 12345
    event.message.sender_id = 11111  # Matches sender.id
    event.message.mentioned = False
    event.message.message = "Test message"
    event.message.replies = MagicMock()
    event.message.replies.replies = 2
    event.message.reactions = None
    # Add date attribute for timestamp field
    from datetime import datetime, timezone

    event.message.date = datetime.now(timezone.utc)
    return event


@pytest.fixture
def app():
    """Create Flask app instance for testing."""
    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    # Create mock config
    mock_config = MagicMock()
    mock_config.channels = []
    mock_config.db_uri = "sqlite:///:memory:"
    mock_config.redis = {
        "host": "localhost",
        "port": 6379,
        "db": 15,  # Use DB 15 for tests
        "stream": "sentinel:messages",
    }
    mock_alerts = MagicMock()
    mock_alerts.mode = "dm"
    mock_config.alerts = mock_alerts

    with patch("app.load_config", return_value=mock_config):
        import app as flask_app  # type: ignore[import-not-found]

        flask_app.app.config["TESTING"] = True
        flask_app.app.config["TGSENTINEL_CONFIG"] = mock_config

        yield flask_app.app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def mock_init():
    """Mock initialization to avoid external dependencies."""
    with (
        patch("ui.app._is_initialized", True),
        patch("ui.app.config") as mock_config,
        patch("ui.app.engine") as mock_engine,
        patch("ui.app.redis_client"),
    ):
        mock_config.telegram_session = "/app/data/test.session"
        mock_config.db_uri = "sqlite:///:memory:"
        mock_config.redis = {"host": "localhost", "port": 6379}

        # Mock database connection
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.scalar.return_value = 0
        mock_conn.execute.return_value = []

        yield mock_config
