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

# Import shared utilities (handle both package and direct imports)
try:
    from .utils import (
        format_timestamp,
        truncate,
        mask_phone,
        normalize_phone,
        format_display_phone,
        normalize_tags,
        fallback_username,
        fallback_avatar,
    )
    from .utils.serializers import serialize_channels
    from .utils.validators import validate_config_payload
    from .services.data_service import DataService
    from .services.profiles_service import ProfileService, init_profile_service
    from .redis_cache import (
        load_cached_user_info,
        wait_for_cached_user_info,
        get_avatar_url,
        credential_fingerprint,
        publish_ui_credentials,
        get_stream_name,
    )
    from .auth import (
        validate_session_file,
        resolve_session_path,
        invalidate_session,
        check_session_missing,
        read_handshake_state,
        request_relogin_handshake,
        finalize_relogin_handshake,
        get_login_context_file_path,
        store_login_context,
        load_login_context,
        clear_login_context,
        submit_auth_request,
        wait_for_worker_authorization,
    )
except ImportError:
    from utils import (
        format_timestamp,
        truncate,
        mask_phone,
        normalize_phone,
        format_display_phone,
        normalize_tags,
        fallback_username,
        fallback_avatar,
    )
    from utils.serializers import serialize_channels
    from utils.validators import validate_config_payload
    from services.data_service import DataService
    from services.profiles_service import ProfileService, init_profile_service
    from redis_cache import (
        load_cached_user_info,
        wait_for_cached_user_info,
        get_avatar_url,
        credential_fingerprint,
        publish_ui_credentials,
        get_stream_name,
    )
    from auth import (
        validate_session_file,
        resolve_session_path,
        invalidate_session,
        check_session_missing,
        read_handshake_state,
        request_relogin_handshake,
        finalize_relogin_handshake,
        get_login_context_file_path,
        store_login_context,
        load_login_context,
        clear_login_context,
        submit_auth_request,
        wait_for_worker_authorization,
    )

# When this module is imported as top-level ``app`` (e.g. tests that do
# ``import app`` after adding the ``ui`` folder to sys.path), ensure that
# ``ui.app`` points at the same module object. This keeps patches applied
# via ``ui.app`` (for example, redis_client overrides) visible to the
# Flask view functions that were registered under the ``app`` name.
if __name__ == "app":  # pragma: no cover - import aliasing glue
    sys.modules.setdefault("ui.app", sys.modules[__name__])


# Backward compatibility aliases (for code that calls _name instead of name)
_format_timestamp = format_timestamp
_truncate = truncate
_mask_phone = mask_phone
_normalize_phone = normalize_phone
_format_display_phone = format_display_phone
_normalise_tags = normalize_tags  # Note: British spelling in original
_fallback_username = fallback_username
_fallback_avatar = fallback_avatar


# Redis cache function wrappers (keep module-level signatures)
def _load_cached_user_info() -> Dict[str, Any] | None:
    return load_cached_user_info(redis_client)


def _wait_for_cached_user_info(timeout: float = 10.0) -> bool:
    return wait_for_cached_user_info(redis_client, timeout)


def _get_avatar_url(entity_id: int, is_user: bool = True) -> str | None:
    return get_avatar_url(redis_client, entity_id, is_user)


def _credential_fingerprint() -> Dict[str, str] | None:
    return credential_fingerprint(config)


def _publish_ui_credentials() -> None:
    publish_ui_credentials(redis_client, config, CREDENTIALS_UI_KEY)


def _get_stream_name() -> str:
    return get_stream_name(config, STREAM_DEFAULT)


# Auth function wrappers (keep module-level signatures)
def _validate_session_file(file_content: bytes) -> Tuple[bool, str]:
    return validate_session_file(file_content)


def _resolve_session_path() -> str | None:
    return resolve_session_path(config, REPO_ROOT)


def _invalidate_session(session_path: str | None) -> Dict[str, Any]:
    return invalidate_session(redis_client, session_path, config, REPO_ROOT)


def _session_missing() -> bool:
    return check_session_missing(redis_client, session)


def _read_handshake_state() -> Dict[str, Any] | None:
    return read_handshake_state(redis_client)


def _request_relogin_handshake(timeout: float = 45.0) -> str | None:
    return request_relogin_handshake(redis_client, timeout)


def _finalize_relogin_handshake(request_id: str | None, status: str) -> None:
    finalize_relogin_handshake(redis_client, request_id, status)


def _ctx_file_for_phone(phone: str) -> Path:
    return get_login_context_file_path(phone, LOGIN_CTX_DIR)


def _store_login_context(phone: str, data: Dict[str, Any]) -> None:
    store_login_context(redis_client, phone, data, LOGIN_CTX_DIR, _login_ctx)


def _load_login_context(phone: str) -> Dict[str, Any] | None:
    return load_login_context(redis_client, phone, LOGIN_CTX_DIR, _login_ctx)


def _clear_login_context(phone: str) -> None:
    clear_login_context(redis_client, phone, LOGIN_CTX_DIR, _login_ctx)


def _submit_auth_request(
    action: str, payload: Dict[str, Any], timeout: float = 90.0
) -> Dict[str, Any]:
    return submit_auth_request(redis_client, action, payload, timeout)


def _wait_for_worker_authorization(timeout: float = 60.0) -> bool:
    return wait_for_worker_authorization(redis_client, timeout)


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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from tgsentinel.config import AppCfg, load_config  # type: ignore  # noqa: E402
from tgsentinel.store import init_db  # type: ignore  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)

# SECRET_KEY configuration
# In production, UI_SECRET_KEY must be provided via environment.
# For local development and tests we fall back to a fixed, clearly
# insecure default to avoid hard failures during imports.
SECRET_KEY = os.environ.get("UI_SECRET_KEY")
if not SECRET_KEY:
    logger.warning(
        "UI_SECRET_KEY not set; using an insecure default key. "
        "Do NOT use this configuration in production."
    )
    SECRET_KEY = "dev-insecure-ui-secret-key"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = SECRET_KEY
# Make sessions permanent (survive browser restart) with 30-day lifetime
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
# Relaxed CORS for private app use
CORS(app, resources={r"/*": {"origins": "*", "supports_credentials": True}})

# Initialize Flask-Limiter for rate limiting (optional, requires Redis)
# DISABLED: Rate limiting disabled for private app environment
limiter = None
logger.info("Rate limiting disabled - private app environment")

# Original rate limiting configuration (disabled):
# is_testing = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
# if not is_testing:
#     try:
#         from flask_limiter import Limiter
#         from flask_limiter.util import get_remote_address
#         redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
#         if redis_url and "redis" in redis_url:
#             limiter = Limiter(
#                 app=app,
#                 key_func=get_remote_address,
#                 default_limits=["200 per day", "50 per hour"],
#                 storage_uri=redis_url,
#                 enabled=True,
#             )
#             logger.info("Initialized Flask-Limiter with Redis storage (avatars exempt)")
#             try:
#                 from ui.api.static_routes import set_limiter, serve_avatar_from_redis
#                 limiter.exempt(serve_avatar_from_redis)
#                 set_limiter(limiter)
#                 logger.info("Avatar endpoint exempted from rate limiting")
#             except Exception as exempt_exc:
#                 logger.warning(f"Could not exempt avatar endpoint: {exempt_exc}")
#     except ImportError:
#         logger.debug("Flask-Limiter not available - rate limiting disabled")
#     except Exception as exc:
#         logger.debug(f"Flask-Limiter disabled: {exc}\")\n#         limiter = None
# else:
#     logger.debug("Flask-Limiter disabled in test environment")


# Add CSP headers that allow inline scripts and eval (relaxed for private app)
@app.after_request
def add_security_headers(response):
    # Relaxed CSP for private internal app - allows inline scripts, eval, and blob URLs
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net https://cdn.socket.io https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob: https:; "
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

# Import dependency container
try:
    from ui.core import Dependencies, get_deps
except ImportError:
    from core import Dependencies, get_deps  # type: ignore

# Initialize dependency container singleton
deps = get_deps()

# Legacy global variables (deprecated - use deps.* instead)
# These are kept for backward compatibility during refactoring
config: AppCfg | None = None
redis_client: Any = None
engine: Engine | None = None
data_service: DataService | None = None
profile_service: ProfileService | None = None
_init_lock = threading.Lock()
_is_initialized = False
_login_ctx: Dict[str, Dict[str, Any]] = {}

# Login context directory for filesystem fallback
LOGIN_CTX_DIR = Path(__file__).parent.parent / "data" / "login_ctx"
LOGIN_CTX_DIR.mkdir(parents=True, exist_ok=True)

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


# In-memory login context fallback (single-worker only)# Cache of expensive lookups with short lifetimes.
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

    # Reset Flask app state to allow re-initialization in tests
    app._got_first_request = False

    # Clear registered view functions and blueprints to prevent conflicts
    # But preserve Flask's built-in static handler
    static_view = app.view_functions.get("static")
    app.view_functions.clear()
    if static_view:
        app.view_functions["static"] = static_view

    app.blueprints.clear()

    # Rebuild URL map from scratch, but add back the static route
    from werkzeug.routing import Map

    app.url_map = Map()
    if static_view:
        # Re-add Flask's static endpoint
        app.add_url_rule(
            f"{app.static_url_path}/<path:filename>",
            endpoint="static",
            view_func=static_view,
        )


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

    global config, redis_client, engine, profile_service, _is_initialized

    logger.info("[INIT] init_app() called - starting initialization")

    with _init_lock:
        if _is_initialized:
            logger.info("[INIT] Already initialized, skipping")
            return

        try:
            config = load_config()
            logger.info("Loaded TG Sentinel configuration")
        except Exception as exc:
            logger.warning("Falling back to environment defaults: %s", exc)
            config = None

        # Initialize UI-specific database (ui.db)
        try:
            try:
                from ui.database import init_ui_db
            except ImportError:
                # Fallback for when running as script (not as module)
                import importlib.util

                db_module_path = Path(__file__).parent / "database.py"
                spec = importlib.util.spec_from_file_location(
                    "ui.database", db_module_path
                )
                if spec is None or spec.loader is None:
                    raise ImportError(f"Cannot load ui.database from {db_module_path}")
                db_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(db_module)
                init_ui_db = db_module.init_ui_db

            # NOTE: UI database (ui.db) removed - it was never used
            # All data is stored in Sentinel's database and accessed via HTTP API
            logger.info("UI database infrastructure removed - using Sentinel API only")
        except Exception as ui_db_exc:
            logger.debug("UI database module not available (expected): %s", ui_db_exc)

        # NOTE: UI no longer opens sentinel DB - violates dual-DB architecture
        # All data access from sentinel happens via HTTP API or Redis
        engine = None

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

        # Initialize data service
        global data_service
        try:
            data_service = DataService(
                redis_client=redis_client,
                config=config,
                query_one_func=_query_one,
                query_all_func=_query_all,
                get_stream_name_func=_get_stream_name,
                truncate_func=_truncate,
                normalize_tags_func=_normalise_tags,
                format_timestamp_func=_format_timestamp,
            )
            logger.info("Initialized DataService")
        except Exception as ds_exc:
            logger.warning("Failed to initialize DataService: %s", ds_exc)
            data_service = None

        # Initialize profile service
        try:
            profile_service = init_profile_service(
                data_dir=Path(__file__).parent.parent / "data"
            )
            logger.info("Initialized ProfileService")
        except Exception as ps_exc:
            logger.warning("Failed to initialize ProfileService: %s", ps_exc)
            profile_service = None

        # Initialize config service
        try:
            from ui.services.config_service import ConfigService

            config_path = Path(os.getenv("TG_CONFIG_PATH", "config/tgsentinel.yml"))
            config_service = ConfigService(config_path)
            logger.info("Initialized ConfigService")
        except Exception as cs_exc:
            logger.warning("Failed to initialize ConfigService: %s", cs_exc)
            config_service = None

        # Register blueprints
        try:
            # Use relative import when running as module, absolute when needed
            try:
                from ui.routes.session import (
                    session_bp,
                    inject_dependencies,
                    inject_helpers,
                )
            except ImportError:
                from routes.session import (
                    session_bp,
                    inject_dependencies,
                    inject_helpers,
                )

            # Inject dependencies into blueprint
            inject_dependencies(config, redis_client, AUTH_REQUEST_TIMEOUT_SECS)

            # Inject helper functions
            inject_helpers(
                load_cached_user_info=_load_cached_user_info,
                fallback_username=_fallback_username,
                fallback_avatar=_fallback_avatar,
                mask_phone=_mask_phone,
                format_display_phone=_format_display_phone,
                validate_session_file=_validate_session_file,
                wait_for_worker_authorization=_wait_for_worker_authorization,
                resolve_session_path=_resolve_session_path,
                invalidate_session=_invalidate_session,
                normalize_phone=_normalize_phone,
                store_login_context=_store_login_context,
                load_login_context=_load_login_context,
                clear_login_context=_clear_login_context,
                submit_auth_request=_submit_auth_request,
                serialize_channels=_serialize_channels,
            )

            # Register blueprint
            app.register_blueprint(session_bp, url_prefix="/api/session")
            logger.info("Registered session blueprint at /api/session")
        except ImportError as bp_exc:
            logger.warning("Failed to import session blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register session blueprint: %s", bp_exc)

        # Register dashboard blueprint
        try:
            try:
                from ui.routes.dashboard import dashboard_bp
            except ImportError:
                # Fallback for when running as script (not as module)
                from routes.dashboard import dashboard_bp  # type: ignore

            # Register dashboard blueprint at single prefix
            app.register_blueprint(dashboard_bp, url_prefix="/api")
            logger.info("Registered dashboard blueprint at /api")
        except ImportError as bp_exc:
            logger.warning("Failed to import dashboard blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error(
                "Failed to register dashboard blueprint: %s (app=%s, dashboard_bp=%s)",
                bp_exc,
                app,
                dashboard_bp,
            )

        # Register worker blueprint
        try:
            try:
                from ui.routes.worker import worker_bp
            except ImportError:
                # Fallback for when running as script (not as module)
                from routes.worker import worker_bp  # type: ignore

            # Register worker blueprint
            app.register_blueprint(worker_bp, url_prefix="/api/worker")
            logger.info("Registered worker blueprint at /api/worker")
        except ImportError as bp_exc:
            logger.warning("Failed to import worker blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register worker blueprint: %s", bp_exc)

        # Register analytics blueprint
        try:
            try:
                from ui.api.analytics_routes import (
                    analytics_bp,
                    init_blueprint as init_analytics,
                )
            except ImportError:
                from api.analytics_routes import analytics_bp, init_blueprint as init_analytics  # type: ignore

            # Inject dependencies into analytics blueprint
            init_analytics(
                query_all=_query_all,
                compute_summary=_compute_summary,
                compute_health=_compute_health,
                serialize_channels=_serialize_channels,
                load_alerts=_load_alerts,
                load_live_feed=_load_live_feed,
                config_obj=config,
                redis_obj=redis_client,
                socketio_obj=socketio,
                ensure_init_decorator=_ensure_init,
            )

            app.register_blueprint(analytics_bp)
            logger.info("Registered analytics blueprint at /api")
        except ImportError as bp_exc:
            logger.warning("Failed to import analytics blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register analytics blueprint: %s", bp_exc)

        # Register console blueprint
        try:
            try:
                from ui.api.console_routes import (
                    console_bp,
                    init_blueprint as init_console,
                )
            except ImportError:
                from api.console_routes import console_bp, init_blueprint as init_console  # type: ignore

            # Inject dependencies into console blueprint
            init_console(
                query_all=_query_all,
                get_stream_name=_get_stream_name,
                reload_config_fn=reload_config,
                clean_database_fn=None,  # Moved to admin blueprint
                config_obj=config,
                redis_obj=redis_client,
                socketio_obj=socketio,
                ensure_init_decorator=_ensure_init,
            )

            app.register_blueprint(console_bp)
            logger.info("Registered console blueprint at /api/console")
        except ImportError as bp_exc:
            logger.warning("Failed to import console blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register console blueprint: %s", bp_exc)

        # Register telegram blueprint
        try:
            try:
                from ui.api.telegram_routes import (
                    telegram_bp,
                    init_blueprint as init_telegram,
                )
            except ImportError:
                from api.telegram_routes import telegram_bp, init_blueprint as init_telegram  # type: ignore

            # Inject dependencies into telegram blueprint
            init_telegram(
                redis_obj=redis_client,
                config_obj=config,
                load_config_fn=load_config,
                ensure_init_decorator=_ensure_init,
            )

            app.register_blueprint(telegram_bp)
            logger.info("Registered telegram blueprint at /api/telegram")
        except ImportError as bp_exc:
            logger.warning("Failed to import telegram blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register telegram blueprint: %s", bp_exc)

        # Register developer blueprint
        try:
            try:
                from ui.api.developer_routes import (
                    developer_bp,
                    init_blueprint as init_developer,
                )
            except ImportError:
                from api.developer_routes import developer_bp, init_blueprint as init_developer  # type: ignore

            # Inject dependencies
            init_developer(
                config_obj=config,
                ensure_init_decorator=_ensure_init,
                redis_client_obj=redis_client,
                limiter_obj=limiter,
            )

            app.register_blueprint(developer_bp)
            logger.info("Registered developer blueprint at /api")
        except ImportError as bp_exc:
            logger.warning("Failed to import developer blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register developer blueprint: %s", bp_exc)

        # Register views blueprint (HTML pages)
        try:
            try:
                from ui.routes.views import views_bp
            except ImportError:
                # Fallback for when running as script (not as module)
                from routes.views import views_bp  # type: ignore

            # Register views blueprint (no url_prefix - routes at root level)
            app.register_blueprint(views_bp)
            logger.info("Registered views blueprint at /")
        except ImportError as bp_exc:
            logger.warning("Failed to import views blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register views blueprint: %s", bp_exc)

        # NOTE: config_routes.py is DEPRECATED - config_info_routes.py is now the primary config blueprint
        # It has been renamed to "config" to avoid naming conflicts
        # Commenting out the old config_bp registration to prevent route conflicts
        # try:
        #     try:
        #         from ui.api.config_routes import (
        #             config_bp,
        #             init_blueprint as init_config,
        #         )
        #     except ImportError:
        #         from api.config_routes import config_bp, init_blueprint as init_config  # type: ignore
        #
        #     # Inject dependencies into config blueprint
        #     init_config(redis_obj=redis_client, config_instance=config)
        #
        #     app.register_blueprint(config_bp)
        #     logger.info("Registered config blueprint at /api/config")
        # except ImportError as bp_exc:
        #     logger.warning("Failed to import config blueprint: %s", bp_exc)
        # except Exception as bp_exc:
        #     logger.error("Failed to register config blueprint: %s", bp_exc)

        # Register profiles blueprint
        try:
            try:
                from ui.routes.profiles import (
                    profiles_bp,
                    init_profiles_routes,
                )
            except ImportError:
                from routes.profiles import profiles_bp, init_profiles_routes  # type: ignore

            # Inject dependencies into profiles blueprint
            init_profiles_routes(
                config=config,
                engine=engine,
                query_one=_query_one,
                query_all=_query_all,
                profile_service=profile_service,
            )

            app.register_blueprint(profiles_bp)
            logger.info("Registered profiles blueprint at /api/profiles")
        except ImportError as bp_exc:
            logger.warning("Failed to import profiles blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register profiles blueprint: %s", bp_exc)

        # Register channels blueprint
        try:
            try:
                from ui.routes.channels import (
                    channels_bp,
                    init_channels_routes,
                )
            except ImportError:
                from routes.channels import channels_bp, init_channels_routes  # type: ignore

            # Inject dependencies into channels blueprint
            with app.app_context():
                init_channels_routes(
                    config=config,
                    reload_config_fn=reload_config,
                )

            app.register_blueprint(channels_bp)
            logger.info("Registered channels blueprint at /api/config/channels")
        except ImportError as bp_exc:
            logger.warning("Failed to import channels blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register channels blueprint: %s", bp_exc)

        # Register users blueprint
        try:
            try:
                from ui.routes.users import (
                    users_bp,
                    set_reload_config_fn,
                )
            except ImportError:
                from routes.users import users_bp, set_reload_config_fn  # type: ignore

            # Inject dependencies into users blueprint
            set_reload_config_fn(reload_config)

            app.register_blueprint(users_bp)
            logger.info("Registered users blueprint at /api/config/users")
        except ImportError as bp_exc:
            logger.warning("Failed to import users blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register users blueprint: %s", bp_exc)

        # Register admin blueprint
        try:
            try:
                from ui.routes.admin import (
                    admin_bp,
                    init_admin_routes,
                )
            except ImportError:
                from routes.admin import admin_bp, init_admin_routes  # type: ignore

            # Inject dependencies into admin blueprint
            init_admin_routes(
                redis_client=redis_client,
                execute_fn=_execute,
                serialize_channels_fn=_serialize_channels,
                get_stream_name_fn=_get_stream_name,
            )

            app.register_blueprint(admin_bp)
            logger.info("Registered admin blueprint for management endpoints")
        except ImportError as bp_exc:
            logger.warning("Failed to import admin blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register admin blueprint: %s", bp_exc)

        # Register static routes blueprint
        try:
            try:
                from ui.api.static_routes import init_static_directories, static_bp
            except ImportError:
                from api.static_routes import init_static_directories, static_bp  # type: ignore

            app.register_blueprint(static_bp)
            logger.info("Registered static routes blueprint")

            # Initialize static directory structure once during startup
            init_static_directories(app)
        except ImportError as bp_exc:
            logger.warning("Failed to import static routes blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register static routes blueprint: %s", bp_exc)

        # Register UI lock routes blueprint
        try:
            try:
                from ui.api.ui_lock_routes import ui_lock_bp
            except ImportError:
                from api.ui_lock_routes import ui_lock_bp  # type: ignore

            app.register_blueprint(ui_lock_bp)
            logger.info("Registered UI lock routes blueprint")
        except ImportError as bp_exc:
            logger.warning("Failed to import UI lock routes blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register UI lock routes blueprint: %s", bp_exc)

        # Register participant routes blueprint
        try:
            try:
                from ui.api.participant_routes import participant_bp
            except ImportError:
                from api.participant_routes import participant_bp  # type: ignore

            app.register_blueprint(participant_bp)
            logger.info("Registered participant routes blueprint")
        except ImportError as bp_exc:
            logger.warning("Failed to import participant routes blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register participant routes blueprint: %s", bp_exc)

        # Register config info routes blueprint
        try:
            try:
                from ui.api.config_info_routes import config_info_bp
            except ImportError:
                from api.config_info_routes import config_info_bp  # type: ignore

            app.register_blueprint(config_info_bp)
            logger.info("Registered config info routes blueprint")
        except ImportError as bp_exc:
            logger.warning("Failed to import config info routes blueprint: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register config info routes blueprint: %s", bp_exc)

        # Register Socket.IO handlers
        try:
            try:
                from ui.websockets import register_socketio_handlers
            except ImportError:
                from websockets import register_socketio_handlers  # type: ignore

            register_socketio_handlers(socketio)
        except ImportError as bp_exc:
            logger.warning("Failed to import Socket.IO handlers: %s", bp_exc)
        except Exception as bp_exc:
            logger.error("Failed to register Socket.IO handlers: %s", bp_exc)

        # Populate dependency container
        deps.config = config
        deps.redis_client = redis_client
        deps.engine = engine
        deps.data_service = data_service
        deps.profile_service = profile_service
        deps.config_service = config_service
        deps._login_ctx = _login_ctx
        deps.mark_initialized()

        # Register Sentinel restart endpoint
        @app.route("/api/sentinel/restart", methods=["POST"])
        def restart_sentinel():
            """Restart Sentinel container when system settings change."""
            try:
                import subprocess

                # Execute docker compose restart sentinel
                result = subprocess.run(
                    ["docker", "compose", "restart", "sentinel"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

                if result.returncode == 0:
                    logger.info("Sentinel container restart initiated successfully")
                    return (
                        jsonify({"status": "ok", "message": "Sentinel is restarting"}),
                        200,
                    )
                else:
                    logger.error(f"Sentinel restart failed: {result.stderr}")
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": f"Restart failed: {result.stderr}",
                            }
                        ),
                        500,
                    )

            except subprocess.TimeoutExpired:
                logger.error("Sentinel restart timed out")
                return jsonify({"status": "error", "message": "Restart timed out"}), 500
            except Exception as e:
                logger.error(f"Sentinel restart failed: {e}", exc_info=True)
                return jsonify({"status": "error", "message": str(e)}), 500

        # Debug: Log config state
        if config:
            logger.info(
                f"Dependency container populated with config: channels={len(config.channels) if hasattr(config, 'channels') else 'N/A'}, users={len(config.monitored_users) if hasattr(config, 'monitored_users') else 'N/A'}"
            )
        else:
            logger.warning("Dependency container populated but config is None")

        logger.info("Dependency container populated")

        # Register compatibility routes (after all blueprints)
        @app.route("/logout", methods=["GET", "POST"])
        def legacy_logout_redirect():
            """Redirect /logout for backward compatibility.

            GET requests (browser navigation): Clear Flask session only and redirect to root
            POST requests: Forward to the API endpoint (which handles full cleanup)
            """
            from flask import redirect, request

            if request.method == "GET":
                # Browser navigation - ONLY clear Flask session markers
                # Redis cleanup is handled by POST /api/session/logout to avoid
                # race conditions where navigation accidentally wipes fresh login data
                try:
                    from flask import session as flask_session

                    flask_session.pop("telegram_authenticated", None)
                    flask_session.pop("ui_locked", None)
                except Exception as clear_exc:
                    logger.debug("Session clear during GET /logout: %s", clear_exc)
                # Redirect to root - authentication gate will show login
                return redirect("/")
            else:
                # POST request - forward to API endpoint (handles Redis + Sentinel cleanup)
                return redirect("/api/session/logout", code=307)

        logger.info(
            "Registered compatibility route: /logout (GET->/, POST->/api/session/logout)"
        )

        # Add global before_request hook for authentication
        @app.before_request
        def check_authentication():
            """Check authentication before each request."""
            # Skip auth check in test mode if configured
            if app.config.get("TESTING") or UI_SKIP_AUTH:
                return None

            from flask import request as req_obj, render_template

            path = req_obj.path

            # Always allow static assets and specific endpoints
            if (
                path.startswith("/static/")
                or path.startswith("/data/")
                or path == "/favicon.ico"
            ):
                return None

            # Helper to check worker authorization
            def _check_worker_auth() -> bool | None:
                try:
                    if redis_client:
                        raw = redis_client.get("tgsentinel:worker_status")
                        if raw:
                            if isinstance(raw, bytes):
                                raw = raw.decode()
                            status = json.loads(str(raw))
                            return status.get("authorized") is True
                    return None
                except Exception:
                    return None

            is_session_missing = _session_missing()
            worker_auth = _check_worker_auth()

            # Default-locked logic: explicitly locked OR (UI_LOCK enabled AND NOT has_been_unlocked)
            explicitly_locked = bool(session.get("ui_locked"))
            ui_lock_enabled = bool(UI_LOCK_PASSWORD or os.getenv("UI_LOCK_TIMEOUT"))
            has_been_unlocked = bool(session.get("ui_has_been_unlocked"))
            is_locked = explicitly_locked or (ui_lock_enabled and not has_been_unlocked)

            # API routes
            if path.startswith("/api/"):
                # Allow certain API endpoints without authentication
                allowed_api_paths = (
                    "/api/session/",
                    "/api/ui/lock",
                    "/api/worker/status",
                    "/api/worker/logout-progress",
                    "/api/worker/login-progress",
                    "/api/avatar/",
                )
                if any(path.startswith(p) for p in allowed_api_paths):
                    return None

                # Require login for other API routes
                if is_session_missing or (worker_auth is False):
                    return (
                        jsonify({"status": "error", "message": "Login required"}),
                        401,
                    )

                # Check UI lock for other APIs
                if is_locked:
                    return (
                        jsonify({"status": "locked", "message": "UI locked"}),
                        423,
                    )
                return None

            # HTML page routes - check UI lock first, then authentication
            # This ensures the lockout screen takes precedence over the login screen
            if is_locked:
                return render_template("locked_ui.html"), 423

            if is_session_missing or (worker_auth is False):
                return render_template("locked.html"), 401

            return None

        logger.info("Registered global authentication check (before_request)")

        _is_initialized = True
        logger.info(
            "[INIT] init_app() completed successfully - all blueprints registered"
        )


def _ensure_init(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that guarantees :func:`init_app` ran before using shared state."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        global _is_initialized
        if not _is_initialized:
            # In tests, allow fixtures to inject their own redis_client/engine
            # without init_app overwriting those patches.
            if app.config.get("TESTING") and redis_client is not None:
                _is_initialized = True
            else:
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

                # Default-locked logic: explicitly locked OR (UI_LOCK enabled AND NOT has_been_unlocked)
                explicitly_locked = bool(session.get("ui_locked"))
                ui_lock_enabled = bool(UI_LOCK_PASSWORD or os.getenv("UI_LOCK_TIMEOUT"))
                has_been_unlocked = bool(session.get("ui_has_been_unlocked"))
                is_locked = explicitly_locked or (
                    ui_lock_enabled and not has_been_unlocked
                )

                if path.startswith("/api/"):
                    allowed = (
                        path.startswith("/api/session/")
                        or path.startswith("/api/ui/lock")
                        or path.startswith("/api/worker/status")
                        or path.startswith(
                            "/api/avatar/"
                        )  # Allow avatar access without auth
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
                        # Check UI lock first, then authentication
                        if is_locked:
                            return _rt("locked_ui.html"), 423
                        if is_session_missing or (worker_auth is False):
                            return _rt("locked.html"), 401
        except Exception:
            pass
        return func(*args, **kwargs)

    return wrapper


def _query_one(sql: str, **params: Any) -> Any:
    """Query UI database for a single value.

    DEPRECATED: UI database (ui.db) has been removed.
    Returns None. All data should be fetched from Sentinel API.
    """
    logger.warning(
        "DEPRECATED: _query_one() called but ui.db removed. Use Sentinel API."
    )
    return None


def _query_all(sql: str, **params: Any) -> List[Dict[str, Any]]:
    """Query UI database for multiple rows.

    DEPRECATED: UI database (ui.db) has been removed.
    Returns empty list. All data should be fetched from Sentinel API.
    """
    logger.warning(
        "DEPRECATED: _query_all() called but ui.db removed. Use Sentinel API."
    )
    return []


def _execute(sql: str, **params: Any) -> None:
    """Execute a write operation (INSERT, UPDATE, DELETE) on UI database.

    DEPRECATED: UI database (ui.db) has been removed.
    Does nothing. All data operations should go through Sentinel API.
    """
    logger.warning("DEPRECATED: _execute() called but ui.db removed. Use Sentinel API.")
    return


# ============================================================================
# Profile Persistence Layer
# ============================================================================

# Thread-safe lock for profile file operations
_profiles_lock = threading.Lock()

# Profiles file path (YAML format)
PROFILES_FILE = Path(__file__).parent.parent / "data" / "profiles.yml"


def _compute_summary() -> Dict[str, Any]:
    """Wrapper for DataService.compute_summary().

    Kept for backward compatibility with existing code.
    """
    if data_service:
        return data_service.compute_summary()

    # Fallback if service not initialized
    return {
        "messages_ingested": 0,
        "alerts_sent": 0,
        "avg_importance": 0.0,
        "feedback_accuracy": 0.0,
    }


def _compute_health() -> Dict[str, Any]:
    """Wrapper for DataService.compute_health().

    Kept for backward compatibility with existing code.
    """
    if data_service:
        return data_service.compute_health(psutil=psutil, redis_module=redis)

    # Fallback if service not initialized
    return {
        "redis_stream_depth": 0,
        "database_size_mb": 0.0,
        "redis_online": False,
        "cpu_percent": None,
        "memory_mb": None,
    }


def _load_live_feed(limit: int = 20) -> List[Dict[str, Any]]:
    """Wrapper for DataService.load_live_feed().

    Kept for backward compatibility with existing code.
    """
    if data_service:
        return data_service.load_live_feed(limit=limit)

    # Fallback if service not initialized
    return []


def _load_alerts(limit: int = 100) -> List[Dict[str, Any]]:
    """Wrapper for DataService.load_alerts().

    Kept for backward compatibility with existing code.
    """
    if data_service:
        return data_service.load_alerts(limit=limit)

    # Fallback if service not initialized
    return []


def _load_digests(limit: int = 14) -> List[Dict[str, Any]]:
    """Wrapper for DataService.load_digests().

    Kept for backward compatibility with existing code.
    """
    if data_service:
        return data_service.load_digests(limit=limit)

    # Fallback if service not initialized
    today = datetime.now(timezone.utc).date().isoformat()
    return [{"date": today, "items": 0, "avg_score": 0.0}]
    return [
        {"date": today, "items": 0, "avg_score": 0.0},
    ]


def _serialize_channels() -> List[Dict[str, Any]]:
    """Wrapper for serialize_channels utility.

    Kept for backward compatibility with existing code.
    """
    return serialize_channels(config)


def _validate_config_payload(payload: Dict[str, Any]) -> None:
    """Wrapper for validate_config_payload utility.

    Kept for backward compatibility with existing code.
    """
    validate_config_payload(payload)


def _ensure_config_file_exists(cfg_path: Path) -> None:
    """Ensure config file exists, creating it with defaults if needed."""
    if cfg_path.exists():
        return

    # Create parent directory if needed
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    # Create minimal default config
    default_config = {
        "telegram": {"session": "/app/data/tgsentinel.session"},
        "alerts": {
            "mode": "dm",
            "target_channel": "",
            "digest": {"hourly": True, "daily": False, "top_n": 10},
        },
        "monitored_users": [],
        "channels": [],
    }

    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Created default configuration file: {cfg_path}")


def _write_config(payload: Dict[str, Any]) -> None:
    global config, _cached_summary, _cached_health

    cfg_path = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
    _ensure_config_file_exists(cfg_path)

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


# ═══════════════════════════════════════════════════════════════════
# All routes moved to blueprints and registered in init_app():
# - Static routes (favicon, avatars, data files) → ui.api.static_routes
# - UI lock routes → ui.api.ui_lock_routes
# - Participant info → ui.api.participant_routes
# - Config routes → ui.api.config_info_routes
# - Session routes → ui.routes.session
# - Dashboard routes → ui.routes.dashboard
# - Worker routes → ui.routes.worker
# - Analytics → ui.api.analytics_routes
# - Console → ui.api.console_routes
# - Telegram → ui.api.telegram_routes
# - Developer → ui.api.developer_routes
# - Views → ui.routes.views
# - Profiles → ui.routes.profiles
# - Channels → ui.routes.channels
# - Admin → ui.routes.admin
# Compatibility routes (e.g. /logout) are registered in init_app()
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# Socket.IO handlers (will be migrated to ui/websockets in future)
# ═══════════════════════════════════════════════════════════════════


@socketio.on("connect")
def socket_connect() -> None:
    """Handle Socket.IO connection events."""
    try:
        emit(
            "status",
            {"connected": True, "timestamp": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as exc:
        logger.debug(f"Socket connect emit failed: {exc}")


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
else:
    # When imported by WSGI server (gunicorn, etc.), initialize the app
    # Tests will skip this via TESTING flag
    if not app.config.get("TESTING"):
        init_app()
