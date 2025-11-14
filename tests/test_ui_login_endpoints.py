"""Tests for login/logout endpoints: start, verify, relogin, logout."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_app():
    os.environ.setdefault("UI_SECRET_KEY", "test-secret")
    os.environ.setdefault("TG_API_ID", "123456")
    os.environ.setdefault("TG_API_HASH", "hash")
    os.environ.setdefault("DB_URI", "sqlite:///:memory:")

    # Mock config
    with patch("app.load_config") as mock_load:
        cfg = MagicMock()
        cfg.channels = []
        cfg.db_uri = "sqlite:///:memory:"
        cfg.redis = {"host": "localhost", "port": 6379, "stream": "tgsentinel:messages"}
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
    resp = client.post("/api/session/login/start", json=payload)
    assert resp.status_code == (400 if missing_field else 200)


def test_login_start_sends_code_and_stores_context():
    app = _make_app()
    client = app.test_client()

    # Patch Redis and TelegramClient
    with patch("app.redis_client") as mock_r, patch("app.TelegramClient") as mock_tg:
        mock_r.setex = MagicMock(return_value=True)

        inst = MagicMock()
        async def _connect():
            return None
        async def _disconnect():
            return None
        async def _send_code(phone):  # noqa: ARG001
            resp = MagicMock()
            resp.phone_code_hash = "abc123"
            return resp
        inst.connect = _connect
        inst.disconnect = _disconnect
        inst.send_code_request = _send_code
        mock_tg.return_value = inst

        resp = client.post("/api/session/login/start", json={"phone": "+1 555-0100"})
        assert resp.status_code == 200
        # Ensure context stored in Redis with normalized key
        assert mock_r.setex.called
        key = mock_r.setex.call_args[0][0]
        assert "tgsentinel:login:phone:" in key


def test_login_verify_410_when_context_missing():
    app = _make_app()
    client = app.test_client()

    with patch("app.redis_client") as mock_r:
        mock_r.get.return_value = None
        resp = client.post("/api/session/login/verify", json={"phone": "+15550100", "code": "12345"})
        assert resp.status_code == 410
        data = resp.get_json()
        assert "expired" in data["message"].lower()


def test_login_verify_success_updates_user_info_and_touches_reload_marker(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Redis get must return stored phone_code_hash
    def get_side_effect(key):  # noqa: ANN001
        if isinstance(key, bytes):
            key = key.decode()
        if "tgsentinel:login:phone:" in str(key):
            return json.dumps({"phone_code_hash": "abc123", "session_path": str(tmp_path / "sess.session")})
        return None

    with patch("app.redis_client") as mock_r, patch("app.TelegramClient") as mock_tg, patch("app.Path") as mock_path:
        mock_r.get.side_effect = get_side_effect
        mock_r.setex = MagicMock(return_value=True)

        # Mock Path touch for reload marker
        path_instance = MagicMock()
        mock_path.return_value = path_instance

        # Telegram client behavior
        inst = MagicMock()

        async def _connect():
            return None

        async def _disconnect():
            return None

        async def _sign_in(**kwargs):  # noqa: ARG001
            return None

        async def _get_me():
            m = MagicMock()
            m.id = 42
            m.username = "analyst"
            m.phone = "+15550100"
            m.first_name = "Ana"
            m.last_name = "Lyst"
            return m

        async def _get_profile_photos(*args, **kwargs):  # noqa: ARG001, ANN001
            return [object()]

        async def _download_photo(*args, **kwargs):  # noqa: ARG001, ANN001
            return None

        inst.connect = _connect
        inst.disconnect = _disconnect
        inst.sign_in = _sign_in
        inst.get_me = _get_me
        inst.get_profile_photos = _get_profile_photos
        inst.download_profile_photo = _download_photo
        mock_tg.return_value = inst

        resp = client.post("/api/session/login/verify", json={"phone": "+1 555 0100", "code": "12345"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        # Verify user_info was set
        assert mock_r.setex.called
        set_key = mock_r.setex.call_args[0][0]
        assert set_key == "tgsentinel:user_info"
        # Verify reload marker touch attempted
        assert mock_path.called
        assert path_instance.touch.called


def test_relogin_and_logout_ok():
    app = _make_app()
    client = app.test_client()

    with patch("app._invalidate_session", return_value={"file_removed": True}) as inv:
        r1 = client.post("/api/session/relogin")
        assert r1.status_code == 200
        r2 = client.post("/api/session/logout")
        assert r2.status_code == 200
        assert inv.call_count == 2

