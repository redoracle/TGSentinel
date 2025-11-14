"""Tests for session endpoints and alerts export/recent APIs."""

import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_app_with_config(tmp_path: Path):
    """Import UI app with a patched config and environment."""
    # Ensure secret for Flask app
    os.environ.setdefault("UI_SECRET_KEY", "test-secret")
    # Minimal env for config loader
    os.environ.setdefault("TG_API_ID", "123456")
    os.environ.setdefault("TG_API_HASH", "hash")
    os.environ.setdefault("DB_URI", "sqlite:///:memory:")

    # Mock config object
    mock_cfg = MagicMock()
    mock_cfg.channels = []
    mock_cfg.db_uri = "sqlite:///:memory:"
    mock_cfg.redis = {
        "host": "localhost",
        "port": 6379,
        "stream": "tgsentinel:messages",
    }
    # Place session file inside tmp
    session_path = tmp_path / "test.session"
    session_path.write_text("")
    mock_cfg.telegram_session = str(session_path)

    with patch("app.load_config", return_value=mock_cfg):
        import app as flask_app  # type: ignore

        flask_app.init_app()
        # Inject mock config directly for endpoints that consult app-config
        flask_app.app.config["TGSENTINEL_CONFIG"] = mock_cfg
        return flask_app.app, mock_cfg


@pytest.mark.parametrize(
    "endpoint",
    [
        ("/api/session/info"),
    ],
)
def test_session_info_ok(tmp_path, endpoint):
    app, _ = _make_app_with_config(tmp_path)
    # Patch Redis user_info so UI fields are populated
    with patch("app.redis_client") as mock_redis:
        mock_redis.get.return_value = json.dumps(
            {
                "username": "tester",
                "phone": "+19 555-007",
                "avatar": "/static/images/logo.png",
            }
        )
        client = app.test_client()
        resp = client.get(endpoint)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["username"] == "tester"
        assert "phone_masked" in data


def test_session_logout_clears_session_and_cache(tmp_path):
    app, cfg = _make_app_with_config(tmp_path)
    # Ensure the session file exists before logout
    session_file = Path(cfg.telegram_session)
    assert session_file.exists()

    # Mock Redis to observe key deletions
    with patch("app.redis_client") as mock_redis:
        mock_redis.delete.return_value = True
        client = app.test_client()
        resp = client.post("/api/session/logout")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["details"]["file_removed"] is True
        # Three known keys are attempted
        assert mock_redis.delete.call_count >= 1


def test_session_relogin_behaves_like_logout(tmp_path):
    app, cfg = _make_app_with_config(tmp_path)
    with patch("app.redis_client") as mock_redis:
        mock_redis.delete.return_value = True
        client = app.test_client()
        resp = client.post("/api/session/relogin")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data.get("relogin_required") is True


def test_export_alerts_with_data(tmp_path):
    app, _ = _make_app_with_config(tmp_path)
    sample_alerts = [
        {
            "chat_name": "Chan",
            "sender": "Alice",
            "excerpt": "Hello",
            "score": 0.9,
            "trigger": "Keywords",
            "sent_to": "dm",
            "created_at": "2025-01-01 10:00:00",
        }
    ]
    with patch("app._load_alerts", return_value=sample_alerts):
        client = app.test_client()
        resp = client.get("/api/export_alerts?limit=10")
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type", "").startswith("text/csv")
        body = resp.data.decode()
        assert "Channel,Sender,Excerpt,Score,Trigger,Destination,Timestamp" in body
        assert "Alice" in body


def test_export_alerts_when_empty(tmp_path):
    app, _ = _make_app_with_config(tmp_path)
    with patch("app._load_alerts", return_value=[]):
        client = app.test_client()
        resp = client.get("/api/export_alerts")
        assert resp.status_code == 200
        body = resp.data.decode()
        # Only header row
        assert "Channel,Sender,Excerpt,Score,Trigger,Destination,Timestamp" in body


def test_recent_alerts_ok(tmp_path):
    app, _ = _make_app_with_config(tmp_path)
    with patch("app._load_alerts", return_value=[{"chat_name": "A", "score": 0.1}]):
        client = app.test_client()
        resp = client.get("/api/alerts/recent?limit=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "alerts" in data
        assert isinstance(data["alerts"], list)
