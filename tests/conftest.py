"""Test configuration and shared fixtures for TG Sentinel tests."""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import create_engine
from redis import Redis


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
    event.chat = MagicMock()
    event.chat.title = "Test Channel"
    event.message = MagicMock()
    event.message.id = 12345
    event.message.sender_id = 11111
    event.message.mentioned = False
    event.message.message = "Test message"
    event.message.replies = MagicMock()
    event.message.replies.replies = 2
    event.message.reactions = None
    return event
