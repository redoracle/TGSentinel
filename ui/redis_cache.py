"""Redis caching operations for TG Sentinel UI.

This module encapsulates all Redis interactions including:
- User info caching
- Avatar URL management
- Credential fingerprinting
- Worker status polling
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

# Import from utils_legacy (sibling module in ui/)
import sys
import time
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).parent))
from utils_legacy import format_display_phone  # noqa: E402

logger = logging.getLogger(__name__)


def load_cached_user_info(redis_client: Any) -> Dict[str, Any] | None:
    """Return cached Telegram account info stored by the worker in Redis.

    Args:
        redis_client: Redis client instance

    Returns:
        User info dict with username, phone, etc. or None if not cached
    """
    if not redis_client:
        logger.warning("[UI-REDIS] No redis_client provided")
        return None

    try:
        raw = redis_client.get("tgsentinel:user_info")
        if not raw:
            logger.warning("[UI-REDIS] No user_info in Redis")
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        info = json.loads(str(raw))
        if not isinstance(info, dict):  # Defensive check
            logger.error("[UI-REDIS] user_info is not a dict: %s", type(info))
            return None

        # Format phone number for display
        phone = info.get("phone")
        if phone:
            formatted = format_display_phone(str(phone))
            info["phone"] = formatted if formatted else phone
        return info
    except Exception as exc:
        logger.error(
            "[UI-REDIS] Failed to load cached user info: %s", exc, exc_info=True
        )
        return None


def wait_for_cached_user_info(redis_client: Any, timeout: float = 10.0) -> bool:
    """Poll Redis until user_info is populated or timeout.

    Args:
        redis_client: Redis client instance
        timeout: Maximum seconds to wait

    Returns:
        True if user_info appeared, False if timeout
    """
    if not redis_client:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = redis_client.get("tgsentinel:user_info")
            if raw:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def get_avatar_url(
    redis_client: Any, entity_id: int, is_user: bool = True
) -> str | None:
    """Get avatar URL for entity if cached in Redis.

    Args:
        redis_client: Redis client instance
        entity_id: User ID or Chat ID
        is_user: True if entity is a user, False if chat/channel

    Returns:
        Avatar URL if cached, None otherwise
    """
    if not redis_client:
        return None

    try:
        prefix = "user" if is_user else "chat"
        cache_key = f"tgsentinel:{prefix}_avatar:{entity_id}"

        if redis_client.exists(cache_key):
            entity_id_abs = abs(int(entity_id))
            return f"/api/avatar/{prefix}/{entity_id_abs}"
    except Exception:
        pass

    return None


def credential_fingerprint(config: Any = None) -> Dict[str, str] | None:
    """Compute credential fingerprint from API_ID and API_HASH.

    Args:
        config: Optional config object with api_id and api_hash attributes

    Returns:
        Dict with api_id and SHA256 hash of api_hash, or None if credentials missing
    """
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


def publish_ui_credentials(
    redis_client: Any,
    config: Any = None,
    credentials_ui_key: str = "tgsentinel:credentials:ui",
) -> None:
    """Publish UI credential fingerprint to Redis for sentinel validation.

    Args:
        redis_client: Redis client instance
        config: Optional config object
        credentials_ui_key: Redis key for storing UI credentials
    """
    if redis_client is None:
        return
    fingerprint = credential_fingerprint(config)
    if not fingerprint:
        return
    payload = {
        "fingerprint": fingerprint,
        "source": "ui",
        "ts": time.time(),
    }
    try:
        redis_client.set(credentials_ui_key, json.dumps(payload), ex=3600)
    except Exception:
        logger.debug("Failed to write credential fingerprint", exc_info=True)


def get_stream_name(
    config: Any = None, stream_default: str = "tgsentinel:messages"
) -> str:
    """Get Redis stream name from config or environment.

    Args:
        config: Optional config object
        stream_default: Default stream name

    Returns:
        Stream name string
    """
    if config and hasattr(config, "system") and config.system.redis:
        return config.system.redis.stream
    return os.getenv("REDIS_STREAM", stream_default)


__all__ = [
    "load_cached_user_info",
    "wait_for_cached_user_info",
    "get_avatar_url",
    "credential_fingerprint",
    "publish_ui_credentials",
    "get_stream_name",
]
