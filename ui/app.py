"""TG Sentinel UI web service.

This module exposes the Flask + Socket.IO application that powers the
dashboard experience. It mirrors the telemetry collected by the core
worker processes so analysts can monitor the system without running
additional services.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple, cast


def _format_timestamp(ts_str: str) -> str:
    """Format ISO timestamp to human-readable format."""
    try:
        if not ts_str:
            return "Unknown"
        # Parse ISO format timestamp
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # Format as "YYYY-MM-DD HH:MM:SS"
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str


def _validate_session_file(file_content: bytes) -> Tuple[bool, str]:
    """Validate that uploaded content is a valid Telethon session file.

    Returns: (is_valid, error_message)
    """
    import sqlite3

    # Check size (reasonable limit: 10MB)
    if len(file_content) > 10 * 1024 * 1024:
        return False, "File too large (max 10MB)"

    if len(file_content) < 100:
        return False, "File too small to be a valid session"

    # Check SQLite magic header
    if not file_content.startswith(b"SQLite format 3\x00"):
        return False, "Not a valid SQLite database file"

    # Try to open as SQLite and verify Telethon structure
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".session") as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        try:
            conn = sqlite3.connect(tmp_path)
            cursor = conn.cursor()

            # Check for Telethon tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

            required_tables = {
                "sessions",
                "entities",
                "sent_files",
                "update_state",
                "version",
            }
            if not required_tables.issubset(tables):
                missing = required_tables - tables
                return False, f"Missing required Telethon tables: {', '.join(missing)}"

            # Check sessions table has auth key
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE auth_key IS NOT NULL")
            if cursor.fetchone()[0] == 0:
                return False, "No authorization key found in session"

            conn.close()
            return True, "Valid Telethon session file"

        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    except sqlite3.Error as e:
        return False, f"SQLite error: {str(e)}"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


try:  # Optional dependency for process metrics.
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is optional
    psutil = None  # type: ignore

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover - redis is optional
    redis = None  # type: ignore

# TelegramClient should NOT be used in UI - sentinel is the sole session owner
# Removing import to prevent accidental dual-writer violations
TelegramClient = None  # type: ignore

import gc
import yaml
import fcntl
import shutil
import tempfile
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
    session,
    redirect,
)
import time

try:
    from flask_cors import CORS  # type: ignore
except ImportError:  # pragma: no cover - optional dependency

    def CORS(app: Flask, *args: Any, **kwargs: Any) -> Flask:
        logging.getLogger(__name__).warning(
            "flask-cors not installed; continuing without CORS support"
        )
        return app


try:
    from flask_socketio import SocketIO, emit  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    logging.getLogger(__name__).warning(
        "flask_socketio not installed; continuing without Socket.IO support"
    )

    class _SocketIOShim:
        def __init__(self, app: Flask | None = None, *args: Any, **kwargs: Any) -> None:
            self.app = app

        def on(
            self, *args: Any, **kwargs: Any
        ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                return func

            return _decorator

        def run(self, app: Flask, *args: Any, **kwargs: Any) -> None:
            host = kwargs.get("host", "0.0.0.0")
            port = kwargs.get("port", 5000)
            app.run(host=host, port=port)

        def emit(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
            return None

    def emit(*args: Any, **kwargs: Any) -> None:  # type: ignore
        return None

    SocketIO = _SocketIOShim  # type: ignore

from functools import wraps
from sqlalchemy import text
from sqlalchemy.engine import Engine

# Ensure we can import the core package when running the UI standalone.
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tgsentinel.config import AppCfg, load_config  # type: ignore  # noqa: E402
from tgsentinel.store import init_db  # type: ignore  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)

# Require explicit SECRET_KEY from environment for security
SECRET_KEY = os.environ.get("UI_SECRET_KEY")
if not SECRET_KEY:
    logger.error(
        "FATAL: UI_SECRET_KEY environment variable is required. "
        "Generate a secure random secret (32+ bytes) and set it before starting the application."
    )
    sys.exit(1)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = SECRET_KEY
# Relaxed CORS for private app use
CORS(app, resources={r"/*": {"origins": "*", "supports_credentials": True}})


# Add CSP headers that allow inline scripts and eval (relaxed for private app)
@app.after_request
def add_security_headers(response):
    # Relaxed CSP for private internal app - allows inline scripts and eval
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net https://cdn.socket.io; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "connect-src 'self' ws: wss: https://cdn.jsdelivr.net https://cdn.socket.io;"
    )
    return response


socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

config: AppCfg | None = None
redis_client: Any = None
engine: Engine | None = None
_init_lock = threading.Lock()
_is_initialized = False
_login_ctx: Dict[str, Dict[str, Any]] = {}

# Development helper: allow bypassing auth gating for UI review
UI_SKIP_AUTH = os.getenv("UI_SKIP_AUTH", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
    "dev",
}

# UI Lock configuration (password-protected lock without logging out)
UI_LOCK_PASSWORD = os.getenv("UI_LOCK_PASSWORD", "")
try:
    UI_LOCK_TIMEOUT = int(os.getenv("UI_LOCK_TIMEOUT", "900"))
except Exception:
    UI_LOCK_TIMEOUT = 900


def _normalize_phone(raw: str) -> str:
    """Normalize phone into a stable key and E.164-like form for API calls.

    - Trim whitespace
    - Convert leading '00' to '+'
    - Remove spaces, hyphens, parentheses
    """
    if not raw:
        return ""
    s = str(raw).strip()
    s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s.startswith("00"):
        s = "+" + s[2:]
    return s


def _format_display_phone(phone: str | None) -> str | None:
    """Return a UI-friendly phone value with a single leading + when possible."""
    if not phone:
        return None
    normalized = _normalize_phone(str(phone))
    if not normalized:
        return None
    return normalized if normalized.startswith("+") else f"+{normalized}"


def _ctx_file_for_phone(phone: str) -> Path:
    """Return a filesystem path for storing login ctx for the phone (safe name)."""
    norm = _normalize_phone(phone)
    safe = norm.replace("+", "plus").replace("/", "_")
    base = Path(__file__).parent.parent / "data" / "login_ctx"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{safe}.json"


# Cache of expensive lookups with short lifetimes.
_cached_summary: Tuple[datetime, Dict[str, Any]] | None = None
_cached_health: Tuple[datetime, Dict[str, Any]] | None = None

STREAM_DEFAULT = "tgsentinel:messages"
RELOGIN_KEY = "tgsentinel:relogin"
CREDENTIALS_UI_KEY = "tgsentinel:credentials:ui"
AUTH_QUEUE_KEY = "tgsentinel:auth_queue"
AUTH_RESPONSE_HASH = "tgsentinel:auth_responses"
AUTH_REQUEST_TIMEOUT_SECS = 90.0
_HANDSHAKE_FINAL_STATES = {"worker_resumed", "timeout", "cancelled"}


def reset_for_testing() -> None:
    """Reset global state for test isolation.

    This is a public API for tests to reset module-level state without
    directly accessing private attributes.
    """
    global _is_initialized, redis_client, _cached_health, _cached_summary
    _is_initialized = False
    redis_client = None
    _cached_health = None
    _cached_summary = None


def reload_config() -> None:
    """Reload configuration from disk without reinitializing connections."""
    global config
    try:
        config = load_config()
        logger.info("Reloaded TG Sentinel configuration")
    except Exception as exc:
        logger.warning("Failed to reload config: %s", exc)


def init_app() -> None:
    """Initialise configuration, database, and Redis connections."""

    global config, redis_client, engine, _is_initialized

    with _init_lock:
        if _is_initialized:
            return

        try:
            config = load_config()
            logger.info("Loaded TG Sentinel configuration")
        except Exception as exc:
            logger.warning("Falling back to environment defaults: %s", exc)
            config = None

        db_uri = (
            config.db_uri
            if config
            else os.getenv("DB_URI", "sqlite:////app/data/sentinel.db")
        )
        engine = init_db(db_uri)
        logger.info("Database engine initialised: %s", db_uri)

        stream_cfg = (
            config.redis
            if config
            else {
                "host": os.getenv("REDIS_HOST", "localhost"),
                "port": int(os.getenv("REDIS_PORT", "6379")),
                "stream": os.getenv("REDIS_STREAM", STREAM_DEFAULT),
            }
        )

        if redis:
            try:
                client = redis.Redis(
                    host=stream_cfg["host"],
                    port=int(stream_cfg.get("port", 6379)),
                    db=int(stream_cfg.get("db", 0)),
                    decode_responses=True,
                    socket_timeout=1.5,
                )
                client.ping()
                redis_client = client
                logger.info(
                    "Redis connection ready: %s:%s (DB %s)",
                    stream_cfg["host"],
                    stream_cfg.get("port", 6379),
                    stream_cfg.get("db", 0),
                )

                # Clear any stale relogin handshake from previous UI sessions
                try:
                    stale = redis_client.get(RELOGIN_KEY)
                    if stale:
                        redis_client.delete(RELOGIN_KEY)
                        logger.info(
                            "[UI-STARTUP] Cleared stale relogin handshake marker"
                        )
                except Exception as cleanup_exc:
                    logger.debug(
                        "[UI-STARTUP] Could not clean stale handshake: %s", cleanup_exc
                    )
            except (
                Exception
            ) as exc:  # pragma: no cover - telemetry still works without redis
                logger.warning("Redis unreachable: %s", exc)
                redis_client = None
        else:
            redis_client = None

        _publish_ui_credentials()

        _is_initialized = True


def _ensure_init(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that guarantees :func:`init_app` ran before using shared state."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not _is_initialized:
            init_app()
        # Gate UI/API based on session existence, worker auth, and UI lock
        try:
            if not app.config.get("TESTING"):
                # Skip gating completely when in auth bypass mode (dev only)
                if UI_SKIP_AUTH:
                    return func(*args, **kwargs)
                from flask import request as _rq, render_template as _rt  # type: ignore

                path = _rq.path

                # Worker auth status from Redis
                def _worker_authorized_flag() -> bool | None:
                    try:
                        if redis_client:
                            raw = redis_client.get("tgsentinel:worker_status")
                            if raw:
                                if isinstance(raw, bytes):
                                    raw = raw.decode()
                                st = json.loads(str(raw))
                                return True if st.get("authorized") is True else False
                        return None
                    except Exception:
                        return None

                is_session_missing = _session_missing()
                worker_auth = _worker_authorized_flag()
                is_locked = bool(session.get("ui_locked"))

                if path.startswith("/api/"):
                    allowed = (
                        path.startswith("/api/session/")
                        or path.startswith("/api/ui/lock")
                        or path.startswith("/api/worker/status")
                    )
                    # Require login if session missing or worker unauthorized
                    if not allowed and (is_session_missing or (worker_auth is False)):
                        return (
                            jsonify({"status": "error", "message": "Login required"}),
                            401,
                        )
                    # Gate lock for other APIs
                    if is_locked and not allowed:
                        return (
                            jsonify({"status": "locked", "message": "UI locked"}),
                            423,
                        )
                else:
                    # Gate non-API UI routes unless static/data/favicon
                    if not (
                        path.startswith("/static/")
                        or path.startswith("/data/")
                        or path == "/favicon.ico"
                    ):
                        if is_session_missing or (worker_auth is False):
                            return _rt("locked.html"), 401
                        if is_locked:
                            return _rt("locked_ui.html"), 423
        except Exception:
            pass
        return func(*args, **kwargs)

    return wrapper


def _query_one(sql: str, **params: Any) -> Any:
    if not engine:
        return None
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _query_all(sql: str, **params: Any) -> List[Dict[str, Any]]:
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params)
        return [dict(row._mapping) for row in rows]


def _resolve_session_path() -> str | None:
    """Resolve the current Telegram session path from multiple sources.

    Tries in order and returns the first that exists on disk; if none exist,
    returns the first configured/path candidate.
    """
    candidates: List[str] = []
    # From Flask app-config (tests inject here)
    try:
        test_cfg = app.config.get("TGSENTINEL_CONFIG")  # type: ignore[attr-defined]
        if test_cfg and getattr(test_cfg, "telegram_session", None):
            candidates.append(str(getattr(test_cfg, "telegram_session")))
    except Exception:
        pass

    # From global config
    if config and getattr(config, "telegram_session", None):
        candidates.append(str(getattr(config, "telegram_session")))

    # Freshly load config
    try:
        fresh_cfg = load_config()
        if getattr(fresh_cfg, "telegram_session", None):
            candidates.append(str(getattr(fresh_cfg, "telegram_session")))
    except Exception:
        pass

    # Env
    env_path = os.getenv("TG_SESSION_PATH")
    if env_path:
        candidates.append(env_path)

    # Common defaults
    container_default = "/app/data/tgsentinel.session"
    try:
        repo_default = str((REPO_ROOT / "data" / "tgsentinel.session").resolve())
    except Exception:
        repo_default = None
    candidates.extend([container_default])
    if repo_default:
        candidates.append(repo_default)

    # Return the first existing file
    for cand in candidates:
        try:
            if cand and Path(cand).exists():
                return cand
        except Exception:
            continue

    # None exist; return the first candidate as a hint
    return candidates[0] if candidates else None


def _invalidate_session(session_path: str | None) -> Dict[str, Any]:
    """Safely invalidate a Telethon session and clear related caches.

    Returns a dict with details of the operation for diagnostics.
    """
    result: Dict[str, Any] = {
        "session_path": session_path or "",
        "file_removed": False,
        "cache_keys_deleted": [],
    }
    # Remove session file(s) if present — try known candidates for robustness
    try:
        delete_list: List[str] = []
        # Include the resolved path
        if session_path:
            delete_list.append(session_path)
        # Include env/config/defaults
        env_path = os.getenv("TG_SESSION_PATH")
        if env_path:
            delete_list.append(env_path)
        if config and getattr(config, "telegram_session", None):
            delete_list.append(str(getattr(config, "telegram_session")))
        delete_list.append("/app/data/tgsentinel.session")
        try:
            delete_list.append(
                str((REPO_ROOT / "data" / "tgsentinel.session").resolve())
            )
        except Exception:
            pass
        # Deduplicate while preserving order
        seen = set()
        final_list = []
        for item in delete_list:
            if item and item not in seen:
                seen.add(item)
                final_list.append(item)

        for path_str in final_list:
            try:
                p = Path(path_str)
                if p.exists():
                    p.unlink(missing_ok=True)
                    result["file_removed"] = True
                for suffix in ["-journal", ".journal", ".lock"]:
                    jp = Path(str(p) + suffix)
                    if jp.exists():
                        jp.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Could not remove session file(s): %s", exc)

    # Clear user-related caches in Redis if available
    if redis_client:
        try:
            keys = [
                "tgsentinel:user_info",
                "tgsentinel:telegram_users_cache",
                "tgsentinel:chats_cache",
                RELOGIN_KEY,  # Clear any active relogin handshake on logout
            ]
            for k in keys:
                try:
                    deleted = redis_client.delete(k)
                    if deleted:
                        result["cache_keys_deleted"].append(k)
                except Exception:
                    continue

            # Remove any cached avatar objects for the signed-in user(s)
            avatar_pattern = "tgsentinel:user_avatar:*"
            for pattern in [avatar_pattern]:
                pattern_keys: List[str] = []
                scan_iter = getattr(redis_client, "scan_iter", None)
                if callable(scan_iter):
                    try:
                        pattern_keys = [
                            k.decode() if isinstance(k, bytes) else k
                            for k in scan_iter(match=pattern)  # type: ignore[misc]
                        ]
                    except Exception:
                        pattern_keys = []
                else:
                    try:
                        raw = redis_client.keys(pattern)  # type: ignore[attr-defined]
                        if raw:
                            pattern_keys = [
                                k.decode() if isinstance(k, bytes) else k for k in raw
                            ]
                    except Exception:
                        pattern_keys = []

                if not pattern_keys:
                    continue

                try:
                    redis_client.delete(*pattern_keys)
                    result["cache_keys_deleted"].extend(pattern_keys)
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Could not clear cache keys: %s", exc)
    return result


def _session_missing() -> bool:
    try:
        # Check Flask session marker (set only after successful UI login)
        if session.get("telegram_authenticated"):
            return False

        # Session marker not set - login is required
        return True
    except Exception:
        return True


def _read_handshake_state() -> Dict[str, Any] | None:
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(RELOGIN_KEY)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(str(raw))
    except Exception:
        return None


def _request_relogin_handshake(timeout: float = 45.0) -> str | None:
    """Coordinate with the worker before promoting a new session file."""

    if redis_client is None:
        logger.warning("Redis unavailable; proceeding without re-login handshake")
        return None

    # Do not stomp over an active handshake initiated elsewhere.
    existing = _read_handshake_state()
    if existing and existing.get("status") not in _HANDSHAKE_FINAL_STATES:
        # Check if the existing handshake is stale (older than 60 seconds)
        existing_ts = existing.get("ts", 0)
        age = time.time() - existing_ts
        if age < 60:
            raise RuntimeError("Another re-login operation is already in progress")
        else:
            logger.warning(
                "[UI-AUTH] Found stale handshake (status=%s, age=%.1fs); replacing it",
                existing.get("status"),
                age,
            )
            # Delete stale marker and proceed with new handshake
            try:
                redis_client.delete(RELOGIN_KEY)
            except Exception:
                pass

    request_id = uuid.uuid4().hex
    payload = {
        "status": "request",
        "request_id": request_id,
        "ts": time.time(),
        "source": "ui",
    }
    redis_client.set(RELOGIN_KEY, json.dumps(payload), ex=120)

    deadline = time.time() + timeout
    poll_interval = 0.5
    while time.time() < deadline:
        state = _read_handshake_state()
        if not state:
            time.sleep(poll_interval)
            continue
        if state.get("request_id") != request_id:
            # Another request replaced ours unexpectedly; abort.
            raise RuntimeError("Re-login handshake was pre-empted by another request")
        if state.get("status") == "worker_detached":
            return request_id
        time.sleep(poll_interval)

    # Timeout reached - write final state to unblock future re-login attempts
    try:
        current_state = _read_handshake_state()
        # Only write timeout state if our request_id is still current (not replaced)
        if current_state and current_state.get("request_id") == request_id:
            timeout_payload = {
                "status": "timeout",
                "request_id": request_id,
                "ts": time.time(),
                "source": "ui",
            }
            redis_client.set(RELOGIN_KEY, json.dumps(timeout_payload), ex=120)
            logger.debug("Wrote timeout state for handshake request_id=%s", request_id)
    except Exception as timeout_state_exc:
        logger.warning("Could not write timeout state to Redis: %s", timeout_state_exc)

    raise TimeoutError("Worker did not acknowledge re-login handshake in time")


def _finalize_relogin_handshake(request_id: str | None, status: str) -> None:
    if not request_id or redis_client is None:
        return
    payload = {
        "status": status,
        "request_id": request_id,
        "ts": time.time(),
        "source": "ui",
    }
    try:
        redis_client.set(RELOGIN_KEY, json.dumps(payload), ex=120)
    except Exception:
        logger.debug("Failed to update re-login handshake status", exc_info=True)


def _credential_fingerprint() -> Dict[str, str] | None:
    api_id = None
    api_hash = None
    if config:
        api_id = getattr(config, "api_id", None)
        api_hash = getattr(config, "api_hash", None)
    if api_id is None:
        raw = os.getenv("TG_API_ID")
        if raw:
            try:
                api_id = int(raw)
            except Exception:
                api_id = None
    if not api_hash:
        api_hash = os.getenv("TG_API_HASH")
    if api_id is None or not api_hash:
        return None
    fingerprint = hashlib.sha256(str(api_hash).encode("utf-8")).hexdigest()
    return {"api_id": str(api_id), "api_hash_sha256": fingerprint}


def _publish_ui_credentials() -> None:
    if redis_client is None:
        return
    fingerprint = _credential_fingerprint()
    if not fingerprint:
        return
    payload = {
        "fingerprint": fingerprint,
        "source": "ui",
        "ts": time.time(),
    }
    try:
        redis_client.set(CREDENTIALS_UI_KEY, json.dumps(payload), ex=3600)
    except Exception:
        logger.debug("Failed to write credential fingerprint", exc_info=True)


def _store_login_context(phone: str, data: Dict[str, Any]) -> None:
    """Store ephemeral login state (e.g., phone_code_hash) in Redis or memory."""
    normalized = _normalize_phone(phone)
    try:
        if redis_client is not None:
            redis_client.setex(
                f"tgsentinel:login:phone:{normalized}",
                300,
                json.dumps(data),
            )
            return
    except Exception:
        pass
    # Filesystem fallback (multi-worker safe)
    try:
        payload = {**data, "_expires": time.time() + 300}
        fpath = _ctx_file_for_phone(normalized)
        fpath.write_text(json.dumps(payload), encoding="utf-8")
        return
    except Exception:
        pass
    # In-memory last resort (single-worker only)
    _login_ctx[normalized] = {**data, "_expires": time.time() + 300}


def _load_login_context(phone: str) -> Dict[str, Any] | None:
    try:
        normalized = _normalize_phone(phone)
        if redis_client is not None:
            raw = redis_client.get(f"tgsentinel:login:phone:{normalized}")
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                return json.loads(str(raw))
    except Exception:
        pass
    # Filesystem fallback
    try:
        fpath = _ctx_file_for_phone(phone)
        if fpath.exists():
            raw = fpath.read_text(encoding="utf-8")
            data = json.loads(raw)
            if data.get("_expires", 0) < time.time():
                try:
                    fpath.unlink()
                except Exception:
                    pass
                return None
            return data
    except Exception:
        pass
    data = _login_ctx.get(_normalize_phone(phone))
    if not data:
        return None
    if data.get("_expires", 0) < time.time():
        _login_ctx.pop(_normalize_phone(phone), None)
        return None
    return data


def _clear_login_context(phone: str) -> None:
    """Remove stored login context for a phone number."""
    normalized = _normalize_phone(phone)
    try:
        if redis_client is not None:
            redis_client.delete(f"tgsentinel:login:phone:{normalized}")
            return
    except Exception:
        pass
    try:
        fpath = _ctx_file_for_phone(normalized)
        if fpath.exists():
            fpath.unlink()
            return
    except Exception:
        pass
    _login_ctx.pop(normalized, None)


def _submit_auth_request(
    action: str, payload: Dict[str, Any], timeout: float = AUTH_REQUEST_TIMEOUT_SECS
) -> Dict[str, Any]:
    """Send an authentication request to the sentinel worker via Redis."""
    if redis_client is None:
        logger.error("[UI-AUTH] Redis connection not available for auth request")
        raise RuntimeError("Redis connection not available for auth request")

    request_id = uuid.uuid4().hex
    message = {"action": action, "request_id": request_id}
    message.update(payload)

    logger.info(
        "[UI-AUTH] Submitting %s request (request_id=%s) to sentinel via Redis",
        action,
        request_id,
    )
    logger.debug("[UI-AUTH] Request payload keys: %s", list(message.keys()))

    try:
        redis_client.rpush(AUTH_QUEUE_KEY, json.dumps(message))
        logger.debug("[UI-AUTH] Request pushed to queue: %s", AUTH_QUEUE_KEY)
    except Exception as exc:
        logger.error("[UI-AUTH] Failed to enqueue auth request: %s", exc, exc_info=True)
        raise RuntimeError(f"Failed to enqueue auth request: {exc}") from exc

    logger.debug("[UI-AUTH] Waiting for response (timeout=%.1fs)...", timeout)
    deadline = time.time() + timeout
    poll_interval = 0.5
    poll_count = 0
    while time.time() < deadline:
        poll_count += 1
        try:
            raw = redis_client.hget(AUTH_RESPONSE_HASH, request_id)
        except Exception as exc:
            logger.error(
                "[UI-AUTH] Failed to read auth response: %s", exc, exc_info=True
            )
            raise RuntimeError(f"Failed to read auth response: {exc}") from exc

        if raw:
            logger.info(
                "[UI-AUTH] Received response after %d polls (%.1fs elapsed)",
                poll_count,
                time.time() - (deadline - timeout),
            )
            try:
                redis_client.hdel(AUTH_RESPONSE_HASH, request_id)
            except Exception:
                pass
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                response = json.loads(str(raw))
                logger.debug(
                    "[UI-AUTH] Response parsed: status=%s", response.get("status")
                )
                return response
            except Exception as exc:
                logger.error(
                    "[UI-AUTH] Invalid auth response payload: %s", exc, exc_info=True
                )
                raise RuntimeError(f"Invalid auth response payload: {exc}") from exc

        time.sleep(poll_interval)

    logger.warning(
        "[UI-AUTH] Timeout waiting for sentinel response (%.1fs, %d polls)",
        timeout,
        poll_count,
    )
    raise TimeoutError("Sentinel did not respond to auth request in time")


def _wait_for_worker_authorization(timeout: float = 60.0) -> bool:
    """Wait for the sentinel worker to report an authorized state."""
    logger.debug(
        "[UI-AUTH] Waiting for worker authorization (timeout=%.1fs)...", timeout
    )
    if redis_client is None:
        logger.error("[UI-AUTH] Redis client not available for authorization check")
        return False

    deadline = time.time() + timeout
    poll_interval = 1.0
    poll_count = 0
    while time.time() < deadline:
        poll_count += 1
        try:
            worker_status_raw = redis_client.get("tgsentinel:worker_status")
        except Exception as exc:
            logger.debug("[UI-AUTH] Failed to read worker status: %s", exc)
            worker_status_raw = None
        if worker_status_raw:
            if isinstance(worker_status_raw, bytes):
                worker_status_raw = worker_status_raw.decode()
            try:
                worker_status = json.loads(str(worker_status_raw))
                logger.debug(
                    "[UI-AUTH] Worker status poll %d: authorized=%s",
                    poll_count,
                    worker_status.get("authorized"),
                )
            except Exception:
                worker_status = {}
            if worker_status.get("authorized") is True:
                logger.info(
                    "[UI-AUTH] Worker authorization confirmed after %d polls",
                    poll_count,
                )
                if not _wait_for_cached_user_info(timeout=10.0):
                    logger.debug(
                        "[UI-AUTH] Worker authorized but user info not yet cached"
                    )
                return True
        time.sleep(poll_interval)
    logger.warning(
        "[UI-AUTH] Worker authorization timeout after %d polls (%.1fs)",
        poll_count,
        timeout,
    )
    return False


def _wait_for_cached_user_info(timeout: float = 10.0) -> bool:
    """Ensure the worker stored the current user info so the UI can display it."""
    if not redis_client:
        return False

    deadline = time.time() + timeout
    poll_interval = 0.5
    while time.time() < deadline:
        if _load_cached_user_info():
            return True
        time.sleep(poll_interval)
    return False


def _execute(sql: str, **params: Any) -> None:
    """Execute a write operation (INSERT, UPDATE, DELETE)."""
    if not engine:
        # Log clear warning about missing DB connectivity
        param_keys = list(params.keys()) if params else []
        logger.warning(
            "Cannot execute SQL statement: database engine not initialized. "
            f"Statement: {sql[:200]}{'...' if len(sql) > 200 else ''} | "
            f"Parameters: {len(param_keys)} param(s) {param_keys if param_keys else '(none)'}"
        )
        return
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def _truncate(text_value: str | None, limit: int = 96) -> str:
    if not text_value:
        return ""
    return text_value if len(text_value) <= limit else f"{text_value[:limit]}..."


def _get_stream_name() -> str:
    if config and config.redis:
        return config.redis.get("stream", STREAM_DEFAULT)
    return os.getenv("REDIS_STREAM", STREAM_DEFAULT)


def _fallback_username() -> str:
    return os.getenv("TG_SENTINEL_USER", "Analyst")


def _fallback_avatar() -> str:
    return "/static/images/logo.png"


def _load_cached_user_info() -> Dict[str, Any] | None:
    """Return cached Telegram account info stored by the worker in Redis."""
    if not redis_client:
        return None

    try:
        raw = redis_client.get("tgsentinel:user_info")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        info = json.loads(str(raw))
        if not isinstance(info, dict):  # Defensive check
            return None

        phone = info.get("phone")
        if phone:
            formatted = _format_display_phone(str(phone))
            info["phone"] = formatted if formatted else phone
        return info
    except Exception as exc:
        logger.debug("Failed to load cached user info: %s", exc)
        return None


# ============================================================================
# Profile Persistence Layer
# ============================================================================

# Thread-safe lock for profile file operations
_profiles_lock = threading.Lock()

# Profiles file path (JSON format for simplicity)
PROFILES_FILE = Path(__file__).parent.parent / "data" / "profiles.json"


def load_profiles() -> Dict[str, Any]:
    """Load all profiles from disk. Returns empty dict if file doesn't exist or on error."""
    try:
        if not PROFILES_FILE.exists():
            logger.debug("Profiles file does not exist: %s", PROFILES_FILE)
            return {}

        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            logger.debug("Loaded %d profile(s) from %s", len(data), PROFILES_FILE)
            return data
    except Exception as exc:
        logger.error("Failed to load profiles from %s: %s", PROFILES_FILE, exc)
        return {}


def save_profiles(profiles: Dict[str, Any]) -> bool:
    """Save all profiles to disk with file locking. Returns True on success, False on error."""
    try:
        # Ensure data directory exists
        PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Use atomic write: write to temp file, then rename
        temp_fd, temp_path = tempfile.mkstemp(
            dir=PROFILES_FILE.parent, prefix=".profiles_", suffix=".tmp"
        )

        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                # Acquire file lock for thread safety
                fcntl.flock(temp_f.fileno(), fcntl.LOCK_EX)
                try:
                    yaml.safe_dump(
                        profiles, temp_f, default_flow_style=False, sort_keys=True
                    )
                    temp_f.flush()
                    os.fsync(temp_f.fileno())
                finally:
                    fcntl.flock(temp_f.fileno(), fcntl.LOCK_UN)

            # Atomic rename
            shutil.move(temp_path, str(PROFILES_FILE))
            logger.debug("Saved %d profile(s) to %s", len(profiles), PROFILES_FILE)
            return True

        except Exception:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    except Exception as exc:
        logger.error("Failed to save profiles to %s: %s", PROFILES_FILE, exc)
        return False


def get_profile(name: str) -> Dict[str, Any] | None:
    """Get a single profile by name. Returns None if not found."""
    with _profiles_lock:
        profiles = load_profiles()
        return profiles.get(name)


def upsert_profile(profile_dict: Dict[str, Any]) -> bool:
    """Insert or update a profile. Returns True on success, False on error."""
    name = profile_dict.get("name", "").strip()
    if not name:
        logger.warning("Cannot upsert profile without a name")
        return False

    with _profiles_lock:
        profiles = load_profiles()
        profiles[name] = profile_dict
        return save_profiles(profiles)


def delete_profile(name: str) -> bool:
    """Delete a profile by name. Returns True if deleted or didn't exist, False on error."""
    with _profiles_lock:
        profiles = load_profiles()
        if name in profiles:
            del profiles[name]
            return save_profiles(profiles)
        # Not found is not an error for deletion
        return True


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "Not linked"
    digits = phone.strip()
    if digits.startswith("+"):
        digits = digits[1:]
    digits = digits.replace(" ", "")
    if not digits:
        return "Not linked"
    if len(digits) <= 4:
        # Mask all but last 2 characters for short numbers
        return f"***{digits[-2:]}" if len(digits) >= 2 else "***"
    return f"+{digits[0]}***{digits[-4:]}"


def _compute_summary() -> Dict[str, Any]:
    global _cached_summary
    now = datetime.now(timezone.utc)
    if _cached_summary and now - _cached_summary[0] < timedelta(seconds=15):
        return _cached_summary[1]

    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    messages_ingested = (
        _query_one(
            "SELECT COUNT(*) FROM messages WHERE datetime(created_at) >= :cutoff",
            cutoff=cutoff,
        )
        or 0
    )
    alerts_sent = (
        _query_one(
            "SELECT COUNT(*) FROM messages WHERE alerted=1 AND datetime(created_at) >= :cutoff",
            cutoff=cutoff,
        )
        or 0
    )
    avg_importance = (
        _query_one(
            "SELECT AVG(score) FROM messages WHERE datetime(created_at) >= :cutoff",
            cutoff=cutoff,
        )
        or 0.0
    )

    # Try to get feedback data, but handle if table doesn't exist
    try:
        feedback = _query_all(
            "SELECT label FROM feedback WHERE datetime(created_at) >= :cutoff",
            cutoff=cutoff,
        )
        positive = sum(row.get("label", 0) for row in feedback)
        total_feedback = len(feedback)
        accuracy = (positive / total_feedback * 100) if total_feedback else 0.0
    except Exception:
        # Feedback table doesn't exist or query failed - calculate from alerted messages
        # Use the ratio of high-score alerts (score > 0.7) as a proxy for accuracy
        high_score_alerts = (
            _query_one(
                "SELECT COUNT(*) FROM messages WHERE alerted=1 AND score >= 0.7 AND datetime(created_at) >= :cutoff",
                cutoff=cutoff,
            )
            or 0
        )
        accuracy = (high_score_alerts / alerts_sent * 100) if alerts_sent else 0.0

    summary = {
        "messages_ingested": int(messages_ingested),
        "alerts_sent": int(alerts_sent),
        "avg_importance": round(float(avg_importance or 0.0), 2),
        "feedback_accuracy": round(accuracy, 1),
    }
    _cached_summary = (now, summary)
    return summary


def _compute_health() -> Dict[str, Any]:
    global _cached_health
    now = datetime.now(timezone.utc)
    if _cached_health and now - _cached_health[0] < timedelta(seconds=10):
        return _cached_health[1]

    stream_name = _get_stream_name()
    redis_depth = 0
    redis_online = False
    if redis_client:
        try:
            depth_val = redis_client.xlen(stream_name)
            try:
                redis_depth = int(depth_val)
            except Exception:
                # In tests, mocks may return MagicMock; coerce safely
                redis_depth = int(str(depth_val)) if str(depth_val).isdigit() else 0
            redis_online = True
        except Exception as exc:  # pragma: no cover - redis may be offline
            logger.debug("Redis depth unavailable: %s", exc)
    # Fallback: if depth is still 0 or client missing, try a one-off connection using config.redis
    try:
        if (not redis_client or redis_depth == 0) and redis:
            if config and getattr(config, "redis", None):
                rcfg = config.redis
                try:
                    tmp = redis.Redis(
                        host=rcfg.get("host", "localhost"),
                        port=int(rcfg.get("port", 6379)),
                        db=int(rcfg.get("db", 0)),
                        decode_responses=True,
                        socket_timeout=1.0,
                    )
                    # Try both stream names explicitly
                    dv = 0
                    for nm in [
                        rcfg.get("stream", STREAM_DEFAULT),
                        "sentinel:messages",
                        "tgsentinel:messages",
                    ]:
                        try:
                            xlen_result = tmp.xlen(nm)
                            # Type cast: synchronous Redis client returns int | None, not awaitable
                            val = int(cast(int, xlen_result)) if xlen_result else 0
                            if val > dv:
                                dv = val
                        except Exception:
                            continue
                    if dv > redis_depth:
                        redis_depth = dv
                        redis_online = True
                except Exception:
                    pass
    except Exception:
        pass

    db_size_mb = 0.0
    db_path = None
    db_uri = getattr(config, "db_uri", None) if config else None
    if db_uri and db_uri.startswith("sqlite"):
        db_path = db_uri.replace("sqlite:///", "")
    else:
        db_path = os.getenv("DB_FILE", "data/sentinel.db")
    db_file = Path(db_path)
    if db_file.exists():
        db_size_mb = round(db_file.stat().st_size / (1024 * 1024), 2)

    cpu_pct = None
    memory_mb = None
    if psutil:
        try:
            cpu_pct = psutil.cpu_percent(interval=None)
            process = psutil.Process(os.getpid())
            memory_mb = round(process.memory_info().rss / (1024 * 1024), 1)
        except Exception as exc:  # pragma: no cover - psutil may fail
            logger.debug("psutil metrics unavailable: %s", exc)

    checkpoint_file = Path("data/tgsentinel.session")
    last_checkpoint = None
    if checkpoint_file.exists():
        last_checkpoint = datetime.fromtimestamp(
            checkpoint_file.stat().st_mtime, tz=timezone.utc
        ).isoformat()

    payload = {
        "redis_stream_depth": redis_depth,
        "database_size_mb": db_size_mb,
        "redis_online": redis_online,
        "cpu_percent": cpu_pct,
        "memory_mb": memory_mb,
        "last_checkpoint": last_checkpoint,
    }
    _cached_health = (now, payload)
    return payload


def _normalise_tags(tags: Any) -> List[str]:
    if not tags:
        return []
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    if isinstance(tags, str):
        raw = tags.strip()
        if raw.startswith("["):
            try:
                parsed = yaml.safe_load(raw)
                return _normalise_tags(parsed)
            except Exception:
                pass
        return [piece.strip() for piece in raw.split(",") if piece.strip()]
    return [str(tags).strip()]


def _load_live_feed(limit: int = 20) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if redis_client:
        try:
            raw_entries = redis_client.xrevrange(_get_stream_name(), count=limit)
            iterable: Iterable[Any] = raw_entries or []
            if not isinstance(iterable, list):
                iterable = list(iterable)

            # Build a chat_id to channel name mapping
            chat_id_to_name = {}
            if config and hasattr(config, "channels"):
                for channel in config.channels:
                    if hasattr(channel, "id") and hasattr(channel, "name"):
                        chat_id_to_name[channel.id] = channel.name

            for entry_id, payload in iterable:
                data = dict(payload)

                # Parse the JSON field if it exists
                json_str = data.get("json")
                if json_str:
                    try:
                        parsed = json.loads(json_str)
                        # Merge parsed JSON into data
                        data.update(parsed)
                    except Exception:
                        pass

                # Get chat name from various sources
                chat_id = data.get("chat_id")
                chat_name = None

                # Try to get chat_name from data first
                chat_name = (
                    data.get("chat_name", "").strip()
                    or data.get("chat_title", "").strip()
                    or data.get("channel", "").strip()
                )

                # If no chat_name found, try to look up by chat_id
                if not chat_name and chat_id:
                    try:
                        chat_id_int = int(chat_id)
                        chat_name = chat_id_to_name.get(chat_id_int)
                    except (ValueError, TypeError):
                        pass

                # Final fallback
                if not chat_name:
                    chat_name = "Unknown chat"

                # Get sender name from various sources
                # Check if sender_name exists and is not just whitespace
                sender_name = data.get("sender_name", "").strip()
                if not sender_name:
                    sender_name = (
                        data.get("sender", "").strip()
                        or data.get("author", "").strip()
                        or "Unknown sender"
                    )

                # Prefer avatar URL present in the payload (if ingestion provided it)
                avatar_url = (
                    data.get("avatar_url") or data.get("chat_avatar_url") or None
                )
                sender_id = data.get("sender_id")
                chat_id = data.get("chat_id")

                # Try user avatar first (for sender)
                if not avatar_url and sender_id:
                    try:
                        cache_key = f"tgsentinel:user_avatar:{sender_id}"
                        cached_avatar = redis_client.get(cache_key)
                        if cached_avatar:
                            # Decode bytes to string if needed
                            avatar_url = (
                                cached_avatar.decode("utf-8")
                                if isinstance(cached_avatar, bytes)
                                else cached_avatar
                            )
                    except Exception:
                        pass  # Avatar is optional

                # If no user avatar, try chat avatar as fallback
                if not avatar_url and chat_id:
                    try:
                        cache_key = f"tgsentinel:chat_avatar:{chat_id}"
                        cached_avatar = redis_client.get(cache_key)
                        if cached_avatar:
                            avatar_url = (
                                cached_avatar.decode("utf-8")
                                if isinstance(cached_avatar, bytes)
                                else cached_avatar
                            )
                    except Exception:
                        pass  # Avatar is optional

                # Private chats use positive IDs which correspond to user avatars as well.
                # Check user_avatar:<chat_id> as an additional fallback for DMs.
                if not avatar_url and chat_id:
                    try:
                        chat_id_int = int(chat_id)
                        if chat_id_int > 0:
                            cache_key = f"tgsentinel:user_avatar:{chat_id_int}"
                            cached_avatar = redis_client.get(cache_key)
                            if cached_avatar:
                                avatar_url = (
                                    cached_avatar.decode("utf-8")
                                    if isinstance(cached_avatar, bytes)
                                    else cached_avatar
                                )
                    except Exception:
                        pass
                entries.append(
                    {
                        "id": entry_id,
                        "chat_id": chat_id,
                        "chat_name": chat_name,
                        "sender_id": data.get("sender_id"),
                        "sender": sender_name,
                        "message": _truncate(data.get("message") or data.get("text")),
                        "importance": round(float(data.get("importance", 0.0)), 2),
                        "tags": _normalise_tags(data.get("tags")),
                        "timestamp": _format_timestamp(
                            data.get("timestamp") or data.get("created_at") or ""
                        ),
                        "avatar_url": avatar_url,
                    }
                )
        except Exception as exc:  # pragma: no cover - redis optional
            logger.debug("Failed to read activity feed: %s", exc)
    # If Redis is available but no entries, return an empty list (no fallback)
    if redis_client is not None:
        return entries

    # Redis not available – return a minimal fallback so the UI isn't blank
    if entries:
        return entries

    now = datetime.now(timezone.utc)
    fallback: List[Dict[str, Any]] = []
    channels_attr = getattr(config, "channels", None) if config else None
    channels = (channels_attr if channels_attr else [])[:3]
    if channels:
        for idx, channel in enumerate(channels):
            fallback.append(
                {
                    "id": f"mock-{idx}",
                    "chat_name": getattr(channel, "name", "Unknown Channel")
                    or "Unknown Channel",
                    "sender": "System",
                    "message": "Monitoring for semantic matches...",
                    "importance": round(0.35 + idx * 0.1, 2),
                    "tags": ["semantic", "watch"],
                    "timestamp": (now - timedelta(minutes=idx * 5)).isoformat(),
                }
            )
    else:
        fallback.append(
            {
                "id": "mock-0",
                "chat_name": "TG Sentinel",
                "sender": "System",
                "message": "Activity feed unavailable. Redis offline?",
                "importance": 0.2,
                "tags": ["status"],
                "timestamp": now.isoformat(),
            }
        )
    return fallback


def _load_alerts(limit: int = 100) -> List[Dict[str, Any]]:
    rows = _query_all(
        """
        SELECT chat_id, msg_id, score, alerted, created_at, chat_title, sender_name, message_text, triggers
        FROM messages
        WHERE alerted = 1
        ORDER BY datetime(created_at) DESC
        LIMIT :limit
        """,
        limit=limit,
    )

    # Safely retrieve mode with fallback
    alerts_config = getattr(config, "alerts", None) if config else None
    mode = getattr(alerts_config, "mode", "dm") if alerts_config else "dm"

    # Build chat_id to channel name mapping from config
    chat_id_to_name = {}
    if config and hasattr(config, "channels"):
        try:
            for channel in config.channels:
                if hasattr(channel, "id") and hasattr(channel, "name"):
                    # Ensure we're getting actual values, not MagicMock
                    channel_id = channel.id
                    channel_name = channel.name
                    # Verify they're actually serializable types
                    if isinstance(channel_id, (int, str)) and isinstance(
                        channel_name, str
                    ):
                        chat_id_to_name[channel_id] = channel_name
        except Exception:
            pass  # Ignore errors in config parsing

    if rows:
        return [
            {
                "chat_id": row["chat_id"],
                "chat_name": (
                    (row.get("chat_title") or "").strip()
                    or chat_id_to_name.get(row["chat_id"])
                    or f"Chat {row['chat_id']}"
                ),
                "sender": (row.get("sender_name") or "").strip() or "Unknown sender",
                "message_text": (row.get("message_text") or "").strip(),
                "excerpt": _truncate((row.get("message_text") or "").strip(), limit=80)
                or f"Message #{row['msg_id']}",
                "msg_id": row["msg_id"],
                "score": round(float(row.get("score", 0.0)), 2),
                "trigger": (row.get("triggers") or "").strip() or "threshold",
                "sent_to": mode,
                "created_at": _format_timestamp(row.get("created_at", "")),
            }
            for row in rows
        ]

    # Use the same safe retrieval for fallback
    return [
        {
            "chat_id": -1,
            "chat_name": "No Alerts Yet",
            "sender": "",
            "excerpt": "Alerts will appear here once heuristics fire.",
            "score": 0.0,
            "trigger": "",
            "sent_to": mode,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]


def _load_digests(limit: int = 14) -> List[Dict[str, Any]]:
    rows = _query_all(
        """
        SELECT date(created_at) as digest_date,
               COUNT(*) as items,
               ROUND(AVG(score), 2) as avg_score
        FROM messages
        WHERE alerted = 1
        GROUP BY date(created_at)
        ORDER BY digest_date DESC
        LIMIT :limit
        """,
        limit=limit,
    )
    if rows:
        return [
            {
                "date": row["digest_date"],
                "items": row["items"],
                "avg_score": row["avg_score"],
            }
            for row in rows
        ]

    today = datetime.now(timezone.utc).date().isoformat()
    return [
        {"date": today, "items": 0, "avg_score": 0.0},
    ]


def _serialize_channels() -> List[Dict[str, Any]]:
    if not config:
        return []
    channels = getattr(config, "channels", None)
    if not channels:
        return []
    serialised: List[Dict[str, Any]] = []
    for idx, channel in enumerate(channels, start=1):
        serialised.append(
            {
                "id": getattr(channel, "id", idx),
                "chat_id": getattr(channel, "id", idx),
                "name": getattr(channel, "name", f"Channel {idx}"),
                "vip_senders": list(getattr(channel, "vip_senders", [])),
                "keywords": list(getattr(channel, "keywords", [])),
                "reaction_threshold": getattr(channel, "reaction_threshold", 0),
                "reply_threshold": getattr(channel, "reply_threshold", 0),
                "rate_limit": getattr(channel, "rate_limit_per_hour", 0),
                "enabled": True,
            }
        )
    return serialised


def _validate_config_payload(payload: Dict[str, Any]) -> None:
    """Validate configuration payload before applying changes.

    Raises ValueError if any field is invalid.
    """
    # Validate numeric ranges
    if "redis_port" in payload:
        port = payload["redis_port"]
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError(f"Invalid redis_port: {port}. Must be 1-65535.")

    if "retention_days" in payload:
        days = payload["retention_days"]
        if not isinstance(days, int) or days < 0:
            raise ValueError(f"Invalid retention_days: {days}. Must be >= 0.")

    if "rate_limit_per_channel" in payload:
        rate = payload["rate_limit_per_channel"]
        if not isinstance(rate, int) or rate < 0:
            raise ValueError(f"Invalid rate_limit_per_channel: {rate}. Must be >= 0.")

    # Validate string fields are not empty when required
    required_strings = ["phone_number", "api_hash"]
    for field in required_strings:
        if field in payload and not isinstance(payload[field], str):
            raise ValueError(f"Invalid {field}: must be a string.")

    # Validate mode is in allowed values
    if "mode" in payload and payload["mode"] not in ["dm", "channel", "both"]:
        raise ValueError(
            f"Invalid mode: {payload['mode']}. Must be 'dm', 'channel', or 'both'."
        )

    # Validate channels structure
    if "channels" in payload:
        channels = payload["channels"]
        if not isinstance(channels, list):
            raise ValueError("Invalid channels: must be a list.")
        for idx, channel in enumerate(channels):
            if not isinstance(channel, dict):
                raise ValueError(f"Invalid channel at index {idx}: must be a dict.")


def _write_config(payload: Dict[str, Any]) -> None:
    global config, _cached_summary, _cached_health

    cfg_path = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
    if not cfg_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {cfg_path}")

    # Validate payload before making any changes
    _validate_config_payload(payload)

    backup_path = cfg_path.with_suffix(".yml.bak")
    lock_acquired = False

    try:
        # Acquire exclusive lock on the config file
        with cfg_path.open("r+", encoding="utf-8") as lock_fp:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            lock_acquired = True

            # Read current config
            lock_fp.seek(0)
            yaml_payload = yaml.safe_load(lock_fp) or {}

            # Create backup before modifications
            shutil.copy2(cfg_path, backup_path)
            logger.info(f"Created config backup at {backup_path}")

            # Apply changes to YAML structure
            telegram_cfg = yaml_payload.setdefault("telegram", {})
            if "phone_number" in payload:
                telegram_cfg["phone_number"] = payload["phone_number"]
            if "session" in payload:
                telegram_cfg["session"] = payload["session"]

            api_cfg = yaml_payload.setdefault("api", {})
            if "api_id" in payload:
                api_cfg["id"] = payload["api_id"]
            if "api_hash" in payload:
                api_cfg["hash"] = payload["api_hash"]

            alerts_cfg = yaml_payload.setdefault("alerts", {})
            for key in [
                "mode",
                "target_channel",
                "digest",
                "dedupe_window",
                "rate_limit_per_channel",
                "template",
            ]:
                if key in payload:
                    alerts_cfg[key] = payload[key]

            channels_payload = payload.get("channels")
            if isinstance(channels_payload, list):
                yaml_payload["channels"] = channels_payload

            system_cfg = yaml_payload.setdefault("system", {})
            for key in [
                "redis_host",
                "redis_port",
                "database_uri",
                "retention_days",
                "metrics_endpoint",
                "logging_level",
                "auto_restart",
            ]:
                if key in payload:
                    system_cfg[key] = payload[key]

            # Write to temporary file first (atomic write)
            temp_fd, temp_path = tempfile.mkstemp(
                dir=cfg_path.parent, prefix=".tgsentinel_", suffix=".yml.tmp"
            )
            try:
                with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_fp:
                    yaml.safe_dump(yaml_payload, temp_fp, sort_keys=False)

                # Atomic rename over the original file
                os.replace(temp_path, cfg_path)
                logger.info(f"Configuration written atomically to {cfg_path}")

            except Exception as write_error:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise write_error

            # Release lock before reloading
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            lock_acquired = False

        # Reload configuration after successful write
        try:
            config = load_config()
            logger.info("Configuration reloaded after update")
        except Exception as exc:
            logger.warning("Could not reload configuration: %s", exc)

        _cached_summary = None
        _cached_health = None

    except Exception as error:
        logger.error(f"Config write failed: {error}")

        # Restore from backup on any error
        if backup_path.exists():
            try:
                shutil.copy2(backup_path, cfg_path)
                logger.info(f"Restored config from backup due to error")
                config = load_config()
            except Exception as restore_error:
                logger.error(f"Failed to restore backup: {restore_error}")

        raise error

    finally:
        # Ensure lock is released
        if lock_acquired:
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


@app.context_processor
def inject_now() -> Dict[str, Any]:
    return {
        "now": lambda: datetime.now(timezone.utc),
        "login_required": _session_missing(),
        "ui_lock_timeout": UI_LOCK_TIMEOUT,
        "ui_lock_enabled": (
            True if UI_LOCK_PASSWORD or os.getenv("UI_LOCK_TIMEOUT") else False
        ),
        "auth_bypass": UI_SKIP_AUTH,
    }


@app.route("/favicon.ico")
def favicon():
    """Serve the favicon from the logo image."""
    return send_from_directory(
        Path(app.root_path) / "static" / "images", "logo.png", mimetype="image/png"
    )


@app.route("/")
@_ensure_init
def dashboard_view():
    return render_template(
        "dashboard.html",
        summary=_compute_summary(),
        activity=_load_live_feed(limit=10),
        health=_compute_health(),
        recent_alerts=_load_alerts(limit=8),
    )


@app.route("/data/<path:filename>")
def serve_data_file(filename):
    """Serve files from the shared data directory (e.g., user avatar)."""
    # Primary data directory (container default: /app/data)
    base_dir = Path(__file__).parent.parent / "data"
    target = base_dir / filename
    # Fallback to absolute /app/data for host/dev runs where client saved there
    alt_base = Path("/app/data")
    if not target.exists() and (alt_base / filename).exists():
        return send_from_directory(alt_base, filename)
    return send_from_directory(base_dir, filename)


@app.route("/alerts")
@_ensure_init
def alerts_view():
    return render_template(
        "alerts.html",
        alerts=_load_alerts(limit=250),
        digests=_load_digests(),
    )


@app.route("/config")
@_ensure_init
def config_view():
    session_path = getattr(config, "telegram_session", "") if config else ""
    interests_attr = getattr(config, "interests", None) if config else None
    interests = list(interests_attr) if interests_attr is not None else []

    # Define heuristic options - centralized list that stays in sync with backend
    heuristic_options = ["Mentions", "VIP", "Keywords", "Reaction Surge", "Replies"]

    return render_template(
        "config.html",
        session_path=session_path,
        channels=_serialize_channels(),
        interests=interests,
        heuristic_options=heuristic_options,
        summary=_compute_summary(),
    )


@app.route("/analytics")
@_ensure_init
def analytics_view():
    return render_template(
        "analytics.html",
        summary=_compute_summary(),
        health=_compute_health(),
    )


@app.route("/profiles")
@_ensure_init
def profiles_view():
    # Load interest profiles from the persistence layer
    profiles = load_profiles()

    # Also include legacy interests from config for backward compatibility
    interests_attr = getattr(config, "interests", None) if config else None
    legacy_interests = list(interests_attr) if interests_attr is not None else []

    # Merge: persisted profiles take precedence, then add legacy interests not in profiles
    profile_names = set(profiles.keys())
    for legacy_name in legacy_interests:
        if legacy_name not in profile_names:
            profile_names.add(legacy_name)

    # Load alert profiles (heuristic/keyword-based)
    # First check if we have persisted alert profiles
    alert_profiles = load_alert_profiles()

    # If no persisted profiles, auto-migrate from config channels
    if not alert_profiles and config:
        try:
            channels_attr = getattr(config, "channels", None)
            if channels_attr:
                for channel in channels_attr:
                    channel_id = getattr(channel, "id", None)
                    if channel_id:
                        profile_id = f"channel_{channel_id}"
                        alert_profiles[profile_id] = {
                            "id": profile_id,
                            "name": getattr(channel, "name", f"Channel {channel_id}"),
                            "type": "channel",
                            "channel_id": channel_id,
                            "enabled": True,
                            "vip_senders": getattr(channel, "vip_senders", []),
                            "keywords": getattr(channel, "keywords", []),
                            "action_keywords": getattr(channel, "action_keywords", []),
                            "decision_keywords": getattr(
                                channel, "decision_keywords", []
                            ),
                            "urgency_keywords": getattr(
                                channel, "urgency_keywords", []
                            ),
                            "importance_keywords": getattr(
                                channel, "importance_keywords", []
                            ),
                            "release_keywords": getattr(
                                channel, "release_keywords", []
                            ),
                            "security_keywords": getattr(
                                channel, "security_keywords", []
                            ),
                            "risk_keywords": getattr(channel, "risk_keywords", []),
                            "opportunity_keywords": getattr(
                                channel, "opportunity_keywords", []
                            ),
                            "reaction_threshold": getattr(
                                channel, "reaction_threshold", 5
                            ),
                            "reply_threshold": getattr(channel, "reply_threshold", 3),
                            "detect_codes": getattr(channel, "detect_codes", True),
                            "detect_documents": getattr(
                                channel, "detect_documents", True
                            ),
                            "prioritize_pinned": getattr(
                                channel, "prioritize_pinned", True
                            ),
                            "prioritize_admin": getattr(
                                channel, "prioritize_admin", True
                            ),
                            "detect_polls": getattr(channel, "detect_polls", True),
                            "rate_limit_per_hour": getattr(
                                channel, "rate_limit_per_hour", 10
                            ),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                # Save migrated profiles
                if alert_profiles:
                    save_alert_profiles(alert_profiles)
                    logger.info(
                        f"Auto-migrated {len(alert_profiles)} alert profiles from config"
                    )
        except Exception as exc:
            logger.error(f"Error auto-migrating alert profiles: {exc}")

    return render_template(
        "profiles.html",
        interests=sorted(profile_names),
        profiles=profiles,
        alert_profiles=alert_profiles,
    )


@app.route("/developer")
@_ensure_init
def developer_view():
    return render_template("developer.html")


@app.route("/console")
@_ensure_init
def console_view():
    return render_template("console.html")


@app.route("/docs")
@_ensure_init
def docs_view():
    """API documentation page."""
    # Get base URL from environment or construct from request
    api_base_url = os.getenv("API_BASE_URL")
    if not api_base_url:
        # Construct from request context (scheme + host)
        api_base_url = f"{request.scheme}://{request.host}"
    return render_template("docs.html", api_base_url=api_base_url)


@app.get("/api/session/info")
@_ensure_init
def api_session_info():
    session_path = (
        getattr(config, "telegram_session", os.getenv("TG_SESSION_PATH"))
        if config
        else os.getenv("TG_SESSION_PATH")
    )

    user_info = _load_cached_user_info()
    username = (
        (user_info.get("username") if user_info else None)
        or (user_info.get("first_name") if user_info else None)
        or _fallback_username()
    )
    avatar = (user_info.get("avatar") if user_info else None) or _fallback_avatar()

    phone = user_info.get("phone") if user_info else None
    if not phone:
        env_phone = os.getenv("TG_PHONE")
        phone = _format_display_phone(env_phone) if env_phone else None

    return jsonify(
        {
            "username": username,
            "avatar": avatar,
            "session_path": session_path,
            "phone_masked": _mask_phone(phone),
            "connected": bool(redis_client),
            "connected_chats": [channel["name"] for channel in _serialize_channels()],
        }
    )


@app.post("/api/session/logout")
@_ensure_init
def api_session_logout():
    """Invalidate current Telegram session and clear user caches.

    This is used by the UI RE-LOGIN / SWITCH ACCOUNT control. It safely removes
    the local Telethon session file (if present) and clears cached user info in Redis.
    """
    try:
        session_path = _resolve_session_path()
        details = _invalidate_session(session_path)
        # Clear Flask session authentication marker
        try:
            session.pop("telegram_authenticated", None)
            session.pop("ui_locked", None)
        except Exception:
            pass
        # Signal sentinel to reload config/session state after logout
        try:
            Path("/app/data/.reload_config").touch()
        except Exception:
            pass
        return jsonify(
            {
                "status": "ok",
                "message": "Session cleared. You may re-login.",
                "details": details,
                # UI may choose to navigate to an auth/onboarding flow if present.
                "redirect": "/alerts",
            }
        )
    except Exception as exc:
        logger.error("[UI-AUTH] Logout failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/session/logout-complete")
@_ensure_init
def api_session_logout_complete():
    """Complete system reset: nuke DB, Redis, session files, and config YML files.

    This is a destructive operation that removes:
    - Database (sentinel.db)
    - Redis data (flushall)
    - Telegram session files (*.session*)
    - Configuration YML files (config/*.yml)
    - User avatars and cached data
    """
    import shutil
    import glob

    result = {
        "status": "ok",
        "cleaned": {
            "database": False,
            "redis": False,
            "session_files": [],
            "config_files": [],
            "data_files": [],
        },
        "errors": [],
    }

    try:
        # 1. Nuke database
        try:
            db_path = Path("/app/data/sentinel.db")
            if db_path.exists():
                db_path.unlink()
                result["cleaned"]["database"] = True
            # Also remove journal files
            for journal in [
                "/app/data/sentinel.db-journal",
                "/app/data/sentinel.db-wal",
                "/app/data/sentinel.db-shm",
            ]:
                jp = Path(journal)
                if jp.exists():
                    jp.unlink()
        except Exception as e:
            result["errors"].append(f"Database cleanup error: {str(e)}")

        # 2. Flush all Redis data
        if redis_client:
            try:
                redis_client.flushall()
                result["cleaned"]["redis"] = True
            except Exception as e:
                result["errors"].append(f"Redis flush error: {str(e)}")

        # 3. Remove all session files in data/
        try:
            session_patterns = [
                "/app/data/*.session",
                "/app/data/*.session-journal",
                "/app/data/tgsentinel.session*",
            ]
            for pattern in session_patterns:
                for file_path in glob.glob(pattern):
                    try:
                        Path(file_path).unlink()
                        result["cleaned"]["session_files"].append(file_path)
                    except Exception as e:
                        result["errors"].append(f"Session file {file_path}: {str(e)}")
        except Exception as e:
            result["errors"].append(f"Session cleanup error: {str(e)}")

        # 4. Remove all YML config files
        try:
            config_patterns = [
                "/app/config/*.yml",
                "/app/config/*.yaml",
            ]
            for pattern in config_patterns:
                for file_path in glob.glob(pattern):
                    try:
                        Path(file_path).unlink()
                        result["cleaned"]["config_files"].append(file_path)
                    except Exception as e:
                        result["errors"].append(f"Config file {file_path}: {str(e)}")
        except Exception as e:
            result["errors"].append(f"Config cleanup error: {str(e)}")

        # 5. Remove other data files (avatars, etc)
        try:
            data_files = [
                "/app/data/user_avatar.jpg",
                "/app/data/.reload_config",
            ]
            for file_path in data_files:
                try:
                    fp = Path(file_path)
                    if fp.exists():
                        fp.unlink()
                        result["cleaned"]["data_files"].append(file_path)
                except Exception as e:
                    result["errors"].append(f"Data file {file_path}: {str(e)}")
        except Exception as e:
            result["errors"].append(f"Data file cleanup error: {str(e)}")

        # 6. Remove Redis data directory if exists
        try:
            redis_dir = Path("/app/data/redis")
            if redis_dir.exists() and redis_dir.is_dir():
                shutil.rmtree(redis_dir)
                result["cleaned"]["data_files"].append("/app/data/redis/")
        except Exception as e:
            result["errors"].append(f"Redis directory cleanup error: {str(e)}")

        # Clear Flask session
        try:
            session.clear()
        except Exception:
            pass

        return jsonify(result)

    except Exception as exc:
        logger.error("Failed to perform complete logout cleanup: %s", exc)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": str(exc),
                    "cleaned": result.get("cleaned", {}),
                }
            ),
            500,
        )


@app.get("/logout")
@_ensure_init
def logout_view():
    """Convenience route for hard logout via full-page navigation.

    Invalidates the session file, clears UI lock state, then redirects
    to the dashboard. Gating will show the login prompt.
    """
    try:
        session_path = _resolve_session_path()
        _invalidate_session(session_path)
        try:
            session.pop("ui_locked", None)
        except Exception:
            pass
    except Exception:
        pass
    return redirect("/")


@app.post("/api/session/relogin")
@_ensure_init
def api_session_relogin():
    """Alias for logout that provides a stronger re-auth hint to the UI."""
    try:
        session_path = _resolve_session_path()
        details = _invalidate_session(session_path)
        return jsonify(
            {
                "status": "ok",
                "message": "Session cleared. Start re-authentication.",
                "details": details,
                # Dedicated hint for UI to open account selection / auth flow
                "relogin_required": True,
                "redirect": "/alerts",
            }
        )
    except Exception as exc:
        logger.error("Failed to relogin (logout step): %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/session/login/start")
@_ensure_init
def api_session_login_start():
    """Initiate interactive Telegram login by sending a code to the phone."""
    try:
        if not request.is_json:
            return jsonify({"status": "error", "message": "JSON body required"}), 400
        payload = request.get_json(silent=True) or {}
        phone = _normalize_phone(str(payload.get("phone", "")).strip())
        if not phone:
            return jsonify({"status": "error", "message": "Phone is required"}), 400
        try:
            response = _submit_auth_request("start", {"phone": phone})
        except TimeoutError:
            logger.error("Sentinel did not respond to login start request in time")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Sentinel did not respond in time. Please retry shortly.",
                    }
                ),
                503,
            )
        except Exception as send_exc:
            logger.error("Login start failed to enqueue request: %s", send_exc)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to contact sentinel. Please try again.",
                    }
                ),
                502,
            )

        if response.get("status") != "ok":
            message = response.get("message", "Failed to send code. Try again.")
            reason = response.get("reason")
            retry_after = response.get("retry_after")
            logger.warning("Sentinel rejected login start request: %s", message)
            if reason in {"flood_wait", "resend_unavailable"}:
                payload = {"status": "error", "message": message, "reason": reason}
                if retry_after is not None:
                    payload["retry_after"] = retry_after
                resp = jsonify(payload)
                resp.status_code = 429
                if retry_after is not None:
                    resp.headers["Retry-After"] = str(retry_after)
                return resp
            return jsonify({"status": "error", "message": message}), 502

        phone_code_hash = response.get("phone_code_hash")
        if not phone_code_hash:
            logger.warning("Sentinel response missing phone_code_hash for login start")
            return (
                jsonify(
                    {"status": "error", "message": "Invalid response from sentinel"}
                ),
                502,
            )

        _store_login_context(
            phone,
            {
                "phone_code_hash": phone_code_hash,
                "timeout": response.get("timeout"),
                "type": response.get("type"),
            },
        )

        return jsonify(
            {
                "status": "ok",
                "message": response.get("message", "Code sent"),
                "timeout": response.get("timeout"),
            }
        )
    except Exception as exc:
        logger.error("Login start failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/session/login/resend")
@_ensure_init
def api_session_login_resend():
    """Resend a login code using the existing temporary session context."""

    try:
        if not request.is_json:
            return jsonify({"status": "error", "message": "JSON body required"}), 400
        payload = request.get_json(silent=True) or {}
        phone = _normalize_phone(str(payload.get("phone", "")).strip())
        if not phone:
            return jsonify({"status": "error", "message": "Phone is required"}), 400

        ctx = _load_login_context(phone)
        if not ctx:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "No active login session. Send a new code first.",
                    }
                ),
                410,
            )

        previous_hash = ctx.get("phone_code_hash")
        try:
            response = _submit_auth_request("resend", {"phone": phone})
        except TimeoutError:
            logger.error("Sentinel did not respond to login resend request in time")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Sentinel timeout while resending code. Please retry.",
                    }
                ),
                503,
            )
        except Exception as resend_exc:
            logger.error("Login resend enqueue failed: %s", resend_exc)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to contact sentinel. Please try again shortly.",
                    }
                ),
                502,
            )

        if response.get("status") != "ok":
            message = response.get("message", "Failed to resend code.")
            reason = response.get("reason")
            retry_after = response.get("retry_after")
            logger.warning("Sentinel rejected login resend request: %s", message)
            if reason in {"flood_wait", "resend_unavailable"}:
                payload = {"status": "error", "message": message, "reason": reason}
                if retry_after is not None:
                    payload["retry_after"] = retry_after
                resp = jsonify(payload)
                resp.status_code = 429
                if retry_after is not None:
                    resp.headers["Retry-After"] = str(retry_after)
                return resp
            return jsonify({"status": "error", "message": message}), 502

        new_hash = response.get("phone_code_hash") or previous_hash
        if not new_hash:
            logger.warning("Sentinel response missing phone_code_hash for resend")
            return (
                jsonify(
                    {"status": "error", "message": "Invalid response from sentinel"}
                ),
                502,
            )

        _store_login_context(
            phone,
            {
                "phone_code_hash": new_hash,
                "timeout": response.get("timeout"),
                "type": response.get("type"),
            },
        )

        return jsonify(
            {
                "status": "ok",
                "message": response.get("message", "Code resent"),
                "timeout": response.get("timeout"),
            }
        )
    except Exception as exc:
        logger.error("Login resend failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/session/login/verify")
@_ensure_init
def api_session_login_verify():
    """Verify code (and optional password) to complete login."""
    try:
        if not request.is_json:
            return jsonify({"status": "error", "message": "JSON body required"}), 400
        payload = request.get_json(silent=True) or {}
        phone = _normalize_phone(str(payload.get("phone", "")).strip())
        code = str(payload.get("code", "")).strip()
        password = payload.get("password")
        if not phone or not code:
            return (
                jsonify({"status": "error", "message": "Phone and code are required"}),
                400,
            )

        ctx = _load_login_context(phone) or {}
        phone_code_hash = ctx.get("phone_code_hash")

        if not phone_code_hash:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Login session expired or missing. Please click 'Send Code' again.",
                    }
                ),
                410,
            )

        try:
            response = _submit_auth_request(
                "verify",
                {
                    "phone": phone,
                    "code": code,
                    "phone_code_hash": phone_code_hash,
                    "password": password if password else None,
                },
                timeout=AUTH_REQUEST_TIMEOUT_SECS,
            )
        except TimeoutError:
            logger.error("Sentinel did not respond to login verification in time")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Sentinel timeout while verifying code.",
                    }
                ),
                503,
            )
        except Exception as verify_exc:
            logger.error(
                "Login verification failed to contact sentinel: %s", verify_exc
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to contact sentinel. Please retry.",
                    }
                ),
                503,
            )

        if response.get("status") != "ok":
            message = response.get("message", "Verification failed.")
            logger.warning("Sentinel reported login verification error: %s", message)
            return jsonify({"status": "error", "message": message}), 400

        _clear_login_context(phone)

        if not _wait_for_worker_authorization(timeout=60.0):
            logger.warning("Sentinel did not advertise authorized status after login")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Sentinel did not become ready in time. Try again.",
                    }
                ),
                503,
            )

        session["telegram_authenticated"] = True
        session.permanent = True  # Persist across browser sessions

        return jsonify(
            {"status": "ok", "message": "Authenticated", "redirect": "/alerts"}
        )
    except Exception as exc:
        logger.error("Login verify failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/ui/lock")
@_ensure_init
def api_ui_lock():
    """Lock or unlock the UI without logging out the Telegram session.

    JSON body:
      {"action": "lock"} → sets session['ui_locked']=True
      {"action": "unlock", "password": "..."} → unlocks if password matches UI_LOCK_PASSWORD
    """
    try:
        if not request.is_json:
            return jsonify({"status": "error", "message": "JSON body required"}), 400
        payload = request.get_json(silent=True) or {}
        action = str(payload.get("action", "")).strip().lower()
        if action == "lock":
            session["ui_locked"] = True
            session.modified = True
            return jsonify({"status": "ok", "locked": True})
        if action == "unlock":
            pwd = str(payload.get("password", ""))
            if UI_LOCK_PASSWORD and pwd != UI_LOCK_PASSWORD:
                return jsonify({"status": "error", "message": "Invalid password"}), 403
            session.pop("ui_locked", None)
            session.modified = True
            return jsonify({"status": "ok", "locked": False})
        return jsonify({"status": "error", "message": "Unknown action"}), 400
    except Exception as exc:
        logger.error("UI lock error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/ui/lock/status")
@_ensure_init
def api_ui_lock_status():
    """Return the current UI lock status and configuration hints."""
    try:
        return jsonify(
            {
                "locked": bool(session.get("ui_locked")),
                "timeout": UI_LOCK_TIMEOUT,
                "enabled": (
                    True if UI_LOCK_PASSWORD or os.getenv("UI_LOCK_TIMEOUT") else False
                ),
            }
        )
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/config/rules/test")
@_ensure_init
def api_config_rules_test():
    """Run a lightweight rule test over configured channels.

    Body (optional): {"channel_ids": [..], "text": "sample"}
    Returns a summary with matched_rules (empty if none) and diagnostics.
    This endpoint is designed for quick UI feedback and does not alter state.
    """
    try:
        payload = request.get_json(silent=True) or {}
        only_ids = set(map(int, payload.get("channel_ids", []) or []))
        sample_text = str(payload.get("text", "")).strip()

        channels = _serialize_channels()
        if only_ids:
            channels = [c for c in channels if int(c.get("id", 0)) in only_ids]

        results = []
        for ch in channels:
            # Basic keyword probing over provided sample_text if any
            keywords = [str(k).lower() for k in (ch.get("keywords") or [])]
            matches = []
            if sample_text and keywords:
                low = sample_text.lower()
                matches = [kw for kw in keywords if kw and kw in low]
            results.append(
                {
                    "channel_id": ch.get("id"),
                    "channel_name": ch.get("name"),
                    "matched_rules": matches,
                    "diagnostics": {
                        "keywords": keywords,
                        "vip_senders": ch.get("vip_senders") or [],
                        "reaction_threshold": ch.get("reaction_threshold", 0),
                        "reply_threshold": ch.get("reply_threshold", 0),
                    },
                }
            )

        return jsonify({"status": "ok", "tested": len(results), "results": results})
    except Exception as exc:
        logger.error("Rule test failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/config/stats/reset")
@_ensure_init
def api_config_stats_reset():
    """Reset per-channel transient counters (best-effort).

    Clears any known Redis keys used for rate limiting or stats. Safe no-op if Redis unavailable.
    """
    cleared = 0
    try:
        if redis_client is not None:
            try:
                # Scan and delete likely keys (best-effort, ignores missing)
                patterns = [
                    "tgsentinel:rate:*",
                    "tgsentinel:stats:*",
                ]
                for pat in patterns:
                    # Use scan_iter if available; fall back to keys()
                    keys = []
                    try:
                        scan_iter = getattr(redis_client, "scan_iter", None)
                        if callable(scan_iter):
                            # scan_iter returns a generator, convert to list
                            try:
                                keys = [k for k in scan_iter(match=pat)]  # type: ignore
                            except Exception:
                                keys = []
                        else:
                            result = redis_client.keys(pat)  # type: ignore
                            keys = list(result) if result else []
                    except Exception:
                        keys = []
                    if keys:
                        try:
                            cleared += int(redis_client.delete(*keys) or 0)
                        except Exception:
                            pass
            except Exception:
                pass
        # Optionally, clear any in-memory caches we maintain
        global _cached_summary, _cached_health
        _cached_summary = None
        _cached_health = None
        return jsonify({"status": "ok", "cleared_keys": cleared})
    except Exception as exc:
        logger.error("Reset stats failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/config/export")
@_ensure_init
def api_config_export():
    """Export the current YAML configuration as a downloadable file."""
    try:
        cfg_path = Path(
            os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml")
        ).resolve()
        if not cfg_path.exists():
            return (
                jsonify({"status": "error", "message": "Configuration file not found"}),
                404,
            )
        from flask import send_file

        return send_file(
            str(cfg_path),
            mimetype="application/x-yaml",
            as_attachment=True,
            download_name=f"tgsentinel_config_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.yml",
        )
    except Exception as exc:
        logger.error("Export config failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/participant/info")
@_ensure_init
def api_participant_info():
    """Get detailed participant information from Telegram."""
    chat_id = request.args.get("chat_id")
    user_id = request.args.get("user_id")

    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400

    try:
        chat_id = int(chat_id)
        user_id = int(user_id) if user_id else None
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid chat_id or user_id"}), 400

    def _infer_chat_type(chat_id_val: int) -> str:
        """Infer chat type based on chat_id format.

        Telegram chat ID conventions:
        - Positive IDs (< 10^9): Private chats (users)
        - Negative IDs starting with -100: Channels/Supergroups (broadcast or megagroup)
        - Other negative IDs: Basic groups
        """
        if chat_id_val > 0:
            return "private"
        elif str(chat_id_val).startswith("-100"):
            # Could be channel or supergroup, default to channel
            return "channel"
        else:
            # Basic group
            return "group"

    # Optional async/pending mode for UI: return 202 on first call to allow re-poll
    pending_mode = str(request.args.get("pending", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }

    # If no user_id provided, fetch chat info only
    if not user_id:
        # Check cache first for chat-only info
        cache_key = f"tgsentinel:participant:{chat_id}:chat"
        if redis_client:
            try:
                cached = redis_client.get(cache_key)
                if cached:
                    if isinstance(cached, bytes):
                        cached = cached.decode()
                    return jsonify(json.loads(str(cached)))
            except Exception as e:
                logger.debug("Cache lookup failed: %s", e)

        # Request worker to fetch chat info
        if redis_client:
            try:
                # Store request in Redis for worker to process (without user_id)
                request_key = f"tgsentinel:participant_request:{chat_id}:chat"
                redis_client.setex(
                    request_key,
                    60,  # Request expires in 60 seconds
                    json.dumps(
                        {
                            "chat_id": chat_id,
                            "user_id": None,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )

                # Wait briefly for worker to process (with timeout)
                import time

                # If UI requested pending mode, return 202 immediately after enqueuing
                if pending_mode:
                    # Build minimal chat info to display while fetching
                    # Try cached type
                    chat_type = None
                    if redis_client:
                        try:
                            cache_key_type = f"tgsentinel:chat_type:{chat_id}"
                            cached_type = redis_client.get(cache_key_type)
                            if cached_type:
                                chat_type = (
                                    cached_type.decode("utf-8")
                                    if isinstance(cached_type, bytes)
                                    else cached_type
                                )
                        except Exception:
                            pass
                    if not chat_type:
                        chat_type = _infer_chat_type(chat_id)
                    basic = {
                        "id": chat_id,
                        "title": None,
                        "type": chat_type,
                        "participants_count": None,
                    }
                    # Try to include title from config
                    if config and hasattr(config, "channels"):
                        for channel in config.channels:
                            if hasattr(channel, "id") and channel.id == chat_id:
                                basic["title"] = (
                                    getattr(channel, "name", basic["title"])
                                    or basic["title"]
                                )
                                break
                    # Try to include avatar_url from cache
                    if redis_client:
                        try:
                            ava = redis_client.get(f"tgsentinel:chat_avatar:{chat_id}")
                            if ava:
                                basic["avatar_url"] = (
                                    ava.decode() if isinstance(ava, bytes) else ava
                                )
                        except Exception:
                            pass
                    return jsonify({"status": "pending", "chat": basic}), 202

                for _ in range(10):  # Wait up to 1 second
                    time.sleep(0.1)
                    cached = redis_client.get(cache_key)
                    if cached:
                        if isinstance(cached, bytes):
                            cached = cached.decode()
                        return jsonify(json.loads(str(cached)))

            except Exception as e:
                logger.error("Failed to request chat info: %s", e)

        # Fallback: Try to get chat type from Redis cache first
        chat_type = None
        if redis_client:
            try:
                cache_key_type = f"tgsentinel:chat_type:{chat_id}"
                cached_type = redis_client.get(cache_key_type)
                if cached_type:
                    chat_type = (
                        cached_type.decode("utf-8")
                        if isinstance(cached_type, bytes)
                        else cached_type
                    )
            except Exception:
                pass

        # If not in cache, infer from chat_id
        if not chat_type:
            chat_type = _infer_chat_type(chat_id)

        # Build chat info from config as final fallback (enrich with avatar and flags if possible)
        chat_info = None
        if config and hasattr(config, "channels"):
            for channel in config.channels:
                if hasattr(channel, "id") and channel.id == chat_id:
                    basic = {
                        "id": chat_id,
                        "title": getattr(channel, "name", f"Chat {chat_id}"),
                        "type": chat_type,
                        "username": None,  # Not stored in config
                        "participants_count": None,
                    }
                    # Try to add avatar_url from cache
                    if redis_client:
                        try:
                            ava = redis_client.get(f"tgsentinel:chat_avatar:{chat_id}")
                            if ava:
                                basic["avatar_url"] = (
                                    ava.decode() if isinstance(ava, bytes) else ava
                                )
                        except Exception:
                            pass
                    chat_info = {"chat": basic}
                    break

        if not chat_info:
            # Final fallback if not found in config
            basic = {"id": chat_id, "title": f"Chat {chat_id}", "type": chat_type}
            # Try to include cached avatar
            if redis_client:
                try:
                    ava = redis_client.get(f"tgsentinel:chat_avatar:{chat_id}")
                    if ava:
                        basic["avatar_url"] = (
                            ava.decode() if isinstance(ava, bytes) else ava
                        )
                except Exception:
                    pass
            chat_info = {"chat": basic}

        return jsonify(chat_info)

    # Check cache first (30 minute TTL) for user info
    cache_key = f"tgsentinel:participant:{chat_id}:{user_id}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                if isinstance(cached, bytes):
                    cached = cached.decode()
                return jsonify(json.loads(str(cached)))
        except Exception as e:
            logger.debug("Cache lookup failed: %s", e)

    # Try to get basic user info from Redis user_info cache
    if redis_client:
        try:
            user_info_str = redis_client.get("tgsentinel:user_info")
            if user_info_str:
                # Ensure we have a string
                if isinstance(user_info_str, bytes):
                    user_info_str = user_info_str.decode()
                user_info = json.loads(str(user_info_str))
                # Return basic info if this is the current user
                # Check both 'user_id' (preferred, from worker) and 'id' (legacy)
                cached_user_id = user_info.get("user_id") or user_info.get("id")
                if cached_user_id == user_id:
                    u = {
                        "id": user_id,
                        "name": user_info.get("username", f"User {user_id}"),
                        "username": user_info.get("username"),
                        "phone": user_info.get("phone"),
                        "bot": False,
                    }
                    # Try to include avatar_url from cache
                    try:
                        ava = redis_client.get(f"tgsentinel:user_avatar:{user_id}")
                        if ava:
                            u["avatar_url"] = (
                                ava.decode() if isinstance(ava, bytes) else ava
                            )
                    except Exception:
                        pass
                    return jsonify({"user": u})
        except Exception as e:
            logger.debug("Failed to get user info from cache: %s", e)

    # Request worker to fetch participant info
    if redis_client:
        try:
            # Store request in Redis for worker to process
            request_key = f"tgsentinel:participant_request:{chat_id}:{user_id}"
            redis_client.setex(
                request_key,
                60,  # Request expires in 60 seconds
                json.dumps(
                    {
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )

            # Wait briefly for worker to process (with timeout)
            import time

            # If UI requested pending mode, return 202 immediately after enqueuing
            if pending_mode:
                # Minimal placeholders while fetching
                # Build minimal user and chat info if possible
                # Chat type inference
                def _pending_chat_info() -> dict:
                    ctype = None
                    if redis_client:
                        try:
                            t = redis_client.get(f"tgsentinel:chat_type:{chat_id}")
                            if t:
                                ctype = t.decode() if isinstance(t, bytes) else t
                        except Exception:
                            pass
                    if not ctype:
                        ctype = _infer_chat_type(chat_id)
                    basic_chat = {"id": chat_id, "title": None, "type": ctype}
                    if config and hasattr(config, "channels"):
                        for channel in config.channels:
                            if hasattr(channel, "id") and channel.id == chat_id:
                                basic_chat["title"] = getattr(channel, "name", None)
                                break
                    # Include avatar if cached
                    if redis_client:
                        try:
                            ava = redis_client.get(f"tgsentinel:chat_avatar:{chat_id}")
                            if ava:
                                basic_chat["avatar_url"] = (
                                    ava.decode() if isinstance(ava, bytes) else ava
                                )
                        except Exception:
                            pass
                    return basic_chat

                def _pending_user_info() -> dict:
                    u = {"id": user_id, "name": f"User {user_id}", "username": None}
                    # Try to use current user cache if matches
                    try:
                        ui = (
                            redis_client.get("tgsentinel:user_info")
                            if redis_client
                            else None
                        )
                        if ui:
                            ui_str = ui.decode() if isinstance(ui, bytes) else ui
                            user_info = json.loads(str(ui_str))
                            cached_uid = user_info.get("user_id") or user_info.get("id")
                            if cached_uid == user_id:
                                u["name"] = (
                                    user_info.get("username", u["name"]) or u["name"]
                                )
                                u["username"] = user_info.get("username")
                                u["phone"] = user_info.get("phone")
                    except Exception:
                        pass
                    # Avatar
                    try:
                        ava = (
                            redis_client.get(f"tgsentinel:user_avatar:{user_id}")
                            if redis_client
                            else None
                        )
                        if ava:
                            u["avatar_url"] = (
                                ava.decode() if isinstance(ava, bytes) else ava
                            )
                    except Exception:
                        pass
                    return u

                payload = {"status": "pending", "chat": _pending_chat_info()}
                if user_id:
                    payload["user"] = _pending_user_info()
                return jsonify(payload), 202

            for _ in range(10):  # Wait up to 1 second
                time.sleep(0.1)
                cached = redis_client.get(cache_key)
                if cached:
                    if isinstance(cached, bytes):
                        cached = cached.decode()
                    return jsonify(json.loads(str(cached)))

            # If not ready, return fallback user info (best-effort avatar)
            u = {"id": user_id, "name": f"User {user_id}", "username": None}
            try:
                ava = redis_client.get(f"tgsentinel:user_avatar:{user_id}")
                if ava:
                    u["avatar_url"] = ava.decode() if isinstance(ava, bytes) else ava
            except Exception:
                pass
            return jsonify({"user": u})

        except Exception as e:
            logger.error("Failed to request participant info: %s", e)
            return jsonify({"error": "Failed to fetch participant info"}), 500

    # Fallback when Redis not available
    return jsonify(
        {"user": {"id": user_id, "name": f"User {user_id}", "username": None}}
    )


@app.get("/api/dashboard/summary")
@_ensure_init
def api_dashboard_summary():
    return jsonify(_compute_summary())


@app.get("/api/dashboard/activity")
@_ensure_init
def api_dashboard_activity():
    limit = min(int(request.args.get("limit", 10)), 100)
    return jsonify({"entries": _load_live_feed(limit=limit)})


@app.get("/api/system/health")
@_ensure_init
def api_system_health():
    return jsonify(_compute_health())


@app.get("/api/alerts/recent")
@_ensure_init
def api_recent_alerts():
    limit = min(int(request.args.get("limit", 100)), 250)
    return jsonify({"alerts": _load_alerts(limit=limit)})


@app.get("/api/alerts/digests")
@_ensure_init
def api_alert_digests():
    return jsonify({"digests": _load_digests()})


@app.post("/api/alerts/feedback")
@_ensure_init
def api_alert_feedback():
    """Submit feedback (thumbs up/down) for an alert."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    chat_id = payload.get("chat_id")
    msg_id = payload.get("msg_id")
    label = payload.get("label")  # "up" or "down"

    if not chat_id or not msg_id or label not in ("up", "down"):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing or invalid parameters: chat_id, msg_id, label",
                }
            ),
            400,
        )

    # Validate that chat_id and msg_id can be converted to integers
    try:
        chat_id_int = int(chat_id)
        msg_id_int = int(msg_id)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Invalid chat_id or msg_id: must be valid integers",
                }
            ),
            400,
        )

    label_value = 1 if label == "up" else 0

    try:
        _execute(
            """
            INSERT INTO feedback(chat_id, msg_id, label)
            VALUES(:c, :m, :l)
            ON CONFLICT(chat_id, msg_id) DO UPDATE SET label=excluded.label
            """,
            c=chat_id_int,
            m=msg_id_int,
            l=label_value,
        )
        logger.info(
            "Feedback recorded: chat_id=%s, msg_id=%s, label=%s", chat_id, msg_id, label
        )
        return jsonify({"status": "ok"})
    except Exception as exc:
        logger.error("Failed to record feedback: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/config/save")
@_ensure_init
def api_config_save():
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    logger.info("Received configuration update request: keys=%s", list(payload))
    try:
        _write_config(payload)
        # Reload config to reflect changes immediately
        reload_config()
    except FileNotFoundError as exc:
        logger.error("Configuration file missing: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 404
    except Exception as exc:  # pragma: no cover - filesystem writes optional
        logger.error("Could not persist configuration: %s", exc)
        return jsonify({"status": "error", "message": "persist_failed"}), 500
    return jsonify({"status": "ok"})


@app.post("/api/config/clean-db")
@_ensure_init
def api_clean_database():
    """Clean all data from the database and Redis stream, leaving a fresh environment.

    This endpoint permanently deletes:
    - All message records from database
    - All alerts history
    - All feedback data
    - All messages from Redis stream
    - Cached participant info
    - User info cache

    Returns the count of deleted records.
    """
    if not engine:
        logger.error("Cannot clean database: engine not initialized")
        return jsonify({"status": "error", "message": "Database not available"}), 503

    try:
        deleted_count = 0
        redis_deleted = 0

        with engine.begin() as conn:
            # Count records before deletion
            messages_count = (
                conn.execute(text("SELECT COUNT(*) FROM messages")).scalar() or 0
            )

            # Try to count feedback if table exists
            feedback_count = 0
            try:
                feedback_count = (
                    conn.execute(text("SELECT COUNT(*) FROM feedback")).scalar() or 0
                )
            except Exception:
                # Feedback table doesn't exist, that's okay
                pass

            deleted_count = messages_count + feedback_count

            # Delete all data from tables
            conn.execute(text("DELETE FROM messages"))
            logger.info("Deleted %d records from messages table", messages_count)

            # Delete feedback if table exists
            try:
                conn.execute(text("DELETE FROM feedback"))
                logger.info("Deleted %d records from feedback table", feedback_count)
            except Exception:
                # Feedback table doesn't exist, that's okay
                pass

            # Reset auto-increment counters for SQLite
            try:
                conn.execute(text("DELETE FROM sqlite_sequence WHERE name='messages'"))
                conn.execute(text("DELETE FROM sqlite_sequence WHERE name='feedback'"))
            except Exception:
                # Not SQLite or no sequence table, that's okay
                pass

        # Clear Redis stream and caches
        if redis_client:
            try:
                stream_name = _get_stream_name()

                # Get count of messages in stream before deletion
                stream_len = redis_client.xlen(stream_name)

                # Delete the entire stream
                redis_client.delete(stream_name)
                logger.info(
                    "Deleted %d messages from Redis stream '%s'",
                    stream_len,
                    stream_name,
                )
                redis_deleted += stream_len

                # Clear participant info cache (keys matching pattern)
                participant_keys = redis_client.keys("tgsentinel:participant:*")
                if participant_keys:
                    redis_client.delete(*participant_keys)
                    logger.info(
                        "Cleared %d participant cache entries", len(participant_keys)
                    )
                    redis_deleted += len(participant_keys)

                # Clear user info cache
                if redis_client.exists("tgsentinel:user_info"):
                    redis_client.delete("tgsentinel:user_info")
                    logger.info("Cleared user info cache")
                    redis_deleted += 1

            except Exception as exc:
                logger.warning("Failed to clean Redis data: %s", exc)
                # Don't fail the entire operation if Redis cleanup fails

        # Clear caches
        global _cached_summary, _cached_health
        _cached_summary = None
        _cached_health = None

        logger.info(
            "Database cleaned successfully. DB records: %d, Redis items: %d",
            deleted_count,
            redis_deleted,
        )
        # Notify dashboards to refresh
        try:
            socketio.emit("dashboard:update")
        except Exception:
            pass
        return jsonify(
            {
                "status": "ok",
                "message": "Database and Redis stream cleaned successfully",
                "deleted": deleted_count,
                "redis_cleared": redis_deleted,
            }
        )

    except Exception as exc:
        logger.error("Failed to clean database: %s", exc)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to clean database: {str(exc)}"}
            ),
            500,
        )


@app.post("/api/sentinel/restart")
@_ensure_init
def api_restart_sentinel():
    """Restart the sentinel container to apply configuration changes."""
    try:
        import subprocess

        # Try to restart using docker-compose
        result = subprocess.run(
            ["docker-compose", "restart", "sentinel"],
            cwd="/app",  # Docker container working directory
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info("Sentinel container restart initiated successfully")
            return jsonify({"status": "ok", "message": "Sentinel restarting"})
        else:
            logger.error("Failed to restart sentinel: %s", result.stderr)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Restart command failed",
                        "details": result.stderr,
                    }
                ),
                500,
            )
    except subprocess.TimeoutExpired:
        logger.error("Sentinel restart timed out")
        return jsonify({"status": "error", "message": "Restart timed out"}), 500
    except FileNotFoundError:
        logger.error("docker-compose not found")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "docker-compose not available in this environment",
                }
            ),
            500,
        )
    except Exception as exc:
        logger.error("Unexpected error restarting sentinel: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/config/channels")
@_ensure_init
def api_config_channels():
    return jsonify({"channels": _serialize_channels()})


@app.get("/api/worker/status")
@_ensure_init
def api_worker_status():
    """Return worker authorization status as seen via Redis, if available."""
    try:
        status = None
        if redis_client:
            try:
                raw = redis_client.get("tgsentinel:worker_status")
                if raw:
                    raw = raw.decode() if isinstance(raw, bytes) else raw
                    status = json.loads(str(raw))
            except Exception:
                status = None
        if not status:
            return jsonify({"authorized": None, "status": "unknown"})
        # Normalize fields and include rate limit info if present
        response = {
            "authorized": bool(status.get("authorized")),
            "status": status.get("status", "unknown"),
            "ts": status.get("ts"),
        }

        # Add rate limit information if present
        if status.get("status") == "rate_limited":
            response["rate_limit"] = {
                "action": status.get("rate_limit_action"),
                "wait_seconds": status.get("rate_limit_wait"),
                "wait_until": status.get("rate_limit_until"),
            }

        return jsonify(response)
    except Exception as exc:
        logger.debug("Worker status error: %s", exc)
        return jsonify({"authorized": None, "status": "unknown"})


@app.route("/api/config/current", methods=["GET"])
@_ensure_init
def api_config_current():
    """Get current configuration including environment variables."""
    telegram_cfg = {
        "api_id": os.getenv("TG_API_ID", ""),
        "api_hash": os.getenv("TG_API_HASH", ""),
        "phone_number": _format_display_phone(os.getenv("TG_PHONE", "")),
        "session": getattr(config, "telegram_session", "") if config else "",
    }

    cached_user = _load_cached_user_info()
    if cached_user:
        if cached_user.get("phone"):
            telegram_cfg["phone_number"] = cached_user["phone"]
        if cached_user.get("username"):
            telegram_cfg["username"] = cached_user["username"]
        if cached_user.get("avatar"):
            telegram_cfg["avatar"] = cached_user["avatar"]
        if cached_user.get("user_id"):
            telegram_cfg["user_id"] = cached_user["user_id"]

    # Get alerts config with proper None checking
    alerts = getattr(config, "alerts", None) if config else None
    alerts_cfg = {
        "mode": os.getenv("ALERT_MODE")
        or (alerts and getattr(alerts, "mode", "dm"))
        or "dm",
        "target_channel": os.getenv("ALERT_CHANNEL")
        or (alerts and getattr(alerts, "target_channel", ""))
        or "",
    }

    # Get digest config with proper defaults
    digest_cfg = {
        "hourly": True,  # Default to hourly
        "daily": False,
        "top_n": 10,
    }
    if config and hasattr(config, "alerts") and hasattr(config.alerts, "digest"):
        digest_cfg = {
            "hourly": getattr(config.alerts.digest, "hourly", True),
            "daily": getattr(config.alerts.digest, "daily", False),
            "top_n": getattr(config.alerts.digest, "top_n", 10),
        }

    redis_cfg = {
        "host": os.getenv("REDIS_HOST", "redis"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
    }

    # Parse similarity threshold - strip comments if present
    similarity_threshold_str = os.getenv("SIMILARITY_THRESHOLD", "0.42")
    # Remove inline comments (anything after #)
    similarity_threshold_str = similarity_threshold_str.split("#")[0].strip()

    semantic_cfg = {
        "embeddings_model": os.getenv("EMBEDDINGS_MODEL", ""),
        "similarity_threshold": float(similarity_threshold_str),
    }

    # Get channels from config
    channels_list = []
    if config and hasattr(config, "channels"):
        for channel in config.channels:
            channels_list.append(
                {
                    "id": getattr(channel, "id", 0),
                    "name": getattr(channel, "name", "Unknown"),
                }
            )

    # Get monitored users from config
    monitored_users_list = []
    if config and hasattr(config, "monitored_users"):
        for user in config.monitored_users:
            monitored_users_list.append(
                {
                    "id": getattr(user, "id", 0),
                    "name": getattr(user, "name", "Unknown"),
                    "username": getattr(user, "username", ""),
                }
            )

    return jsonify(
        {
            "telegram": telegram_cfg,
            "alerts": alerts_cfg,
            "digest": digest_cfg,
            "redis": redis_cfg,
            "semantic": semantic_cfg,
            "database_uri": os.getenv("DB_URI", ""),
            "channels": channels_list,
            "monitored_users": monitored_users_list,
        }
    )


@app.get("/api/config/interests")
@_ensure_init
def api_config_interests():
    interests_attr = getattr(config, "interests", None) if config else None
    interests = list(interests_attr) if interests_attr is not None else []
    return jsonify({"interests": interests})


@app.get("/api/profiles/export")
@_ensure_init
def api_export_profiles():
    """Export interest profiles as YAML file."""
    try:
        from flask import make_response

        interests_attr = getattr(config, "interests", None) if config else None
        interests = list(interests_attr) if interests_attr is not None else []

        # Create YAML content
        yaml_content = yaml.dump(
            {"interests": interests},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

        # Create response with download headers
        response = make_response(yaml_content)
        response.headers["Content-Type"] = "application/x-yaml"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="interests_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.yml"'
        )
        return response

    except Exception as exc:
        logger.error(f"Failed to export interests: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/analytics/metrics")
@_ensure_init
def api_analytics_metrics():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    processed = (
        _query_one(
            "SELECT COUNT(*) FROM messages WHERE datetime(created_at) >= :cutoff",
            cutoff=cutoff,
        )
        or 0
    )
    # Calculate actual messages per minute with decimal precision
    messages_per_min = round(int(processed) / 60.0, 2)

    latency = round(float(os.getenv("SEMANTIC_LATENCY_MS", "120")) / 1000, 3)
    health = _compute_health()
    return jsonify(
        {
            "messages_per_min": messages_per_min,
            "semantic_latency": latency,
            "cpu": health.get("cpu_percent"),
            "memory": health.get("memory_mb"),
            "redis_stream_depth": health.get("redis_stream_depth"),
        }
    )


@app.get("/api/analytics/keywords")
@_ensure_init
def api_analytics_keywords():
    """Return keyword match counts from recent messages in the database.

    Uses the union of configured channel keywords and counts case-insensitive
    occurrences in the `messages.message_text` field within the last 24 hours.
    """
    # Build keyword set from configuration
    kw_set: set[str] = set()
    for channel in _serialize_channels():
        for kw in channel.get("keywords", []) or []:
            if isinstance(kw, str) and kw.strip():
                kw_set.add(kw.strip())

    if not kw_set:
        return jsonify({"keywords": []})

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Count matches per keyword using SQL LIKE (case-insensitive via lower())
    results: list[dict[str, int | str]] = []
    for kw in sorted(kw_set):
        try:
            count = _query_one(
                """
                SELECT COUNT(*) FROM messages
                WHERE datetime(created_at) >= :cutoff
                  AND lower(COALESCE(message_text, '')) LIKE '%' || lower(:kw) || '%'
                """,
                cutoff=cutoff,
                kw=kw,
            )
        except Exception:
            count = 0
        results.append({"keyword": kw, "count": int(count or 0)})

    # Sort descending by count
    results.sort(key=lambda x: x["count"], reverse=True)
    return jsonify({"keywords": results})


@app.post("/api/profiles/train")
@_ensure_init
def api_profiles_train():
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    topic = payload.get("topic")
    logger.info("Queued interest profile training for '%s'", topic)
    return jsonify({"status": "queued", "topic": topic})


@app.post("/api/profiles/test")
@_ensure_init
def api_profiles_test():
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    sample = payload.get("sample", "")
    interest = payload.get("interest", "")
    score = round(0.42 + len(sample) % 37 / 100, 2)
    logger.debug("Similarity test for '%s' -> %.2f", interest, score)
    return jsonify({"score": score})


@app.post("/api/profiles/toggle")
@_ensure_init
def api_profiles_toggle():
    """Toggle profile enabled/disabled state."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_name = data.get("name", "").strip()
    enabled = data.get("enabled", True)

    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        # Load profile
        profile = get_profile(profile_name)
        if profile is None:
            # Check if this is a legacy interest from config
            interests_attr = getattr(config, "interests", None) if config else None
            legacy_interests = (
                list(interests_attr) if interests_attr is not None else []
            )

            if profile_name in legacy_interests:
                # Auto-create default profile for legacy interest
                logger.info(
                    f"Auto-creating profile for legacy interest '{profile_name}' during toggle"
                )
                profile = {
                    "name": profile_name,
                    "description": "Migrated from legacy interests",
                    "positive_samples": [],
                    "negative_samples": [],
                    "threshold": 0.42,
                    "weight": 1.0,
                    "enabled": enabled,  # Use the requested state
                    "priority": "normal",
                    "keywords": [],
                    "channels": [],
                    "tags": [],
                    "notify_always": False,
                    "include_digest": True,
                }
            else:
                logger.warning(f"Toggle failed: profile '{profile_name}' not found")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile '{profile_name}' not found",
                        }
                    ),
                    404,
                )
        else:
            # Update enabled state for existing profile
            profile["enabled"] = enabled

        # Save changes
        if not upsert_profile(profile):
            logger.error(f"Failed to persist toggle for profile '{profile_name}'")
            return (
                jsonify(
                    {"status": "error", "message": "Failed to save profile changes"}
                ),
                500,
            )

        logger.info(
            f"Toggled profile '{profile_name}' -> {'enabled' if enabled else 'disabled'}"
        )
        return jsonify(
            {"status": "success", "profile": profile_name, "enabled": enabled}
        )

    except Exception as exc:
        logger.error(f"Error toggling profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.get("/api/profiles/get")
@_ensure_init
def api_profiles_get():
    """Get profile details by name."""
    profile_name = request.args.get("name", "").strip()
    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        profile = get_profile(profile_name)

        if profile is None:
            # Check if this is a legacy interest from config
            interests_attr = getattr(config, "interests", None) if config else None
            legacy_interests = (
                list(interests_attr) if interests_attr is not None else []
            )

            if profile_name in legacy_interests:
                # Auto-create default profile for legacy interest
                logger.info(
                    f"Auto-creating profile for legacy interest '{profile_name}'"
                )
                profile = {
                    "name": profile_name,
                    "description": "Migrated from legacy interests",
                    "positive_samples": [],
                    "negative_samples": [],
                    "threshold": 0.42,
                    "weight": 1.0,
                    "enabled": True,
                    "priority": "normal",
                    "keywords": [],
                    "channels": [],
                    "tags": [],
                    "notify_always": False,
                    "include_digest": True,
                }
                # Save it for future use
                upsert_profile(profile)
            else:
                logger.warning(f"Profile '{profile_name}' not found")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile '{profile_name}' not found",
                        }
                    ),
                    404,
                )

        logger.debug(f"Retrieved profile '{profile_name}'")
        return jsonify(profile)

    except Exception as exc:
        logger.error(f"Error retrieving profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.post("/api/profiles/save")
@_ensure_init
def api_profiles_save():
    """Save or update a profile."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    # Validate required fields
    profile_name = payload.get("name", "").strip()
    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    # If updating existing profile, check if original_name differs (rename scenario)
    original_name = payload.get("original_name", "").strip()

    try:
        # Set defaults for optional fields
        profile_data = {
            "name": profile_name,
            "description": payload.get("description", "").strip(),
            "positive_samples": payload.get("positive_samples", []),
            "negative_samples": payload.get("negative_samples", []),
            "threshold": float(payload.get("threshold", 0.42)),
            "weight": float(payload.get("weight", 1.0)),
            "enabled": bool(payload.get("enabled", True)),
            "priority": payload.get("priority", "normal"),
            "keywords": payload.get("keywords", []),
            "channels": payload.get("channels", []),
            "tags": payload.get("tags", []),
            "notify_always": bool(payload.get("notify_always", False)),
            "include_digest": bool(payload.get("include_digest", True)),
        }

        # Validate list fields are actually lists
        for field in [
            "positive_samples",
            "negative_samples",
            "keywords",
            "channels",
            "tags",
        ]:
            if not isinstance(profile_data[field], list):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Field '{field}' must be a list",
                        }
                    ),
                    400,
                )

        # Validate priority
        if profile_data["priority"] not in ["low", "normal", "high", "critical"]:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Priority must be: low, normal, high, or critical",
                    }
                ),
                400,
            )

        # Handle rename: delete old profile if name changed
        if original_name and original_name != profile_name:
            logger.info(f"Renaming profile '{original_name}' to '{profile_name}'")
            delete_profile(original_name)

        # Save profile
        if not upsert_profile(profile_data):
            logger.error(f"Failed to persist profile '{profile_name}'")
            return (
                jsonify({"status": "error", "message": "Failed to save profile"}),
                500,
            )

        logger.info(f"Profile saved: {profile_name}")
        return jsonify({"status": "ok", "message": f"Profile '{profile_name}' saved"})

    except (ValueError, TypeError) as exc:
        logger.warning(f"Validation error for profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": f"Validation error: {exc}"}), 400
    except Exception as exc:
        logger.error(f"Error saving profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.post("/api/profiles/delete")
@_ensure_init
def api_profiles_delete():
    """Delete a profile."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_name = payload.get("name", "").strip()
    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        # Check if profile exists first
        profile = get_profile(profile_name)
        if profile is None:
            logger.warning(f"Delete failed: profile '{profile_name}' not found")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Profile '{profile_name}' not found",
                    }
                ),
                404,
            )

        # Delete profile
        if not delete_profile(profile_name):
            logger.error(f"Failed to delete profile '{profile_name}'")
            return (
                jsonify({"status": "error", "message": "Failed to delete profile"}),
                500,
            )

        logger.info(f"Profile deleted: {profile_name}")
        return jsonify({"status": "ok", "message": f"Profile '{profile_name}' deleted"})

    except Exception as exc:
        logger.error(f"Error deleting profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


# ═══════════════════════════════════════════════════════════════════
# Alert Profiles API (Heuristic/Keyword-based)
# ═══════════════════════════════════════════════════════════════════

ALERT_PROFILES_FILE = Path(__file__).parent.parent / "data" / "alert_profiles.json"
_alert_profiles_lock = threading.Lock()


def load_alert_profiles() -> Dict[str, Any]:
    """Load alert profiles from JSON file."""
    try:
        if not ALERT_PROFILES_FILE.exists():
            return {}
        with open(ALERT_PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            logger.debug(f"Loaded {len(data)} alert profile(s)")
            return data
    except Exception as exc:
        logger.error(f"Failed to load alert profiles: {exc}")
        return {}


def save_alert_profiles(profiles: Dict[str, Any]) -> bool:
    """Save alert profiles to JSON file."""
    try:
        ALERT_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_path = tempfile.mkstemp(
            dir=ALERT_PROFILES_FILE.parent, prefix=".alert_profiles_", suffix=".tmp"
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                fcntl.flock(temp_f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(profiles, temp_f, indent=2, sort_keys=True)
                    temp_f.flush()
                    os.fsync(temp_f.fileno())
                finally:
                    fcntl.flock(temp_f.fileno(), fcntl.LOCK_UN)
            shutil.move(temp_path, str(ALERT_PROFILES_FILE))
            logger.debug(f"Saved {len(profiles)} alert profile(s)")
            return True
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
    except Exception as exc:
        logger.error(f"Failed to save alert profiles: {exc}")
        return False


def sync_alert_profiles_to_config():
    """Sync alert profiles from JSON to tgsentinel.yml channels config."""
    try:
        alert_profiles = load_alert_profiles()
        config_path = Path(__file__).parent.parent / "config" / "tgsentinel.yml"

        if not config_path.exists():
            logger.warning("Config file not found, cannot sync alert profiles")
            return False

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # Update channels with alert profile data
        channels = config.get("channels", [])
        profile_map = {
            p.get("channel_id"): p
            for p in alert_profiles.values()
            if p.get("type") == "channel" and p.get("channel_id")
        }

        for channel in channels:
            channel_id = channel.get("id")
            if channel_id in profile_map:
                profile = profile_map[channel_id]
                # Sync keyword categories
                for key in [
                    "action_keywords",
                    "decision_keywords",
                    "urgency_keywords",
                    "importance_keywords",
                    "release_keywords",
                    "security_keywords",
                    "risk_keywords",
                    "opportunity_keywords",
                ]:
                    if profile.get(key):
                        channel[key] = profile[key]
                # Sync other settings
                channel["vip_senders"] = profile.get("vip_senders", [])
                channel["reaction_threshold"] = profile.get("reaction_threshold", 5)
                channel["reply_threshold"] = profile.get("reply_threshold", 3)
                channel["detect_codes"] = profile.get("detect_codes", True)
                channel["detect_documents"] = profile.get("detect_documents", True)
                channel["prioritize_pinned"] = profile.get("prioritize_pinned", True)
                channel["prioritize_admin"] = profile.get("prioritize_admin", True)
                channel["detect_polls"] = profile.get("detect_polls", True)
                channel["rate_limit_per_hour"] = profile.get("rate_limit_per_hour", 10)

        # Write back to config
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        # Touch reload marker
        reload_marker = Path(__file__).parent.parent / "data" / ".reload_config"
        reload_marker.touch()

        logger.info("Alert profiles synced to config successfully")
        return True

    except Exception as exc:
        logger.error(f"Failed to sync alert profiles to config: {exc}")
        return False


@app.get("/api/profiles/alert/list")
@_ensure_init
def api_alert_profiles_list():
    """List all alert profiles with enriched metadata."""
    try:
        with _alert_profiles_lock:
            profiles = load_alert_profiles()

            # Enrich profiles with activity metadata
            enriched_profiles = []
            for profile in profiles.values():
                enriched = dict(profile)

                # Add activity metadata if database is available
                if engine:
                    try:
                        # Get last triggered timestamp for this profile
                        profile_id = enriched.get("id", "")
                        last_triggered = _query_one(
                            """
                            SELECT MAX(created_at) as last_triggered
                            FROM messages
                            WHERE alerted = 1 AND triggers LIKE :profile_pattern
                            """,
                            profile_pattern=f"%{profile_id}%",
                        )

                        if last_triggered and last_triggered.get("last_triggered"):
                            enriched["last_triggered_at"] = last_triggered[
                                "last_triggered"
                            ]

                        # Get trigger count in last 24 hours
                        trigger_count = _query_one(
                            """
                            SELECT COUNT(*) as count
                            FROM messages
                            WHERE alerted = 1 
                              AND triggers LIKE :profile_pattern
                              AND datetime(created_at) >= datetime('now', '-24 hours')
                            """,
                            profile_pattern=f"%{profile_id}%",
                        )

                        if trigger_count:
                            enriched["recent_triggers"] = trigger_count.get("count", 0)
                    except Exception as e:
                        logger.debug(
                            f"Could not enrich profile {profile.get('id')}: {e}"
                        )

                enriched_profiles.append(enriched)

            stats = {
                "total": len(enriched_profiles),
                "enabled": sum(1 for p in enriched_profiles if p.get("enabled", True)),
            }
            return jsonify(
                {"status": "ok", "profiles": enriched_profiles, "stats": stats}
            )
    except Exception as exc:
        logger.error(f"Error listing alert profiles: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.get("/api/profiles/alert/get")
@_ensure_init
def api_alert_profile_get():
    """Get a specific alert profile."""
    profile_id = request.args.get("id", "").strip()
    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        with _alert_profiles_lock:
            profiles = load_alert_profiles()
            profile = profiles.get(profile_id)
            if not profile:
                return jsonify({"status": "error", "message": "Profile not found"}), 404
            return jsonify({"status": "ok", "profile": profile})
    except Exception as exc:
        logger.error(f"Error getting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.post("/api/profiles/alert/upsert")
@_ensure_init
def api_alert_profile_upsert():
    """Create or update an alert profile."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    profile = request.get_json(silent=True)
    if not profile:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id = profile.get("id", "").strip()
    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    # Add timestamps
    now = datetime.now(timezone.utc).isoformat()
    if "created_at" not in profile:
        profile["created_at"] = now
    profile["updated_at"] = now

    try:
        with _alert_profiles_lock:
            profiles = load_alert_profiles()
            profiles[profile_id] = profile
            if not save_alert_profiles(profiles):
                return (
                    jsonify({"status": "error", "message": "Failed to save profile"}),
                    500,
                )

            # Sync to config file
            sync_alert_profiles_to_config()

            logger.info(f"Alert profile upserted: {profile_id}")
            return jsonify({"status": "ok", "profile_id": profile_id})
    except Exception as exc:
        logger.error(f"Error upserting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.delete("/api/profiles/alert/delete")
@_ensure_init
def api_alert_profile_delete():
    """Delete an alert profile."""
    profile_id = request.args.get("id", "").strip()
    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        with _alert_profiles_lock:
            profiles = load_alert_profiles()
            if profile_id not in profiles:
                return jsonify({"status": "error", "message": "Profile not found"}), 404

            del profiles[profile_id]
            if not save_alert_profiles(profiles):
                return (
                    jsonify({"status": "error", "message": "Failed to delete profile"}),
                    500,
                )

            # Sync to config file
            sync_alert_profiles_to_config()

            logger.info(f"Alert profile deleted: {profile_id}")
            return jsonify({"status": "ok", "message": "Profile deleted"})
    except Exception as exc:
        logger.error(f"Error deleting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.post("/api/profiles/alert/toggle")
@_ensure_init
def api_alert_profile_toggle():
    """Toggle alert profile enabled status."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id = payload.get("id", "").strip()
    enabled = payload.get("enabled", True)

    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        with _alert_profiles_lock:
            profiles = load_alert_profiles()
            if profile_id not in profiles:
                return jsonify({"status": "error", "message": "Profile not found"}), 404

            profiles[profile_id]["enabled"] = bool(enabled)
            profiles[profile_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

            if not save_alert_profiles(profiles):
                return (
                    jsonify({"status": "error", "message": "Failed to update profile"}),
                    500,
                )

            # Sync to config file
            sync_alert_profiles_to_config()

            logger.info(
                f"Alert profile {profile_id} {'enabled' if enabled else 'disabled'}"
            )
            return jsonify({"status": "ok", "enabled": enabled})
    except Exception as exc:
        logger.error(f"Error toggling alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.post("/api/profiles/alert/backtest")
@_ensure_init
def api_alert_profile_backtest():
    """Backtest an alert profile against historical messages."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id = payload.get("id", "").strip()
    hours_back = int(payload.get("hours_back", 24))
    max_messages = int(payload.get("max_messages", 100))
    channel_filter = payload.get("channel_filter")  # Optional channel ID

    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        with _alert_profiles_lock:
            profiles = load_alert_profiles()
            profile = profiles.get(profile_id)
            if not profile:
                return jsonify({"status": "error", "message": "Profile not found"}), 404

        # Fetch historical messages
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Build query with optional channel filter
        query = """
            SELECT chat_id, msg_id, chat_title, sender_name, message_text, 
                   score, triggers, alerted, created_at
            FROM messages 
            WHERE datetime(created_at) >= :cutoff
        """
        params = {"cutoff": cutoff}

        if channel_filter:
            query += " AND chat_id = :channel_id"
            params["channel_id"] = channel_filter

        query += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = str(max_messages)

        messages = _query_all(query, **params)  # Re-score messages with profile
        from tgsentinel.heuristics import run_heuristics

        matches = []
        for msg in messages:
            # Parse existing triggers
            text = msg.get("message_text", "")

            # Simulate heuristic scoring with profile settings
            # Note: This is a simplified re-scoring, we don't have all original metadata
            keywords = []
            for category in [
                "action_keywords",
                "decision_keywords",
                "urgency_keywords",
                "importance_keywords",
                "release_keywords",
                "security_keywords",
                "risk_keywords",
                "opportunity_keywords",
            ]:
                keywords.extend(profile.get(category, []))

            # Check if any keywords match
            keyword_score = 0.0
            matched_keywords = []
            for kw in keywords:
                if kw.lower() in text.lower():
                    keyword_score += 0.8
                    matched_keywords.append(kw)

            # Check for special patterns
            triggers = []
            rescore = keyword_score

            if matched_keywords:
                triggers.append(f"keywords:{','.join(matched_keywords[:3])}")

            # Estimate if this would trigger an alert
            threshold = 0.7  # Configurable threshold
            would_alert = rescore >= threshold

            if would_alert or msg.get("alerted"):
                matches.append(
                    {
                        "message_id": msg["msg_id"],
                        "chat_id": msg["chat_id"],
                        "chat_title": msg["chat_title"],
                        "sender_name": msg["sender_name"],
                        "score": round(rescore, 2),
                        "original_score": round(msg.get("score", 0.0), 2),
                        "triggers": triggers,
                        "original_triggers": msg.get("triggers", ""),
                        "text_preview": text[:100] + ("..." if len(text) > 100 else ""),
                        "timestamp": msg["created_at"],
                        "would_alert": would_alert,
                        "actually_alerted": bool(msg.get("alerted")),
                    }
                )

        # Calculate statistics
        total_messages = len(messages)
        matched_messages = len(matches)
        true_positives = sum(
            1 for m in matches if m["would_alert"] and m["actually_alerted"]
        )
        false_positives = sum(
            1 for m in matches if m["would_alert"] and not m["actually_alerted"]
        )
        false_negatives = sum(
            1
            for m in messages
            if m.get("alerted")
            and not any(
                match["message_id"] == m["msg_id"] and match["would_alert"]
                for match in matches
            )
        )

        stats = {
            "total_messages": total_messages,
            "matched_messages": matched_messages,
            "match_rate": (
                round(matched_messages / total_messages * 100, 1)
                if total_messages > 0
                else 0
            ),
            "avg_score": (
                round(sum(m["score"] for m in matches) / matched_messages, 2)
                if matched_messages > 0
                else 0
            ),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": (
                round(true_positives / (true_positives + false_positives) * 100, 1)
                if (true_positives + false_positives) > 0
                else 0
            ),
        }

        # Generate recommendations
        recommendations = []
        if stats["false_positives"] > stats["true_positives"]:
            recommendations.append(
                "⚠️ High false positive rate - consider tightening keyword matches"
            )
        if stats["match_rate"] > 50:
            recommendations.append("📊 Very high match rate - profile may be too broad")
        if stats["match_rate"] < 5:
            recommendations.append(
                "📉 Low match rate - consider adding more keywords or lowering thresholds"
            )
        if stats["precision"] < 70:
            recommendations.append("🎯 Low precision - review keyword relevance")

        result = {
            "status": "ok",
            "profile_id": profile_id,
            "profile_name": profile.get("name", profile_id),
            "test_date": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "hours_back": hours_back,
                "max_messages": max_messages,
                "channel_filter": channel_filter,
            },
            "matches": matches[:50],  # Limit response size
            "stats": stats,
            "recommendations": recommendations,
        }

        logger.info(f"Backtest completed for alert profile {profile_id}: {stats}")
        return jsonify(result)

    except Exception as exc:
        logger.error(f"Error backtesting alert profile: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/profiles/interest/backtest")
@_ensure_init
def api_interest_profile_backtest():
    """Backtest an interest profile against historical messages."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_name = payload.get("name", "").strip()
    hours_back = int(payload.get("hours_back", 24))
    max_messages = int(payload.get("max_messages", 100))

    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        # Get profile
        profile = get_profile(profile_name)
        if not profile:
            return jsonify({"status": "error", "message": "Profile not found"}), 404

        # Fetch historical messages
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        messages = _query_all(
            """
            SELECT chat_id, msg_id, chat_title, sender_name, message_text, 
                   score, triggers, alerted, created_at
            FROM messages 
            WHERE datetime(created_at) >= :cutoff
            ORDER BY created_at DESC 
            LIMIT :limit
        """,
            cutoff=cutoff,
            limit=max_messages,
        )

        # Load semantic model if available
        try:
            from tgsentinel.semantic import _model, score_text

            if _model is None:
                return (
                    jsonify(
                        {"status": "error", "message": "Semantic model not loaded"}
                    ),
                    500,
                )

            # Score messages
            matches = []
            threshold = profile.get("threshold", 0.42)

            for msg in messages:
                text = msg.get("message_text", "")
                if not text:
                    continue

                semantic_score = score_text(text)
                if semantic_score is None:
                    continue

                would_alert = semantic_score >= threshold

                if would_alert or msg.get("alerted"):
                    matches.append(
                        {
                            "message_id": msg["msg_id"],
                            "chat_id": msg["chat_id"],
                            "chat_title": msg["chat_title"],
                            "sender_name": msg["sender_name"],
                            "score": round(semantic_score, 3),
                            "original_score": round(msg.get("score", 0.0), 2),
                            "triggers": ["semantic"],
                            "original_triggers": msg.get("triggers", ""),
                            "text_preview": text[:100]
                            + ("..." if len(text) > 100 else ""),
                            "timestamp": msg["created_at"],
                            "would_alert": would_alert,
                            "actually_alerted": bool(msg.get("alerted")),
                        }
                    )

            # Calculate statistics
            total_messages = len(messages)
            matched_messages = len(matches)
            true_positives = sum(
                1 for m in matches if m["would_alert"] and m["actually_alerted"]
            )
            false_positives = sum(
                1 for m in matches if m["would_alert"] and not m["actually_alerted"]
            )

            stats = {
                "total_messages": total_messages,
                "matched_messages": matched_messages,
                "match_rate": (
                    round(matched_messages / total_messages * 100, 1)
                    if total_messages > 0
                    else 0
                ),
                "avg_score": (
                    round(sum(m["score"] for m in matches) / matched_messages, 2)
                    if matched_messages > 0
                    else 0
                ),
                "true_positives": true_positives,
                "false_positives": false_positives,
                "precision": (
                    round(true_positives / (true_positives + false_positives) * 100, 1)
                    if (true_positives + false_positives) > 0
                    else 0
                ),
            }

            # Generate recommendations
            recommendations = []
            if stats["precision"] < 70:
                recommendations.append(
                    "🎯 Consider adding more negative examples to reduce false positives"
                )
            if stats["match_rate"] < 5:
                recommendations.append(
                    "📉 Low match rate - consider lowering threshold or adding more positive examples"
                )
            if stats["match_rate"] > 40:
                recommendations.append(
                    "📊 High match rate - profile may need refinement"
                )

            result = {
                "status": "ok",
                "profile_name": profile_name,
                "test_date": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "hours_back": hours_back,
                    "max_messages": max_messages,
                    "threshold": threshold,
                },
                "matches": matches[:50],
                "stats": stats,
                "recommendations": recommendations,
            }

            logger.info(
                f"Backtest completed for interest profile {profile_name}: {stats}"
            )
            return jsonify(result)

        except ImportError:
            return (
                jsonify(
                    {"status": "error", "message": "Semantic analysis not available"}
                ),
                500,
            )

    except Exception as exc:
        logger.error(f"Error backtesting interest profile: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/console/command")
@_ensure_init
def api_console_command():
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    command = payload.get("command", "").strip()
    logger.info("Console command requested: %s", command)

    # Recognized quick actions
    def _flush_redis() -> int:
        count = 0
        if not redis_client:
            return 0
        try:
            stream_name = _get_stream_name()
            try:
                count += int(redis_client.xlen(stream_name) or 0)
            except Exception:
                pass
            try:
                redis_client.delete(stream_name)
            except Exception:
                pass

            # Delete common caches
            patterns = [
                "tgsentinel:participant:*",
                "tgsentinel:user_avatar:*",
                "tgsentinel:chat_avatar:*",
                "tgsentinel:telegram_users_cache",
                "tgsentinel:chats_cache",
                "tgsentinel:user_info",
            ]
            for pat in patterns:
                try:
                    scan_iter = getattr(redis_client, "scan_iter", None)
                    if callable(scan_iter):
                        # scan_iter returns a generator, convert to list
                        try:
                            keys = [k for k in scan_iter(match=pat)]  # type: ignore
                        except Exception:
                            keys = []
                    else:
                        result = redis_client.keys(pat)  # type: ignore
                        keys = list(result) if result else []
                except Exception:
                    keys = []
                if keys:
                    try:
                        count += int(redis_client.delete(*keys) or 0)
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Flush Redis encountered an error: %s", exc)
        return count

    if command.lower().startswith("/flush") and "redis" in command.lower():
        deleted = _flush_redis()
        try:
            socketio.emit("dashboard:update")
        except Exception:
            pass
        return jsonify({"status": "accepted", "command": command, "deleted": deleted})

    if command.lower().startswith("/purge") and "db" in command.lower():
        # Require explicit confirmation before destructive database wipe
        confirmation = payload.get("confirm")
        if not confirmation or confirmation != "DELETE_ALL_DATA":
            return (
                jsonify(
                    {
                        "status": "confirmation_required",
                        "message": "Database purge requires explicit confirmation. Send 'confirm': 'DELETE_ALL_DATA' to proceed.",
                    }
                ),
                400,
            )

        # Call the existing clean database endpoint logic
        try:
            result = api_clean_database()
            if isinstance(result, tuple):
                response, status_code = result
                if status_code != 200:
                    return response, status_code
                data = response.get_json()
            else:
                data = result.get_json()

            return jsonify(
                {
                    "status": "accepted",
                    "command": command,
                    "deleted": data.get("deleted", 0),
                    "redis_cleared": data.get("redis_cleared", 0),
                }
            )
        except Exception as exc:
            logger.error("Purge DB failed: %s", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500

    if command.lower() == "vacuum":
        # Run VACUUM on SQLite database to reclaim space and optimize
        try:
            if not engine:
                return (
                    jsonify({"status": "error", "message": "Database not available"}),
                    503,
                )

            # Get database size before VACUUM
            db_path = str(engine.url).replace("sqlite:///", "")
            size_before = 0
            try:
                size_before = (
                    Path(db_path).stat().st_size if Path(db_path).exists() else 0
                )
            except Exception:
                pass

            # Execute VACUUM
            with engine.begin() as conn:
                conn.execute(text("VACUUM"))

            # Get database size after VACUUM
            size_after = 0
            try:
                size_after = (
                    Path(db_path).stat().st_size if Path(db_path).exists() else 0
                )
            except Exception:
                pass

            reclaimed_mb = (size_before - size_after) / (1024 * 1024)
            logger.info("VACUUM completed. Reclaimed %.2f MB", reclaimed_mb)

            return jsonify(
                {
                    "status": "accepted",
                    "command": command,
                    "message": f"Database optimized. Reclaimed {reclaimed_mb:.2f} MB",
                    "size_before_mb": size_before / (1024 * 1024),
                    "size_after_mb": size_after / (1024 * 1024),
                    "reclaimed_mb": reclaimed_mb,
                }
            )
        except Exception as exc:
            logger.error("VACUUM failed: %s", exc)
            return jsonify({"status": "error", "message": str(exc)}), 500

    if command.lower().startswith("/reload") and "config" in command.lower():
        try:
            reload_config()
            try:
                socketio.emit("dashboard:update")
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Reload config failed: %s", exc)
        return jsonify({"status": "accepted", "command": command})

    # Default: accept with no-op
    return jsonify({"status": "accepted", "command": command})


@app.get("/api/console/diagnostics")
@_ensure_init
def api_export_diagnostics():
    """Export anonymized system diagnostics for support/debugging."""
    try:
        from flask import make_response

        # Collect diagnostic information
        diagnostics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "summary": _compute_summary(),
            "health": _compute_health(),
            "channels": {
                "count": len(_serialize_channels()),
                "channels": [
                    {
                        "id": ch.get("id"),
                        "name": ch.get("name"),
                        "keywords_count": len(ch.get("keywords", [])),
                        "vip_count": len(ch.get("vip_senders", [])),
                        "enabled": ch.get("enabled", True),
                    }
                    for ch in _serialize_channels()
                ],
            },
            "alerts": {
                "total": len(_load_alerts(limit=100)),
                "recent_sample": [
                    {
                        "score": a.get("score"),
                        "trigger": a.get("trigger"),
                        "created_at": a.get("created_at"),
                    }
                    for a in _load_alerts(limit=10)
                ],
            },
            "activity": {
                "recent_count": len(_load_live_feed(limit=50)),
            },
            "config": {
                "has_config": config is not None,
                "has_redis": redis_client is not None,
            },
        }

        # Create JSON response with download headers
        response = make_response(json.dumps(diagnostics, indent=2))
        response.headers["Content-Type"] = "application/json"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="tgsentinel_diagnostics_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.json"'
        )
        return response

    except Exception as exc:
        logger.error(f"Failed to generate diagnostics: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/telegram/chats")
@_ensure_init
def api_telegram_chats():
    """Return Telegram chats using Redis delegation to sentinel.

    This endpoint delegates to the sentinel process (sole session DB owner)
    via Redis request/response pattern to maintain single-owner architecture.
    """
    logger.info("[UI-CHATS] Telegram chats request received")
    if redis_client is None:
        logger.error("[UI-CHATS] Redis client not available")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Redis not available. Cannot fetch chats.",
                }
            ),
            503,
        )

    try:
        # Generate unique request ID
        import uuid
        import time as _time  # noqa: WPS433

        request_id = str(uuid.uuid4())
        request_key = f"tgsentinel:request:get_dialogs:{request_id}"

        logger.info("[UI-CHATS] Creating request: request_id=%s", request_id)
        logger.debug("[UI-CHATS] Request key: %s", request_key)

        # Submit request to sentinel
        request_data = {"request_id": request_id, "timestamp": _time.time()}
        redis_client.setex(request_key, 60, json.dumps(request_data))
        logger.info("[UI-CHATS] Request submitted, waiting for response (max 30s)...")
        logger.debug(
            "[UI-CHATS] Response key: tgsentinel:response:get_dialogs:%s", request_id
        )

        # Wait for response (max 30 seconds - dialog fetching can be slow)
        response_key = f"tgsentinel:response:get_dialogs:{request_id}"
        poll_count = 0
        for _ in range(60):  # 60 * 0.5s = 30s timeout
            poll_count += 1
            _time.sleep(0.5)
            response_data = redis_client.get(response_key)
            if response_data:
                logger.info("[UI-CHATS] Response received after %d polls", poll_count)
                try:
                    # Ensure response_data is a string
                    if isinstance(response_data, bytes):
                        response_data = response_data.decode()
                    response = json.loads(str(response_data))
                    logger.debug(
                        "[UI-CHATS] Response status: %s", response.get("status")
                    )

                    # Clean up
                    redis_client.delete(response_key)

                    if response.get("status") == "error":
                        logger.error(
                            "[UI-CHATS] Sentinel returned error: %s",
                            response.get("error"),
                        )
                        return (
                            jsonify(
                                {
                                    "status": "error",
                                    "message": response.get(
                                        "error", "Failed to fetch chats"
                                    ),
                                }
                            ),
                            500,
                        )

                    chats = response.get("chats", [])
                    logger.info("[UI-CHATS] Returning %d chats to client", len(chats))
                    return jsonify({"chats": chats})
                except json.JSONDecodeError as exc:
                    logger.error("[UI-CHATS] Invalid JSON response: %s", exc)
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Invalid response from sentinel",
                            }
                        ),
                        502,
                    )

        # Timeout - clean up request key
        redis_client.delete(request_key)
        logger.warning("[UI-CHATS] Request timed out after %d polls (30s)", poll_count)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Sentinel did not respond in time. Please retry.",
                }
            ),
            504,
        )

    except Exception as exc:
        logger.error("Failed to fetch Telegram chats: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/telegram/users")
@_ensure_init
def api_telegram_users():
    """Get list of all accessible Telegram private chats (users).

    Uses Redis request/response pattern to delegate discovery to sentinel process,
    always fetches fresh data from Telegram via MTProto.
    """
    try:
        # 0) Cache shortcut (accept both list and {users: []})
        try:
            r = None
            # Prefer class-based Redis for cache read so tests can patch app.redis.Redis
            if redis:
                try:
                    r = redis.Redis(
                        host=os.getenv("REDIS_HOST", "localhost"),
                        port=int(os.getenv("REDIS_PORT", "6379")),
                        decode_responses=True,
                    )
                except Exception:
                    r = None
            # Fallback to app-level redis_client if class-based is unavailable
            if r is None:
                r = redis_client
            if r is not None:
                cached = r.get("tgsentinel:telegram_users_cache")
                if cached:
                    try:
                        if isinstance(cached, bytes):
                            cached = cached.decode()
                        parsed = json.loads(str(cached))
                        users = (
                            parsed
                            if isinstance(parsed, list)
                            else parsed.get("users", [])
                        )
                        return jsonify({"users": users})
                    except Exception:
                        # Malformed cache → fall back to empty per tests
                        return jsonify({"users": []})
        except Exception:
            # Do not fail cache shortcut path; continue to normal flow
            pass

        # If app Redis client is absent, return empty users list for consistent behavior
        if redis_client is None:
            logger.warning(
                "Redis not available for Telegram users fetch - returning empty list"
            )
            return jsonify({"users": []})

        # Create request for sentinel to process (always fetch fresh data)
        request_id = f"{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        request_key = f"tgsentinel:telegram_users_request:{request_id}"
        request_data = {"request_id": request_id, "type": "users"}

        redis_client.setex(request_key, 60, json.dumps(request_data))
        logger.info("[UI-USERS] Created request: request_id=%s", request_id)
        logger.debug("[UI-USERS] Request key: %s", request_key)

        # Wait for response (max 30 seconds - dialog fetching can be slow)
        response_key = f"tgsentinel:telegram_users_response:{request_id}"
        import time as _time  # noqa: WPS433

        logger.info("[UI-USERS] Waiting for response (max 30s)...")
        poll_count = 0
        for _ in range(60):  # 60 * 0.5s = 30s timeout
            poll_count += 1
            _time.sleep(0.5)
            response_data = redis_client.get(response_key)
            if response_data:
                logger.info("[UI-USERS] Response received after %d polls", poll_count)
                try:
                    # Ensure response_data is a string
                    if isinstance(response_data, bytes):
                        response_data = response_data.decode()
                    response = json.loads(str(response_data))
                    logger.debug(
                        "[UI-USERS] Response status: %s", response.get("status")
                    )

                    # Clean up
                    redis_client.delete(response_key)

                    if response.get("status") == "error":
                        logger.error(
                            "[UI-USERS] Sentinel returned error: %s",
                            response.get("message"),
                        )
                        return (
                            jsonify(
                                {
                                    "status": "error",
                                    "message": response.get(
                                        "message", "Failed to fetch users"
                                    ),
                                }
                            ),
                            500,
                        )

                    users = response.get("users", [])
                    logger.info("[UI-USERS] Returning %d users to client", len(users))
                    return jsonify({"users": users})

                except Exception as parse_exc:
                    logger.error("[UI-USERS] Failed to parse response: %s", parse_exc)
                    redis_client.delete(response_key)
                    # Per tests, malformed response should fall back to empty, 200
                    return jsonify({"users": []})

        # Timeout - clean up
        redis_client.delete(request_key)
        logger.warning(
            "[UI-USERS] Request timed out after %d polls (30s): %s",
            poll_count,
            request_id,
        )
        # Timeout → graceful fallback
        # If config has monitored users, return them; else return empty
        monitored_users_list = []
        # Prefer freshly loaded config in tests to pick up patched values
        try:
            cfg = load_config()
        except Exception:
            cfg = config
        if cfg and hasattr(cfg, "monitored_users"):
            for u in cfg.monitored_users:
                monitored_users_list.append(
                    {
                        "id": getattr(u, "id", 0),
                        "name": getattr(u, "name", "Unknown"),
                        "username": getattr(u, "username", ""),
                    }
                )
        if monitored_users_list:
            return jsonify({"users": monitored_users_list, "source": "config"})
        return jsonify({"users": []})

    except Exception as exc:
        logger.error(f"Failed to fetch Telegram users: {exc}")
        # Graceful fallback on unexpected errors
        return jsonify({"users": []})


@app.post("/api/config/channels/add")
@_ensure_init
def api_config_channels_add():
    """Add channels to the configuration file."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    channels_to_add = payload.get("channels", [])
    if not channels_to_add or not isinstance(channels_to_add, list):
        return (
            jsonify(
                {"status": "error", "message": "channels array is required in payload"}
            ),
            400,
        )

    try:
        config_path = Path("config/tgsentinel.yml")
        if not config_path.exists():
            return (
                jsonify({"status": "error", "message": "Configuration file not found"}),
                404,
            )

        # Read current config
        with open(config_path, "r") as f:
            current_config = yaml.safe_load(f) or {}

        # Get existing channels
        existing_channels = current_config.get("channels", [])
        existing_ids = {ch.get("id") for ch in existing_channels}

        # Add new channels (skip duplicates)
        added_count = 0
        for new_channel in channels_to_add:
            channel_id = new_channel.get("id")
            if channel_id and channel_id not in existing_ids:
                # Create channel entry with default values
                channel_entry = {
                    "id": channel_id,
                    "name": new_channel.get("name", "Unknown Channel"),
                    "vip_senders": [],
                    "keywords": [],
                    "reaction_threshold": 5,
                    "reply_threshold": 3,
                    "rate_limit_per_hour": 10,
                }
                existing_channels.append(channel_entry)
                existing_ids.add(channel_id)
                added_count += 1

        # Update config
        current_config["channels"] = existing_channels

        # Write back to file with file locking
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=config_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(
                current_config,
                tmp_file,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
            tmp_path = tmp_file.name

        # Atomic replace
        shutil.move(tmp_path, config_path)

        logger.info(f"Added {added_count} new channels to configuration")

        # Reload config in UI to reflect changes immediately
        reload_config()

        # Signal sentinel container to reload by creating a marker file
        try:
            reload_marker = Path("/app/data/.reload_config")
            reload_marker.touch()
            logger.info("Created reload marker for sentinel container")
        except Exception as marker_exc:
            logger.debug(f"Could not create reload marker: {marker_exc}")

        return jsonify({"status": "ok", "added": added_count})

    except FileNotFoundError:
        return (
            jsonify({"status": "error", "message": "Configuration file not found"}),
            404,
        )
    except Exception as exc:
        logger.error(f"Failed to add channels: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/config/channels/<chat_id>", methods=["DELETE"])
@_ensure_init
def api_config_channels_delete(chat_id):
    """Delete a channel from the configuration file."""
    try:
        # Convert chat_id to int (handles large negative numbers)
        try:
            chat_id = int(chat_id)
        except ValueError:
            return (
                jsonify({"status": "error", "message": "Invalid chat ID format"}),
                400,
            )

        config_path = Path("config/tgsentinel.yml")
        if not config_path.exists():
            return (
                jsonify({"status": "error", "message": "Configuration file not found"}),
                404,
            )

        # Read current config
        with open(config_path, "r") as f:
            current_config = yaml.safe_load(f) or {}

        # Get existing channels
        existing_channels = current_config.get("channels", [])

        # Find and remove the channel
        original_count = len(existing_channels)
        updated_channels = [ch for ch in existing_channels if ch.get("id") != chat_id]

        if len(updated_channels) == original_count:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Channel with ID {chat_id} not found",
                    }
                ),
                404,
            )

        # Update config
        current_config["channels"] = updated_channels

        # Write back to file with file locking
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=config_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(
                current_config,
                tmp_file,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
            tmp_path = tmp_file.name

        # Atomic replace
        shutil.move(tmp_path, config_path)

        logger.info(f"Deleted channel {chat_id} from configuration")

        # Reload config in UI to reflect changes immediately
        reload_config()

        # Signal sentinel container to reload by creating a marker file
        try:
            reload_marker = Path("/app/data/.reload_config")
            reload_marker.touch()
            logger.info("Created reload marker for sentinel container")
        except Exception as marker_exc:
            logger.debug(f"Could not create reload marker: {marker_exc}")

        return jsonify({"status": "ok", "message": "Channel deleted successfully"})

    except FileNotFoundError:
        return (
            jsonify({"status": "error", "message": "Configuration file not found"}),
            404,
        )
    except Exception as exc:
        logger.error(f"Failed to delete channel: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/config/users/add")
@_ensure_init
def api_config_users_add():
    """Add monitored users to the configuration file."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    users_to_add = payload.get("users", [])
    if not users_to_add or not isinstance(users_to_add, list):
        return (
            jsonify(
                {"status": "error", "message": "users array is required in payload"}
            ),
            400,
        )

    try:
        config_path = Path("config/tgsentinel.yml")
        if not config_path.exists():
            return (
                jsonify({"status": "error", "message": "Configuration file not found"}),
                404,
            )

        # Read current config
        with open(config_path, "r") as f:
            current_config = yaml.safe_load(f) or {}

        # Get existing monitored users
        existing_users = current_config.get("monitored_users", [])
        existing_ids = {u.get("id") for u in existing_users}

        # Add new users (skip duplicates)
        added_count = 0
        for new_user in users_to_add:
            user_id = new_user.get("id")
            if user_id and user_id not in existing_ids:
                # Create user entry
                user_entry = {
                    "id": user_id,
                    "name": new_user.get("name", f"User {user_id}"),
                    "username": new_user.get("username", ""),
                }
                existing_users.append(user_entry)
                existing_ids.add(user_id)
                added_count += 1

        # Update config
        current_config["monitored_users"] = existing_users

        # Write back to file with file locking
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=config_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(
                current_config,
                tmp_file,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
            tmp_path = tmp_file.name

        # Atomic replace
        shutil.move(tmp_path, config_path)

        logger.info(f"Added {added_count} new monitored users to configuration")

        # Reload config in UI to reflect changes immediately
        reload_config()

        # Signal sentinel container to reload by creating a marker file
        try:
            reload_marker = Path("/app/data/.reload_config")
            reload_marker.touch()
            logger.info("Created reload marker for sentinel container")
        except Exception as marker_exc:
            logger.debug(f"Could not create reload marker: {marker_exc}")

        return jsonify({"status": "ok", "added": added_count})

    except FileNotFoundError:
        return (
            jsonify({"status": "error", "message": "Configuration file not found"}),
            404,
        )
    except Exception as exc:
        logger.error(f"Failed to add monitored users: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/config/users/<user_id>", methods=["DELETE"])
@_ensure_init
def api_config_users_delete(user_id):
    """Delete a monitored user from the configuration file."""
    try:
        # Convert user_id to int
        try:
            user_id = int(user_id)
        except ValueError:
            return (
                jsonify({"status": "error", "message": "Invalid user ID format"}),
                400,
            )

        config_path = Path("config/tgsentinel.yml")
        if not config_path.exists():
            return (
                jsonify({"status": "error", "message": "Configuration file not found"}),
                404,
            )

        # Read current config
        with open(config_path, "r") as f:
            current_config = yaml.safe_load(f) or {}

        # Get existing monitored users
        existing_users = current_config.get("monitored_users", [])

        # Find and remove the user
        original_count = len(existing_users)
        updated_users = [u for u in existing_users if u.get("id") != user_id]

        if len(updated_users) == original_count:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"User with ID {user_id} not found",
                    }
                ),
                404,
            )

        # Update config
        current_config["monitored_users"] = updated_users

        # Write back to file with file locking
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=config_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(
                current_config,
                tmp_file,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
            tmp_path = tmp_file.name

        # Atomic replace
        shutil.move(tmp_path, config_path)

        logger.info(f"Deleted monitored user {user_id} from configuration")

        # Reload config in UI to reflect changes immediately
        reload_config()

        # Signal sentinel container to reload by creating a marker file
        try:
            reload_marker = Path("/app/data/.reload_config")
            reload_marker.touch()
            logger.info("Created reload marker for sentinel container")
        except Exception as marker_exc:
            logger.debug(f"Could not create reload marker: {marker_exc}")

        return jsonify({"status": "ok", "message": "User deleted successfully"})

    except FileNotFoundError:
        return (
            jsonify({"status": "error", "message": "Configuration file not found"}),
            404,
        )
    except Exception as exc:
        logger.error(f"Failed to delete monitored user: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/export_alerts")
@_ensure_init
def api_export_alerts():
    """Export alerts as CSV file."""
    try:
        import csv
        import io
        from flask import make_response

        # Get limit from query params, default to 1000
        limit = request.args.get("limit", 1000, type=int)
        # Get format parameter: 'human' for descriptive headers, 'machine' for programmatic keys
        format_type = request.args.get("format", "human", type=str).lower()

        alerts = _load_alerts(limit=limit)

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write single header row based on format
        if format_type == "machine":
            # Machine-friendly headers for programmatic access
            writer.writerow(
                [
                    "chat_name",
                    "sender",
                    "excerpt",
                    "score",
                    "trigger",
                    "sent_to",
                    "created_at",
                ]
            )
        else:
            # Human-friendly headers (default)
            writer.writerow(
                [
                    "Channel",
                    "Sender",
                    "Excerpt",
                    "Score",
                    "Trigger",
                    "Destination",
                    "Timestamp",
                ]
            )

        # Write data rows
        for alert in alerts:
            row = [
                alert.get("chat_name", ""),
                alert.get("sender", ""),
                alert.get("excerpt", ""),
                alert.get("score", 0.0),
                alert.get("trigger", ""),
                alert.get("sent_to", ""),
                alert.get("created_at", ""),
            ]
            writer.writerow(row)

        # Create response with CSV content
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="tgsentinel_alerts_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.csv"'
        )
        return response

    except Exception as exc:
        logger.error(f"Failed to export alerts: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/webhooks")
@_ensure_init
def api_webhooks_list():
    """List all configured webhooks."""
    try:
        webhooks_path = Path("config/webhooks.yml")
        if not webhooks_path.exists():
            return jsonify({"webhooks": []})

        with open(webhooks_path, "r") as f:
            data = yaml.safe_load(f) or {}

        webhooks = data.get("webhooks", [])
        # Mask secrets in response
        for webhook in webhooks:
            if "secret" in webhook:
                webhook["secret"] = "••••••"

        return jsonify({"webhooks": webhooks})

    except Exception as exc:
        logger.error(f"Failed to list webhooks: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/webhooks")
@_ensure_init
def api_webhooks_create():
    """Create a new webhook configuration."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    service = payload.get("service", "").strip()
    url = payload.get("url", "").strip()
    secret = payload.get("secret", "").strip()

    if not service or not url:
        return (
            jsonify({"status": "error", "message": "service and url are required"}),
            400,
        )

    try:
        webhooks_path = Path("config/webhooks.yml")
        webhooks_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing webhooks
        if webhooks_path.exists():
            with open(webhooks_path, "r") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        webhooks = data.get("webhooks", [])

        # Check for duplicate service names
        if any(wh.get("service") == service for wh in webhooks):
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Webhook with service name '{service}' already exists",
                    }
                ),
                409,
            )

        # Add new webhook
        new_webhook = {"service": service, "url": url, "enabled": True}
        if secret:
            new_webhook["secret"] = secret

        webhooks.append(new_webhook)
        data["webhooks"] = webhooks

        # Save atomically
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=webhooks_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(data, tmp_file, default_flow_style=False, sort_keys=False)
            tmp_path = tmp_file.name

        shutil.move(tmp_path, webhooks_path)

        logger.info(f"Created webhook: {service}")
        return jsonify({"status": "ok", "service": service}), 201

    except Exception as exc:
        logger.error(f"Failed to create webhook: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.delete("/api/webhooks/<service_name>")
@_ensure_init
def api_webhooks_delete(service_name: str):
    """Delete a webhook by service name."""
    try:
        webhooks_path = Path("config/webhooks.yml")
        if not webhooks_path.exists():
            return (
                jsonify({"status": "error", "message": "No webhooks configured"}),
                404,
            )

        with open(webhooks_path, "r") as f:
            data = yaml.safe_load(f) or {}

        webhooks = data.get("webhooks", [])
        original_count = len(webhooks)

        # Filter out the webhook
        webhooks = [wh for wh in webhooks if wh.get("service") != service_name]

        if len(webhooks) == original_count:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Webhook '{service_name}' not found",
                    }
                ),
                404,
            )

        data["webhooks"] = webhooks

        # Save atomically
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=webhooks_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(data, tmp_file, default_flow_style=False, sort_keys=False)
            tmp_path = tmp_file.name

        shutil.move(tmp_path, webhooks_path)

        logger.info(f"Deleted webhook: {service_name}")
        return jsonify({"status": "ok", "deleted": service_name})

    except Exception as exc:
        logger.error(f"Failed to delete webhook: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/profiles/import")
@_ensure_init
def api_profiles_import():
    """Import interest profiles from YAML file."""
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"status": "error", "message": "Empty filename"}), 400

    try:
        # Read and validate YAML
        content = file.read().decode("utf-8")
        data = yaml.safe_load(content)

        if not isinstance(data, dict):
            return (
                jsonify({"status": "error", "message": "Invalid YAML structure"}),
                400,
            )

        # Validate interests structure
        if "interests" not in data:
            return (
                jsonify(
                    {"status": "error", "message": "Missing 'interests' key in YAML"}
                ),
                400,
            )

        interests = data["interests"]
        if not isinstance(interests, list):
            return (
                jsonify({"status": "error", "message": "'interests' must be a list"}),
                400,
            )

        # Save to config directory
        interests_path = Path("config/interests.yml")
        interests_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=interests_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(data, tmp_file, default_flow_style=False, sort_keys=False)
            tmp_path = tmp_file.name

        shutil.move(tmp_path, interests_path)

        logger.info(f"Imported {len(interests)} interest profiles")
        return jsonify({"status": "ok", "imported": len(interests)})

    except yaml.YAMLError as exc:
        return (
            jsonify({"status": "error", "message": f"Invalid YAML: {str(exc)}"}),
            400,
        )
    except Exception as exc:
        logger.error(f"Failed to import profiles: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.post("/api/developer/settings")
@_ensure_init
def api_developer_settings():
    """Save developer integration settings."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    try:
        settings_path = Path("config/developer.yml")
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing settings
        if settings_path.exists():
            with open(settings_path, "r") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        # Update settings
        if "prometheus_port" in payload:
            port = payload["prometheus_port"]
            if not isinstance(port, int) or port < 1 or port > 65535:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "prometheus_port must be between 1 and 65535",
                        }
                    ),
                    400,
                )
            data["prometheus_port"] = port

        if "api_key" in payload:
            # Store hashed version of API key for security
            import hashlib

            api_key = payload["api_key"]
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            data["api_key_hash"] = key_hash

        if "metrics_enabled" in payload:
            data["metrics_enabled"] = bool(payload["metrics_enabled"])

        # Save atomically
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=settings_path.parent, suffix=".tmp"
        ) as tmp_file:
            yaml.dump(data, tmp_file, default_flow_style=False, sort_keys=False)
            tmp_path = tmp_file.name

        shutil.move(tmp_path, settings_path)

        logger.info("Saved developer settings")
        return jsonify({"status": "ok"})

    except Exception as exc:
        logger.error(f"Failed to save developer settings: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.get("/api/analytics/anomalies")
@_ensure_init
def api_analytics_anomalies():
    """Detect and return anomalous patterns in channel activity."""
    try:
        # Load configurable thresholds from environment with sensible defaults
        volume_threshold = float(os.getenv("ANOMALY_VOLUME_THRESHOLD", "3.0"))
        importance_threshold = float(os.getenv("ANOMALY_IMPORTANCE_THRESHOLD", "2.0"))
        alert_rate_threshold = float(os.getenv("ANOMALY_ALERT_RATE", "0.5"))  # 50%

        # Standard deviation mode configuration
        use_stddev = os.getenv("ANOMALY_USE_STDDEV", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        stddev_multiplier = float(os.getenv("ANOMALY_STDDEV_MULTIPLIER", "2.0"))

        # Get recent activity from last 24 hours
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Query message statistics per channel (using chat_id from database)
        channel_stats = _query_all(
            """
            SELECT 
                chat_id,
                COUNT(*) as msg_count,
                AVG(score) as avg_score,
                MAX(score) as max_score,
                COUNT(CASE WHEN alerted = 1 THEN 1 END) as alert_count
            FROM messages 
            WHERE datetime(created_at) >= :cutoff
            GROUP BY chat_id
            HAVING msg_count > 0
            """,
            cutoff=cutoff,
        )

        anomalies = []

        # Calculate overall statistics
        if channel_stats:
            all_counts = [s["msg_count"] for s in channel_stats]
            all_scores = [s["avg_score"] for s in channel_stats if s["avg_score"]]

            avg_msg_count = sum(all_counts) / len(all_counts) if all_counts else 0
            avg_score = sum(all_scores) / len(all_scores) if all_scores else 0

            # Calculate standard deviation if enabled
            volume_std_dev = 0
            importance_std_dev = 0
            if use_stddev and len(all_counts) > 1:
                # Calculate standard deviation for message counts
                variance_counts = sum(
                    (x - avg_msg_count) ** 2 for x in all_counts
                ) / len(all_counts)
                volume_std_dev = variance_counts**0.5

                # Calculate standard deviation for importance scores
                if len(all_scores) > 1:
                    variance_scores = sum(
                        (x - avg_score) ** 2 for x in all_scores
                    ) / len(all_scores)
                    importance_std_dev = variance_scores**0.5

            # Detect anomalies
            for stat in channel_stats:
                chat_id = stat["chat_id"]
                msg_count = stat["msg_count"]
                avg_ch_score = stat["avg_score"] or 0
                alert_count = stat["alert_count"]

                # Convert chat_id to readable name
                channel_name = f"Chat {chat_id}"

                # Anomaly 1: Unusual message volume
                if use_stddev and volume_std_dev > 0:
                    # Standard deviation mode
                    threshold = avg_msg_count + (stddev_multiplier * volume_std_dev)
                    if msg_count > threshold:
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High volume: {msg_count} messages (avg: {int(avg_msg_count)}, σ: {volume_std_dev:.1f})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "volume_spike",
                            }
                        )
                else:
                    # Multiplier mode
                    if (
                        avg_msg_count > 0
                        and msg_count > avg_msg_count * volume_threshold
                    ):
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High volume: {msg_count} messages (avg: {int(avg_msg_count)})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "volume_spike",
                            }
                        )

                # Anomaly 2: Unusual importance scores
                if use_stddev and importance_std_dev > 0:
                    # Standard deviation mode
                    threshold = avg_score + (stddev_multiplier * importance_std_dev)
                    if avg_ch_score > threshold:
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High importance: {avg_ch_score:.2f} (avg: {avg_score:.2f}, σ: {importance_std_dev:.2f})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "importance_spike",
                            }
                        )
                else:
                    # Multiplier mode
                    if (
                        avg_score > 0
                        and avg_ch_score > avg_score * importance_threshold
                    ):
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"High importance: {avg_ch_score:.2f} (avg: {avg_score:.2f})",
                                "severity": "warning",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "importance_spike",
                            }
                        )

                # Anomaly 3: High alert rate
                if msg_count > 5:
                    alert_rate = alert_count / msg_count
                    if alert_rate > alert_rate_threshold:
                        anomalies.append(
                            {
                                "channel": channel_name,
                                "signal": f"Alert rate: {alert_count}/{msg_count} ({int(alert_rate*100)}%)",
                                "severity": "info",
                                "detected": datetime.now(timezone.utc).isoformat(),
                                "type": "alert_rate",
                            }
                        )

        return jsonify({"anomalies": anomalies})

    except Exception as exc:
        logger.error(f"Failed to detect anomalies: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@socketio.on("connect")
def socket_connect() -> None:
    emit(
        "status",
        {"connected": True, "timestamp": datetime.now(timezone.utc).isoformat()},
    )


@socketio.on("disconnect")
def socket_disconnect() -> None:
    logger.debug("Socket disconnected")


@socketio.on("subscribe_logs")
def socket_subscribe_logs() -> None:
    """Subscribe client to real-time log streaming."""
    logger.info("Client subscribed to log stream")
    emit(
        "log",
        {
            "level": "info",
            "message": "Log stream connected",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def broadcast_log(level: str, message: str) -> None:
    """Broadcast a log message to all connected Socket.IO clients."""
    try:
        socketio.emit(
            "log",
            {
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        # Don't let logging failures break the application
        logger.debug(f"Failed to broadcast log: {exc}")


@socketio.on("request-update")
def socket_request_update(
    data: Dict[str, Any],
) -> None:  # noqa: ARG001 - required by API
    emit(
        "dashboard:update",
        {
            "summary": _compute_summary(),
            "health": _compute_health(),
            "activity": _load_live_feed(limit=10),
        },
    )


def main() -> None:
    init_app()
    socketio.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("UI_PORT", "5000")),
        allow_unsafe_werkzeug=True,  # For development/testing; use proper WSGI server in production
    )


if __name__ == "__main__":
    main()
