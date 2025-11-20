"""Tests for login/logout endpoints: start, verify, relogin, logout."""

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


@contextmanager
def mock_redis_client(mock_client):
    """Context manager to temporarily replace redis_client and deps.redis_client."""
    # Import both ui.app (actual implementation) and ui.core (deps)
    import ui.app
    from ui.core.dependencies import Dependencies

    original_redis = ui.app.redis_client
    deps = Dependencies.get_instance()
    original_deps_redis = deps.redis_client

    # Replace both references
    ui.app.redis_client = mock_client
    deps.redis_client = mock_client

    try:
        yield
    finally:
        ui.app.redis_client = original_redis
        deps.redis_client = original_deps_redis


def _make_app():
    """Create a test Flask app with proper configuration."""
    # Set test environment
    os.environ["UI_SECRET_KEY"] = "test-secret"
    os.environ["TG_API_ID"] = "123456"
    os.environ["TG_API_HASH"] = "hash"
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"

    # Ensure ui module path is available
    ui_path = Path(__file__).parent.parent / "ui"
    if str(ui_path) not in sys.path:
        sys.path.insert(0, str(ui_path))

    # Remove cached modules to force fresh import
    for mod in list(sys.modules.keys()):
        if mod.startswith(("app", "ui.")):
            del sys.modules[mod]

    # Mock config to return test values
    from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg, RedisCfg, SystemCfg

    cfg = AppCfg(
        telegram_session="/tmp/test.session",
        api_id=123456,
        api_hash="hash",
        alerts=AlertsCfg(
            mode="both",
            target_channel="@test_bot",
            digest=DigestCfg(hourly=True, daily=True, top_n=10),
        ),
        channels=[],
        monitored_users=[],
        interests=[],
        system=SystemCfg(
            redis=RedisCfg(
                host="localhost",
                port=6379,
                stream="tgsentinel:messages",
            ),
            database_uri="sqlite:///:memory:",
        ),
        embeddings_model="all-MiniLM-L6-v2",
        similarity_threshold=0.42,
    )

    with patch("redis.Redis") as mock_redis:
        mock_redis_instance = MagicMock()
        mock_redis_instance.ping.return_value = True
        mock_redis.return_value = mock_redis_instance

        with patch("ui.app.load_config", return_value=cfg):
            import ui.app as flask_app

            # Reset module state for test isolation
            flask_app.reset_for_testing()

            flask_app.app.config["TESTING"] = True
            flask_app.app.config["TGSENTINEL_CONFIG"] = cfg

            # Initialize app to register blueprints
            flask_app.init_app()

            # Return the app for testing
            return flask_app.app


def _mock_redis_for_auth(response_data, context_data=None):
    """Helper to create a properly configured Redis mock for auth endpoints."""
    mock_redis = MagicMock()
    mock_redis.rpush = MagicMock(return_value=1)
    mock_redis.hdel = MagicMock(return_value=1)
    mock_redis.setex = MagicMock(return_value=True)
    mock_redis.delete = MagicMock(return_value=1)

    # Mock hget for auth responses - return bytes (what Redis client returns)
    # For test simplicity, always return the response_data for any hget call
    if response_data:
        mock_redis.hget = MagicMock(return_value=json.dumps(response_data).encode())
    else:
        mock_redis.hget = MagicMock(return_value=None)

    # Mock get for login context
    def get_side_effect(key):
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone:" in str(key) and context_data:
            return json.dumps(context_data).encode()
        return None

    mock_redis.get = MagicMock(side_effect=get_side_effect)
    return mock_redis


@pytest.mark.parametrize("missing_field", ["phone", None])
def test_login_start_requires_phone(missing_field):
    app = _make_app()
    client = app.test_client()
    payload = {"phone": "+15550100"}
    if missing_field:
        payload.pop("phone")

    # Setup Redis mock to return proper auth response
    mock_redis_instance = _mock_redis_for_auth(
        {"status": "ok", "phone_code_hash": "abc"}
    )

    with mock_redis_client(mock_redis_instance):
        resp = client.post("/api/session/login/start", json=payload)
        data = resp.get_json()
        assert resp.status_code == (400 if missing_field else 200)
        if missing_field:
            # Should not attempt Redis operations when field is missing
            mock_redis_instance.rpush.assert_not_called()
        else:
            # Should successfully submit auth request when phone is present
            mock_redis_instance.rpush.assert_called_once()
            assert data["status"] == "ok"


def test_login_start_sends_code_and_stores_context():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth(
        {"status": "ok", "phone_code_hash": "abc123", "timeout": 30}
    )

    with mock_redis_client(mock_redis):
        resp = client.post("/api/session/login/start", json={"phone": "+41 2600 0000"})
        assert resp.status_code == 200
        assert mock_redis.rpush.called
        assert mock_redis.setex.called
        key = mock_redis.setex.call_args[0][0]
        assert "tgsentinel:login:phone:" in key
        stored = json.loads(mock_redis.setex.call_args[0][2])
        assert stored["phone_code_hash"] == "abc123"


def test_login_verify_410_when_context_missing():
    app = _make_app()
    client = app.test_client()

    mock_r = MagicMock()
    mock_r.get.return_value = None

    with (
        mock_redis_client(mock_r),
        patch("ui.app._submit_auth_request") as mock_submit,
    ):
        resp = client.post(
            "/api/session/login/verify", json={"phone": "+15550100", "code": "12345"}
        )
        assert resp.status_code == 410
        data = resp.get_json()
        assert "expired" in data["message"].lower()
        mock_submit.assert_not_called()


def test_login_verify_success_sets_session_and_clears_context():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth(
        {"status": "ok", "message": "Authenticated"}, {"phone_code_hash": "abc123"}
    )

    # Patch in blueprint module where function is used after injection
    with (
        mock_redis_client(mock_redis),
        patch("ui.routes.session._wait_for_worker_authorization", return_value=True),
    ):
        resp = client.post(
            "/api/session/login/verify",
            json={"phone": "+41 2600 0000", "code": "12345"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert mock_redis.rpush.called
        assert mock_redis.delete.called
        with client.session_transaction() as sess:
            assert sess.get("telegram_authenticated") is True


def test_relogin_and_logout_ok():
    app = _make_app()
    client = app.test_client()

    # Patch in blueprint module where function is used after injection
    with patch(
        "ui.routes.session._invalidate_session", return_value={"file_removed": True}
    ) as inv:
        r1 = client.post("/api/session/relogin")
        assert r1.status_code == 200
        r2 = client.post("/api/session/logout")
        assert r2.status_code == 200
        assert inv.call_count == 2


def test_login_resend_requires_existing_context():
    app = _make_app()
    client = app.test_client()

    mock_r = MagicMock()
    mock_r.get.return_value = None

    with mock_redis_client(mock_r):
        resp = client.post("/api/session/login/resend", json={"phone": "+15550100"})
        assert resp.status_code == 410


def test_login_resend_updates_context_via_sentinel():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth(
        {"status": "ok", "phone_code_hash": "newhash"}, {"phone_code_hash": "oldhash"}
    )

    with mock_redis_client(mock_redis):
        resp = client.post("/api/session/login/resend", json={"phone": "+41 2600 0000"})
        assert resp.status_code == 200
        assert mock_redis.rpush.called
        assert mock_redis.setex.called
        stored = json.loads(mock_redis.setex.call_args[0][2])
        assert stored["phone_code_hash"] == "newhash"


def test_login_start_handles_sentinel_error():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth({"status": "error", "message": "failure"})

    with mock_redis_client(mock_redis):
        resp = client.post("/api/session/login/start", json={"phone": "+41 2600 0000"})
        assert resp.status_code == 502
        data = resp.get_json()
        assert data["status"] == "error"


def test_login_start_handles_timeout():
    app = _make_app()
    client = app.test_client()

    mock_redis = MagicMock()
    mock_redis.rpush = MagicMock(return_value=1)
    mock_redis.hget = MagicMock(return_value=None)  # Timeout - no response
    mock_redis.hdel = MagicMock(return_value=1)
    mock_redis.setex = MagicMock(return_value=True)

    with (
        mock_redis_client(mock_redis),
        patch("ui.app.AUTH_REQUEST_TIMEOUT_SECS", 0.1),  # Short timeout for testing
    ):
        resp = client.post("/api/session/login/start", json={"phone": "+41 2600 0000"})
        assert resp.status_code == 503


def test_login_resend_handles_timeout():
    app = _make_app()
    client = app.test_client()

    mock_redis = MagicMock()
    mock_redis.rpush = MagicMock(return_value=1)
    mock_redis.hget = MagicMock(return_value=None)  # Timeout - no response
    mock_redis.hdel = MagicMock(return_value=1)
    mock_redis.setex = MagicMock(return_value=True)

    def get_side_effect(key):
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone" in str(key):
            return json.dumps({"phone_code_hash": "oldhash"}).encode()
        return None

    mock_redis.get = MagicMock(side_effect=get_side_effect)

    with (
        mock_redis_client(mock_redis),
        patch("ui.app.AUTH_REQUEST_TIMEOUT_SECS", 0.1),  # Short timeout for testing
    ):
        resp = client.post("/api/session/login/resend", json={"phone": "+41 2600 0000"})
        assert resp.status_code == 503


def test_login_verify_propagates_sentinel_error():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth(
        {"status": "error", "message": "bad code"}, {"phone_code_hash": "abc"}
    )

    with (
        mock_redis_client(mock_redis),
        patch("ui.app._wait_for_worker_authorization", return_value=True),
    ):
        resp = client.post(
            "/api/session/login/verify",
            json={"phone": "+41 2600 0000", "code": "99999"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "bad code" in data["message"]
