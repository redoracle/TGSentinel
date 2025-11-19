"""Static file serving routes for TG Sentinel UI.

This module handles serving static files from the UI's data volume:
- Favicon
- Avatars from Redis
- Data files from UI volume
"""

import base64
import logging
from pathlib import Path

from flask import Blueprint, Response, current_app, redirect, send_from_directory

try:  # Prefer package import when available
    from ui.core import get_deps
except ImportError:  # pragma: no cover - fallback for script execution
    from core import get_deps  # type: ignore

logger = logging.getLogger(__name__)

static_bp = Blueprint("static", __name__)

# Get limiter instance if available (will be None if rate limiting disabled)
_limiter = None


def init_static_directories(app):
    """Initialize static directories during application startup.

    Creates .well-known/appspecific directory structure once to avoid
    creating it on every request.

    Args:
        app: Flask application instance
    """
    try:
        static_folder = app.static_folder or str(Path(app.root_path) / "static")
        manifest_dir = Path(static_folder) / ".well-known" / "appspecific"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized static directories: {manifest_dir}")
    except Exception as exc:
        logger.error(f"Failed to initialize static directories: {exc}", exc_info=True)


def set_limiter(limiter_instance):
    """Set the limiter instance for exempting avatar endpoint."""
    global _limiter
    _limiter = limiter_instance


def _resolve_redis_client():
    """Return the Redis client from the dependency container or Flask app."""

    try:
        deps = get_deps()
        client = getattr(deps, "redis_client", None)
        if client is not None:
            return client
    except Exception as deps_exc:  # pragma: no cover - defensive logging only
        logger.debug("Dependency container not ready: %s", deps_exc)

    return getattr(current_app, "redis_client", None)


@static_bp.route("/favicon.ico")
def favicon():
    """Serve the favicon from the logo image."""
    return send_from_directory(
        Path(current_app.root_path) / "static" / "images",
        "logo.png",
        mimetype="image/png",
    )


@static_bp.route("/api/avatar/<avatar_type>/<int:entity_id>")
def serve_avatar_from_redis(avatar_type, entity_id):
    """Serve avatar images from Redis.

    Avatars are stored in Redis as base64-encoded images by the sentinel worker.
    This endpoint retrieves them and serves them as images.

    This endpoint is exempt from rate limiting to allow unlimited avatar
    requests during cache warmup and user switches.

    Args:
        avatar_type: Either 'user' or 'chat'
        entity_id: The Telegram user_id or chat_id
    """
    redis_client = _resolve_redis_client()

    if not redis_client:
        logger.warning("Redis not available for avatar retrieval")
        return "Service unavailable", 503

    try:
        # Construct Redis key
        redis_key = f"tgsentinel:{avatar_type}_avatar:{entity_id}"

        # Get base64-encoded avatar from Redis
        avatar_b64 = redis_client.get(redis_key)

        if not avatar_b64:
            logger.debug(f"Avatar not found in Redis: {redis_key}")
            # Return default avatar
            return redirect("/static/images/logo.png")

        # Decode base64 to bytes
        if isinstance(avatar_b64, bytes):
            avatar_b64 = avatar_b64.decode("utf-8")

        avatar_bytes = base64.b64decode(avatar_b64)

        # Serve as image with cache control to prevent stale avatars between user switches
        response = Response(avatar_bytes, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    except Exception as exc:
        logger.error(f"Error serving avatar from Redis: {exc}", exc_info=True)
        return redirect("/static/images/logo.png")


@static_bp.route("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_manifest():
    """Serve Chrome DevTools metadata to silence automated probes."""

    static_folder = current_app.static_folder or str(
        Path(current_app.root_path) / "static"
    )
    manifest_dir = Path(static_folder) / ".well-known" / "appspecific"
    return send_from_directory(
        manifest_dir,
        "com.chrome.devtools.json",
        mimetype="application/json",
    )


@static_bp.route("/data/<path:filename>")
def serve_data_file(filename):
    """Serve files from the UI data directory ONLY.

    ARCHITECTURAL NOTE: UI has its own /app/data volume (tgsentinel_ui_data).
    Never access sentinel's volume - that would violate dual-DB architecture.
    Avatars and other assets should be stored in UI volume or served via API.
    """
    # UI data directory (tgsentinel_ui_data volume mounted at /app/data in ui container)
    ui_data_dir = Path("/app/data")
    target = ui_data_dir / filename

    if not target.exists():
        logger.warning(f"File not found in UI volume: {filename}")
        return "File not found", 404

    # Security: prevent directory traversal
    if not target.resolve().is_relative_to(ui_data_dir.resolve()):
        logger.warning(f"Directory traversal attempt blocked: {filename}")
        return "Forbidden", 403

    return send_from_directory(ui_data_dir, filename)
