"""
DM (Direct Message) polling handler for TG Sentinel.

Polls monitored users' conversations periodically to ingest private messages.
This runs independently from the NewMessage event handler which only reliably
works for channels/groups, not private DMs.
"""

import asyncio
import json
import logging
from typing import Any, Callable

from redis import Redis
from telethon import TelegramClient
from telethon.tl.types import User

from .config import AppCfg

log = logging.getLogger(__name__)


class DMPoller:
    """
    Polls direct messages from monitored users.

    Architecture:
    - Runs as independent async task
    - Polls each monitored user's conversation every N seconds
    - Tracks last seen message ID per user to avoid duplicates
    - Pushes messages to same Redis stream as event handler
    - Generation-aware: stops/restarts on session changes
    """

    def __init__(
        self,
        cfg: AppCfg,
        client_ref: Callable[[], TelegramClient],
        redis_client: Redis,
        authorized_check: Callable[[], bool],
        poll_interval: int = 30,  # seconds between polls
    ):
        """
        Initialize DM poller.

        Args:
            cfg: Application configuration
            client_ref: Callable returning current Telegram client
            redis_client: Redis client for stream publishing
            authorized_check: Function returning current authorization status
            poll_interval: Seconds between polling cycles (default: 30)
        """
        self.cfg = cfg
        self.client_ref = client_ref
        self.redis = redis_client
        self.authorized_check = authorized_check
        self.poll_interval = poll_interval
        self.stream = cfg.system.redis.stream

        # Track last seen message ID per user to avoid duplicates
        # Key: user_id (int), Value: last_msg_id (int)
        self._last_seen: dict[int, int] = {}

        # Generation tracking (reset on session change)
        self._current_generation: int | None = None

    async def start(self) -> None:
        """
        Main polling loop.

        Continuously polls monitored users' DMs and ingests new messages.
        Handles generation changes and authorization state.
        """
        log.info(
            "[DM-POLLER] Starting DM polling handler (interval=%ds, monitored_users=%d)",
            self.poll_interval,
            len(self.cfg.monitored_users),
        )

        if not self.cfg.monitored_users:
            log.warning(
                "[DM-POLLER] No monitored users configured, DM polling disabled"
            )
            return

        while True:
            try:
                # Check authorization before polling
                if not self.authorized_check():
                    log.debug("[DM-POLLER] Not authorized, waiting...")
                    await asyncio.sleep(self.poll_interval)
                    continue

                # Check for generation change (session import)
                current_gen = self._get_current_generation()
                if current_gen != self._current_generation:
                    log.info(
                        "[DM-POLLER] Generation change detected: %s -> %s (clearing state)",
                        self._current_generation,
                        current_gen,
                    )
                    self._last_seen.clear()
                    self._current_generation = current_gen

                # Poll each monitored user
                await self._poll_all_users()

            except asyncio.CancelledError:
                log.info("[DM-POLLER] DM poller cancelled, shutting down")
                raise
            except Exception as e:
                log.exception("[DM-POLLER] Error in polling loop: %s", e)

            # Wait before next poll cycle
            await asyncio.sleep(self.poll_interval)

    async def _poll_all_users(self) -> None:
        """Poll all monitored users for new DM messages."""
        client = self.client_ref()

        for user_config in self.cfg.monitored_users:
            if not user_config.enabled:
                log.debug("[DM-POLLER] Skipping disabled user: %s", user_config.id)
                continue

            try:
                await self._poll_user(client, user_config.id)
            except Exception as e:
                log.error(
                    "[DM-POLLER] Error polling user %s: %s",
                    user_config.id,
                    e,
                    exc_info=True,
                )

    async def _poll_user(self, client: TelegramClient, user_id: int) -> None:
        """
        Poll a specific user's conversation for new messages.

        Args:
            client: Telegram client
            user_id: User ID to poll
        """
        try:
            # Get the last N messages from this user's conversation
            # limit=10 means we check last 10 messages, should be enough for poll_interval=30s
            messages = await client.get_messages(user_id, limit=10)

            if not messages:
                log.debug("[DM-POLLER] No messages from user %s", user_id)
                return

            # Ensure messages is a list (get_messages can return a single Message or a list)
            messages_list = messages if isinstance(messages, list) else [messages]

            # First-time initialization: mark the most recent message as seen to avoid ingesting history
            if user_id not in self._last_seen:
                most_recent_id = max(
                    (getattr(m, "id", 0) for m in messages_list), default=0
                )
                self._last_seen[user_id] = most_recent_id
                log.info(
                    "[DM-POLLER] First poll for user %s: initialized last_seen to %s (skipping %d historical messages)",
                    user_id,
                    most_recent_id,
                    len(messages_list),
                )
                return

            # Process messages in chronological order (oldest first)
            messages_reversed = list(reversed(messages_list))

            new_count = 0
            for msg in messages_reversed:
                # Skip if we've already seen this message
                last_seen_id = self._last_seen.get(user_id, 0)
                if msg.id <= last_seen_id:
                    continue

                # Check if message is FROM the monitored user (incoming to us)
                # Skip messages we sent (outgoing)
                if msg.out:
                    log.debug(
                        "[DM-POLLER] Skipping outgoing message: user=%s, msg_id=%s",
                        user_id,
                        msg.id,
                    )
                    # Still update last_seen to avoid re-checking
                    self._last_seen[user_id] = max(
                        self._last_seen.get(user_id, 0), msg.id
                    )
                    continue

                # Ingest this message
                await self._ingest_message(client, user_id, msg)
                new_count += 1

                # Update last seen message ID
                self._last_seen[user_id] = max(self._last_seen.get(user_id, 0), msg.id)

            if new_count > 0:
                log.info(
                    "[DM-POLLER] Ingested %d new messages from user %s (last_seen_id=%s)",
                    new_count,
                    user_id,
                    self._last_seen.get(user_id),
                )

        except Exception as e:
            log.error(
                "[DM-POLLER] Error fetching messages from user %s: %s",
                user_id,
                e,
                exc_info=True,
            )

    async def _ingest_message(
        self, client: TelegramClient, user_id: int, msg: Any
    ) -> None:
        """
        Ingest a single DM message into Redis stream.

        Args:
            client: Telegram client
            user_id: User ID (chat ID for private conversation)
            msg: Telethon Message object
        """
        try:
            # Build minimal payload (similar to event handler in client.py)
            # Get sender info
            sender_name = ""
            sender_id = getattr(msg, "sender_id", None)

            try:
                sender = await msg.get_sender()
                if sender and isinstance(sender, User):
                    name_parts = []
                    if getattr(sender, "first_name", None):
                        name_parts.append(sender.first_name)
                    if getattr(sender, "last_name", None):
                        name_parts.append(sender.last_name)
                    sender_name = " ".join(name_parts)
            except Exception as e:
                log.debug("[DM-POLLER] Could not get sender info: %s", e)

            # Build payload
            msg_date = getattr(msg, "date", None)
            timestamp = msg_date.isoformat() if msg_date else None
            msg_id = getattr(msg, "id", None)
            is_reply = bool(getattr(msg, "is_reply", False))
            media = getattr(msg, "media", None)
            forward = getattr(msg, "forward", None)

            payload = {
                "chat_id": user_id,
                "chat_title": sender_name or f"User {user_id}",
                "msg_id": msg_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "mentioned": bool(getattr(msg, "mentioned", False)),
                "text": getattr(msg, "message", "") or "",
                "replies": 0,  # DMs don't have public reply counts
                "reactions": 0,  # DMs don't have public reactions
                "timestamp": timestamp,
                "avatar_url": None,  # Will be enriched later if needed
                "chat_avatar_url": None,
                "is_reply": is_reply,
                "reply_to_msg_id": (
                    getattr(msg, "reply_to_msg_id", None) if is_reply else None
                ),
                "has_media": bool(media),
                "media_type": media.__class__.__name__ if media else None,
                "is_pinned": False,  # DMs don't have pinned messages
                "has_forward": bool(forward),
                "forward_from": None,  # Could be enriched if needed
            }

            # Push to Redis stream (same stream as event handler)
            self.redis.xadd(
                self.stream,
                {"json": json.dumps(payload)},
                maxlen=100000,
                approximate=True,
            )

            log.info(
                "[DM-POLLER] Message ingested: chat_id=%s, sender=%s (%s), msg_id=%s",
                user_id,
                sender_name or sender_id,
                sender_id,
                msg_id,
            )

        except Exception as e:
            log.exception("[DM-POLLER] Error ingesting message: %s", e)

    def _get_current_generation(self) -> int:
        """
        Get current session generation from Redis.

        Returns:
            Current generation number (1-based), or 0 if not set
        """
        try:
            worker_status_key = "tgsentinel:worker_status"
            status_json = self.redis.get(worker_status_key)

            if not status_json:
                return 0

            # Decode bytes to string if needed
            status_str = (
                status_json.decode()
                if isinstance(status_json, bytes)
                else str(status_json)
            )

            status = json.loads(status_str)
            return status.get("generation", 0)

        except Exception as e:
            log.debug("[DM-POLLER] Could not get generation: %s", e)
            return 0


async def start_dm_poller(
    cfg: AppCfg,
    client_ref: Callable[[], TelegramClient],
    redis_client: Redis,
    authorized_check: Callable[[], bool],
    poll_interval: int = 30,
) -> None:
    """
    Start the DM polling handler.

    This is the entry point called from main.py/worker_orchestrator.py.

    Args:
        cfg: Application configuration
        client_ref: Callable returning current Telegram client
        redis_client: Redis client
        authorized_check: Function returning authorization status
        poll_interval: Seconds between polls (default: 30)
    """
    poller = DMPoller(cfg, client_ref, redis_client, authorized_check, poll_interval)
    await poller.start()
