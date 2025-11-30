"""Session management for TG Sentinel.

Handles session file coordination during relogin and periodic persistence.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Optional, Union

from redis import Redis
from telethon import TelegramClient

logger = logging.getLogger(__name__)


async def relogin_coordinator(
    client_ref: Callable[[], Optional[TelegramClient]],
    client_setter: Callable[[TelegramClient], None],
    redis_client: Redis,
    handshake_gate: asyncio.Event,
    authorized_setter: Callable[[bool], None],
    make_client_func: Callable[[Any], TelegramClient],
    cfg: Any,
    close_session_func: Callable[[], None],
    refresh_user_identity_func: Callable[[Any], Any],
    mark_authorized_func: Union[
        Callable[[], None], Callable[[], Coroutine[Any, Any, None]]
    ],
) -> None:
    """Pause Telegram client while UI replaces the session file.

    Coordinates the relogin handshake protocol:
    1. UI requests relogin by setting Redis marker to "request"
    2. Worker disconnects client and updates marker to "worker_detached"
    3. UI uploads new session file and updates marker to "promotion_complete"
    4. Worker recreates client with new session, reconnects, and validates auth
    5. Worker updates marker to "worker_resumed"

    Args:
        client_ref: Function returning current client instance
        client_setter: Function to update client instance
        redis_client: Redis client
        handshake_gate: Event to pause workers during relogin
        authorized_setter: Function to update authorized flag
        make_client_func: Function to create new client instance
        cfg: Configuration object
        close_session_func: Function to close session bindings
        refresh_user_identity_func: Function to refresh user identity cache
        mark_authorized_func: Function to mark as authorized
    """
    key = "tgsentinel:relogin"
    active_request_id: str | None = None
    deadline: datetime | None = None

    while True:
        await asyncio.sleep(0.5)
        try:
            raw = redis_client.get(key)
        except Exception as redis_err:
            logger.debug("Could not read re-login marker: %s", redis_err)
            continue

        state = None
        if raw:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                state = json.loads(str(raw))
            except Exception as parse_err:
                logger.debug("Invalid re-login marker payload: %s", parse_err)
                state = None

        if not state:
            if active_request_id and deadline and datetime.now(timezone.utc) > deadline:
                logger.warning(
                    "Re-login handshake %s timed out (marker disappeared); resuming worker",
                    active_request_id,
                )
                client = client_ref()
                if client:
                    try:
                        await client.connect()
                    except Exception as exc:
                        logger.debug("Reconnect after timeout failed: %s", exc)
                handshake_gate.set()
                active_request_id = None
                deadline = None
            continue

        status = state.get("status")
        request_id = state.get("request_id")

        if status == "request" and request_id:
            if active_request_id and request_id != active_request_id:
                # Another request arrived while one is active - wait for completion
                continue
            if active_request_id == request_id and not handshake_gate.is_set():
                # Already processing this request
                continue

            active_request_id = request_id
            deadline = datetime.now(timezone.utc) + timedelta(seconds=180)
            handshake_gate.clear()
            logger.info(
                "Re-login handshake %s requested; disconnecting Telegram client",
                request_id,
            )
            client = client_ref()
            if client:
                try:
                    # Explicitly save session before disconnect
                    try:
                        if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                            client.session.save()  # type: ignore[misc]
                    except Exception:
                        pass

                    await client.disconnect()  # type: ignore[misc]
                    close_session_func()
                except Exception as exc:
                    logger.debug("Client disconnect during handshake failed: %s", exc)
            try:
                redis_client.set(
                    key,
                    json.dumps(
                        {
                            "status": "worker_detached",
                            "request_id": request_id,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                    ex=180,
                )
            except Exception:
                pass
            try:
                redis_client.setex(
                    "tgsentinel:worker_status",
                    30,
                    json.dumps(
                        {
                            "authorized": False,
                            "status": "relogin_paused",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )
            except Exception:
                pass
            continue

        if (
            active_request_id
            and request_id == active_request_id
            and status in {"promotion_complete", "cancelled"}
        ):
            logger.info(
                "Re-login handshake %s acknowledged with status %s; reconnecting",
                request_id,
                status,
            )
            try:
                logger.info("[RELOGIN] Step 1: Recreating client with new session")
                # Telethon caches session state in memory - we must create a new client
                # instance to pick up the newly written session file
                client = client_ref()
                if not client:
                    logger.error("[RELOGIN] Client ref returned None, cannot proceed")
                    continue
                old_client = client
                try:
                    # Remove all event handlers before disconnect to prevent duplicate processing
                    try:
                        handlers = old_client.list_event_handlers()
                        for callback, event in handlers:
                            old_client.remove_event_handler(callback, event)
                        logger.debug(
                            "[RELOGIN] Removed %d event handler(s) from old client",
                            len(handlers),
                        )
                    except Exception as handler_exc:
                        logger.debug("Failed to remove handlers: %s", handler_exc)

                    # Telethon's disconnect() is synchronous and returns None
                    old_client.disconnect()  # type: ignore[misc]
                    close_session_func()
                    # Ensure session file locks are released
                    await asyncio.sleep(0.5)
                    logger.debug("[RELOGIN] Old client disconnected and session closed")
                except Exception as disc_exc:
                    logger.debug("Disconnect during relogin: %s", disc_exc)

                # Explicitly delete old client reference to release resources
                del old_client
                await asyncio.sleep(0.3)  # Additional time for SQLite lock release

                # Create fresh client with updated session file
                logger.debug("[RELOGIN] Creating new client instance")
                client = make_client_func(cfg)
                client_setter(client)

                # Try connection with retries
                logger.info("[RELOGIN] Step 2: Connecting to Telegram")
                connection_success = False
                for attempt in range(3):
                    try:
                        await asyncio.wait_for(client.connect(), timeout=30)
                        logger.info(
                            "[RELOGIN] Step 2a: Connection established (attempt %d)",
                            attempt + 1,
                        )
                        connection_success = True
                        break
                    except asyncio.TimeoutError:
                        if attempt < 2:
                            logger.warning(
                                "[RELOGIN] Connection timeout (attempt %d/3), retrying...",
                                attempt + 1,
                            )
                            await asyncio.sleep(2)
                        else:
                            logger.error("[RELOGIN] Connection failed after 3 attempts")
                    except Exception as conn_exc:
                        logger.warning(
                            "[RELOGIN] Connection error (attempt %d/3): %s",
                            attempt + 1,
                            conn_exc,
                        )
                        if attempt < 2:
                            await asyncio.sleep(2)

                if not connection_success:
                    logger.error(
                        "[RELOGIN] Failed to connect after retries; handshake failed"
                    )
                    raise RuntimeError("Connection failed after retries")

                logger.info("[RELOGIN] Step 3: Checking authorization")
                authorized = False
                try:
                    authorized = await asyncio.wait_for(
                        client.is_user_authorized(), timeout=20
                    )
                    logger.info(
                        "[RELOGIN] Step 3a: Authorization check result: %s", authorized
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[RELOGIN] Authorization check timed out, assuming not authorized"
                    )
                    authorized = False
                except Exception as auth_check_exc:
                    logger.warning(
                        "[RELOGIN] Authorization check failed: %s", auth_check_exc
                    )
                    authorized = False

                authorized_setter(authorized)

                if authorized:
                    logger.info("[RELOGIN] Step 4: Getting user info")
                    try:
                        me = await asyncio.wait_for(
                            client.get_me(), timeout=20  # type: ignore[misc]
                        )
                        logger.info(
                            "[RELOGIN] Step 4a: Got user: %s",
                            getattr(me, "username", None) if me else None,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("[RELOGIN] get_me() timed out")
                        me = None
                    except Exception as getme_exc:
                        logger.warning("[RELOGIN] get_me() failed: %s", getme_exc)
                        me = None

                    logger.info("[RELOGIN] Step 5: Refreshing identity cache")
                    try:
                        await refresh_user_identity_func(me)
                        logger.info("[RELOGIN] Step 5a: Cache refresh completed")
                    except Exception as cache_exc:
                        logger.warning("[RELOGIN] Cache refresh failed: %s", cache_exc)

                    # Mark authorized and trigger auth_event to unblock startup wait loop
                    # Don't pass user object to avoid overwriting avatar with default
                    logger.info("[RELOGIN] Step 6: Marking as authorized")
                    result = mark_authorized_func()
                    # Handle both sync and async callbacks
                    if asyncio.iscoroutine(result):
                        await result
                    logger.info(
                        "Re-login handshake %s completed; client authorized and ready",
                        request_id,
                    )
                else:
                    logger.warning(
                        "[RELOGIN] Client not authorized after session promotion"
                    )
                    logger.info(
                        "[RELOGIN] Session file may be invalid or expired - waiting for new auth"
                    )
            except Exception as exc:
                logger.error("[RELOGIN] Reconnect failed: %s", exc, exc_info=True)
            handshake_gate.set()
            active_request_id = None
            deadline = None
            try:
                redis_client.set(
                    key,
                    json.dumps(
                        {
                            "status": "worker_resumed",
                            "request_id": request_id,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                    ex=60,
                )
            except Exception:
                pass
            continue

        if active_request_id and deadline and datetime.now(timezone.utc) > deadline:
            logger.warning(
                "Re-login handshake %s timed out waiting for completion; resuming worker",
                active_request_id,
            )
            client = client_ref()
            if client:
                try:
                    await client.connect()
                except Exception as exc:
                    logger.debug("Reconnect after handshake timeout failed: %s", exc)
            handshake_gate.set()
            try:
                redis_client.set(
                    key,
                    json.dumps(
                        {
                            "status": "timeout",
                            "request_id": active_request_id,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                    ex=60,
                )
            except Exception:
                pass
            active_request_id = None
            deadline = None


async def session_persistence_handler(
    client_ref: Callable[[], TelegramClient],
    authorized_check_func: Callable[[], bool],
    redis_client: Redis | None = None,
) -> None:
    """Periodically save session to disk for durability.

    This ensures that the authenticated session is always persisted to disk,
    even if the process crashes. Critical for avoiding re-authentication.

    Args:
        client_ref: Function returning current client instance
        authorized_check_func: Function returning current authorization state
        redis_client: Redis client for checking logout status (optional)
    """
    # Wait for authorization before starting
    while not authorized_check_func():
        await asyncio.sleep(1)

    logger.info("Session persistence handler started")
    while True:
        try:
            await asyncio.sleep(60)  # Save every 60 seconds

            # Only save session if we're authorized (avoid recreating files after logout)
            if not authorized_check_func():
                logger.debug("Session persistence skipped (not authorized)")
                continue

            # Double-check Redis worker status before saving
            # This prevents recreation after UI-initiated logout
            if redis_client is not None:
                try:
                    worker_status = redis_client.get("tgsentinel:worker_status")
                    if worker_status:
                        # Decode bytes to string if needed
                        status_str = (
                            worker_status.decode("utf-8")
                            if isinstance(worker_status, bytes)
                            else str(worker_status)
                        )
                        status_data = json.loads(status_str)
                        if (
                            not status_data.get("authorized", False)
                            or status_data.get("status") == "logged_out"
                        ):
                            logger.debug(
                                "Session persistence skipped (worker status indicates logout)"
                            )
                            continue
                except Exception:
                    pass

            # Explicitly save session to SQLite
            client = client_ref()
            try:
                if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                    client.session.save()  # type: ignore[misc]
                    logger.debug("Session persisted to disk")
            except Exception as save_exc:
                logger.debug("Could not persist session: %s", save_exc)
        except asyncio.CancelledError:
            # Handle shutdown gracefully
            client = client_ref()
            try:
                if hasattr(client, "session") and hasattr(client.session, "save"):  # type: ignore[misc]
                    client.session.save()  # type: ignore[misc]
                    logger.debug("Session persisted during shutdown")
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.debug("Session persistence handler error: %s", exc)
