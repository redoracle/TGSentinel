"""Tests for login/logout endpoints: start, verify, relogin, logout."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


def _make_app():
    os.environ["UI_SECRET_KEY"] = "test-secret"
    os.environ["TG_API_ID"] = "123456"
    os.environ["TG_API_HASH"] = "hash"
    os.environ["DB_URI"] = "sqlite:///:memory:"

    # Clear any cached module
    import sys

    if "app" in sys.modules:
        del sys.modules["app"]

    # Mock config to return test values
    with patch("ui.app.load_config") as mock_load:
        cfg = MagicMock()
        cfg.channels = []
        cfg.db_uri = "sqlite:///:memory:"
        cfg.redis = {"host": "localhost", "port": 6379, "stream": "tgsentinel:messages"}
        cfg.api_id = 123456
        cfg.api_hash = "hash"
        mock_load.return_value = cfg

        import app as flask_app  # type: ignore

        flask_app.init_app()
        return flask_app.app


def _mock_redis_for_auth(response_data, context_data=None):
    """Helper to create a properly configured Redis mock for auth endpoints."""
    mock_redis = MagicMock()
    mock_redis.rpush = MagicMock(return_value=1)
    mock_redis.hdel = MagicMock(return_value=1)
    mock_redis.setex = MagicMock(return_value=True)
    mock_redis.delete = MagicMock(return_value=1)

    # Mock hget for auth responses
    mock_redis.hget = MagicMock(
        return_value=json.dumps(response_data) if response_data else None
    )

    # Mock get for login context
    def get_side_effect(key):
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone:" in str(key) and context_data:
            return json.dumps(context_data)
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
    mock_redis_instance = MagicMock()
    mock_redis_instance.rpush = MagicMock(return_value=1)
    mock_redis_instance.hget = MagicMock(
        return_value=json.dumps({"status": "ok", "phone_code_hash": "abc"})
    )
    mock_redis_instance.hdel = MagicMock(return_value=1)

    with patch("ui.app.redis_client", mock_redis_instance):
        resp = client.post("/api/session/login/start", json=payload)
        assert resp.status_code == (400 if missing_field else 200)
        if missing_field:
            # Should not attempt Redis operations when field is missing
            mock_redis_instance.rpush.assert_not_called()
        else:
            # Should successfully submit auth request when phone is present
            mock_redis_instance.rpush.assert_called_once()
            assert resp.json["status"] == "ok"


def test_login_start_sends_code_and_stores_context():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth(
        {"status": "ok", "phone_code_hash": "abc123", "timeout": 30}
    )

    with patch("ui.app.redis_client", mock_redis):
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

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request") as mock_submit,
    ):
        mock_r.get.return_value = None
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

    with (
        patch("ui.app.redis_client", mock_redis),
        patch("ui.app._wait_for_worker_authorization", return_value=True),
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

    with patch(
        "ui.app._invalidate_session", return_value={"file_removed": True}
    ) as inv:
        r1 = client.post("/api/session/relogin")
        assert r1.status_code == 200
        r2 = client.post("/api/session/logout")
        assert r2.status_code == 200
        assert inv.call_count == 2


def test_login_resend_requires_existing_context():
    app = _make_app()
    client = app.test_client()

    with patch("ui.app.redis_client") as mock_r:
        mock_r.get.return_value = None
        resp = client.post("/api/session/login/resend", json={"phone": "+15550100"})
        assert resp.status_code == 410


def test_login_resend_updates_context_via_sentinel():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth(
        {"status": "ok", "phone_code_hash": "newhash"}, {"phone_code_hash": "oldhash"}
    )

    with patch("ui.app.redis_client", mock_redis):
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

    with patch("ui.app.redis_client", mock_redis):
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

    with patch("ui.app.redis_client", mock_redis):
        resp = client.post("/api/session/login/start", json={"phone": "+41 2600 0000"})
        assert resp.status_code == 503


def test_login_resend_handles_timeout():
    app = _make_app()
    client = app.test_client()

    mock_redis = MagicMock()
    mock_redis.rpush = MagicMock(return_value=1)
    mock_redis.hget = MagicMock(return_value=None)  # Timeout - no response
    mock_redis.hdel = MagicMock(return_value=1)

    def get_side_effect(key):
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone" in str(key):
            return json.dumps({"phone_code_hash": "oldhash"})
        return None

    mock_redis.get = MagicMock(side_effect=get_side_effect)

    with patch("ui.app.redis_client", mock_redis):
        resp = client.post("/api/session/login/resend", json={"phone": "+41 2600 0000"})
        assert resp.status_code == 503


def test_login_verify_propagates_sentinel_error():
    app = _make_app()
    client = app.test_client()

    mock_redis = _mock_redis_for_auth(
        {"status": "error", "message": "bad code"}, {"phone_code_hash": "abc"}
    )

    with (
        patch("ui.app.redis_client", mock_redis),
        patch("ui.app._wait_for_worker_authorization", return_value=True),
    ):
        resp = client.post(
            "/api/session/login/verify",
            json={"phone": "+41 2600 0000", "code": "99999"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "bad code" in data["message"]
