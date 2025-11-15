"""Tests for login/logout endpoints: start, verify, relogin, logout."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


def _make_app():
    os.environ.setdefault("UI_SECRET_KEY", "test-secret")
    os.environ.setdefault("TG_API_ID", "123456")
    os.environ.setdefault("TG_API_HASH", "hash")
    os.environ.setdefault("DB_URI", "sqlite:///:memory:")

    # Mock config
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


@pytest.mark.parametrize("missing_field", ["phone", None])
def test_login_start_requires_phone(missing_field):
    app = _make_app()
    client = app.test_client()
    payload = {"phone": "+15550100"}
    if missing_field:
        payload.pop("phone")
    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request") as mock_submit,
    ):
        mock_r.setex = MagicMock(return_value=True)
        mock_submit.return_value = {"status": "ok", "phone_code_hash": "abc"}
        resp = client.post("/api/session/login/start", json=payload)
        assert resp.status_code == (400 if missing_field else 200)
        if missing_field:
            mock_submit.assert_not_called()
        else:
            mock_submit.assert_called_once_with("start", {"phone": "+15550100"})


def test_login_start_sends_code_and_stores_context():
    app = _make_app()
    client = app.test_client()

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request") as mock_submit,
    ):
        mock_r.setex = MagicMock(return_value=True)
        mock_submit.return_value = {
            "status": "ok",
            "phone_code_hash": "abc123",
            "timeout": 30,
        }

        resp = client.post("/api/session/login/start", json={"phone": "+1 555-0100"})
        assert resp.status_code == 200
        mock_submit.assert_called_once_with("start", {"phone": "+15550100"})

        assert mock_r.setex.called
        key = mock_r.setex.call_args[0][0]
        assert "tgsentinel:login:phone:" in key
        stored = json.loads(mock_r.setex.call_args[0][2])
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

    def get_side_effect(key):  # noqa: ANN001
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone:" in str(key):
            return json.dumps({"phone_code_hash": "abc123"})
        return None

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request") as mock_submit,
        patch("ui.app._wait_for_worker_authorization", return_value=True),
    ):
        mock_r.get.side_effect = get_side_effect
        mock_r.setex = MagicMock(return_value=True)
        mock_r.delete = MagicMock(return_value=1)
        mock_submit.return_value = {"status": "ok", "message": "Authenticated"}

        resp = client.post(
            "/api/session/login/verify", json={"phone": "+1 555 0100", "code": "12345"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        mock_submit.assert_called_once()
        mock_r.delete.assert_called_once()
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

    ctx_payload = {"phone_code_hash": "oldhash"}

    def get_side_effect(key):  # noqa: ANN001
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone" in str(key):
            return json.dumps(ctx_payload)
        return None

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request") as mock_submit,
    ):
        mock_r.get.side_effect = get_side_effect
        mock_r.setex = MagicMock(return_value=True)
        mock_submit.return_value = {"status": "ok", "phone_code_hash": "newhash"}

        resp = client.post("/api/session/login/resend", json={"phone": "+1 555 0100"})
        assert resp.status_code == 200
        mock_submit.assert_called_once_with("resend", {"phone": "+15550100"})
        assert mock_r.setex.called
        stored = json.loads(mock_r.setex.call_args[0][2])
        assert stored["phone_code_hash"] == "newhash"


def test_login_start_handles_sentinel_error():
    app = _make_app()
    client = app.test_client()

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request") as mock_submit,
    ):
        mock_r.setex = MagicMock(return_value=True)
        mock_submit.return_value = {"status": "error", "message": "failure"}
        resp = client.post("/api/session/login/start", json={"phone": "+1 555 0100"})
        assert resp.status_code == 502
        data = resp.get_json()
        assert data["status"] == "error"


def test_login_start_handles_timeout():
    app = _make_app()
    client = app.test_client()

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request", side_effect=TimeoutError),
    ):
        mock_r.setex = MagicMock(return_value=True)
        resp = client.post("/api/session/login/start", json={"phone": "+1 555 0100"})
        assert resp.status_code == 503


def test_login_resend_handles_timeout():
    app = _make_app()
    client = app.test_client()

    ctx_payload = {"phone_code_hash": "oldhash"}

    def get_side_effect(key):  # noqa: ANN001
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone" in str(key):
            return json.dumps(ctx_payload)
        return None

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request", side_effect=TimeoutError),
    ):
        mock_r.get.side_effect = get_side_effect
        resp = client.post("/api/session/login/resend", json={"phone": "+1 555 0100"})
        assert resp.status_code == 503


def test_login_verify_propagates_sentinel_error():
    app = _make_app()
    client = app.test_client()

    with (
        patch("ui.app.redis_client") as mock_r,
        patch("ui.app._submit_auth_request") as mock_submit,
        patch("ui.app._wait_for_worker_authorization", return_value=True),
    ):
        mock_r.get.return_value = json.dumps({"phone_code_hash": "abc"})
        mock_submit.return_value = {"status": "error", "message": "bad code"}
        resp = client.post(
            "/api/session/login/verify", json={"phone": "+1 555 0100", "code": "99999"}
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "bad code" in data["message"]
