"""
Integration test to verify UI config page loads environment variables.
This test uses a real browser simulation to test the JavaScript loading.
"""

import os
import time
from unittest.mock import MagicMock, patch

import pytest


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
    os.environ["TG_PHONE"] = "+1234567890"
    os.environ["ALERT_MODE"] = "both"
    os.environ["ALERT_CHANNEL"] = "@test_bot"

    try:
        # Mock Redis
        with patch("redis.Redis") as mock_redis:
            mock_redis_instance = MagicMock()
            mock_redis_instance.ping.return_value = True
            mock_redis_instance.xlen.return_value = 0
            mock_redis.return_value = mock_redis_instance

            # Mock config
            from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg

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
                interests=[],
                redis={"host": "redis", "port": 6379, "stream": "test"},
                db_uri="sqlite:///test.db",
                embeddings_model="all-MiniLM-L6-v2",
                similarity_threshold=0.42,
            )

            with patch("app.load_config", return_value=mock_config):
                import app as flask_app  # type: ignore[import-not-found]

                flask_app.app.config["TESTING"] = True
                flask_app.config = mock_config
                flask_app.redis_client = mock_redis_instance

                with flask_app.app.test_client() as client:
                    # Test 1: API endpoint returns env vars
                    api_response = client.get("/api/config/current")
                    assert api_response.status_code == 200

                    api_data = api_response.get_json()
                    assert api_data["telegram"]["api_id"] == "29548417"
                    assert api_data["telegram"]["api_hash"] == "test_hash_12345"
                    assert api_data["telegram"]["phone_number"] == "+1234567890"
                    assert api_data["alerts"]["mode"] == "both"
                    assert api_data["alerts"]["target_channel"] == "@test_bot"

                    # Test 2: Config page includes the JavaScript loader
                    page_response = client.get("/config")
                    assert page_response.status_code == 200

                    html = page_response.data.decode("utf-8")

                    # Verify the form fields exist
                    assert 'id="api-id"' in html
                    assert 'id="api-hash"' in html
                    assert 'id="phone-number"' in html
                    assert 'id="alert-mode"' in html
                    assert 'id="alert-channel"' in html

                    # Verify the JavaScript function exists
                    assert "loadCurrentConfig" in html
                    assert "api/config/current" in html
                    assert "DOMContentLoaded" in html

                    # Verify the JavaScript fetches and populates fields
                    assert 'document.getElementById("api-id")' in html
                    assert 'document.getElementById("api-hash")' in html
                    assert 'document.getElementById("phone-number")' in html
                    assert "apiIdInput.value = config.telegram.api_id" in html
                    assert "apiHashInput.value = config.telegram.api_hash" in html
                    assert "phoneInput.value = config.telegram.phone_number" in html

    finally:
        # Cleanup environment variables
        for key in [
            "TG_API_ID",
            "TG_API_HASH",
            "TG_PHONE",
            "ALERT_MODE",
            "ALERT_CHANNEL",
        ]:
            if key in os.environ:
                del os.environ[key]


if __name__ == "__main__":
    test_config_ui_loads_env_vars_integration()
    print("✅ Integration test passed!")
