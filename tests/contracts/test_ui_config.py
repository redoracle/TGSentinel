"""Tests for UI configuration endpoints."""

import os
from unittest.mock import MagicMock, patch

import pytest

from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg, RedisCfg, SystemCfg

pytestmark = [pytest.mark.integration, pytest.mark.contract]


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
    # Import here to avoid circular dependencies
    import sys
    from pathlib import Path

    # Add ui directory to path
    ui_path = Path(__file__).parent.parent / "ui"
    if str(ui_path) not in sys.path:
        sys.path.insert(0, str(ui_path))

    # Set test environment
    os.environ["UI_SECRET_KEY"] = "test-secret"
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"

    # Remove cached modules
    for mod in list(sys.modules.keys()):
        if mod.startswith(("app", "ui.")):
            del sys.modules[mod]

    # Mock Redis before importing app
    with patch("redis.Redis") as mock_redis:
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis_instance.xlen.return_value = 0
        mock_redis.return_value = mock_redis_instance

        # Mock Sentinel API calls to return a proper response
        with patch("requests.get") as mock_requests_get:
            # Create a mock response that dynamically reads env vars at call time
            def get_dynamic_sentinel_response(*args, **kwargs):
                mock_response = MagicMock()
                mock_response.ok = True
                mock_response.json.return_value = {
                    "status": "ok",
                    "data": {
                        "telegram": {
                            "api_id": os.getenv("TG_API_ID", ""),
                            "api_hash": os.getenv("TG_API_HASH", ""),
                            "phone_number": os.getenv("TG_PHONE", ""),
                            "session": "/tmp/test.session",
                        },
                        "alerts": {
                            "mode": os.getenv("ALERT_MODE", "dm"),
                            "target_channel": os.getenv("ALERT_CHANNEL", ""),
                        },
                        "digest": {"hourly": True, "daily": False, "top_n": 10},
                        "redis": {
                            "host": os.getenv("REDIS_HOST", "redis"),
                            "port": int(os.getenv("REDIS_PORT", "6379")),
                        },
                        # These fields are at top level in Sentinel response
                        "embeddings_model": os.getenv("EMBEDDINGS_MODEL", ""),
                        "similarity_threshold": float(
                            os.getenv("SIMILARITY_THRESHOLD", "0.42")
                        ),
                        "database_uri": os.getenv("DB_URI", ""),
                        "channels": [],
                        "monitored_users": [],
                    },
                }
                return mock_response

            mock_requests_get.side_effect = get_dynamic_sentinel_response

            # Mock config loading
            with patch("ui.app.load_config", return_value=mock_config):
                # Import app after mocking
                import ui.app as flask_app

                # Reset and initialize
                flask_app.reset_for_testing()
                flask_app.app.config["TESTING"] = True
                flask_app.app.config["TGSENTINEL_CONFIG"] = mock_config

                # Initialize app to register all blueprints
                flask_app.init_app()

                with flask_app.app.test_client() as client:
                    yield client


def test_api_config_current_endpoint_exists(app_client):
    """Test that /api/config/current endpoint exists and returns 200."""
    response = app_client.get("/api/config/current")
    assert response.status_code == 200
    assert response.content_type == "application/json"


def test_api_config_current_returns_telegram_config(app_client):
    """Test that endpoint returns Telegram configuration from environment."""
    # Set environment variables
    os.environ["TG_API_ID"] = "29548417"
    os.environ["TG_API_HASH"] = "test_api_hash"
    os.environ["TG_PHONE"] = "+1234567890"

    response = app_client.get("/api/config/current")
    assert response.status_code == 200

    data = response.get_json()
    assert "telegram" in data
    assert data["telegram"]["api_id"] == "29548417"
    assert data["telegram"]["api_hash"] == "test_api_hash"
    # Phone is masked for security: +1234567890 -> +1*******90
    assert data["telegram"]["phone_number"] == "+1*******90"
    assert "session" in data["telegram"]

    # Cleanup
    del os.environ["TG_API_ID"]
    del os.environ["TG_API_HASH"]
    del os.environ["TG_PHONE"]


def test_api_config_current_returns_alerts_config(app_client):
    """Test that endpoint returns alerts configuration."""
    os.environ["ALERT_MODE"] = "both"
    os.environ["ALERT_CHANNEL"] = "@kit_red_bot"

    response = app_client.get("/api/config/current")
    assert response.status_code == 200

    data = response.get_json()
    assert "alerts" in data
    assert data["alerts"]["mode"] == "both"
    assert data["alerts"]["target_channel"] == "@kit_red_bot"

    # Cleanup
    del os.environ["ALERT_MODE"]
    del os.environ["ALERT_CHANNEL"]


def test_api_config_current_returns_digest_config(app_client):
    """Test that endpoint returns digest configuration."""
    response = app_client.get("/api/config/current")
    assert response.status_code == 200

    data = response.get_json()
    assert "digest" in data
    assert "hourly" in data["digest"]
    assert "daily" in data["digest"]
    assert "top_n" in data["digest"]
    assert isinstance(data["digest"]["hourly"], bool)
    assert isinstance(data["digest"]["daily"], bool)
    assert isinstance(data["digest"]["top_n"], int)

    # Test defaults: hourly should be True by default
    assert data["digest"]["hourly"] is True
    assert data["digest"]["top_n"] == 10


def test_api_config_current_returns_redis_config(app_client):
    """Test that endpoint returns Redis configuration."""
    os.environ["REDIS_HOST"] = "test-redis"
    os.environ["REDIS_PORT"] = "6380"

    response = app_client.get("/api/config/current")
    assert response.status_code == 200

    data = response.get_json()
    assert "redis" in data
    assert data["redis"]["host"] == "test-redis"
    assert data["redis"]["port"] == 6380

    # Cleanup
    del os.environ["REDIS_HOST"]
    del os.environ["REDIS_PORT"]


def test_api_config_current_returns_semantic_config(app_client):
    """Test that endpoint returns semantic configuration."""
    os.environ["EMBEDDINGS_MODEL"] = "test-model"
    os.environ["SIMILARITY_THRESHOLD"] = "0.75"

    response = app_client.get("/api/config/current")
    assert response.status_code == 200

    data = response.get_json()
    assert "semantic" in data
    assert data["semantic"]["embeddings_model"] == "test-model"
    assert data["semantic"]["similarity_threshold"] == 0.75

    # Cleanup
    del os.environ["EMBEDDINGS_MODEL"]
    del os.environ["SIMILARITY_THRESHOLD"]


def test_api_config_current_returns_database_uri(app_client):
    """Test that endpoint returns database URI."""
    os.environ["DB_URI"] = "sqlite:////test/path.db"

    response = app_client.get("/api/config/current")
    assert response.status_code == 200

    data = response.get_json()
    assert "database_uri" in data
    assert data["database_uri"] == "sqlite:////test/path.db"

    # Cleanup
    del os.environ["DB_URI"]


def test_api_config_current_handles_missing_env_vars(app_client):
    """Test that endpoint handles missing environment variables gracefully."""
    # Clear all relevant env vars
    env_vars = [
        "TG_API_ID",
        "TG_API_HASH",
        "TG_PHONE",
        "ALERT_MODE",
        "ALERT_CHANNEL",
        "REDIS_HOST",
        "REDIS_PORT",
        "EMBEDDINGS_MODEL",
        "SIMILARITY_THRESHOLD",
        "DB_URI",
    ]
    saved_values = {}
    for var in env_vars:
        if var in os.environ:
            saved_values[var] = os.environ[var]
            del os.environ[var]

    response = app_client.get("/api/config/current")
    assert response.status_code == 200

    data = response.get_json()
    # Should return empty strings/defaults instead of failing
    assert "telegram" in data
    assert "alerts" in data
    assert "redis" in data
    assert "semantic" in data

    # Restore env vars
    for var, value in saved_values.items():
        os.environ[var] = value


def test_config_page_renders(app_client):
    """Test that the config page renders successfully."""
    response = app_client.get("/config")
    assert response.status_code == 200
    assert b"Telegram Account" in response.data
    assert b"api-id" in response.data
    assert b"api-hash" in response.data
    assert b"phone-number" in response.data
