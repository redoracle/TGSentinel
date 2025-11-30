"""
Integration test to verify UI config page loads environment variables.
This test uses a real browser simulation to test the JavaScript loading.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.contract]


def test_config_ui_loads_env_vars_integration():
    """
    Integration test: Verify the config page JavaScript loads env vars from API.

    This test verifies the full flow:
    1. /api/config/current endpoint returns env vars
    2. JavaScript fetches the config
    3. Form fields are populated with the values

    Since we can't run a real browser in tests, we verify:
    - The API endpoint returns correct data
    - The HTML template includes the JavaScript
    - The JavaScript function exists and is called on DOMContentLoaded
    """
    import sys
    from pathlib import Path

    # Add ui directory to path
    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    # Set test environment variables
    os.environ["TG_API_ID"] = "29548417"
    os.environ["TG_API_HASH"] = "test_hash_12345"
    os.environ["ALERT_MODE"] = "both"
    os.environ["ALERT_CHANNEL"] = "@test_bot"

    try:
        # Mock Redis
        with patch("redis.Redis") as mock_redis:
            mock_redis_instance = MagicMock()
            mock_redis_instance.ping.return_value = True
            mock_redis_instance.xlen.return_value = 0
            mock_redis.return_value = mock_redis_instance

            # Mock Sentinel API calls
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

                # Mock config
                from tgsentinel.config import (
                    AlertsCfg,
                    AppCfg,
                    DigestCfg,
                    RedisCfg,
                    SystemCfg,
                )

                mock_config = AppCfg(
                    telegram_session="/tmp/test.session",
                    api_id=12345,
                    api_hash="test",
                    alerts=AlertsCfg(
                        mode="both",
                        target_channel="@test",
                        digest=DigestCfg(hourly=True, daily=True, top_n=10),
                    ),
                    channels=[],
                    monitored_users=[],
                    interests=[],
                    system=SystemCfg(
                        redis=RedisCfg(host="redis", port=6379, stream="test"),
                        database_uri="sqlite:///test.db",
                    ),
                    embeddings_model="all-MiniLM-L6-v2",
                    similarity_threshold=0.42,
                )

                with patch("ui.app.load_config", return_value=mock_config):
                    import ui.app as flask_app

                    # Reset and initialize
                    flask_app.reset_for_testing()
                    flask_app.app.config["TESTING"] = True
                    flask_app.app.config["TGSENTINEL_CONFIG"] = mock_config

                    # Initialize app to register blueprints
                    flask_app.init_app()

                    with flask_app.app.test_client() as client:
                        # Test 1: API endpoint returns env vars
                        api_response = client.get("/api/config/current")
                        assert api_response.status_code == 200

                        api_data = api_response.get_json()
                        assert api_data["telegram"]["api_id"] == "29548417"
                        assert api_data["telegram"]["api_hash"] == "test_hash_12345"
                        # Phone number comes from authenticated session, not env vars
                        assert api_data["alerts"]["mode"] == "both"
                        assert api_data["alerts"]["target_channel"] == "@test_bot"

                        # Test 2: Config page includes the JavaScript loader
                        page_response = client.get("/config")
                        assert page_response.status_code == 200

                        html = page_response.data.decode("utf-8")

                        # Verify the form fields exist
                        assert 'id="api-id"' in html
                        assert 'id="api-hash"' in html
                        assert (
                            'id="phone-number"' in html
                        )  # Field exists but populated from session
                        assert 'id="alert-mode"' in html
                        assert 'id="alert-channel"' in html

                        # Verify the JavaScript function exists
                        assert "loadCurrentConfig" in html
                        assert "api/config/current" in html
                        assert "DOMContentLoaded" in html

                        # Verify the JavaScript fetches and populates fields
                        assert 'document.getElementById("api-id")' in html
                        assert 'document.getElementById("api-hash")' in html
                        # Phone field populated from session, not from config.telegram.phone_number
                        assert "apiIdInput.value = config.telegram.api_id" in html
                        assert "apiHashInput.value = config.telegram.api_hash" in html

    finally:
        # Cleanup environment variables
        for key in [
            "TG_API_ID",
            "TG_API_HASH",
            "ALERT_MODE",
            "ALERT_CHANNEL",
        ]:
            if key in os.environ:
                del os.environ[key]


if __name__ == "__main__":
    test_config_ui_loads_env_vars_integration()
    print("✅ Integration test passed!")
