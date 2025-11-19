"""Authentication and session management for TG Sentinel UI.

This module handles all authentication-related operations including:
- Session file validation
- Login/logout flows
- Handshake coordination with sentinel worker
- Login context persistence (phone codes, etc.)
- Worker authorization state tracking

ARCHITECTURAL NOTE:
- UI must NEVER open or modify tgsentinel.session directly
- All auth operations coordinate with sentinel via Redis/HTTP
- Session files are validated then forwarded to sentinel API
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Handshake coordination constants
_HANDSHAKE_FINAL_STATES = {"completed", "timeout", "error"}

# Redis keys for auth coordination
RELOGIN_KEY = "tgsentinel:relogin:handshake"
AUTH_QUEUE_KEY = "tgsentinel:auth_queue"
AUTH_RESPONSE_HASH = "tgsentinel:auth_responses"

# Auth request timeout
AUTH_REQUEST_TIMEOUT_SECS = 90.0


def validate_session_file(file_content: bytes) -> Tuple[bool, str]:
    """Validate that uploaded content is a valid Telethon session file.

    Args:
        file_content: Raw bytes of uploaded file

    Returns:
        Tuple of (is_valid, error_message)
    """
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


def resolve_session_path(
    config: Any = None, repo_root: Path | None = None
) -> str | None:
    """Resolve the current Telegram session path from multiple sources.

    Tries in order and returns the first that exists on disk; if none exist,
    returns the first configured/path candidate.

    Args:
        config: TG Sentinel config object (optional)
        repo_root: Repository root path (optional)

    Returns:
        Session path string or None if no candidates found
    """
    candidates: List[str] = []

    # From config
    if config and getattr(config, "telegram_session", None):
        candidates.append(str(getattr(config, "telegram_session")))

    # Env
    env_path = os.getenv("TG_SESSION_PATH")
    if env_path:
        candidates.append(env_path)

    # Common defaults
    container_default = "/app/data/tgsentinel.session"
    try:
        if repo_root:
            repo_default = str((repo_root / "data" / "tgsentinel.session").resolve())
        else:
            repo_default = None
    except Exception:
        repo_default = None
    candidates.append(container_default)
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


def invalidate_session(
    redis_client: Any,
    session_path: str | None,
    config: Any = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """Safely invalidate a Telethon session and clear related caches.

    Args:
        redis_client: Redis client instance
        session_path: Path to session file to remove
        config: TG Sentinel config object (optional)
        repo_root: Repository root path (optional)

    Returns:
        Dict with details of the operation for diagnostics
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
            if repo_root:
                delete_list.append(
                    str((repo_root / "data" / "tgsentinel.session").resolve())
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


def check_session_missing(redis_client: Any, flask_session: Any) -> bool:
    """Check if user session is missing or invalid.

    Returns True if either:
    1. Flask session marker is not set, OR
    2. Sentinel worker is not authorized

    This ensures UI and Sentinel stay in sync.

    Args:
        redis_client: Redis client instance
        flask_session: Flask session object

    Returns:
        True if session is missing/invalid, False otherwise
    """
    try:
        # First check Flask session marker
        flask_authenticated = flask_session.get("telegram_authenticated")

        # Then check if Sentinel worker is actually authorized
        sentinel_authorized = False
        if redis_client:
            try:
                raw = redis_client.get("tgsentinel:worker_status")
                if raw:
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    worker_status = json.loads(str(raw))
                    sentinel_authorized = worker_status.get("authorized") is True
            except Exception:
                pass

        # Sync Flask session with Sentinel state
        if sentinel_authorized and not flask_authenticated:
            # Sentinel is authenticated but Flask session isn't - sync it
            try:
                flask_session["telegram_authenticated"] = True
                flask_session.permanent = True
                logger.debug("[UI-AUTH] Synced Flask session with Sentinel auth state")
            except Exception:
                pass
        elif flask_authenticated and not sentinel_authorized:
            # Flask says authenticated but Sentinel is not - clear Flask session
            try:
                flask_session.pop("telegram_authenticated", None)
                logger.debug(
                    "[UI-AUTH] Cleared stale Flask session - Sentinel not authorized"
                )
            except Exception:
                pass
            return True

        # Session is valid only if Sentinel is authorized
        return not sentinel_authorized
    except Exception:
        return True


def read_handshake_state(redis_client: Any) -> Dict[str, Any] | None:
    """Read current re-login handshake state from Redis.

    Args:
        redis_client: Redis client instance

    Returns:
        Handshake state dict or None
    """
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


def request_relogin_handshake(redis_client: Any, timeout: float = 45.0) -> str | None:
    """Coordinate with the worker before promoting a new session file.

    Args:
        redis_client: Redis client instance
        timeout: Timeout in seconds

    Returns:
        Request ID if successful, None if Redis unavailable

    Raises:
        RuntimeError: If another operation is in progress
        TimeoutError: If worker doesn't respond in time
    """
    if redis_client is None:
        logger.warning("Redis unavailable; proceeding without re-login handshake")
        return None

    # Do not stomp over an active handshake initiated elsewhere.
    existing = read_handshake_state(redis_client)
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
        state = read_handshake_state(redis_client)
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
        current_state = read_handshake_state(redis_client)
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


def finalize_relogin_handshake(
    redis_client: Any, request_id: str | None, status: str
) -> None:
    """Finalize re-login handshake by writing final status.

    Args:
        redis_client: Redis client instance
        request_id: Handshake request ID
        status: Final status to write
    """
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


def get_login_context_file_path(phone: str, login_ctx_dir: Path) -> Path:
    """Get filesystem path for login context storage.

    Args:
        phone: Normalized phone number
        login_ctx_dir: Directory for login context files

    Returns:
        Path to context file
    """
    safe_phone = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:16]
    return login_ctx_dir / f"login_ctx_{safe_phone}.json"


def store_login_context(
    redis_client: Any,
    phone: str,
    data: Dict[str, Any],
    login_ctx_dir: Path,
    login_ctx_memory: Dict[str, Any],
) -> None:
    """Store ephemeral login state (e.g., phone_code_hash) in Redis or fallback storage.

    Args:
        redis_client: Redis client instance
        phone: Normalized phone number
        data: Login context data to store
        login_ctx_dir: Directory for filesystem fallback
        login_ctx_memory: In-memory fallback dict
    """
    try:
        if redis_client is not None:
            redis_client.setex(
                f"tgsentinel:login:phone:{phone}",
                300,
                json.dumps(data),
            )
            return
    except Exception:
        pass
    # Filesystem fallback (multi-worker safe)
    try:
        payload = {**data, "_expires": time.time() + 300}
        fpath = get_login_context_file_path(phone, login_ctx_dir)
        fpath.write_text(json.dumps(payload), encoding="utf-8")
        return
    except Exception:
        pass
    # In-memory last resort (single-worker only)
    login_ctx_memory[phone] = {**data, "_expires": time.time() + 300}


def load_login_context(
    redis_client: Any,
    phone: str,
    login_ctx_dir: Path,
    login_ctx_memory: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Load ephemeral login state from Redis or fallback storage.

    Args:
        redis_client: Redis client instance
        phone: Normalized phone number
        login_ctx_dir: Directory for filesystem fallback
        login_ctx_memory: In-memory fallback dict

    Returns:
        Login context data or None if not found/expired
    """
    try:
        if redis_client is not None:
            raw = redis_client.get(f"tgsentinel:login:phone:{phone}")
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                return json.loads(str(raw))
    except Exception:
        pass
    # Filesystem fallback
    try:
        fpath = get_login_context_file_path(phone, login_ctx_dir)
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
    data = login_ctx_memory.get(phone)
    if not data:
        return None
    if data.get("_expires", 0) < time.time():
        login_ctx_memory.pop(phone, None)
        return None
    return data


def clear_login_context(
    redis_client: Any,
    phone: str,
    login_ctx_dir: Path,
    login_ctx_memory: Dict[str, Any],
) -> None:
    """Remove stored login context for a phone number.

    Args:
        redis_client: Redis client instance
        phone: Normalized phone number
        login_ctx_dir: Directory for filesystem fallback
        login_ctx_memory: In-memory fallback dict
    """
    try:
        if redis_client is not None:
            redis_client.delete(f"tgsentinel:login:phone:{phone}")
            return
    except Exception:
        pass
    try:
        fpath = get_login_context_file_path(phone, login_ctx_dir)
        if fpath.exists():
            fpath.unlink()
            return
    except Exception:
        pass
    login_ctx_memory.pop(phone, None)


def submit_auth_request(
    redis_client: Any,
    action: str,
    payload: Dict[str, Any],
    timeout: float = AUTH_REQUEST_TIMEOUT_SECS,
) -> Dict[str, Any]:
    """Send an authentication request to the sentinel worker via Redis.

    Args:
        redis_client: Redis client instance
        action: Auth action name (e.g., "send_code", "submit_code")
        payload: Request payload dict
        timeout: Timeout in seconds

    Returns:
        Response dict from worker

    Raises:
        RuntimeError: If Redis unavailable or request fails
        TimeoutError: If worker doesn't respond in time
    """
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


def wait_for_worker_authorization(redis_client: Any, timeout: float = 60.0) -> bool:
    """Wait for the sentinel worker to report an authorized state.

    Args:
        redis_client: Redis client instance
        timeout: Timeout in seconds

    Returns:
        True if worker became authorized, False otherwise
    """
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
                return True
        time.sleep(poll_interval)
    logger.warning(
        "[UI-AUTH] Timeout waiting for worker authorization (%.1fs, %d polls)",
        timeout,
        poll_count,
    )
    return False
