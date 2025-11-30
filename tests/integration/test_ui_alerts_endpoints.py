"""Tests for session endpoints and alerts export/recent APIs."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg, RedisCfg, SystemCfg

pytestmark = pytest.mark.integration


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock configuration object."""
    session_path = tmp_path / "test.session"
    session_path.write_text("")

    return AppCfg(
        telegram_session=str(session_path),
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
        system=SystemCfg(
            redis=RedisCfg(host="redis", port=6379, stream="test"),
            database_uri="sqlite:///test.db",
        ),
        embeddings_model="all-MiniLM-L6-v2",
        similarity_threshold=0.42,
    )


@pytest.fixture
def app_client(mock_config):
    """Create a Flask test client with mocked dependencies."""
    import sys
    from pathlib import Path

    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    # Set test environment variables
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"
    os.environ["UI_SECRET_KEY"] = "test-secret-key"

    # Remove cached app module
    if "ui.app" in sys.modules:
        del sys.modules["ui.app"]

    with patch("redis.Redis") as mock_redis:
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis_instance.xlen.return_value = 0
        mock_redis.return_value = mock_redis_instance

        with patch("ui.app.load_config", return_value=mock_config):
            import ui.app as flask_app

            # Reset state and reinitialize for test isolation
            flask_app.reset_for_testing()

            flask_app.app.config["TESTING"] = True
            flask_app.app.config["TGSENTINEL_CONFIG"] = mock_config
            flask_app.config = mock_config
            flask_app.redis_client = mock_redis_instance

            # Initialize app to register all routes
            flask_app.init_app()

            with flask_app.app.test_client() as client:
                yield client


@pytest.mark.parametrize(
    "endpoint",
    [
        ("/api/session/info"),
    ],
)
def test_session_info_ok(app_client, endpoint):
    """Test session info endpoint returns user data."""
    # Mock the helper function that loads user info from Redis
    with patch("ui.routes.session._load_cached_user_info") as mock_load_user:
        # Mock worker_status to show authorized
        with patch("ui.routes.session.redis_client") as mock_redis:
            mock_redis.get.return_value = json.dumps(
                {"authorized": True, "status": "authorized"}
            ).encode()

            # Mock user info
            mock_load_user.return_value = {
                "username": "tester",
                "phone": "+19 555-007",
                "avatar": "/static/images/logo.png",
            }

            resp = app_client.get(endpoint)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["username"] == "tester"
            assert "phone_masked" in data


def test_session_logout_clears_session_and_cache(app_client, mock_config):
    """Test logout endpoint removes session file and Redis keys."""
    # Ensure the session file exists before logout
    session_file = Path(mock_config.telegram_session)
    assert session_file.exists()

    # Mock the invalidate_session helper to return expected result
    with patch("ui.routes.session._invalidate_session") as mock_invalidate:
        mock_invalidate.return_value = {
            "file_removed": True,
            "cache_keys_deleted": ["tgsentinel:worker_status", "tgsentinel:user_info"],
            "session_path": str(session_file),
        }

        resp = app_client.post("/api/session/logout")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["details"]["file_removed"] is True
        # The helper should have been called
        assert mock_invalidate.call_count == 1


def test_session_relogin_behaves_like_logout(app_client):
    """Test relogin endpoint clears session and sets relogin flag."""
    with patch("ui.routes.session.redis_client") as mock_redis:
        mock_redis.delete.return_value = True
        resp = app_client.post("/api/session/relogin")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data.get("relogin_required") is True


def test_export_alerts_with_data(app_client):
    """Test CSV export with alert data."""
    sample_alerts = [
        {
            "message_id": 123,
            "chat_id": 1001,
            "chat_title": "Chan",
            "sender_name": "Alice",
            "message_text": "Hello",
            "score": 0.9,
            "triggers": "Keywords",
            "sent_to": "dm",
            "timestamp": "2025-01-01T10:00:00Z",
        }
    ]

    # Mock DataService to return alerts
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "status": "ok",
            "data": {"alerts": sample_alerts},
        }
        mock_get.return_value = mock_response

        resp = app_client.get("/api/export_alerts?limit=10")
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type", "").startswith("text/csv")
        body = resp.data.decode()
        assert "Channel,Sender,Excerpt,Score,Trigger,Destination,Timestamp" in body
        assert "Alice" in body


def test_export_alerts_when_empty(app_client):
    """Test CSV export when no alerts exist."""
    # Mock DataService to return no alerts
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"status": "ok", "data": {"alerts": []}}
        mock_get.return_value = mock_response

        resp = app_client.get("/api/export_alerts")
        assert resp.status_code == 200
        body = resp.data.decode()
        # Should have header row
        assert "Channel,Sender,Excerpt,Score,Trigger,Destination,Timestamp" in body


def test_recent_alerts_ok(app_client):
    """Test recent alerts endpoint returns alert list."""
    sample_alerts = [
        {
            "chat_id": 1,
            "chat_title": "A",
            "score": 0.1,
            "timestamp": "2025-01-01T10:00:00Z",
        }
    ]

    # Mock DataService to return alerts
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "status": "ok",
            "data": {"alerts": sample_alerts},
        }
        mock_get.return_value = mock_response

        resp = app_client.get("/api/alerts/recent?limit=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "alerts" in data
        assert isinstance(data["alerts"], list)
