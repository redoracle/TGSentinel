"""
Comprehensive live test for dashboard endpoints.
Tests actual app instance with real data to ensure dashboard displays correctly.
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
def live_test_setup():
    """Set up a complete test environment with data."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Initialize database with schema
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create messages table
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

    # Insert test data with recent timestamps
    now = datetime.now()
    test_messages = [
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
        (355791041, 3001, "hash5", 0.78, 1, now.isoformat()),
    ]

    cursor.executemany(
        "INSERT INTO messages (chat_id, msg_id, content_hash, score, alerted, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        test_messages,
    )
    conn.commit()
    conn.close()

    # Set up Redis with test data
    test_redis = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
    test_redis.delete("sentinel:messages")

    # Add messages to Redis stream
    redis_messages = [
        {
            "chat_id": -100123456789,
            "chat_title": "Test Channel Alpha",
            "msg_id": 1001,
            "sender_id": 12345,
            "text": "Important security update detected",
            "replies": 5,
            "reactions": 10,
        },
        {
            "chat_id": -100987654321,
            "chat_title": "Test Channel Beta",
            "msg_id": 2001,
            "sender_id": 67890,
            "text": "Critical vulnerability CVE-2024-9999",
            "replies": 8,
            "reactions": 15,
        },
        {
            "chat_id": 355791041,
            "chat_title": "Test Channel Gamma",
            "msg_id": 3001,
            "sender_id": 11111,
            "text": "Regular update notification",
            "replies": 2,
            "reactions": 3,
        },
    ]

    for msg in redis_messages:
        test_redis.xadd("sentinel:messages", {"json": json.dumps(msg)})

    yield {"db_path": db_path, "redis": test_redis, "db_uri": f"sqlite:///{db_path}"}

    # Cleanup
    test_redis.delete("sentinel:messages")
    test_redis.close()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def live_app(live_test_setup):
    """Create Flask app with live data."""
    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    # Create mock config
    mock_config = MagicMock()
    mock_config.channels = [
        MagicMock(id=-100123456789, name="Test Channel Alpha"),
        MagicMock(id=-100987654321, name="Test Channel Beta"),
        MagicMock(id=355791041, name="Test Channel Gamma"),
    ]
    mock_config.db_uri = live_test_setup["db_uri"]
    mock_config.redis = {
        "host": "localhost",
        "port": 6379,
        "db": 15,
        "stream": "sentinel:messages",
    }
    # Mock alerts config properly to avoid MagicMock serialization issues
    mock_alerts = MagicMock()
    mock_alerts.mode = "dm"
    mock_config.alerts = mock_alerts

    with patch("app.load_config", return_value=mock_config):
        with patch("redis.Redis") as mock_redis_class:
            mock_redis_class.return_value = live_test_setup["redis"]

            import app as flask_app  # type: ignore[import-not-found]

            flask_app.app.config["TESTING"] = True
            flask_app.config = mock_config

            yield flask_app.app


def test_dashboard_page_loads(live_app):
    """Test that dashboard page loads with all data."""
    client = live_app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode("utf-8")

    # Check that key elements are present
    assert "System Health" in html
    assert "Live Activity Feed" in html
    assert "Latest Alerts" in html

    # Check that data is rendered (not showing "No activity" or "No alerts")
    assert (
        "Test Channel Alpha" in html
        or "Test Channel Beta" in html
        or "Test Channel Gamma" in html
    )


def test_system_health_endpoint_with_live_data(live_app):
    """Test /api/system/health returns valid data."""
    client = live_app.test_client()
    response = client.get("/api/system/health")

    assert response.status_code == 200
    assert response.content_type == "application/json"

    data = response.get_json()

    # Verify all expected fields are present
    assert "redis_stream_depth" in data
    assert "database_size_mb" in data
    assert "redis_online" in data
    assert "cpu_percent" in data
    assert "memory_mb" in data
    assert "last_checkpoint" in data

    # Verify we have actual data
    assert data["redis_stream_depth"] == 3  # We added 3 messages
    assert data["redis_online"] is True
    assert data["database_size_mb"] > 0


def test_dashboard_activity_endpoint_with_live_data(live_app):
    """Test /api/dashboard/activity returns live feed with proper chat names."""
    client = live_app.test_client()
    response = client.get("/api/dashboard/activity")

    assert response.status_code == 200
    assert response.content_type == "application/json"

    data = response.get_json()
    assert "entries" in data

    entries = data["entries"]
    assert len(entries) == 3

    # Verify chat names are resolved (not "Unknown chat")
    chat_names = [entry["chat_name"] for entry in entries]
    assert "Unknown chat" not in chat_names

    # Verify expected channel names are present
    expected_names = {"Test Channel Alpha", "Test Channel Beta", "Test Channel Gamma"}
    actual_names = set(chat_names)
    assert actual_names == expected_names

    # Verify structure of entries
    for entry in entries:
        assert "chat_name" in entry
        assert "message" in entry or "text" in entry
        assert "sender" in entry
        assert "importance" in entry
        assert "timestamp" in entry


def test_analytics_metrics_with_live_data(live_app):
    """Test /api/analytics/metrics returns proper metrics."""
    client = live_app.test_client()
    response = client.get("/api/analytics/metrics")

    assert response.status_code == 200
    assert response.content_type == "application/json"

    data = response.get_json()

    # Verify all expected fields
    assert "messages_per_min" in data
    assert "semantic_latency" in data
    assert "cpu" in data
    assert "memory" in data
    assert "redis_stream_depth" in data

    # We have 5 messages in the database within the last hour
    # messages_per_min should be > 0 (5 messages / 60 minutes ≈ 0.08)
    assert data["messages_per_min"] > 0
    assert data["redis_stream_depth"] == 3


def test_recent_alerts_endpoint_with_live_data(live_app):
    """Test /api/alerts/recent returns alerts from database."""
    client = live_app.test_client()
    response = client.get("/api/alerts/recent")

    assert response.status_code == 200
    assert response.content_type == "application/json"

    data = response.get_json()
    assert "alerts" in data

    alerts = data["alerts"]
    # We have at least 1 alerted message in the database
    assert len(alerts) >= 1

    # Verify alert structure
    for alert in alerts[:3]:
        assert "chat_id" in alert
        assert "score" in alert
        assert "created_at" in alert


def test_dashboard_summary_with_live_data(live_app):
    """Test /api/dashboard/summary returns aggregated stats."""
    client = live_app.test_client()
    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    assert response.content_type == "application/json"

    data = response.get_json()

    # Verify summary fields
    assert "messages_ingested" in data
    assert "alerts_sent" in data
    assert "avg_importance" in data
    assert "feedback_accuracy" in data

    # Verify we have some messages and alerts
    assert data["messages_ingested"] >= 1
    assert data["alerts_sent"] >= 1
    assert data["avg_importance"] > 0


def test_favicon_endpoint(live_app):
    """Test that favicon endpoint exists and returns an image."""
    client = live_app.test_client()
    response = client.get("/favicon.ico")

    # Should return 200 and an image
    assert response.status_code == 200
    assert "image" in response.content_type


def test_analytics_page_resource_utilization(live_app):
    """Test analytics page loads and contains resource utilization data."""
    client = live_app.test_client()
    response = client.get("/analytics")

    assert response.status_code == 200
    html = response.data.decode("utf-8")

    # Check for key analytics sections
    assert "Messages per minute" in html
    assert "Semantic latency" in html
    assert "Feedback accuracy" in html
    assert "Resource Utilisation" in html or "Resource Utilization" in html


def test_all_dashboard_endpoints_return_json(live_app):
    """Verify all dashboard API endpoints return proper JSON."""
    client = live_app.test_client()

    endpoints = [
        "/api/system/health",
        "/api/dashboard/activity",
        "/api/dashboard/summary",
        "/api/alerts/recent",
        "/api/analytics/metrics",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint)
        assert (
            response.status_code == 200
        ), f"{endpoint} failed with {response.status_code}"
        assert (
            response.content_type == "application/json"
        ), f"{endpoint} returned {response.content_type}"
        data = response.get_json()
        assert data is not None, f"{endpoint} returned no data"
