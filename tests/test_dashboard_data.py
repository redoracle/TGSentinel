"""
Comprehensive test for dashboard data endpoints.
Tests the full data pipeline: Redis → API → Frontend
"""

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import redis


@pytest.fixture
def test_db():
    """Create a temporary test database with messages table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE messages (
            chat_id INTEGER,
            msg_id INTEGER,
            content_hash TEXT,
            score REAL,
            alerted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (chat_id, msg_id)
        )
    """
    )

    # Insert test messages with different timestamps
    now = datetime.now()
    test_data = [
        (
            -100123456789,
            1001,
            "hash1",
            0.85,
            1,
            (now - timedelta(minutes=5)).isoformat(),
        ),
        (
            -100123456789,
            1002,
            "hash2",
            0.65,
            0,
            (now - timedelta(minutes=3)).isoformat(),
        ),
        (
            -100987654321,
            2001,
            "hash3",
            0.92,
            1,
            (now - timedelta(minutes=2)).isoformat(),
        ),
        (
            -100987654321,
            2002,
            "hash4",
            0.55,
            0,
            (now - timedelta(minutes=1)).isoformat(),
        ),
    ]

    cursor.executemany(
        "INSERT INTO messages (chat_id, msg_id, content_hash, score, alerted, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        test_data,
    )
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def test_redis():
    """Create a test Redis connection with sample messages."""
    # Use Redis database 15 for testing (usually unused)
    r = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)

    # Clear any existing test data
    r.delete("sentinel:messages")

    # Add test messages to the stream
    test_messages = [
        {
            "chat_id": -100123456789,
            "chat_title": "Algorand Dev",
            "msg_id": 1001,
            "sender_id": 12345,
            "mentioned": False,
            "text": "Test message about security vulnerability",
            "replies": 3,
            "reactions": 5,
        },
        {
            "chat_id": -100987654321,
            "chat_title": "Security Feeds",
            "msg_id": 2001,
            "sender_id": 67890,
            "mentioned": True,
            "text": "Critical alert: CVE-2024-1234",
            "replies": 10,
            "reactions": 15,
        },
        {
            "chat_id": 355791041,
            "chat_title": "Stakelovelace Italia",
            "msg_id": 3001,
            "sender_id": 11111,
            "mentioned": False,
            "text": "Regular channel update",
            "replies": 0,
            "reactions": 2,
        },
    ]

    for msg in test_messages:
        r.xadd("sentinel:messages", {"json": json.dumps(msg)})

    yield r

    # Cleanup
    r.delete("sentinel:messages")
    r.close()


@pytest.fixture
def app_with_test_data(test_db, test_redis):
    """Create Flask app with test database and Redis."""

    # Add ui path to sys.path
    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    # Mock config with test channels
    mock_config = MagicMock()
    mock_config.channels = [
        MagicMock(id=-100123456789, name="Algorand Dev", enabled=True),
        MagicMock(id=-100987654321, name="Security Feeds", enabled=True),
        MagicMock(id=355791041, name="Stakelovelace Italia", enabled=True),
        MagicMock(id=186207886, name="Stakelovelace Official ENG", enabled=True),
        MagicMock(id=742138586, name="Redoracle Security", enabled=True),
    ]
    mock_config.db_uri = f"sqlite:///{test_db}"
    mock_config.redis = {
        "host": "localhost",
        "port": 6379,
        "db": 15,
        "stream": "sentinel:messages",
    }

    with patch("app.load_config", return_value=mock_config):
        import app as flask_app

        # Use public API to reset global state for testing
        flask_app.reset_for_testing()

        with patch.object(flask_app.redis, "Redis", return_value=test_redis):
            flask_app.app.config["TESTING"] = True
            flask_app.config = mock_config

            yield flask_app.app


def test_system_health_with_data(app_with_test_data, test_redis):
    """Test /api/system/health returns valid metrics with real data."""
    client = app_with_test_data.test_client()

    # test_redis fixture already added 3 messages, no need to add more

    response = client.get("/api/system/health")
    assert response.status_code == 200

    data = response.get_json()
    assert data is not None

    # Check all required fields are present
    assert "redis_stream_depth" in data
    assert "database_size_mb" in data
    assert "redis_online" in data
    assert "cpu_percent" in data
    assert "memory_mb" in data
    assert "last_checkpoint" in data

    # Verify Redis stream depth (fixture adds 3 messages)
    assert data["redis_stream_depth"] == 3
    assert data["redis_online"] is True

    # Database size should be > 0
    assert data["database_size_mb"] > 0

    # CPU and memory should be reasonable values (or None if psutil not available)
    if data["cpu_percent"] is not None:
        assert 0 <= data["cpu_percent"] <= 100
    if data["memory_mb"] is not None:
        assert data["memory_mb"] > 0

    # Timestamp should be valid (or None if session file doesn't exist)
    # In test environment, session file may not exist
    assert data["last_checkpoint"] is None or isinstance(data["last_checkpoint"], str)


def test_analytics_metrics_with_data(app_with_test_data):
    """Test /api/analytics/metrics returns valid metrics with database data."""
    client = app_with_test_data.test_client()

    response = client.get("/api/analytics/metrics")
    assert response.status_code == 200

    data = response.get_json()
    assert data is not None

    # Check all required fields
    assert "messages_per_min" in data
    assert "semantic_latency" in data
    assert "cpu" in data
    assert "memory" in data
    assert "redis_stream_depth" in data

    # We have 4 messages in the database, all within the last hour
    # messages_per_min should be > 0
    assert data["messages_per_min"] >= 0

    # Redis stream depth should match
    assert data["redis_stream_depth"] == 3

    # Metrics should be reasonable (or None if psutil not available)
    if data["cpu"] is not None:
        assert 0 <= data["cpu"] <= 100
    if data["memory"] is not None:
        assert data["memory"] > 0
    assert data["semantic_latency"] >= 0


def test_dashboard_activity_with_data(app_with_test_data):
    """Test /api/dashboard/activity returns proper feed with chat names."""
    client = app_with_test_data.test_client()

    response = client.get("/api/dashboard/activity")
    assert response.status_code == 200

    data = response.get_json()
    assert isinstance(data, dict)
    assert "entries" in data

    entries = data["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 3  # We added 3 messages to Redis

    # Check first message has proper structure
    first_msg = entries[0]
    assert "chat_name" in first_msg
    assert "message" in first_msg or "text" in first_msg

    # Verify chat names are resolved (not "Unknown chat")
    chat_names = [msg["chat_name"] for msg in entries]
    assert "Unknown chat" not in chat_names

    # Check expected channel names appear
    expected_names = {"Algorand Dev", "Security Feeds", "Stakelovelace Italia"}
    actual_names = set(chat_names)
    assert actual_names == expected_names


def test_dashboard_activity_unknown_chat_fallback(app_with_test_data):
    """Test that unknown chat IDs still display properly."""
    client = app_with_test_data.test_client()

    # Add a message with an unknown chat ID directly to Redis
    r = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
    unknown_msg = {
        "chat_id": -9999999999,
        "chat_title": "Unknown Channel",
        "msg_id": 9999,
        "sender_id": 99999,
        "mentioned": False,
        "text": "Message from unknown channel",
        "replies": 0,
        "reactions": 0,
    }
    r.xadd("sentinel:messages", {"json": json.dumps(unknown_msg)})

    response = client.get("/api/dashboard/activity")
    assert response.status_code == 200

    data = response.get_json()
    entries = data["entries"]
    assert len(entries) == 4  # Now we have 4 messages

    # Find the unknown channel message (look for chat_id in any field that might have it)
    unknown = next(
        (m for m in entries if "Unknown Channel" in m.get("chat_name", "")), None
    )
    assert unknown is not None
    # Should use chat_title from the message
    assert unknown["chat_name"] == "Unknown Channel"

    # Cleanup
    r.close()


def test_analytics_with_empty_database(app_with_test_data, test_db):
    """Test analytics endpoint handles empty database gracefully."""
    # Clear the database
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages")
    conn.commit()
    conn.close()

    client = app_with_test_data.test_client()
    response = client.get("/api/analytics/metrics")
    assert response.status_code == 200

    data = response.get_json()
    # Should still return valid structure, messages_per_min should be 0 or close to 0
    assert data["messages_per_min"] <= 0.1  # Allow small rounding differences


def test_dashboard_activity_with_empty_redis(test_db, test_redis):
    """Test dashboard activity handles empty Redis stream gracefully."""
    # Start with an empty stream (test_redis fixture is already clear before we add data)
    # We need to create a new app with empty Redis

    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    mock_config = MagicMock()
    mock_config.channels = []  # Empty channels list
    mock_config.db_uri = f"sqlite:///{test_db}"
    mock_config.redis = {
        "host": "localhost",
        "port": 6379,
        "db": 15,
        "stream": "sentinel:messages",
    }

    # Clear the Redis stream first
    test_redis.delete("sentinel:messages")

    with patch("app.load_config", return_value=mock_config):
        with patch("redis.Redis") as mock_redis_class:
            mock_redis_class.return_value = test_redis

            import app as flask_app  # type: ignore[import-not-found]

            flask_app.app.config["TESTING"] = True
            flask_app.config = mock_config

            client = flask_app.app.test_client()
            response = client.get("/api/dashboard/activity")
            assert response.status_code == 200

            data = response.get_json()
            assert isinstance(data, dict)
            assert "entries" in data
            entries = data["entries"]
            assert isinstance(entries, list)
            # When Redis is empty, it might return a default message or empty list
            # Either is acceptable
            assert len(entries) <= 1
