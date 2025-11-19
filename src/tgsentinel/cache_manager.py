"""Cache management for TG Sentinel.

Provides background cache refresh for channels, users, and avatars to ensure
UI has instant access to fresh data without timeouts.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from redis import Redis
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat as TgChat, User

logger = logging.getLogger(__name__)


async def _precache_handler_responses(redis_client: Redis, generation: int) -> None:
    """Pre-cache handler responses by triggering dialogs and users requests.

    This populates the response cache so UI requests get instant responses.

    Args:
        redis_client: Redis client
        generation: Current session generation
    """
    import uuid

    logger.info("[CACHE-REFRESHER] Starting pre-cache of handler responses...")

    try:
        # Trigger dialogs handler
        dialogs_request_id = str(uuid.uuid4())
        dialogs_request_key = f"tgsentinel:request:get_dialogs:{dialogs_request_id}"
        dialogs_response_key = f"tgsentinel:response:get_dialogs:{dialogs_request_id}"

        request_data = {
            "request_id": dialogs_request_id,
            "limit": 50,
            "offset_date": None,
            "offset_id": 0,
            "offset_peer": None,
        }

        redis_client.setex(dialogs_request_key, 60, json.dumps(request_data))
        logger.debug(
            f"[CACHE-REFRESHER] Triggered dialogs pre-cache request {dialogs_request_id}"
        )

        # Wait for response (timeout 10s)
        for _ in range(20):  # 20 * 0.5s = 10s timeout
            if redis_client.exists(dialogs_response_key):
                logger.info("[CACHE-REFRESHER] ✓ Dialogs pre-cached successfully")
                break
            await asyncio.sleep(0.5)

        # Trigger users handler
        users_request_id = str(uuid.uuid4())
        users_request_key = f"tgsentinel:request:get_users:{users_request_id}"
        users_response_key = f"tgsentinel:response:get_users:{users_request_id}"

        users_request_data = {"request_id": users_request_id}
        redis_client.setex(users_request_key, 60, json.dumps(users_request_data))
        logger.debug(
            f"[CACHE-REFRESHER] Triggered users pre-cache request {users_request_id}"
        )

        # Wait for response (timeout 10s)
        for _ in range(20):
            if redis_client.exists(users_response_key):
                logger.info("[CACHE-REFRESHER] ✓ Users pre-cached successfully")
                break
            await asyncio.sleep(0.5)

        logger.info("[CACHE-REFRESHER] ✓ Pre-caching complete")

    except Exception as precache_exc:
        logger.warning(
            "[CACHE-REFRESHER] Pre-cache failed (non-critical): %s",
            precache_exc,
            exc_info=False,
        )


async def cache_avatars_parallel(
    entities_with_photos: list,
    client: TelegramClient,
    redis_client: Redis,
    get_session_generation_func: Optional[Callable[[], int]] = None,
    my_generation: Optional[int] = None,
) -> bool:
    """Cache avatars for entities in parallel batches to respect rate limits.

    Args:
        entities_with_photos: List of tuples (entity_id, photo, display_name)
        client: Telethon client
        redis_client: Redis client
        get_session_generation_func: Function to get current session generation
        my_generation: The generation this caching started with

    Returns:
        True if completed normally, False if generation changed (early exit)
    """
    from .client import _cache_avatar

    BATCH_SIZE = 10  # Download 10 avatars concurrently
    BATCH_DELAY = 1  # Wait 1 second between batches to respect rate limits

    avatar_tasks = []
    total_cached = 0
    total_skipped = 0
    total_errors = 0

    for entity_id, photo, display_name in entities_with_photos:
        # Create task for avatar caching
        task = _cache_avatar(client, entity_id, photo, redis_client)
        avatar_tasks.append((task, entity_id, display_name))

        # Process batch when full
        if len(avatar_tasks) >= BATCH_SIZE:
            tasks_only = [t[0] for t in avatar_tasks]
            results = await asyncio.gather(*tasks_only, return_exceptions=True)

            # Count results
            for idx, result in enumerate(results):
                entity_id_item = avatar_tasks[idx][1]
                if isinstance(result, Exception):
                    total_errors += 1
                    logger.debug(
                        f"[CACHE-REFRESHER] Avatar error for {entity_id_item}: {result}"
                    )
                elif result:  # Avatar URL returned = cached
                    total_cached += 1
                else:  # None returned = already cached or no photo
                    total_skipped += 1

            if total_cached > 0 or total_errors > 0:
                logger.info(
                    f"[CACHE-REFRESHER] Cached batch: {total_cached} new, "
                    f"{total_skipped} skipped, {total_errors} errors"
                )

            avatar_tasks = []

            # Check for generation change between batches (user switch detection)
            if (
                get_session_generation_func is not None
                and my_generation is not None
                and get_session_generation_func() != my_generation
            ):
                logger.info(
                    "[CACHE-REFRESHER] Generation changed during avatar caching (%d -> %d), aborting avatar cache",
                    my_generation,
                    get_session_generation_func(),
                )
                return False

            await asyncio.sleep(BATCH_DELAY)

    # Process remaining tasks
    if avatar_tasks:
        tasks_only = [t[0] for t in avatar_tasks]
        results = await asyncio.gather(*tasks_only, return_exceptions=True)
        for idx, result in enumerate(results):
            entity_id_item = avatar_tasks[idx][1]
            if isinstance(result, Exception):
                total_errors += 1
                logger.debug(
                    f"[CACHE-REFRESHER] Avatar error for {entity_id_item}: {result}"
                )
            elif result:
                total_cached += 1
            else:
                total_skipped += 1

    logger.info(
        f"[CACHE-REFRESHER] ✓ Avatar caching complete: {total_cached} cached, "
        f"{total_skipped} skipped, {total_errors} errors"
    )

    return True  # Completed normally


async def channels_users_cache_refresher(
    get_client_func,
    redis_client: Redis,
    get_cached_dialogs_func,
    handshake_gate: asyncio.Event,
    authorized_check_func,
    auth_event: asyncio.Event,
    cache_ready_event: asyncio.Event,
    logout_event: asyncio.Event,
    get_session_generation_func,
    get_authorized_user_id_func,
) -> None:
    """Background task to refresh channels and users cache every 10 minutes.

    This prevents UI timeouts by maintaining fresh Redis cache that can be
    served instantly. Uses differential updates to add new entries and remove
    channels/groups we're no longer part of.

    Generation-aware: Only processes caches for the current session_generation.
    On logout or user switch, stops and waits for next auth_event.

    Args:
        get_client_func: Function that returns current Telethon client (dynamic lookup)
        redis_client: Redis client
        get_cached_dialogs_func: Function to fetch cached dialogs
        handshake_gate: Event to pause during relogin
        authorized_check_func: Function returning current authorization state
        auth_event: Event signaling authorization
        cache_ready_event: Event to set when initial cache warm-up completes
        logout_event: Event signaling logout/user switch
        get_session_generation_func: Function returning current session_generation
        get_authorized_user_id_func: Function returning current authorized_user_id
    """
    logger.info(
        "[CACHE-REFRESHER] Starting channels/users cache refresher (generation-aware)"
    )

    CACHE_INTERVAL = 600  # 10 minutes
    CACHE_TTL = 900  # 15 minutes (longer than refresh interval for safety)
    UNAUTH_GRACE_SECONDS = 10  # allow transient reconnects without tearing down caches

    # Subscribe to session update events for immediate refresh on user switch
    pubsub = redis_client.pubsub()
    await asyncio.to_thread(pubsub.subscribe, "tgsentinel:session_updated")
    logger.info("[CACHE-REFRESHER] Subscribed to session_updated events")

    # Outer loop: wait for each new auth/generation
    while True:
        try:
            logger.info(
                "[CACHE-REFRESHER] ========== OUTER LOOP ITERATION START =========="
            )

            # Phase 0: Wait for authorization
            if not authorized_check_func():
                logger.info("[CACHE-REFRESHER] Waiting for authorization...")
                await auth_event.wait()
                logger.info("[CACHE-REFRESHER] Authorization event received!")
            else:
                logger.info(
                    "[CACHE-REFRESHER] Already authorized, proceeding to generation check"
                )

            # Capture current generation at start of this auth cycle
            my_generation = get_session_generation_func()
            my_user_id = get_authorized_user_id_func()
            logger.info(
                "[CACHE-REFRESHER] Starting cache cycle for generation=%d, user_id=%s",
                my_generation,
                my_user_id,
            )

            # Generation-scoped Redis keys
            REDIS_CHANNELS_KEY = f"tgsentinel:{my_generation}:cached_channels"
            REDIS_USERS_KEY = f"tgsentinel:{my_generation}:cached_users"
            REDIS_CACHE_READY_KEY = f"tgsentinel:{my_generation}:cache_ready"

            event_set_for_generation = False
            precache_done = False

            async def perform_cache_refresh():
                nonlocal event_set_for_generation
                """Perform the actual cache refresh operation."""
                try:
                    logger.info("[CACHE-REFRESHER] Fetching dialogs for cache refresh.")
                    # Fetch fresh dialogs (fast - ~20 seconds for 365 dialogs)
                    dialogs = await get_cached_dialogs_func(force_refresh=True)
                    # Note: get_cached_dialogs_func() already logs "Fetched X dialogs"

                    # Process channels/groups metadata (fast - in-memory only)
                    channels_list = []
                    users_list = []
                    entities_with_photos = (
                        []
                    )  # Collect entities for parallel avatar caching

                    for dialog in dialogs:
                        entity = dialog.entity

                        # Process channels and groups
                        if isinstance(entity, (Channel, TgChat)):
                            chat_type = "group"
                            if isinstance(entity, Channel):
                                if getattr(entity, "broadcast", False):
                                    chat_type = "channel"
                                elif getattr(entity, "megagroup", False):
                                    chat_type = "supergroup"
                                else:
                                    chat_type = "group"

                            name = getattr(entity, "title", None) or getattr(
                                entity, "first_name", "Unknown"
                            )
                            entity_id = getattr(entity, "id", 0)

                            # Collect entity for parallel avatar caching later
                            photo = getattr(entity, "photo", None)
                            if photo and entity_id:
                                entities_with_photos.append((entity_id, photo, name))

                            channels_list.append(
                                {
                                    "id": entity_id,
                                    "name": name,
                                    "type": chat_type,
                                    "username": getattr(entity, "username", None),
                                }
                            )

                        # Process users
                        elif isinstance(entity, User):
                            name_parts = []
                            if hasattr(entity, "first_name") and entity.first_name:
                                name_parts.append(entity.first_name)
                            if hasattr(entity, "last_name") and entity.last_name:
                                name_parts.append(entity.last_name)

                            display_name = (
                                " ".join(name_parts)
                                if name_parts
                                else (
                                    entity.username
                                    if hasattr(entity, "username") and entity.username
                                    else f"User {entity.id}"
                                )
                            )

                            # Collect user for parallel avatar caching later
                            user_id = getattr(entity, "id", 0)
                            photo = getattr(entity, "photo", None)
                            if photo and user_id:
                                entities_with_photos.append(
                                    (user_id, photo, display_name)
                                )

                            users_list.append(
                                {
                                    "id": entity.id,
                                    "name": display_name,
                                    "username": getattr(entity, "username", None),
                                    "phone": getattr(entity, "phone", None),
                                    "bot": getattr(entity, "bot", False),
                                }
                            )

                    # Store metadata in Redis with TTL (both generation-scoped and global)
                    await asyncio.to_thread(
                        redis_client.setex,
                        REDIS_CHANNELS_KEY,
                        CACHE_TTL,
                        json.dumps(channels_list),
                    )
                    await asyncio.to_thread(
                        redis_client.setex,
                        "tgsentinel:cached_channels",
                        CACHE_TTL,
                        json.dumps(channels_list),
                    )
                    await asyncio.to_thread(
                        redis_client.setex,
                        REDIS_USERS_KEY,
                        CACHE_TTL,
                        json.dumps(users_list),
                    )
                    await asyncio.to_thread(
                        redis_client.setex,
                        "tgsentinel:cached_users",
                        CACHE_TTL,
                        json.dumps(users_list),
                    )

                    # Mark cache as ready (both generation-scoped and global for compatibility)
                    await asyncio.to_thread(
                        redis_client.setex, REDIS_CACHE_READY_KEY, CACHE_TTL, "1"
                    )
                    # Keep global cache_ready flag alive until explicit logout cleanup
                    await asyncio.to_thread(
                        redis_client.set,
                        "tgsentinel:cache_ready",
                        "1",
                    )

                    # Publish notification that cache is ready
                    await asyncio.to_thread(
                        redis_client.publish,
                        "tgsentinel:cache_ready_event",
                        json.dumps(
                            {
                                "event": "cache_updated",
                                "channels_count": len(channels_list),
                                "users_count": len(users_list),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )

                    logger.info(
                        f"[CACHE-REFRESHER] ✓ Updated cache: {len(channels_list)} channels, "
                        f"{len(users_list)} users"
                    )

                    # PUBLISH CACHE READY STATUS (95% - before avatar caching)
                    # This allows UI to become responsive immediately while avatars load in background
                    try:
                        login_progress_exists = await asyncio.to_thread(
                            redis_client.exists, "tgsentinel:login_progress"
                        )
                        if login_progress_exists:
                            await asyncio.to_thread(
                                redis_client.setex,
                                "tgsentinel:login_progress",
                                300,  # 5 minute TTL
                                json.dumps(
                                    {
                                        "stage": "cache_ready",
                                        "percent": 95,
                                        "message": f"✓ Loaded {len(channels_list)} channels, {len(users_list)} users. Caching avatars...",
                                        "timestamp": datetime.now(
                                            timezone.utc
                                        ).isoformat(),
                                    }
                                ),
                            )
                            logger.info(
                                "[CACHE-REFRESHER] Published cache ready status (95%) with TTL"
                            )
                    except Exception as progress_exc:
                        logger.debug(
                            "[CACHE-REFRESHER] Failed to publish progress: %s",
                            progress_exc,
                        )

                    if not event_set_for_generation:
                        cache_ready_event.set()
                        event_set_for_generation = True
                        logger.info(
                            "[CACHE-REFRESHER] ✓ Cache ready event set for generation=%d",
                            my_generation,
                        )
                        try:
                            from .redis_operations import RedisManager

                            redis_mgr = RedisManager(redis_client)
                            redis_mgr.publish_worker_status(
                                authorized=True,
                                status="ready",
                                ttl=3600,
                                extra_fields={
                                    "user_id": my_user_id,
                                    "session_generation": my_generation,
                                },
                            )
                        except Exception as status_exc:
                            logger.debug(
                                "[CACHE-REFRESHER] Failed to update worker status: %s",
                                status_exc,
                            )

                    # NOW cache avatars in parallel batches (slow - but non-blocking for login)
                    if entities_with_photos:
                        logger.info(
                            f"[CACHE-REFRESHER] Starting parallel avatar caching for {len(entities_with_photos)} entities..."
                        )
                        # Get current client dynamically (handles session imports)
                        current_client = get_client_func()
                        avatar_completed = await cache_avatars_parallel(
                            entities_with_photos,
                            current_client,
                            redis_client,
                            get_session_generation_func,
                            my_generation,
                        )
                        if not avatar_completed:
                            logger.info(
                                "[CACHE-REFRESHER] Avatar caching aborted due to generation change"
                            )
                            # Exit perform_cache_refresh early - generation check will restart outer loop
                            return
                        logger.info(
                            f"[CACHE-REFRESHER] ✓ Completed avatar caching (next refresh in {CACHE_INTERVAL}s)"
                        )

                except Exception as refresh_err:
                    logger.error(
                        "[CACHE-REFRESHER] Failed to refresh cache: %s",
                        refresh_err,
                        exc_info=True,
                    )

            # Phase 1: Initial cache warm-up (critical for login flow)
            logger.info(
                "[CACHE-REFRESHER] ========== STARTING INITIAL CACHE WARM-UP FOR GENERATION=%d ==========",
                my_generation,
            )
            await perform_cache_refresh()
            logger.info(
                "[CACHE-REFRESHER] ========== INITIAL CACHE WARM-UP COMPLETED FOR GENERATION=%d ==========",
                my_generation,
            )

            # Check if generation still matches after warm-up
            if get_session_generation_func() != my_generation:
                logger.warning(
                    "[CACHE-REFRESHER] Generation changed during warm-up (%d -> %d), aborting",
                    my_generation,
                    get_session_generation_func(),
                )
                continue  # Restart outer loop for new generation

            logger.info(
                "[CACHE-REFRESHER] ✓ Initial cache warm-up complete, proceeding to precache handler responses"
            )

            # Pre-cache dialogs and users data by triggering handler requests
            # This ensures UI requests get instant responses from the response cache
            await _precache_handler_responses(redis_client, my_generation)

            logger.info(
                "[CACHE-REFRESHER] ✓ All initialization complete, entering periodic refresh loop"
            )

            # Phase 2: Event-driven refresh loop for this generation
            # Uses asyncio tasks to immediately detect generation changes via pubsub
            while (
                authorized_check_func()
                and get_session_generation_func() == my_generation
                and not logout_event.is_set()
            ):
                try:
                    await handshake_gate.wait()

                    # Check generation BEFORE starting wait (immediate detection of relogin)
                    if get_session_generation_func() != my_generation:
                        logger.info(
                            "[CACHE-REFRESHER] Generation changed (%d -> %d), restarting for new generation",
                            my_generation,
                            get_session_generation_func(),
                        )
                        break

                    # Event-driven wait: monitors pubsub for session_authorized events
                    # This replaces the slow 600-second polling loop with immediate detection
                    wait_start = asyncio.get_event_loop().time()
                    session_change_detected = False

                    while (
                        asyncio.get_event_loop().time() - wait_start
                    ) < CACHE_INTERVAL:
                        # Check generation/logout state every second
                        if (
                            not authorized_check_func()
                            or get_session_generation_func() != my_generation
                            or logout_event.is_set()
                        ):
                            logger.info(
                                "[CACHE-REFRESHER] Generation change or logout detected during wait, exiting refresh loop"
                            )
                            session_change_detected = True
                            break

                        # Check for session_updated events (non-blocking with 1s timeout)
                        try:
                            message = await asyncio.wait_for(
                                asyncio.to_thread(pubsub.get_message, timeout=0.1),
                                timeout=1.0,
                            )
                            if message and message.get("type") == "message":
                                try:
                                    msg_data = message["data"]
                                    if isinstance(msg_data, bytes):
                                        msg_data = msg_data.decode("utf-8")
                                    data = json.loads(msg_data)
                                    event_type = data.get("event")
                                    if event_type == "session_authorized":
                                        logger.info(
                                            "[CACHE-REFRESHER] ⚡ session_authorized event received, restarting for new generation"
                                        )
                                        session_change_detected = True
                                        break
                                except Exception as parse_err:
                                    logger.debug(
                                        "[CACHE-REFRESHER] Failed to parse pubsub message: %s",
                                        parse_err,
                                    )
                        except asyncio.TimeoutError:
                            # Normal - just means no message in 1 second, continue waiting
                            pass
                        except Exception as msg_err:
                            logger.debug(
                                "[CACHE-REFRESHER] Pubsub message check error: %s",
                                msg_err,
                            )
                            await asyncio.sleep(1)

                    # Exit if generation changed or logged out during wait
                    if session_change_detected or (
                        not authorized_check_func()
                        or get_session_generation_func() != my_generation
                        or logout_event.is_set()
                    ):
                        break

                    # Perform periodic refresh (only if no session change detected)
                    logger.info(
                        "[CACHE-REFRESHER] Periodic refresh for generation=%d",
                        my_generation,
                    )
                    await perform_cache_refresh()

                except Exception as e:
                    logger.error(
                        "[CACHE-REFRESHER] Periodic refresh error: %s", e, exc_info=True
                    )
                    await asyncio.sleep(60)

            # Generation ended or logout - clean up and wait for next auth
            logger.info(
                "[CACHE-REFRESHER] ========== EXITING GENERATION %d, CLEARING CACHE_READY_EVENT ==========",
                my_generation,
            )
            cache_ready_event.clear()
            logger.info(
                "[CACHE-REFRESHER] ========== RESTARTING OUTER LOOP TO WAIT FOR NEXT AUTH/GENERATION =========="
            )
            # Loop will restart and check authorization status

        except Exception as outer_exc:
            logger.error(
                "[CACHE-REFRESHER] Outer loop error: %s", outer_exc, exc_info=True
            )
            await asyncio.sleep(60)
