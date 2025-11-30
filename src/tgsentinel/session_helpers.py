"""Session helper functions for TG Sentinel.

This module provides utilities for managing Telegram session files,
user identity caching, and client session operations.
"""

import asyncio
import base64
import io
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.tl.types import User as TgUser

from .redis_operations import RedisManager


class SessionHelpers:
    """Helper methods for Telegram session management."""

    def __init__(
        self,
        client: TelegramClient,
        session_file_path: Path,
        redis_manager: RedisManager,
        client_lock: Optional[asyncio.Lock] = None,
    ):
        """Initialize session helpers.

        Args:
            client: Telegram client instance
            session_file_path: Path to session file
            redis_manager: RedisManager instance
            client_lock: Optional asyncio.Lock to serialize client operations (prevents SQLite locking)
        """
        self.client = client
        self.session_file_path = session_file_path
        self.redis_mgr = redis_manager
        self.client_lock = client_lock or asyncio.Lock()
        self.log = logging.getLogger(__name__)

    def update_client(self, new_client: TelegramClient) -> None:
        """Update the client reference (used after logout or session import).

        Args:
            new_client: New TelegramClient instance
        """
        self.client = new_client
        self.log.debug("Updated SessionHelpers client reference")

    def close_session_binding(self) -> None:
        """Close the Telegram session binding."""
        sess = getattr(self.client, "session", None)
        closer = getattr(sess, "close", None)
        if callable(closer):
            try:
                closer()
                self.log.debug("Closed Telegram session binding")
            except Exception as close_exc:
                self.log.debug(
                    "Failed to close Telegram session binding: %s", close_exc
                )

    def rebind_session_binding(self) -> None:
        """Reload session from disk by explicitly calling session.load().

        This forces Telethon to re-read the auth_key from SQLite without
        creating a new client instance.
        """
        try:
            if hasattr(self.client.session, "load"):
                self.client.session.load()  # type: ignore[attr-defined]
                self.log.debug(
                    "Reloaded Telegram session from %s", self.session_file_path
                )
            else:
                self.log.warning(
                    "Session does not support reload; attempting reconnection"
                )
        except Exception as rebind_exc:
            self.log.error("Failed to reload Telegram session: %s", rebind_exc)

    async def refresh_user_identity_cache(
        self, user_obj: Optional[TgUser] = None
    ) -> None:
        """Refresh user identity cache in Redis with current user info.

        Args:
            user_obj: Optional User object, will fetch if not provided
        """
        me = user_obj
        if me is None:
            try:
                me = await self.client.get_me()  # type: ignore[misc]
            except Exception as info_exc:
                self.log.debug(
                    "Could not fetch user info for cache refresh: %s", info_exc
                )
                return

        if not me:
            self.log.warning("get_me() returned None; skipping user cache refresh")
            return

        avatar_url = "/static/images/logo.png"

        # Try to fetch and cache avatar
        try:
            photos = await self.client.get_profile_photos("me", limit=1)  # type: ignore[misc]
            if photos:
                # Download avatar to memory
                avatar_bytes = io.BytesIO()
                try:
                    await self.client.download_profile_photo("me", file=avatar_bytes)  # type: ignore[misc]
                    avatar_bytes.seek(0)
                    avatar_b64 = base64.b64encode(avatar_bytes.read()).decode("utf-8")

                    # Store in Redis with user_id key
                    user_id = getattr(me, "id", None)
                    if user_id:
                        self.redis_mgr.cache_avatar(user_id, avatar_b64, is_user=True)
                        avatar_url = f"/api/avatar/user/{user_id}"
                        self.log.info(f"Stored user avatar in Redis: user {user_id}")
                except Exception as avatar_exc:
                    self.log.debug("Could not download user avatar: %s", avatar_exc)
        except Exception as photo_exc:
            self.log.debug("Could not refresh profile photos: %s", photo_exc)

        # Build and store complete user info with actual avatar path
        try:
            ui = {
                "username": getattr(me, "username", None)
                or getattr(me, "first_name", "Unknown"),
                "first_name": getattr(me, "first_name", ""),
                "last_name": getattr(me, "last_name", ""),
                "phone": getattr(me, "phone", ""),
                "user_id": getattr(me, "id", None),
                "avatar": avatar_url,
            }
            self.redis_mgr.cache_user_info(ui)
            self.log.info(
                "Stored user info in Redis after refresh: %s (avatar: %s)",
                ui.get("username"),
                avatar_url,
            )
        except Exception as cache_exc:
            self.log.error(
                "Could not store refreshed user info: %s", cache_exc, exc_info=True
            )

    async def ensure_client_connected(self) -> None:
        """Ensure the Telegram client is connected.

        Uses client_lock to prevent concurrent connect() calls that would
        cause SQLite "database is locked" errors on tgsentinel.session.
        """
        if not self.client.is_connected():  # type: ignore[misc]
            async with self.client_lock:
                # Double-check after acquiring lock (another handler may have connected)
                if not self.client.is_connected():  # type: ignore[misc]
                    await self._connect_with_retry()
                else:
                    self.log.debug("Client already connected by another handler")

    async def _connect_with_retry(self) -> None:
        """Connect to Telegram client with retries when SQLite is locked."""

        backoff = 1.0
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                self.log.debug("Connecting Telegram client (attempt %d)", attempt)
                await self.client.connect()  # type: ignore[misc]
                self.log.debug("Telegram client connected successfully")
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise

                self.log.warning(
                    "Telegram session database locked (attempt %d/%d); retrying",
                    attempt,
                    max_attempts,
                )
                self.close_session_binding()
                self.rebind_session_binding()
                await asyncio.sleep(backoff)
                backoff *= 2
            except Exception:
                self.log.error("Unexpected error while connecting", exc_info=True)
                raise

        raise sqlite3.OperationalError(
            "Telegram session database remained locked after retries"
        )
