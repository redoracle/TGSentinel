"""Session management routes for TG Sentinel UI.

This blueprint handles all session-related operations including:
- Session info retrieval
- Session file upload
- Login/logout flows
- Interactive authentication (phone code, password)
- UI lock/unlock (password-protected without logout)

ARCHITECTURAL NOTE:
- UI never directly accesses tgsentinel.session file
- All session operations coordinate with Sentinel via HTTP/Redis
- Session files are validated then forwarded to Sentinel API
"""

from __future__ import annotations

import base64
import glob
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from flask import Blueprint, jsonify, request, session

# Create blueprint
session_bp = Blueprint("session", __name__)

logger = logging.getLogger(__name__)

# These will be injected by app.py when blueprint is registered
config: Any = None
redis_client: Any = None
AUTH_REQUEST_TIMEOUT_SECS: float = 90.0


def inject_dependencies(app_config, app_redis_client, auth_timeout):
    """Inject dependencies from main app into blueprint module."""
    global config, redis_client, AUTH_REQUEST_TIMEOUT_SECS
    config = app_config
    redis_client = app_redis_client
    AUTH_REQUEST_TIMEOUT_SECS = auth_timeout


# Import helper functions (will be available after app init)
# Type: Callable, but declared as Any to avoid Optional[Callable] complexity

_load_cached_user_info: Any = None
_fallback_username: Any = None
_fallback_avatar: Any = None
_mask_phone: Any = None
_format_display_phone: Any = None
_validate_session_file: Any = None
_wait_for_worker_authorization: Any = None
_resolve_session_path: Any = None
_invalidate_session: Any = None
_normalize_phone: Any = None
_store_login_context: Any = None
_load_login_context: Any = None
_clear_login_context: Any = None
_submit_auth_request: Any = None
_serialize_channels: Any = None


def inject_helpers(**helpers: Any) -> None:
    """Inject helper functions from main app."""
    global _load_cached_user_info, _fallback_username, _fallback_avatar
    global _mask_phone, _format_display_phone, _validate_session_file
    global _wait_for_worker_authorization, _resolve_session_path, _invalidate_session
    global _normalize_phone, _store_login_context, _load_login_context
    global _clear_login_context, _submit_auth_request, _serialize_channels

    _load_cached_user_info = helpers.get("load_cached_user_info")
    _fallback_username = helpers.get("fallback_username")
    _fallback_avatar = helpers.get("fallback_avatar")
    _mask_phone = helpers.get("mask_phone")
    _format_display_phone = helpers.get("format_display_phone")
    _validate_session_file = helpers.get("validate_session_file")
    _wait_for_worker_authorization = helpers.get("wait_for_worker_authorization")
    _resolve_session_path = helpers.get("resolve_session_path")
    _invalidate_session = helpers.get("invalidate_session")
    _normalize_phone = helpers.get("normalize_phone")
    _store_login_context = helpers.get("store_login_context")
    _load_login_context = helpers.get("load_login_context")
    _clear_login_context = helpers.get("clear_login_context")
    _submit_auth_request = helpers.get("submit_auth_request")
    _serialize_channels = helpers.get("serialize_channels")


@session_bp.route("/download", methods=["GET"])
def session_download():
    """Proxy session download to Sentinel API.

    SECURITY: UI never directly accesses session files.
    All downloads are proxied through Sentinel's /api/session/download,
    which enforces that only tgsentinel.session can be served.
    """
    try:
        sentinel_api_base = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )
        sentinel_url = f"{sentinel_api_base}/session/download"

        # Add admin authentication header (required by Sentinel)
        headers = {}
        admin_token = os.getenv("ADMIN_TOKEN", "")
        if admin_token:
            headers["X-Admin-Token"] = admin_token

        response = requests.get(sentinel_url, timeout=30, stream=True, headers=headers)

        if response.status_code == 200:
            # Stream the file content from Sentinel to client
            from flask import Response

            return Response(
                response.iter_content(chunk_size=8192),
                content_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                headers={
                    "Content-Disposition": 'attachment; filename="tgsentinel.session"'
                },
            )
        else:
            # Forward error response from Sentinel
            try:
                error_data = response.json()
                return jsonify(error_data), response.status_code
            except Exception:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Sentinel returned status {response.status_code}",
                        }
                    ),
                    response.status_code,
                )

    except requests.Timeout:
        logger.error("[SESSION-DOWNLOAD] Timeout connecting to Sentinel")
        return jsonify({"status": "error", "message": "Request timeout"}), 504
    except requests.RequestException as e:
        logger.error(f"[SESSION-DOWNLOAD] Failed to proxy download: {e}", exc_info=True)
        return (
            jsonify({"status": "error", "message": "Failed to download session file"}),
            502,
        )


@session_bp.route("/info", methods=["GET"])
def session_info():
    """Get current session information.

    Returns user info from Redis if authorized, or falls back to defaults.
    Includes authorization status so UI can handle logged-out state properly.
    """
    session_path = (
        getattr(config, "telegram_session", os.getenv("TG_SESSION_PATH"))
        if config
        else os.getenv("TG_SESSION_PATH")
    )

    # Check worker authorization status from Redis
    is_authorized = False
    worker_status_raw = None
    try:
        if redis_client:
            worker_status_raw = redis_client.get("tgsentinel:worker_status")
            if worker_status_raw:
                if isinstance(worker_status_raw, bytes):
                    worker_status_raw = worker_status_raw.decode()
                worker_status = json.loads(str(worker_status_raw))
                is_authorized = worker_status.get("authorized") is True
    except Exception as status_exc:
        logger.debug("Failed to check worker status: %s", status_exc)

    # Load user info from Redis (only populated when authorized)
    user_info = _load_cached_user_info() if is_authorized else None

    # Extract user data with proper fallbacks
    username = None
    avatar = None
    phone = None

    if user_info:
        # Use actual user info from Redis
        username = user_info.get("username") or user_info.get("first_name")
        phone = user_info.get("phone")

        # Get avatar - use URL from user_info as-is
        # The avatar URL points to Sentinel API which the browser can access
        avatar = user_info.get("avatar") if user_info else None

        # Fallback to avatar field in user_info if available
        if not avatar:
            avatar = user_info.get("avatar")

    # Apply fallbacks only if data not available
    if not username:
        username = _fallback_username()
    if not avatar:
        avatar = _fallback_avatar()
    # Phone number comes from authenticated session via Redis cache only

    return jsonify(
        {
            "authorized": is_authorized,
            "username": username,
            "avatar": avatar,
            "session_path": session_path,
            "phone_masked": _mask_phone(phone),
            "connected": bool(redis_client),
            "connected_chats": [channel["name"] for channel in _serialize_channels()],
        }
    )


@session_bp.route("/upload", methods=["POST"])
def session_upload():
    """Upload a Telethon session file and forward it to the sentinel worker API.

    This endpoint:
    1. Validates the uploaded session file
    2. Forwards it to the sentinel's /api/session/import endpoint
    3. Returns the sentinel's response to the browser

    The UI no longer directly writes to the filesystem - all session management
    is handled by the sentinel worker through its HTTP API.
    """
    try:
        if "session_file" not in request.files:
            return (
                jsonify(
                    {"status": "error", "message": "No session file part in request"}
                ),
                400,
            )

        file_storage = request.files.get("session_file")
        if not file_storage or not file_storage.filename:
            return (
                jsonify({"status": "error", "message": "No session file was selected"}),
                400,
            )

        content = file_storage.read() or b""

        # Basic validation before forwarding
        is_valid, validation_msg = _validate_session_file(content)
        if not is_valid:
            return (
                jsonify({"status": "error", "message": validation_msg}),
                400,
            )

        # Get sentinel API base URL from environment
        sentinel_api_base = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )
        sentinel_import_url = f"{sentinel_api_base}/session/import"

        logger.info(
            f"[UI-SESSION] Forwarding session file to sentinel at: {sentinel_import_url}"
        )

        # Forward to sentinel API using base64-encoded JSON
        try:
            payload = {"session_data": base64.b64encode(content).decode("utf-8")}

            response = requests.post(
                sentinel_import_url,
                json=payload,
                timeout=30.0,
                headers={"Content-Type": "application/json"},
            )

            # Parse sentinel's response
            try:
                sentinel_response = response.json()
            except Exception:
                logger.error(
                    f"[UI-SESSION] Sentinel returned non-JSON response: {response.text[:500]}"
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Sentinel returned invalid response (HTTP {response.status_code})",
                        }
                    ),
                    502,
                )

            # Check if sentinel accepted the session
            if response.status_code == 200 and sentinel_response.get("status") == "ok":
                logger.info("[UI-SESSION] Session successfully imported by sentinel")

                # Wait for sentinel to reconnect and authorize
                if not _wait_for_worker_authorization(timeout=60.0):
                    logger.warning(
                        "[UI-AUTH] Session imported but worker did not become ready in time"
                    )
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": (
                                    "Session imported but sentinel did not become ready in time. "
                                    "Please check worker logs."
                                ),
                            }
                        ),
                        503,
                    )

                # Mark UI session as authenticated
                try:
                    session["telegram_authenticated"] = True
                    session.permanent = True
                    # Set unlocked flag to bypass UI lock after successful authentication
                    session["ui_has_been_unlocked"] = True
                except Exception:
                    pass

                return (
                    jsonify(
                        {
                            "status": "ok",
                            "message": "Session uploaded and imported successfully",
                            "redirect": "/alerts",
                            "sentinel_response": sentinel_response.get("data", {}),
                        }
                    ),
                    200,
                )

            else:
                # Sentinel rejected the session
                error_msg = sentinel_response.get(
                    "message", "Unknown error from sentinel"
                )
                error_code = sentinel_response.get("code", "SENTINEL_ERROR")
                logger.error(f"[UI-SESSION] Sentinel rejected session: {error_msg}")

                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Sentinel rejected session: {error_msg}",
                            "code": error_code,
                        }
                    ),
                    response.status_code,
                )

        except requests.exceptions.Timeout:
            logger.error("[UI-SESSION] Timeout connecting to sentinel API")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Timeout connecting to sentinel worker. Please ensure sentinel is running.",
                    }
                ),
                504,
            )

        except requests.exceptions.ConnectionError as conn_exc:
            logger.error(f"[UI-SESSION] Connection error to sentinel: {conn_exc}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": (
                            f"Cannot connect to sentinel worker at {sentinel_import_url}. "
                            "Please ensure sentinel container is running."
                        ),
                    }
                ),
                503,
            )

        except Exception as fwd_exc:
            logger.error(
                f"[UI-SESSION] Failed to forward session to sentinel: {fwd_exc}",
                exc_info=True,
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Failed to forward session to sentinel: {str(fwd_exc)}",
                    }
                ),
                500,
            )

    except Exception as exc:
        logger.error(f"[UI-SESSION] Session upload failed: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@session_bp.route("/logout", methods=["POST"])
def session_logout():
    """Invalidate current Telegram session and clear user caches.

    This is used by the UI RE-LOGIN / SWITCH ACCOUNT control. It safely removes
    the local Telethon session file (if present) and clears cached user info in Redis.
    Also cleans up UI container's data/ and config/ directories.
    """
    try:
        session_path = _resolve_session_path()
        details = _invalidate_session(session_path)

        # Clear Flask session authentication marker
        try:
            session.pop("telegram_authenticated", None)
            session.pop("ui_locked", None)
            session.modified = True  # Ensure Flask saves the cleared session
        except Exception:
            pass

        # Clean UI container's data/ and config/ directories
        try:
            import shutil

            # Clean /app/data/ (except login_ctx directory)
            ui_data_dir = Path("/app/data")
            if ui_data_dir.exists():
                for item in ui_data_dir.iterdir():
                    # Skip login_ctx directory to preserve login state templates
                    if item.name == "login_ctx":
                        logger.info("[UI-AUTH] Skipping /app/data/login_ctx/ directory")
                        continue

                    try:
                        if item.is_file():
                            item.unlink()
                            logger.info("[UI-AUTH] Deleted UI data file: %s", item.name)
                        elif item.is_dir():
                            shutil.rmtree(item)
                            logger.info(
                                "[UI-AUTH] Deleted UI data directory: %s", item.name
                            )
                    except Exception as del_exc:
                        logger.warning(
                            "[UI-AUTH] Could not delete %s: %s", item.name, del_exc
                        )

            # Clean /app/config/ files (preserve directories like backups/)
            ui_config_dir = Path("/app/config")
            if ui_config_dir.exists():
                for item in ui_config_dir.iterdir():
                    if item.is_file():
                        try:
                            item.unlink()
                            logger.info(
                                "[UI-AUTH] Deleted UI config file: %s", item.name
                            )
                        except Exception as del_exc:
                            logger.warning(
                                "[UI-AUTH] Could not delete config file %s: %s",
                                item.name,
                                del_exc,
                            )

            logger.info("[UI-AUTH] UI volume cleanup completed")
        except Exception as cleanup_exc:
            logger.warning("[UI-AUTH] UI volume cleanup failed: %s", cleanup_exc)

        # Signal sentinel to reload config/session state after logout
        try:
            Path("/app/data/.reload_config").touch()
        except Exception:
            pass

        # Notify sentinel container to disconnect and clear its session
        if redis_client:
            try:
                # Immediately update worker_status to logged_out
                # This ensures the UI sees the logout state right away,
                # even if sentinel takes time to process the pub/sub event
                redis_client.set(
                    "tgsentinel:worker_status",
                    json.dumps(
                        {
                            "authorized": False,
                            "status": "logged_out",
                            "ts": datetime.now().isoformat(),
                        }
                    ),
                    ex=60,  # Short TTL since sentinel will clean this up properly
                )
                logger.info("[UI-AUTH] Set worker_status to logged_out")

                # Publish event for sentinel to complete cleanup
                redis_client.publish(
                    "tgsentinel:session_updated",
                    json.dumps(
                        {
                            "event": "session_logout",
                            "timestamp": datetime.now().isoformat(),
                        }
                    ),
                )
                logger.info("[UI-AUTH] Published logout event to sentinel")
            except Exception as pub_exc:
                logger.warning("[UI-AUTH] Failed to publish logout event: %s", pub_exc)

        return jsonify(
            {
                "status": "ok",
                "message": "Session cleared. You may re-login.",
                "details": details,
                "redirect": "/alerts",
            }
        )
    except Exception as exc:
        logger.error("[UI-AUTH] Logout failed: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@session_bp.route("/logout-complete", methods=["POST"])
def session_logout_complete():
    """Complete system reset: nuke DB, Redis, session files, and config YML files.

    This is a destructive operation that removes:
    - Database (sentinel.db)
    - Redis data (flushall)
    - Telegram session files (*.session*)
    - Configuration YML files (config/*.yml)
    - User avatars and cached data
    """
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


@session_bp.route("/relogin", methods=["POST"])
def session_relogin():
    """Alias for logout that provides a stronger re-auth hint to the UI."""
    try:
        session_path = _resolve_session_path()
        details = _invalidate_session(session_path)
        return jsonify(
            {
                "status": "ok",
                "message": "Session cleared. Start re-authentication.",
                "details": details,
                "relogin_required": True,
                "redirect": "/alerts",
            }
        )
    except Exception as exc:
        logger.error("Failed to relogin (logout step): %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@session_bp.route("/login/start", methods=["POST"])
def session_login_start():
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


@session_bp.route("/login/resend", methods=["POST"])
def session_login_resend():
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


@session_bp.route("/login/verify", methods=["POST"])
def session_login_verify():
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
        session.permanent = True
        # Set unlocked flag to bypass UI lock after successful authentication
        session["ui_has_been_unlocked"] = True

        return jsonify(
            {"status": "ok", "message": "Authenticated", "redirect": "/alerts"}
        )
    except Exception as exc:
        logger.error("Login verify failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@session_bp.route("/api/avatar/<prefix>/<entity_id>")
def api_avatar_proxy(prefix: str, entity_id: str):
    """Proxy avatar requests to Sentinel API.

    This endpoint forwards avatar requests from the browser to the Sentinel service,
    solving cross-origin issues since the browser can only directly access the UI port.

    Args:
        prefix: Entity type prefix ("user" or "chat")
        entity_id: Numeric entity ID

    Returns:
        JPEG image data or 404 if not found
    """
    from flask import Response

    try:
        # Get Sentinel base URL from environment
        sentinel_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080")
        avatar_url = f"{sentinel_url}/api/avatar/{prefix}/{entity_id}"

        # Forward request to Sentinel
        response = requests.get(avatar_url, timeout=5)

        if response.status_code == 200:
            # Return the image data with correct content type
            return Response(response.content, mimetype="image/jpeg")
        else:
            # Sentinel returned error, pass it through
            return Response(response.content, status=response.status_code)

    except requests.Timeout:
        logger.warning(f"Avatar request timeout for {prefix}/{entity_id}")
        return jsonify({"error": "Timeout fetching avatar"}), 504
    except Exception as exc:
        logger.error(f"Avatar proxy error for {prefix}/{entity_id}: {exc}")
        return jsonify({"error": "Failed to fetch avatar"}), 500
