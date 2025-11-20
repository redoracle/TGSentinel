"""
Session lifecycle management for TG Sentinel.

Handles session imports, logout operations, and client reconnection logic.
Monitors Redis pub/sub for session events and coordinates with RedisManager.
"""

import asyncio
import glob
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, Union

from redis import Redis
from telethon import TelegramClient
from telethon.tl.types import User as TgUser
from telethon.tl.types import UserProfilePhoto

from .config import AppCfg
from .redis_operations import RedisManager
from .session_helpers import SessionHelpers

log = logging.getLogger(__name__)


class SessionLifecycleManager:
    """Manages session lifecycle events (import, logout, reconnection)."""

    def __init__(
        self,
        cfg: AppCfg,
        redis_client: Redis,
        redis_manager: RedisManager,
        session_helpers: SessionHelpers,
        session_file_path: Path,
        make_client_func: Callable[[AppCfg], TelegramClient],
        start_ingestion_func: Callable[[AppCfg, TelegramClient, Redis], None],
        mark_authorized_func: Optional[
            Union[Callable[[Any], None], Callable[[Any], Coroutine[Any, Any, None]]]
        ] = None,
    ):
        """
        Initialize session lifecycle manager.

        Args:
            cfg: Application configuration
            redis_client: Redis client instance
            redis_manager: RedisManager for cache operations
            session_helpers: SessionHelpers for session operations
            session_file_path: Path to session file
            make_client_func: Function to create new TelegramClient
            start_ingestion_func: Function to start message ingestion
            mark_authorized_func: Function to mark session as authorized (triggers generation increment)
        """
        self.cfg = cfg
        self.redis_client = redis_client
        self.redis_mgr = redis_manager
        self.session_helpers = session_helpers
        self.session_file_path = session_file_path
        self.make_client = make_client_func
        self.start_ingestion = start_ingestion_func
        self.mark_authorized_func = mark_authorized_func

        # Optional attributes set after initialization for client updates
        self.auth_manager: Optional[Any] = None  # AuthManager instance
        self.participant_handler: Optional[Any] = (
            None  # ParticipantInfoHandler instance
        )

    async def _download_avatar_async(
        self, client: TelegramClient, user_id: int, photo
    ) -> None:
        """
        Download full avatar in background without blocking login flow.

        Args:
            client: Telegram client
            user_id: User ID
            photo: Photo object from User
        """
        try:
            from .client import _cache_avatar

            log.info(
                "[SESSION-MONITOR] Background avatar download started for user %s",
                user_id,
            )
            avatar_url = await _cache_avatar(client, user_id, photo, self.redis_client)
            if avatar_url:
                # Update user_info in Redis with full avatar
                user_info = self.redis_mgr.get_user_info()
                if user_info:
                    user_info["avatar"] = avatar_url
                    self.redis_mgr.cache_user_info(user_info)
                    log.info(
                        "[SESSION-MONITOR] ✓ Updated user_info with full avatar: %s",
                        avatar_url,
                    )
        except Exception as e:
            log.warning("[SESSION-MONITOR] Background avatar download failed: %s", e)

    async def handle_session_import(
        self,
        current_client: TelegramClient,
        auth_event: asyncio.Event,
        handshake_gate: asyncio.Event,
        authorized_ref: dict[str, bool],
        dialogs_cache_ref: dict[str, Any],
        client_ref: Optional[dict[str, TelegramClient]] = None,
    ) -> Optional[TelegramClient]:
        """
        Handle session import event.

        Returns new client if successful, None otherwise.
        """
        log.info(
            "[SESSION-MONITOR] Session upload detected, recreating client with new session"
        )

        try:
            old_client = current_client
            try:
                if old_client.is_connected():
                    # Remove event handlers to prevent duplicate processing
                    try:
                        handlers = old_client.list_event_handlers()
                        for callback, event in handlers:
                            old_client.remove_event_handler(callback, event)
                        log.debug(
                            "[SESSION-MONITOR] Removed %d event handler(s) from old client",
                            len(handlers),
                        )
                    except Exception as handler_exc:
                        log.debug("Failed to remove handlers: %s", handler_exc)

                    # Disconnect old client
                    old_client.disconnect()
                self.session_helpers.close_session_binding()
                # Ensure session file locks are released
                await asyncio.sleep(0.5)
                log.debug(
                    "[SESSION-MONITOR] Old client disconnected and session closed"
                )
            except Exception as disc_exc:
                log.debug("Disconnect during session reload: %s", disc_exc)

            # Delete old client reference
            del old_client

            # Create fresh client with uploaded session file
            log.info("[SESSION-MONITOR] Creating new client instance")
            new_client = self.make_client(self.cfg)

            # Update SessionHelpers to use the new client
            self.session_helpers.update_client(new_client)
            log.info("[SESSION-MONITOR] Updated SessionHelpers with new client")

            # Update AuthManager to use the new client
            if hasattr(self, "auth_manager") and self.auth_manager:
                self.auth_manager.update_client(new_client)
                log.info("[SESSION-MONITOR] Updated AuthManager with new client")

            # Update ParticipantInfoHandler to use the new client
            if hasattr(self, "participant_handler") and self.participant_handler:
                self.participant_handler.update_client(new_client)
                log.info(
                    "[SESSION-MONITOR] Updated ParticipantInfoHandler with new client"
                )

            # Publish login progress: connecting
            self.redis_mgr.publish_login_progress(
                "connecting", 40, "Connecting to Telegram..."
            )

            # Connect to Telegram
            log.info("[SESSION-MONITOR] Connecting to Telegram with new session")
            await asyncio.wait_for(new_client.connect(), timeout=30)
            log.info("[SESSION-MONITOR] Connection established, checking authorization")

            # Publish login progress: verifying
            self.redis_mgr.publish_login_progress(
                "verifying", 60, "Verifying authorization..."
            )

            # Check authorization
            me = await asyncio.wait_for(new_client.get_me(), timeout=10)
            log.info("[SESSION-MONITOR] get_me() returned: %s", me)

            if me:
                # Type assertion: get_me() returns User when authorized
                assert isinstance(me, TgUser), "Expected User object from get_me()"
                # Type narrowing: me is now confirmed to be TgUser
                user: TgUser = me
                log.info(
                    "[SESSION-MONITOR] ✓ New session authorized: @%s (ID: %s)",
                    user.username or "no_username",
                    user.id,
                )

                # Mark as authorized
                authorized_ref["value"] = True
                auth_event.set()
                handshake_gate.set()

                log.info("[SESSION-MONITOR] About to update worker status in Redis")
                # Update worker status in Redis
                try:
                    log.info(
                        "[SESSION-MONITOR] Setting worker_status to authorized in Redis"
                    )
                    self.redis_mgr.publish_worker_status(
                        authorized=True, status="authorized", ttl=3600
                    )
                    log.info("[SESSION-MONITOR] ✓ Worker status updated to authorized")

                    # Publish login progress: downloading avatar
                    try:
                        self.redis_mgr.publish_login_progress(
                            stage="avatar",
                            percent=70,
                            message="Downloading user avatar...",
                        )
                    except Exception:
                        pass

                    log.info("[SESSION-MONITOR] Building user_info dict")
                    # Store user info in Redis
                    user_info = {
                        "username": user.username,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "phone": user.phone,
                        "user_id": user.id,
                    }

                    log.info("[SESSION-MONITOR] Checking if user has avatar photo")
                    # Cache user avatar if available - use stripped_thumb for quick retrieval
                    if hasattr(user, "photo") and isinstance(
                        user.photo, UserProfilePhoto
                    ):
                        log.info("[SESSION-MONITOR] User has photo with stripped_thumb")
                        try:
                            import base64

                            # Use stripped_thumb from photo object for instant avatar (ultra-light preview)
                            if (
                                hasattr(user.photo, "stripped_thumb")
                                and user.photo.stripped_thumb
                            ):
                                # Store stripped thumb in Redis for quick retrieval
                                cache_key = f"tgsentinel:user_avatar:{user.id}"
                                avatar_b64 = base64.b64encode(
                                    user.photo.stripped_thumb
                                ).decode("utf-8")
                                self.redis_client.set(cache_key, avatar_b64)  # No TTL
                                avatar_url = f"/api/avatar/user/{user.id}"
                                user_info["avatar"] = avatar_url
                                log.info(
                                    "[SESSION-MONITOR] ✓ Cached stripped_thumb avatar: %s",
                                    avatar_url,
                                )
                            else:
                                # Fallback to downloading full photo (async, don't block login)
                                log.info(
                                    "[SESSION-MONITOR] No stripped_thumb, scheduling async download"
                                )
                                user_info["avatar"] = "/static/images/logo.png"
                                # Schedule download in background without blocking
                                asyncio.create_task(
                                    self._download_avatar_async(
                                        new_client, user.id, user.photo
                                    )
                                )
                        except Exception as avatar_exc:
                            log.warning(
                                "[SESSION-MONITOR] Failed to cache stripped_thumb: %s",
                                avatar_exc,
                            )
                            user_info["avatar"] = "/static/images/logo.png"
                    else:
                        # No avatar available, use default
                        user_info["avatar"] = "/static/images/logo.png"
                        log.debug("[SESSION-MONITOR] No avatar photo, using default")

                    user_info_json = json.dumps(user_info)
                    log.info(
                        "[SESSION-MONITOR] About to write user_info to Redis: %s",
                        user_info_json,
                    )
                    self.redis_mgr.cache_user_info(user_info)
                    log.info(
                        "[SESSION-MONITOR] ✓ Updated Redis with user info (avatar: %s)",
                        user_info.get("avatar", "not set"),
                    )

                    # Verify
                    verify_ui = self.redis_mgr.get_user_info()
                    if verify_ui:
                        log.info("[SESSION-MONITOR] Verified user_info in Redis")
                    else:
                        log.error(
                            "[SESSION-MONITOR] Failed to verify user_info in Redis!"
                        )

                    # Publish login progress: fetching dialogs (80%)
                    self.redis_mgr.publish_login_progress(
                        "fetching_dialogs",
                        80,
                        "Loading channels and contacts...",
                    )
                except Exception as redis_exc:
                    log.error(
                        "[SESSION-MONITOR] Failed to update Redis after auth: %s",
                        redis_exc,
                        exc_info=True,
                    )

                # Re-register message ingestion handler
                try:
                    self.start_ingestion(self.cfg, new_client, self.redis_client)
                    log.info(
                        "[SESSION-MONITOR] ✓ Message ingestion handler re-registered"
                    )
                except Exception as ingestion_exc:
                    log.error(
                        "[SESSION-MONITOR] Failed to re-register ingestion handler: %s",
                        ingestion_exc,
                        exc_info=True,
                    )

                # Clear dialogs cache
                try:
                    dialogs_cache_ref["value"] = None
                    log.info("[SESSION-MONITOR] ✓ Cleared dialogs cache")
                except Exception as cache_exc:
                    log.debug(
                        "[SESSION-MONITOR] Failed to clear dialogs cache: %s",
                        cache_exc,
                    )

                # Publish session_updated event to trigger immediate cache refresh
                try:
                    self.redis_mgr.publish_session_event(
                        "session_authorized", user_id=user.id
                    )
                    log.info(
                        "[SESSION-MONITOR] ✓ Published session_authorized event to trigger immediate cache refresh"
                    )
                except Exception as pub_exc:
                    log.error(
                        "[SESSION-MONITOR] Failed to publish session_updated: %s",
                        pub_exc,
                    )

                # Update client_ref BEFORE calling mark_authorized to ensure handlers use new client
                if client_ref is not None:
                    client_ref["value"] = new_client
                    log.info("[SESSION-MONITOR] ✓ Updated client_ref with new client")

                # Call mark_authorized to trigger generation-based handler startup
                if self.mark_authorized_func:
                    log.info(
                        "[SESSION-MONITOR] Calling mark_authorized to trigger handler startup..."
                    )
                    try:
                        result = self.mark_authorized_func(user)
                        # Handle both sync and async callbacks
                        if asyncio.iscoroutine(result):
                            await result
                        log.info(
                            "[SESSION-MONITOR] ✓ mark_authorized called, handlers should start"
                        )
                    except Exception as mark_exc:
                        log.error(
                            "[SESSION-MONITOR] Failed to call mark_authorized: %s",
                            mark_exc,
                            exc_info=True,
                        )
                else:
                    log.warning(
                        "[SESSION-MONITOR] mark_authorized_func not provided, handlers may not start"
                    )

                # Wait briefly for cache refresher to start processing
                log.info(
                    "[SESSION-MONITOR] Waiting for cache refresher to initialize..."
                )
                await asyncio.sleep(2)

                # Publish login completion (100%) with TTL
                try:
                    self.redis_client.setex(
                        "tgsentinel:login_progress",
                        300,  # 5 minute TTL to prevent stale data
                        json.dumps(
                            {
                                "stage": "completed",
                                "percent": 100,
                                "message": "Session switch complete! Loading channels and contacts...",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        ),
                    )
                    log.info(
                        "[SESSION-MONITOR] Published login completion (100%) with TTL"
                    )
                except Exception as completion_exc:
                    log.debug(
                        "[SESSION-MONITOR] Failed to publish login completion: %s",
                        completion_exc,
                    )

                return new_client
            else:
                log.warning(
                    "[SESSION-MONITOR] get_me() returned None - session file appears invalid or expired"
                )
                log.warning(
                    "[SESSION-MONITOR] The session may need phone number verification or the credentials don't match"
                )
                return None

        except asyncio.TimeoutError:
            log.error("[SESSION-MONITOR] Connection or auth check timed out")
            return None
        except Exception as e:
            log.error(
                "[SESSION-MONITOR] Client recreation failed: %s", e, exc_info=True
            )
            return None

    async def handle_logout(
        self,
        current_client: TelegramClient,
        auth_event: asyncio.Event,
        authorized_ref: dict[str, bool],
    ) -> None:
        """Handle session logout event."""
        log.info("[SESSION-MONITOR] Logout request detected, disconnecting...")

        # Publish initial logout progress (20%)
        try:
            self.redis_mgr.publish_logout_progress(
                "disconnecting", 20, "Disconnecting from Telegram..."
            )
            log.info("[SESSION-MONITOR] Published logout progress (20%)")
        except Exception:
            pass

        try:
            # Disconnect from Telegram
            if current_client.is_connected():
                log.debug("[SESSION-MONITOR] Disconnecting client gracefully...")

                # Cancel all pending Telethon tasks before disconnect
                all_tasks = asyncio.all_tasks()
                telethon_tasks = [
                    t
                    for t in all_tasks
                    if not t.done()
                    and any(
                        name in str(t.get_coro())
                        for name in [
                            "_recv_loop",
                            "_send_loop",
                            "MTProtoSender",
                            "Connection",
                        ]
                    )
                ]
                if telethon_tasks:
                    log.info(
                        f"[SESSION-MONITOR] Cancelling {len(telethon_tasks)} pending Telethon tasks"
                    )
                    for task in telethon_tasks:
                        task.cancel()
                    # Wait for tasks to be cancelled with timeout
                    await asyncio.wait(telethon_tasks, timeout=2.0)
                    log.info("[SESSION-MONITOR] Telethon tasks cancelled")

                # Remove all event handlers
                try:
                    handlers = current_client.list_event_handlers()
                    for callback, event in handlers:
                        current_client.remove_event_handler(callback, event)
                    log.debug(
                        "[SESSION-MONITOR] Removed %d event handler(s)",
                        len(handlers),
                    )
                except Exception as handler_exc:
                    log.debug("Failed to remove handlers: %s", handler_exc)

                # Disconnect without saving (we're about to delete the session)
                try:
                    # Add timeout to prevent hanging indefinitely
                    disconnect_coro = current_client.disconnect()
                    if disconnect_coro is not None:
                        await asyncio.wait_for(disconnect_coro, timeout=10.0)
                    log.info("[SESSION-MONITOR] ✓ Client disconnected")
                except asyncio.TimeoutError:
                    log.warning(
                        "[SESSION-MONITOR] Disconnect timed out after 10s, forcing cleanup"
                    )
                except Exception as disc_exc:
                    log.warning("[SESSION-MONITOR] Disconnect error: %s", disc_exc)

                self.session_helpers.close_session_binding()

                # Wait for Telethon cleanup and ensure DB writes complete
                # Increased to 3.5s to ensure SQLite releases all locks (session + -shm + -wal files)
                await asyncio.sleep(3.5)
                log.info("[SESSION-MONITOR] \u2713 Disconnected from Telegram")

            # Update progress: disconnected
            self.redis_mgr.publish_logout_progress(
                "disconnected", 50, "Disconnected from Telegram"
            )

            # Delete session files and clean up data/config directories
            try:
                await asyncio.sleep(0.5)

                # Update progress: deleting files
                self.redis_mgr.publish_logout_progress(
                    "deleting_files",
                    70,
                    "Removing session files and cleaning directories...",
                )

                # Try to delete files with exponential backoff retry
                max_retries = 3
                for attempt in range(max_retries):
                    files_remaining = False

                    # Delete session files
                    for suffix in ["", "-shm", "-wal", "-journal"]:
                        session_file = Path(str(self.session_file_path) + suffix)
                        if session_file.exists():
                            try:
                                session_file.unlink()
                                log.info(
                                    "[SESSION-MONITOR] Deleted session file: %s",
                                    session_file.name,
                                )
                            except PermissionError as perm_exc:
                                files_remaining = True
                                if attempt < max_retries - 1:
                                    log.warning(
                                        "[SESSION-MONITOR] File locked, will retry: %s (attempt %d/%d)",
                                        session_file.name,
                                        attempt + 1,
                                        max_retries,
                                    )
                                else:
                                    log.error(
                                        "[SESSION-MONITOR] Failed to delete %s after %d attempts: %s",
                                        session_file.name,
                                        max_retries,
                                        perm_exc,
                                    )

                    # Clean data/ directory (except plugins/)
                    data_dir = self.session_file_path.parent  # Usually /app/data
                    log.info("[SESSION-MONITOR] Cleaning data directory: %s", data_dir)
                    if data_dir.exists() and data_dir.is_dir():
                        items = list(data_dir.iterdir())
                        log.info(
                            "[SESSION-MONITOR] Found %d items in data/", len(items)
                        )
                        for item in items:
                            # Skip plugins directory
                            if item.name == "plugins":
                                log.info(
                                    "[SESSION-MONITOR] Skipping data/plugins/ directory"
                                )
                                continue

                            try:
                                if item.is_file():
                                    item.unlink()
                                    log.info(
                                        "[SESSION-MONITOR] Deleted data file: %s",
                                        item.name,
                                    )
                                elif item.is_dir() and item.name != "plugins":
                                    import shutil

                                    shutil.rmtree(item)
                                    log.info(
                                        "[SESSION-MONITOR] Deleted data directory: %s",
                                        item.name,
                                    )
                            except Exception as del_exc:
                                log.warning(
                                    "[SESSION-MONITOR] Could not delete %s: %s",
                                    item.name,
                                    del_exc,
                                )
                    else:
                        log.warning(
                            "[SESSION-MONITOR] Data directory does not exist or is not a directory: %s",
                            data_dir,
                        )

                    # Clean config/ directory
                    config_dir = Path("/app/config")
                    if config_dir.exists() and config_dir.is_dir():
                        for item in config_dir.iterdir():
                            # Only delete files, not the directory itself
                            if item.is_file():
                                try:
                                    item.unlink()
                                    log.info(
                                        "[SESSION-MONITOR] Deleted config file: %s",
                                        item.name,
                                    )
                                except Exception as del_exc:
                                    log.warning(
                                        "[SESSION-MONITOR] Could not delete config file %s: %s",
                                        item.name,
                                        del_exc,
                                    )

                    if not files_remaining:
                        break

                    if attempt < max_retries - 1:
                        # Exponential backoff: 1s, 2s, 4s
                        await asyncio.sleep(2**attempt)
            except Exception as file_exc:
                log.error(
                    "[SESSION-MONITOR] Unexpected error during cleanup: %s",
                    file_exc,
                    exc_info=True,
                )

            # Mark as not authorized
            authorized_ref["value"] = False
            auth_event.clear()

            # Clear worker status and cache keys in Redis
            try:
                # Update progress: clearing Redis
                self.redis_mgr.publish_logout_progress(
                    "clearing_redis",
                    85,
                    "Clearing authentication and cache...",
                )

                self.redis_mgr.publish_worker_status(
                    authorized=False, status="logged_out", ttl=0
                )

                # Clear user session data
                self.redis_mgr.clear_user_session_data()

                # Clear avatar cache
                try:
                    # Ensure we clear all avatar key variants that may exist
                    for pattern in [
                        "tgsentinel:user_avatar:*",
                        "tgsentinel:chat_avatar:*",
                        "tgsentinel:channel_avatar:*",
                    ]:
                        cursor = 0
                        keys_to_delete = []
                        while True:
                            cursor, keys = self.redis_client.scan(cursor=cursor, match=pattern, count=100)  # type: ignore[assignment]
                            if keys:
                                keys_to_delete.extend(keys)
                            if cursor == 0:
                                break
                        if keys_to_delete:
                            # Redis delete accepts bytes or strings; pass through
                            self.redis_client.delete(*keys_to_delete)
                    log.info("[SESSION-MONITOR] Cleared avatar cache")
                except Exception as avatar_exc:
                    log.debug(
                        "[SESSION-MONITOR] Failed to clear avatar cache: %s",
                        avatar_exc,
                    )

                # Clear message ingestion stream
                try:
                    stream_key = self.cfg.redis["stream"]
                    self.redis_client.delete(stream_key)
                    log.info("[SESSION-MONITOR] Cleared message stream: %s", stream_key)
                except Exception as stream_exc:
                    log.debug(
                        "[SESSION-MONITOR] Failed to clear message stream: %s",
                        stream_exc,
                    )

                log.info(
                    "[SESSION-MONITOR] Cleared user info, cache, and progress keys from Redis"
                )
            except Exception as redis_exc:
                log.warning(
                    "[SESSION-MONITOR] Failed to clear Redis data: %s", redis_exc
                )

            # Clear config files on logout
            try:
                config_files = glob.glob("config/*.yml") + glob.glob("config/*.yaml")
                for config_file in config_files:
                    try:
                        os.remove(config_file)
                        log.info(
                            "[SESSION-MONITOR] Deleted config file: %s",
                            config_file,
                        )
                    except Exception as file_exc:
                        log.debug(
                            "[SESSION-MONITOR] Failed to delete config file %s: %s",
                            config_file,
                            file_exc,
                        )
            except Exception as config_exc:
                log.debug(
                    "[SESSION-MONITOR] Failed to clear config files: %s",
                    config_exc,
                )

            # Log completion
            log.info(
                "[SESSION-MONITOR] Logout completed, session files deleted, waiting for new session"
            )

            # Create a new dummy client with MemorySession to replace the old client
            # This prevents any stale references from accessing the deleted session files
            try:
                from telethon.sessions import MemorySession

                dummy_client = TelegramClient(
                    MemorySession(), self.cfg.api_id, self.cfg.api_hash
                )
                # Update SessionHelpers with dummy client
                self.session_helpers.update_client(dummy_client)
                log.info(
                    "[SESSION-MONITOR] Updated SessionHelpers with dummy MemorySession client"
                )

                # Update AuthManager with dummy client
                if hasattr(self, "auth_manager") and self.auth_manager:
                    self.auth_manager.update_client(dummy_client)
                    log.info(
                        "[SESSION-MONITOR] Updated AuthManager with dummy MemorySession client"
                    )

                # Update ParticipantInfoHandler with dummy client
                if hasattr(self, "participant_handler") and self.participant_handler:
                    self.participant_handler.update_client(dummy_client)
                    log.info(
                        "[SESSION-MONITOR] Updated ParticipantInfoHandler with dummy MemorySession client"
                    )
            except Exception as client_exc:
                log.warning(
                    "[SESSION-MONITOR] Failed to create dummy client: %s", client_exc
                )

            # Final progress: completed (100%) with redirect
            try:
                self.redis_client.set(
                    "tgsentinel:logout_progress",
                    json.dumps(
                        {
                            "stage": "completed",
                            "percent": 100,
                            "message": "Logout complete! Redirecting...",
                            "redirect": "/logout",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                )
                log.info(
                    "[SESSION-MONITOR] Published logout completion (100%) with redirect"
                )
            except Exception:
                pass

        except Exception as e:
            log.error("[SESSION-MONITOR] Logout failed: %s", e)

    async def monitor_session_events(
        self,
        client_ref: dict[str, TelegramClient],
        auth_event: asyncio.Event,
        handshake_gate: asyncio.Event,
        authorized_ref: dict[str, bool],
        dialogs_cache_ref: dict[str, Any],
    ) -> None:
        """
        Monitor Redis pub/sub for session events.

        Args:
            client_ref: Dictionary with 'value' key containing current client
            auth_event: Event to signal authorization status
            handshake_gate: Event to control request handlers
            authorized_ref: Dictionary with 'value' key for authorization flag
            dialogs_cache_ref: Dictionary with 'value' key for dialogs cache
        """
        pubsub = self.redis_client.pubsub()
        try:
            await asyncio.to_thread(pubsub.subscribe, "tgsentinel:session_updated")
            await asyncio.to_thread(pubsub.subscribe, "tgsentinel:config_updated")
            log.info(
                "[SESSION-MONITOR] Listening for session uploads, logout events, and config updates"
            )

            while True:
                message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                if message and message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        event_type = data.get("event")

                        if event_type == "config_reloaded":
                            # Config was updated via API, reload it in the worker
                            try:
                                from tgsentinel.config import load_config

                                new_config = load_config()
                                config_keys = data.get("config_keys", [])
                                log.info(
                                    "[SESSION-MONITOR] Config reloaded from API update (keys: %s)",
                                    config_keys,
                                )
                                # Update any module-level config references if needed
                                # The load_config() call updates the global state
                            except Exception as config_exc:
                                log.error(
                                    "[SESSION-MONITOR] Failed to reload config: %s",
                                    config_exc,
                                    exc_info=True,
                                )

                        elif event_type == "session_imported":
                            new_client = await self.handle_session_import(
                                client_ref["value"],
                                auth_event,
                                handshake_gate,
                                authorized_ref,
                                dialogs_cache_ref,
                                client_ref=client_ref,  # Pass client_ref so it can be updated before mark_authorized
                            )
                            if new_client:
                                # client_ref already updated inside handle_session_import
                                log.debug(
                                    "[SESSION-MONITOR] Client reference already updated"
                                )

                        elif event_type == "session_logout":
                            await self.handle_logout(
                                client_ref["value"], auth_event, authorized_ref
                            )

                    except Exception as e:
                        log.debug("[SESSION-MONITOR] Parse error: %s", e)

                await asyncio.sleep(0.1)

        except Exception as e:
            log.error("[SESSION-MONITOR] Fatal error: %s", e)
        finally:
            try:
                await asyncio.to_thread(pubsub.close)
            except:
                pass
