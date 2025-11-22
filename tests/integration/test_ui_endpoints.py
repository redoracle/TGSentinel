"""
Comprehensive tests for all UI endpoints to ensure templates work correctly.
"""

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

    # Cleanup
    if "UI_DB_URI" in os.environ:
        del os.environ["UI_DB_URI"]
    if "UI_SECRET_KEY" in os.environ:
        del os.environ["UI_SECRET_KEY"]


# Test View Endpoints
def test_dashboard_view(app_client):
    """Test dashboard page renders."""
    response = app_client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data or b"dashboard" in response.data


def test_alerts_view(app_client):
    """Test alerts page renders."""
    response = app_client.get("/alerts")
    assert response.status_code == 200
    assert b"Alerts" in response.data or b"alerts" in response.data


def test_analytics_view(app_client):
    """Test analytics page renders."""
    response = app_client.get("/analytics")
    assert response.status_code == 200
    assert b"Analytics" in response.data or b"analytics" in response.data


def test_config_view(app_client):
    """Test config page renders."""
    response = app_client.get("/config")
    assert response.status_code == 200
    assert b"Configuration" in response.data or b"Telegram Account" in response.data


def test_console_view(app_client):
    """Test console page renders."""
    response = app_client.get("/console")
    assert response.status_code == 200
    assert b"Console" in response.data or b"console" in response.data


def test_developer_view(app_client):
    """Test developer page renders."""
    response = app_client.get("/developer")
    assert response.status_code == 200
    assert b"Developer" in response.data or b"developer" in response.data


def test_profiles_view(app_client):
    """Test profiles page renders."""
    response = app_client.get("/profiles")
    assert response.status_code == 200
    assert b"Profiles" in response.data or b"Interest" in response.data


def test_docs_view(app_client):
    """Test API documentation page renders."""
    response = app_client.get("/docs")
    assert response.status_code == 200
    assert b"API Documentation" in response.data or b"API" in response.data
    # Check for key sections
    assert b"Dashboard" in response.data or b"dashboard" in response.data
    assert b"Webhooks" in response.data or b"webhooks" in response.data
    assert b"Examples" in response.data or b"examples" in response.data


# Test API Endpoints - Dashboard
def test_api_dashboard_summary(app_client):
    """Test /api/dashboard/summary endpoint."""
    response = app_client.get("/api/dashboard/summary")
    assert response.status_code == 200
    data = response.get_json()
    assert "messages_ingested" in data
    assert "alerts_sent" in data


def test_api_system_health(app_client):
    """Test /api/system/health endpoint."""
    response = app_client.get("/api/system/health")
    assert response.status_code == 200
    data = response.get_json()
    assert "redis_stream_depth" in data or "redis_online" in data


def test_api_dashboard_activity(app_client):
    """Test /api/dashboard/activity endpoint."""
    response = app_client.get("/api/dashboard/activity?limit=10")
    assert response.status_code == 200
    data = response.get_json()
    assert "entries" in data


# Test API Endpoints - Alerts
def test_api_recent_alerts(app_client):
    """Test /api/alerts/recent endpoint."""
    response = app_client.get("/api/alerts/recent?limit=20")
    assert response.status_code == 200
    data = response.get_json()
    assert "alerts" in data


def test_api_alert_digests(app_client):
    """Test /api/alerts/digests endpoint."""
    response = app_client.get("/api/alerts/digests")
    assert response.status_code == 200
    data = response.get_json()
    assert "digests" in data


# Test API Endpoints - Analytics
def test_api_analytics_metrics(app_client):
    """Test /api/analytics/metrics endpoint."""
    response = app_client.get("/api/analytics/metrics")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, dict)


def test_api_analytics_keywords(app_client):
    """Test /api/analytics/keywords endpoint."""
    response = app_client.get("/api/analytics/keywords")
    assert response.status_code == 200
    data = response.get_json()
    assert "keywords" in data


# Test API Endpoints - Config
def test_api_config_channels(app_client):
    """Test /api/config/channels endpoint."""
    response = app_client.get("/api/config/channels")
    assert response.status_code == 200
    data = response.get_json()
    assert "channels" in data


def test_api_config_interests(app_client):
    """Test /api/config/interests endpoint."""
    response = app_client.get("/api/config/interests")
    assert response.status_code == 200
    data = response.get_json()
    assert "interests" in data


def test_api_config_save(app_client):
    """Test /api/config/save endpoint."""
    payload = {
        "api_id": "12345",
        "api_hash": "test",
        "mode": "dm",  # Use valid mode (dm, channel, or both)
        "interests": ["test"],
    }
    response = app_client.post(
        "/api/config/save",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    # The endpoint returns 503 when config service is not available in test env
    # or 200/400 if it is available
    assert response.status_code in [200, 400, 503]


# Test API Endpoints - Profiles
def test_api_profiles_train(app_client):
    """Test /api/profiles/train endpoint."""
    payload = {"topic": "test topic"}
    response = app_client.post(
        "/api/profiles/train",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code in [200, 400]


def test_api_profiles_test(app_client):
    """Test /api/profiles/test endpoint."""
    payload = {"sample": "test message", "interest": "test interest"}
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "score": 0.75,
            "interpretation": "high similarity",
            "model": "all-MiniLM-L6-v2",
        }
        mock_post.return_value = mock_response

        response = app_client.post(
            "/api/profiles/test",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in [200, 400, 500]


def test_api_export_profiles(app_client):
    """Test /api/profiles/export endpoint."""
    response = app_client.get("/api/profiles/export")
    assert response.status_code == 200


# Test API Endpoints - Console
def test_api_console_command(app_client):
    """Test /api/console/command endpoint."""
    payload = {"command": "/test"}
    response = app_client.post(
        "/api/console/command",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code in [200, 400]


# Test API Endpoints - Developer
def test_api_export_diagnostics(app_client):
    """Test /api/console/diagnostics endpoint."""
    response = app_client.get("/api/console/diagnostics")
    assert response.status_code == 200


# Test API Endpoints - Session
def test_api_session_info(app_client):
    """Test /api/session/info endpoint."""
    os.environ["TG_PHONE"] = "+1234567890"
    try:
        response = app_client.get("/api/session/info")
        assert response.status_code == 200
        data = response.get_json()
        assert "username" in data
        assert "phone_masked" in data
        assert "connected" in data
    finally:
        os.environ.pop("TG_PHONE", None)


# Test Content-Type validation
def test_post_endpoint_requires_json_content_type(app_client):
    """Test that POST endpoints require Content-Type: application/json."""
    # Test without Content-Type header
    response = app_client.post("/api/config/save", data='{"test": "data"}')
    assert response.status_code == 400


def test_post_endpoint_rejects_invalid_json(app_client):
    """Test that POST endpoints reject invalid JSON."""
    response = app_client.post(
        "/api/config/save",
        data="invalid json{",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


# Test error handling
def test_404_for_nonexistent_endpoint(app_client):
    """Test that nonexistent endpoints return 404."""
    response = app_client.get("/api/nonexistent/endpoint")
    assert response.status_code == 404


def test_405_for_wrong_http_method(app_client):
    """Test that wrong HTTP methods return 405."""
    response = app_client.post("/api/dashboard/summary")
    assert response.status_code == 405
