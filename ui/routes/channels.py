"""Channel management API routes for TG Sentinel UI.

This blueprint handles all channel-related operations:
- List channels
- Add channels
- Delete channels
"""

import logging
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Callable, Dict, List

import yaml
from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

# Lua script for atomic lock release (check and delete in one operation)
# Returns 1 if lock was released, 0 if lock didn't exist or belongs to another owner
RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Module-level cached Redis client (lazy initialization with thread safety)
_redis_client = None
_redis_client_lock = threading.Lock()


def _get_redis_client():
    """Get or create a Redis client with connection pooling and timeouts.

    Uses double-checked locking pattern for thread-safe lazy initialization.

    Returns:
        redis.Redis: Configured Redis client with connection pooling

    Raises:
        redis.ConnectionError: If Redis is unavailable
        redis.TimeoutError: If connection times out
    """
    global _redis_client

    # First check without lock (fast path)
    if _redis_client is None:
        # Acquire lock for initialization
        with _redis_client_lock:
            # Double-check inside lock to prevent race condition
            if _redis_client is None:
                import redis

                redis_host = os.getenv("REDIS_HOST", "redis")
                redis_port = int(os.getenv("REDIS_PORT", "6379"))

                # Create client with connection pooling and timeouts
                _redis_client = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    decode_responses=True,
                    socket_connect_timeout=2,  # 2s timeout for initial connection
                    socket_timeout=5,  # 5s timeout for read/write operations
                    socket_keepalive=True,
                    health_check_interval=30,  # Check connection health every 30s
                )
                logger.info(f"Initialized Redis client: {redis_host}:{redis_port}")

    # Health check: verify Redis is responsive
    try:
        _redis_client.ping()
    except (redis.ConnectionError, redis.TimeoutError) as e:
        logger.error(f"Redis health check failed: {e}")
        raise

    return _redis_client


# Blueprint setup
channels_bp = Blueprint("channels", __name__, url_prefix="/api/config/channels")


def init_channels_routes(
    config=None, reload_config_fn: Callable[[], Any] | None = None
):
    """Initialize channels blueprint with dependencies.

    Args:
        config: Application config object
        reload_config_fn: Function to reload configuration
    """
    # Store dependencies in current_app.extensions to avoid module-level globals
    if not hasattr(current_app, "extensions"):
        current_app.extensions = {}

    if "channels" not in current_app.extensions:
        current_app.extensions["channels"] = {}

    current_app.extensions["channels"]["config"] = config
    current_app.extensions["channels"]["reload_config_fn"] = reload_config_fn
    logger.info("Channels routes initialized")


def _current_config():
    """Return the most up-to-date config object available."""
    try:
        from core import get_deps

        deps = get_deps()
        if getattr(deps, "config", None):
            return deps.config
    except Exception as exc:  # pragma: no cover - defensive path
        logger.debug("Could not obtain deps.config: %s", exc)

    # Fallback to config stored in app.extensions
    try:
        return current_app.extensions.get("channels", {}).get("config")
    except RuntimeError:  # pragma: no cover - outside app context
        return None


def _get_sentinel_api_url() -> str:
    """Get the Sentinel API base URL."""
    return os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")


def _fetch_sentinel_config() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    """Fetch current config from Sentinel API.

    Returns:
        (config_dict, error_response_tuple) where error_response_tuple is None on success
    """
    import requests

    sentinel_api_url = _get_sentinel_api_url()
    try:
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if not response.ok:
            logger.error(f"Sentinel GET /config failed: {response.status_code}")
            return None, (
                jsonify(
                    {
                        "status": "error",
                        "message": "Could not fetch current config from Sentinel",
                    }
                ),
                503,
            )

        config = response.json().get("data", {})
        return config, None

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return None, (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )


def _update_sentinel_config(channels: list[dict[str, Any]]) -> tuple[Any, int] | None:
    """Update channels config via Sentinel API.

    Args:
        channels: List of channel dictionaries to save

    Returns:
        error_response_tuple or None on success
    """
    import requests

    sentinel_api_url = _get_sentinel_api_url()
    try:
        update_response = requests.post(
            f"{sentinel_api_url}/config",
            json={"channels": channels},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if not update_response.ok:
            logger.error(f"Sentinel POST /config failed: {update_response.status_code}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to update config via Sentinel",
                    }
                ),
                502,
            )

        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )


def _reload_ui_config() -> None:
    """Reload UI config if reload function is available."""
    reload_fn = current_app.extensions.get("channels", {}).get("reload_config_fn")
    if reload_fn:
        reload_fn()
        logger.debug("UI config reloaded")


def _fetch_channels_from_sentinel() -> List[Dict[str, Any]] | None:
    """Fetch channels from Sentinel API (single source of truth)."""
    import requests

    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if response.ok:
            config_data = response.json().get("data", {})
            channels = config_data.get("channels", [])
            logger.debug(f"Fetched {len(channels)} channels from Sentinel API")
            return list(channels)
        else:
            logger.warning(
                f"Failed to fetch channels from Sentinel: {response.status_code}"
            )
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return None


def _normalize_channel_entry(channel: Any, fallback_id: int) -> Dict[str, Any]:
    """Convert raw channel objects/dicts into the API schema."""

    def _get(field: str, default: Any) -> Any:
        if isinstance(channel, dict):
            return channel.get(field, default)
        return getattr(channel, field, default)

    channel_id = _get("id", fallback_id)
    return {
        "id": channel_id,
        "name": _get("name", f"Channel {channel_id}"),
        "vip_senders": list(_get("vip_senders", [])),
        "keywords": list(_get("keywords", [])),
        "reaction_threshold": _get("reaction_threshold", 5),
        "reply_threshold": _get("reply_threshold", 3),
        "rate_limit_per_hour": _get("rate_limit_per_hour", 10),
        "enabled": _get("enabled", True),
    }


def _serialize_channels():
    """Serialize channels from Sentinel API to JSON-friendly format."""
    channels_from_sentinel = _fetch_channels_from_sentinel()
    if channels_from_sentinel is not None:
        source_channels = channels_from_sentinel
    else:
        # Fallback to deps.config if Sentinel unavailable
        config_obj = _current_config()
        source_channels = list(getattr(config_obj, "channels", []) or [])

    result: List[Dict[str, Any]] = []
    for idx, channel in enumerate(source_channels, start=1):
        result.append(_normalize_channel_entry(channel, idx))
    return result


def _ensure_config_file_exists(config_path: Path):
    """Ensure config file exists, create from template if needed."""
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # Create minimal config
        with open(config_path, "w") as f:
            yaml.dump({"channels": []}, f)
        logger.info(f"Created new config file at {config_path}")


# ═══════════════════════════════════════════════════════════════════
# Channel Routes
# ═══════════════════════════════════════════════════════════════════


@channels_bp.get("")
def list_channels():
    """Get list of all configured channels."""
    return jsonify({"channels": _serialize_channels()})


@channels_bp.post("/add")
def add_channels():
    """Add channels via Sentinel API (single source of truth)."""
    import requests

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
        # Get current config from Sentinel
        current_config, error = _fetch_sentinel_config()
        if error:
            return error

        if current_config is None:
            return (
                jsonify({"status": "error", "message": "Failed to fetch config"}),
                503,
            )

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

        # Update config via Sentinel API
        error = _update_sentinel_config(existing_channels)
        if error:
            return error

        logger.info(f"Added {added_count} new channels via Sentinel API")

        # Reload config in UI to reflect changes immediately
        _reload_ui_config()

        return jsonify({"status": "ok", "added": added_count})

    except Exception as exc:
        logger.error(f"Channel add operation failed: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@channels_bp.put("/<int:chat_id>")
def update_channel(chat_id):
    """Update a channel's configuration via Sentinel API.

    This is the unified channel update endpoint that supports:
    - Basic channel settings (name, VIP senders, keywords, thresholds)
    - Profile bindings (two-layer architecture)
    - Per-channel overrides (keywords_extra, min_score, scoring_weights)

    Uses distributed locking via Redis to prevent concurrent update conflicts.

    Request body should contain fields to update:
    - name: Channel name
    - vip_senders: List of VIP sender IDs or usernames
    - keywords: Legacy keyword list (deprecated, use profiles instead)
    - profiles: List of profile IDs to bind
    - overrides: Dict with keywords_extra, scoring_weights, min_score, etc.
    - reaction_threshold: int
    - reply_threshold: int
    - rate_limit_per_hour: int
    """
    import time

    import redis
    import requests

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
        # Validate profile IDs if present (before acquiring lock)
        if "profiles" in payload:
            from ui.services.profiles_service import get_profile_service

            requested = payload.get("profiles") or []
            if not isinstance(requested, list):
                return (
                    jsonify(
                        {"status": "error", "message": "'profiles' must be a list"}
                    ),
                    400,
                )

            svc = get_profile_service()

            # Fetch available profiles with timeout to prevent blocking
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(svc.list_global_profiles)
                    available_profiles = future.result(timeout=5)  # 5s timeout
                    available = {p["id"] for p in available_profiles}
            except FuturesTimeoutError:
                logger.error("Profile service timed out during validation")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Profile service timed out",
                        }
                    ),
                    504,
                )
            except Exception as e:
                logger.error(f"Profile service error during validation: {e}")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Profile service unavailable",
                        }
                    ),
                    502,
                )

            invalid = [p for p in requested if p not in available]
            if invalid:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Invalid profile ids: %s" % ",".join(invalid),
                        }
                    ),
                    400,
                )

        # Get Redis client for distributed locking (with health check)
        import redis

        try:
            redis_client = _get_redis_client()
        except (redis.ConnectionError, redis.TimeoutError) as e:
            logger.error(f"Redis unavailable for channel update: {e}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Distributed locking service unavailable",
                    }
                ),
                503,
            )

        # Configuration for optimistic retry
        MAX_RETRIES = 3
        LOCK_TIMEOUT = 25  # seconds - safe for ~15s operation with buffer
        BASE_RETRY_DELAY = 0.5  # seconds - base for exponential backoff
        lock_key = f"tgsentinel:config_lock:channel:{chat_id}"

        last_error = None

        # Retry loop with distributed lock
        for attempt in range(MAX_RETRIES):
            lock_acquired = False
            lock_identifier = None

            try:
                # Acquire distributed lock with timeout
                lock_identifier = f"ui-{os.getpid()}-{time.time()}"
                lock_acquired = redis_client.set(
                    lock_key,
                    lock_identifier,
                    nx=True,  # Only set if not exists
                    ex=LOCK_TIMEOUT,  # Expire after timeout to prevent deadlocks
                )

                if not lock_acquired:
                    logger.warning(
                        f"Failed to acquire lock for channel {chat_id} on attempt {attempt + 1}/{MAX_RETRIES}"
                    )
                    last_error = "Another update is in progress"
                    time.sleep(BASE_RETRY_DELAY * (2**attempt))
                    continue

                logger.debug(
                    f"Acquired lock for channel {chat_id} (attempt {attempt + 1})"
                )

                # Get current config from Sentinel
                current_config, error = _fetch_sentinel_config()
                if error:
                    last_error = "Could not fetch current config from Sentinel"
                    continue

                if current_config is None:
                    last_error = "Config fetch returned None"
                    continue

                channels = current_config.get("channels", [])

                # Find the channel to update
                channel_index = None
                for i, ch in enumerate(channels):
                    if ch.get("id") == chat_id:
                        channel_index = i
                        break

                if channel_index is None:
                    return (
                        jsonify({"status": "error", "message": "Channel not found"}),
                        404,
                    )

                # Update channel fields from payload
                channel = channels[channel_index]

                # Update basic fields
                if "name" in payload:
                    channel["name"] = payload["name"]
                if "vip_senders" in payload:
                    channel["vip_senders"] = payload["vip_senders"]
                if "keywords" in payload:
                    channel["keywords"] = payload["keywords"]
                if "reaction_threshold" in payload:
                    channel["reaction_threshold"] = int(payload["reaction_threshold"])
                if "reply_threshold" in payload:
                    channel["reply_threshold"] = int(payload["reply_threshold"])
                if "rate_limit_per_hour" in payload:
                    channel["rate_limit_per_hour"] = int(payload["rate_limit_per_hour"])

                # Update profile bindings (two-layer architecture)
                if "profiles" in payload:
                    channel["profiles"] = payload["profiles"]
                if "overrides" in payload:
                    channel["overrides"] = payload["overrides"]

                # Update config via Sentinel API
                error = _update_sentinel_config(channels)
                if error:
                    last_error = "Sentinel rejected update"
                    logger.error(f"Sentinel POST failed on attempt {attempt + 1}")
                    time.sleep(BASE_RETRY_DELAY * (2**attempt))
                    continue

                logger.info(
                    f"Updated channel {chat_id} via Sentinel API (attempt {attempt + 1})"
                )

                # Reload config in UI
                _reload_ui_config()

                return (
                    jsonify(
                        {
                            "status": "ok",
                            "message": "Channel updated",
                            "channel": channel,
                        }
                    ),
                    200,
                )

            except requests.exceptions.Timeout:
                last_error = "Request timeout"
                logger.warning(f"Timeout on attempt {attempt + 1}/{MAX_RETRIES}")
                time.sleep(BASE_RETRY_DELAY * (2**attempt))
                continue

            except requests.exceptions.RequestException as e:
                last_error = f"Connection error: {str(e)}"
                logger.error(f"Connection error on attempt {attempt + 1}: {e}")
                time.sleep(BASE_RETRY_DELAY * (2**attempt))
                continue

            finally:
                # Always release lock if we acquired it
                if lock_acquired and lock_identifier:
                    try:
                        # Atomically release lock only if we still own it
                        # Uses Lua script to prevent race condition between check and delete
                        result = redis_client.eval(
                            RELEASE_LOCK_SCRIPT,
                            1,  # number of keys
                            lock_key,  # KEYS[1]
                            lock_identifier,  # ARGV[1]
                        )
                        if result == 1:
                            logger.debug(f"Released lock for channel {chat_id}")
                        else:
                            logger.warning(
                                f"Lock for channel {chat_id} was already released or taken by another process"
                            )
                    except Exception as lock_err:
                        logger.error(f"Failed to release lock: {lock_err}")

        # All retries exhausted
        logger.error(
            f"Failed to update channel {chat_id} after {MAX_RETRIES} attempts: {last_error}"
        )
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Update failed after {MAX_RETRIES} retries: {last_error}",
                }
            ),
            503,
        )

    except ValueError as e:
        return jsonify({"status": "error", "message": f"Invalid value: {e}"}), 400
    except Exception as exc:
        logger.error(f"Channel update operation failed: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@channels_bp.get("/<int:chat_id>")
def get_channel(chat_id):
    """Get a single channel's configuration."""
    try:
        # Get current config from Sentinel
        current_config, error = _fetch_sentinel_config()
        if error:
            return error

        if current_config is None:
            return (
                jsonify({"status": "error", "message": "Failed to fetch config"}),
                503,
            )

        channels = current_config.get("channels", [])

        # Find the channel
        for ch in channels:
            if ch.get("id") == chat_id:
                return jsonify({"status": "ok", "channel": ch})

        return jsonify({"status": "error", "message": "Channel not found"}), 404

    except Exception as exc:
        logger.error(f"Failed to get channel: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@channels_bp.route("/<int:chat_id>", methods=["DELETE"])
def delete_channel(chat_id):
    """Remove a channel from monitoring configuration via Sentinel API."""
    import requests

    try:
        # Get current config from Sentinel
        config, error = _fetch_sentinel_config()
        if error:
            return error

        if config is None:
            return (
                jsonify({"status": "error", "message": "Failed to fetch config"}),
                503,
            )

        channels = config.get("channels", [])
        original_count = len(channels)

        # Remove channel
        channels = [c for c in channels if c.get("id") != chat_id]

        if len(channels) == original_count:
            return jsonify({"status": "error", "message": "Channel not found"}), 404

        # Update config via Sentinel API
        error = _update_sentinel_config(channels)
        if error:
            return error

        logger.info(f"Removed channel {chat_id} via Sentinel API")

        # Reload config in UI
        _reload_ui_config()

        return jsonify({"status": "ok", "message": "Channel removed"}), 200

    except Exception as e:
        logger.error(f"Failed to delete channel: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
