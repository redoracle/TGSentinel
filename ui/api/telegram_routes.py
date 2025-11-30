"""
Telegram API Routes Blueprint

Handles Telegram entity queries (chats and users) via Redis delegation to sentinel.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

# Create blueprint
telegram_bp = Blueprint("telegram", __name__, url_prefix="/api/telegram")

# Dependencies (injected during registration)
redis_client = None
config = None
load_config: Callable | None = None


def init_blueprint(
    redis_obj: Any,
    config_obj: Any,
    load_config_fn: Callable,
    ensure_init_decorator: Callable,
) -> None:
    """Initialize blueprint with dependencies."""
    global redis_client, config, load_config

    redis_client = redis_obj
    config = config_obj
    load_config = load_config_fn


@telegram_bp.get("/chats")
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
        # FAST PATH: Try to read directly from Redis cache first
        # This provides instant UX without waiting for handlers
        cached_channels_key = "tgsentinel:cached_channels"
        cached_data = redis_client.get(cached_channels_key)

        if cached_data:
            try:
                if isinstance(cached_data, bytes):
                    cached_data = cached_data.decode()
                chats = json.loads(str(cached_data))
                logger.info(
                    "[UI-CHATS] Returning %d chats from cache (fast path)", len(chats)
                )
                return jsonify({"chats": chats})
            except (json.JSONDecodeError, TypeError) as cache_exc:
                logger.warning(
                    "[UI-CHATS] Cache read failed: %s, falling back to request/response",
                    cache_exc,
                )

        # SLOW PATH: Cache miss or invalid - use request/response pattern
        logger.info("[UI-CHATS] Cache miss, using request/response pattern")

        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request_key = f"tgsentinel:request:get_dialogs:{request_id}"

        logger.info("[UI-CHATS] Creating request: request_id=%s", request_id)
        logger.debug("[UI-CHATS] Request key: %s", request_key)

        # Submit request to sentinel
        request_data = {"request_id": request_id, "timestamp": time.time()}
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
            time.sleep(0.5)
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


@telegram_bp.get("/users")
def api_telegram_users():
    """Get list of all accessible Telegram private chats (users).

    Uses Redis request/response pattern to delegate discovery to sentinel process,
    always fetches fresh data from Telegram via MTProto.
    """
    try:
        # FAST PATH: Try to read directly from Redis cache first (instant UX)
        try:
            r = None
            # Prefer class-based Redis for cache read so tests can patch app.redis.Redis
            try:
                import redis

                r = redis.Redis(
                    host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    decode_responses=True,
                )
            except Exception:
                # Fallback to app-level redis_client if class-based is unavailable
                r = redis_client

            if r is not None:
                # Use standard cache key set by cache_manager
                cached = r.get("tgsentinel:cached_users")
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
                        logger.info(
                            "[UI-USERS] Returning %d users from cache (fast path)",
                            len(users),
                        )
                        return jsonify({"users": users})
                    except Exception as cache_exc:
                        logger.warning(
                            "[UI-USERS] Cache read failed: %s, falling back to request/response",
                            cache_exc,
                        )
        except Exception:
            # Do not fail cache shortcut path; continue to normal flow
            pass

        # SLOW PATH: Cache miss - use request/response pattern
        logger.info("[UI-USERS] Cache miss, using request/response pattern")

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

        logger.info("[UI-USERS] Waiting for response (max 30s)...")
        poll_count = 0
        for _ in range(60):  # 60 * 0.5s = 30s timeout
            poll_count += 1
            time.sleep(0.5)
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
            if load_config:
                cfg = load_config()  # type: ignore
            else:
                cfg = config
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
