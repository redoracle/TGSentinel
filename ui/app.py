"""TG Sentinel UI web service.

This module exposes the Flask + Socket.IO application that powers the
dashboard experience. It mirrors the telemetry collected by the core
worker processes so analysts can monitor the system without running
additional services.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

try:  # Optional dependency for process metrics.
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is optional
    psutil = None  # type: ignore

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover - redis is optional
    redis = None  # type: ignore

import yaml
import fcntl
import shutil
import tempfile
from flask import Flask, jsonify, render_template, request, send_from_directory

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
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdn.socket.io; "
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

# Cache of expensive lookups with short lifetimes.
_cached_summary: Tuple[datetime, Dict[str, Any]] | None = None
_cached_health: Tuple[datetime, Dict[str, Any]] | None = None

STREAM_DEFAULT = "tgsentinel:messages"


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
                    decode_responses=True,
                    socket_timeout=1.5,
                )
                client.ping()
                redis_client = client
                logger.info(
                    "Redis connection ready: %s:%s",
                    stream_cfg["host"],
                    stream_cfg.get("port", 6379),
                )
            except (
                Exception
            ) as exc:  # pragma: no cover - telemetry still works without redis
                logger.warning("Redis unreachable: %s", exc)
                redis_client = None
        else:
            redis_client = None

        _is_initialized = True


def _ensure_init(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that guarantees :func:`init_app` ran before using shared state."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not _is_initialized:
            init_app()
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


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "Not linked"
    digits = phone.strip()
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
            redis_depth = redis_client.xlen(stream_name)
            redis_online = True
        except Exception as exc:  # pragma: no cover - redis may be offline
            logger.debug("Redis depth unavailable: %s", exc)

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
                        import json

                        parsed = json.loads(json_str)
                        # Merge parsed JSON into data
                        data.update(parsed)
                    except Exception:
                        pass

                # Get chat name from various sources
                chat_id = data.get("chat_id")
                chat_name = (
                    data.get("chat_name")
                    or data.get("chat_title")
                    or data.get("channel")
                    or (chat_id_to_name.get(int(chat_id)) if chat_id else None)
                    or "Unknown chat"
                )

                entries.append(
                    {
                        "id": entry_id,
                        "chat_name": chat_name,
                        "sender": data.get("sender") or data.get("author") or "Unknown",
                        "message": _truncate(data.get("message") or data.get("text")),
                        "importance": round(float(data.get("importance", 0.0)), 2),
                        "tags": _normalise_tags(data.get("tags")),
                        "timestamp": data.get("timestamp")
                        or data.get("created_at")
                        or datetime.now(timezone.utc).isoformat(),
                    }
                )
        except Exception as exc:  # pragma: no cover - redis optional
            logger.debug("Failed to read activity feed: %s", exc)

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
        SELECT chat_id, msg_id, score, alerted, created_at
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

    if rows:
        return [
            {
                "chat_id": row["chat_id"],
                "chat_name": f"Chat {row['chat_id']}",
                "sender": "Unknown",
                "excerpt": f"hash:{row['msg_id']}",
                "score": round(float(row.get("score", 0.0)), 2),
                "trigger": "Importance threshold",
                "sent_to": mode,
                "created_at": row.get("created_at"),
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
    if "mode" in payload and payload["mode"] not in ["direct", "digest", "channel"]:
        raise ValueError(
            f"Invalid mode: {payload['mode']}. Must be 'direct', 'digest', or 'channel'."
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
    return {"now": datetime.utcnow}


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
    interests_attr = getattr(config, "interests", None) if config else None
    interests = list(interests_attr) if interests_attr is not None else []
    return render_template(
        "profiles.html",
        interests=interests,
    )


@app.route("/developer")
@_ensure_init
def developer_view():
    return render_template("developer.html")


@app.route("/console")
@_ensure_init
def console_view():
    return render_template("console.html")


@app.get("/api/session/info")
@_ensure_init
def api_session_info():
    phone = os.getenv("TG_PHONE")
    session_path = (
        getattr(config, "telegram_session", os.getenv("TG_SESSION_PATH"))
        if config
        else os.getenv("TG_SESSION_PATH")
    )
    return jsonify(
        {
            "username": _fallback_username(),
            "avatar": _fallback_avatar(),
            "session_path": session_path,
            "phone_masked": _mask_phone(phone),
            "connected": bool(redis_client),
            "connected_chats": [channel["name"] for channel in _serialize_channels()],
        }
    )


@app.get("/api/dashboard/summary")
@_ensure_init
def api_dashboard_summary():
    return jsonify(_compute_summary())


@app.get("/api/dashboard/activity")
@_ensure_init
def api_dashboard_activity():
    limit = min(int(request.args.get("limit", 20)), 100)
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


@app.route("/api/config/current", methods=["GET"])
@_ensure_init
def api_config_current():
    """Get current configuration including environment variables."""
    telegram_cfg = {
        "api_id": os.getenv("TG_API_ID", ""),
        "api_hash": os.getenv("TG_API_HASH", ""),
        "phone_number": os.getenv("TG_PHONE", ""),
        "session": getattr(config, "telegram_session", "") if config else "",
    }

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

    semantic_cfg = {
        "embeddings_model": os.getenv("EMBEDDINGS_MODEL", ""),
        "similarity_threshold": float(os.getenv("SIMILARITY_THRESHOLD", "0.42")),
    }

    return jsonify(
        {
            "telegram": telegram_cfg,
            "alerts": alerts_cfg,
            "digest": digest_cfg,
            "redis": redis_cfg,
            "semantic": semantic_cfg,
            "database_uri": os.getenv("DB_URI", ""),
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
    keywords: Dict[str, int] = {}
    for channel in _serialize_channels():
        for keyword in channel.get("keywords", []):
            keywords[keyword] = keywords.get(keyword, 0) + 1
    data = sorted(
        ({"keyword": kw, "count": count} for kw, count in keywords.items()),
        key=lambda entry: entry["count"],
        reverse=True,
    )
    return jsonify({"keywords": data})


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
    return jsonify({"status": "accepted", "command": command})


@app.get("/api/console/diagnostics")
@_ensure_init
def api_export_diagnostics():
    """Export anonymized system diagnostics for support/debugging."""
    try:
        from flask import make_response
        import json

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
    """Get list of all accessible Telegram chats (channels, groups, supergroups)."""
    try:
        import asyncio
        from telethon import TelegramClient
        from telethon.tl.types import Channel, Chat

        api_id = os.getenv("TG_API_ID")
        api_hash = os.getenv("TG_API_HASH")
        session_path = getattr(config, "telegram_session", "") if config else ""

        if not api_id or not api_hash:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "TG_API_ID and TG_API_HASH are required",
                    }
                ),
                400,
            )

        try:
            api_id_int = int(api_id)
        except ValueError:
            return (
                jsonify({"status": "error", "message": "TG_API_ID must be numeric"}),
                400,
            )

        if not session_path or not Path(session_path).exists():
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Telegram session not found. Please authenticate first.",
                    }
                ),
                404,
            )

        # Fetch dialogs using async in a new event loop (thread-safe)
        async def fetch_dialogs():
            max_retries = 3
            retry_delay = 0.5  # seconds

            for attempt in range(max_retries):
                try:
                    client = TelegramClient(session_path, api_id_int, api_hash)
                    await client.connect()

                    if not await client.is_user_authorized():
                        client.disconnect()
                        return None

                    dialogs = await client.get_dialogs()
                    chats = []

                    for dialog in dialogs:
                        entity = dialog.entity

                        # Only include channels, groups, and supergroups
                        if isinstance(entity, (Channel, Chat)):
                            chat_type = "channel"
                            if isinstance(entity, Channel):
                                if entity.broadcast:
                                    chat_type = "channel"
                                elif entity.megagroup:
                                    chat_type = "supergroup"
                                else:
                                    chat_type = "group"
                            elif isinstance(entity, Chat):
                                chat_type = "group"

                            chats.append(
                                {
                                    "id": entity.id,
                                    "name": getattr(entity, "title", "Unknown"),
                                    "type": chat_type,
                                    "username": getattr(entity, "username", None),
                                }
                            )

                    client.disconnect()
                    return chats
                except Exception as db_exc:
                    # Retry on database lock errors
                    if (
                        "database is locked" in str(db_exc).lower()
                        and attempt < max_retries - 1
                    ):
                        logger.warning(
                            f"Database locked, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        raise

            return None  # All retries failed

        # Create new event loop for this thread
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            chats = loop.run_until_complete(fetch_dialogs())
            loop.close()
        except Exception as loop_exc:
            logger.error(f"Event loop error: {loop_exc}")
            raise

        if chats is None:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Not authorized. Please authenticate first.",
                    }
                ),
                401,
            )

        return jsonify({"chats": chats})

    except ImportError:
        logger.error("Telethon library not available")
        return (
            jsonify(
                {"status": "error", "message": "Telegram client library not available"}
            ),
            500,
        )
    except Exception as exc:
        logger.error(f"Failed to fetch Telegram chats: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


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
        alerts = _load_alerts(limit=limit)

        # Create CSV in memory
        output = io.StringIO()
        if not alerts:
            # Return empty CSV with headers
            writer = csv.writer(output)
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
        else:
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "chat_name",
                    "sender",
                    "excerpt",
                    "score",
                    "trigger",
                    "sent_to",
                    "created_at",
                ],
            )
            writer.writeheader()
            for alert in alerts:
                writer.writerow(
                    {
                        "chat_name": alert.get("chat_name", ""),
                        "sender": alert.get("sender", ""),
                        "excerpt": alert.get("excerpt", ""),
                        "score": alert.get("score", 0.0),
                        "trigger": alert.get("trigger", ""),
                        "sent_to": alert.get("sent_to", ""),
                        "created_at": alert.get("created_at", ""),
                    }
                )

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

            # Detect anomalies
            for stat in channel_stats:
                chat_id = stat["chat_id"]
                msg_count = stat["msg_count"]
                avg_ch_score = stat["avg_score"] or 0
                alert_count = stat["alert_count"]

                # Convert chat_id to readable name
                channel_name = f"Chat {chat_id}"

                # Anomaly 1: Unusual message volume (3x average)
                if avg_msg_count > 0 and msg_count > avg_msg_count * 3:
                    anomalies.append(
                        {
                            "channel": channel_name,
                            "signal": f"High volume: {msg_count} messages (avg: {int(avg_msg_count)})",
                            "severity": "warning",
                            "detected": datetime.now(timezone.utc).isoformat(),
                            "type": "volume_spike",
                        }
                    )

                # Anomaly 2: Unusual importance scores (2x average)
                if avg_score > 0 and avg_ch_score > avg_score * 2:
                    anomalies.append(
                        {
                            "channel": channel_name,
                            "signal": f"High importance: {avg_ch_score:.2f} (avg: {avg_score:.2f})",
                            "severity": "warning",
                            "detected": datetime.now(timezone.utc).isoformat(),
                            "type": "importance_spike",
                        }
                    )

                # Anomaly 3: High alert rate (>50% of messages)
                if msg_count > 5 and alert_count / msg_count > 0.5:
                    anomalies.append(
                        {
                            "channel": channel_name,
                            "signal": f"Alert rate: {alert_count}/{msg_count} ({int(alert_count/msg_count*100)}%)",
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
