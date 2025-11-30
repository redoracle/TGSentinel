"""Telegram participant information routes.

This module provides endpoints for fetching detailed participant information
from Telegram, including chat and user details with caching.
"""

import json
import logging
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

participant_bp = Blueprint("participant", __name__)


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


def _get_avatar_url(entity_id: int, is_user: bool) -> str | None:
    """Get avatar URL from Redis cache if available.

    Args:
        entity_id: User ID or chat ID
        is_user: True if user, False if chat

    Returns:
        Avatar URL or None if not cached
    """
    from core import get_deps

    deps = get_deps()
    redis_client = deps.redis_client

    if not redis_client:
        return None

    try:
        prefix = "user" if is_user else "chat"
        cache_key = f"tgsentinel:{prefix}_avatar:{entity_id}"
        if redis_client.exists(cache_key):
            return f"/api/avatar/{prefix}/{entity_id}"
    except Exception:
        pass
    return None


@participant_bp.get("/api/participant/info")
def api_participant_info():
    """Get detailed participant information from Telegram."""
    from core import get_deps

    deps = get_deps()
    redis_client = deps.redis_client
    config = deps.config

    # Debug hook to understand test behaviour; kept silent in production by log level.
    logger.debug("api_participant_info called; redis_client=%r", redis_client)
    chat_id = request.args.get("chat_id")
    user_id = request.args.get("user_id")

    if not chat_id:
        return jsonify({"error": "chat_id is required"}), 400

    try:
        chat_id = int(chat_id)
        user_id = int(user_id) if user_id else None
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid chat_id or user_id"}), 400

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
                    avatar_url = _get_avatar_url(chat_id, is_user=False)
                    if avatar_url:
                        basic["avatar_url"] = avatar_url
                    return jsonify({"status": "pending", "chat": basic}), 202

                # Wait briefly for worker to process (with timeout)
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
                    avatar_url = _get_avatar_url(chat_id, is_user=False)
                    if avatar_url:
                        basic["avatar_url"] = avatar_url
                    chat_info = {"chat": basic}
                    break

        if not chat_info:
            # Final fallback if not found in config
            basic = {"id": chat_id, "title": f"Chat {chat_id}", "type": chat_type}
            # Try to include cached avatar
            avatar_url = _get_avatar_url(chat_id, is_user=False)
            if avatar_url:
                basic["avatar_url"] = avatar_url
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
                    avatar_url = _get_avatar_url(user_id, is_user=True)
                    if avatar_url:
                        u["avatar_url"] = avatar_url
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
                            cache_key_avatar = f"tgsentinel:chat_avatar:{chat_id}"
                            if redis_client.exists(cache_key_avatar):
                                chat_id_abs = abs(int(chat_id))
                                is_chat = int(chat_id) < 0
                                prefix = "chat" if is_chat else "user"
                                basic_chat["avatar_url"] = (
                                    f"/api/avatar/{prefix}/{chat_id_abs}"
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
                        cache_key_avatar = f"tgsentinel:user_avatar:{user_id}"
                        if redis_client and redis_client.exists(cache_key_avatar):
                            u["avatar_url"] = f"/api/avatar/user/{user_id}"
                    except Exception:
                        pass
                    return u

                payload = {"status": "pending", "chat": _pending_chat_info()}
                if user_id:
                    payload["user"] = _pending_user_info()
                return jsonify(payload), 202

            # Wait briefly for worker to process (with timeout)
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
                cache_key_avatar = f"tgsentinel:user_avatar:{user_id}"
                if redis_client.exists(cache_key_avatar):
                    u["avatar_url"] = f"/api/avatar/user/{user_id}"
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
