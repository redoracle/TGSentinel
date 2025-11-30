"""Telegram request handlers for TG Sentinel.

This module provides handler classes for processing various Telegram-related
requests from the UI, including participant info, chats, dialogs, and users.
"""

import asyncio
import logging
from typing import Any, Callable, Dict

from redis import Redis
from telethon import TelegramClient

from .participant_info import fetch_participant_info
from .redis_operations import RedisManager


class BaseRequestHandler:
    """Base class for handling Redis-based request/response patterns."""

    def __init__(
        self,
        redis_client: Redis,
        redis_manager: RedisManager,
        handshake_gate: asyncio.Event,
        authorized_check: Callable[[], bool],
        auth_event: asyncio.Event,
        cache_ready_event: asyncio.Event,
        logout_event: asyncio.Event,
        get_session_generation: Callable[[], int],
        get_authorized_user_id: Callable[[], int | None],
    ):
        """Initialize base request handler.

        Args:
            redis_client: Raw Redis client (for compatibility)
            redis_manager: RedisManager instance
            handshake_gate: Event to wait for before processing
            authorized_check: Function that returns current authorization status
            auth_event: Event that is set when authorization completes
            cache_ready_event: Event that is set when initial cache warm-up completes
            logout_event: Event that is set when logout begins
            get_session_generation: Function returning current session_generation
            get_authorized_user_id: Function returning current authorized_user_id
        """
        self.redis = redis_client
        self.redis_mgr = redis_manager
        self.handshake_gate = handshake_gate
        self.is_authorized = authorized_check
        self.auth_event = auth_event
        self.cache_ready_event = cache_ready_event
        self.logout_event = logout_event
        self.get_session_generation = get_session_generation
        self.get_authorized_user_id = get_authorized_user_id
        self.log = logging.getLogger(self.__class__.__name__)

    async def handle_requests(
        self, request_pattern: str, process_func: Callable
    ) -> None:
        """Generic request handling loop with generation awareness.

        Args:
            request_pattern: Redis key pattern to scan for requests
            process_func: Async function to process each request
        """
        handler_name = self.__class__.__name__.replace("Handler", "").upper()

        # Outer loop: wait for each new auth/generation
        while True:
            # Wait for authorization before starting
            if not self.is_authorized():
                self.log.info("[%s-HANDLER] Waiting for authorization...", handler_name)
                await self.auth_event.wait()

            # Wait for cache to be ready before serving requests
            await self.cache_ready_event.wait()

            # Capture current generation
            my_generation = self.get_session_generation()
            my_user_id = self.get_authorized_user_id()

            self.log.info(
                "[%s-HANDLER] Starting request handler loop, generation=%d, user_id=%s",
                handler_name,
                my_generation,
                my_user_id,
            )

            # Inner loop: process requests for this generation
            while (
                self.is_authorized()
                and self.get_session_generation() == my_generation
                and not self.logout_event.is_set()
            ):
                try:
                    await self.handshake_gate.wait()
                    await asyncio.sleep(1)

                    # Scan for requests
                    requests = self.redis_mgr.scan_and_get_requests(request_pattern)

                    if requests:
                        self.log.info(
                            "[%s-HANDLER] Found %d request(s)",
                            handler_name,
                            len(requests),
                        )

                    for key, req in requests:
                        try:
                            # Re-check generation before processing each request
                            if self.get_session_generation() != my_generation:
                                self.log.info(
                                    "[%s-HANDLER] Generation changed, stopping request processing",
                                    handler_name,
                                )
                                break

                            await process_func(key, req)
                        except Exception as exc:
                            self.log.error(
                                "[%s-HANDLER] Error processing request %s: %s",
                                handler_name,
                                key,
                                exc,
                            )
                            self.redis_mgr.delete_request_key(key)

                except Exception as exc:
                    self.log.error("[%s-HANDLER] Handler error: %s", handler_name, exc)
                    await asyncio.sleep(5)

            # Generation ended - clean up and wait for next auth
            self.log.info(
                "[%s-HANDLER] Exiting generation %d, waiting for next authorization",
                handler_name,
                my_generation,
            )


class ParticipantInfoHandler(BaseRequestHandler):
    """Handles participant info requests from UI."""

    def __init__(
        self,
        client: TelegramClient,
        redis_client: Redis,
        redis_manager: RedisManager,
        handshake_gate: asyncio.Event,
        authorized_check: Callable[[], bool],
        auth_event: asyncio.Event,
        cache_ready_event: asyncio.Event,
        logout_event: asyncio.Event,
        get_session_generation: Callable[[], int],
        get_authorized_user_id: Callable[[], int | None],
    ):
        """Initialize participant info handler.

        Args:
            client: Telegram client instance
            redis_client: Raw Redis client
            redis_manager: RedisManager instance
            handshake_gate: Event to wait for before processing
            authorized_check: Function that returns authorization status
            auth_event: Event that is set when authorization completes
            cache_ready_event: Event that is set when cache warm-up completes
            logout_event: Event that is set when logout begins
            get_session_generation: Function returning current session_generation
            get_authorized_user_id: Function returning current authorized_user_id
        """
        super().__init__(
            redis_client,
            redis_manager,
            handshake_gate,
            authorized_check,
            auth_event,
            cache_ready_event,
            logout_event,
            get_session_generation,
            get_authorized_user_id,
        )
        self.client = client

    def update_client(self, new_client: TelegramClient) -> None:
        """Update the client reference (used after session import or logout).

        Args:
            new_client: New TelegramClient instance
        """
        self.client = new_client
        self.log.info("ParticipantInfoHandler client reference updated")

    async def run(self) -> None:
        """Run the participant info handler loop."""

        async def process_request(key: str, req: Dict[str, Any]) -> None:
            chat_id = req.get("chat_id")
            user_id = req.get("user_id")

            if not chat_id:
                self.log.warning("Missing chat_id in request")
                self.redis_mgr.delete_request_key(key)
                return

            # Fetch participant info
            participant_info = await fetch_participant_info(
                self.client, int(chat_id), user_id, self.redis, self.log
            )

            # Cache the result (30 minute TTL)
            cache_key = (
                f"tgsentinel:participant:{chat_id}:{user_id if user_id else 'chat'}"
            )
            self.redis_mgr.set_response_with_ttl(cache_key, participant_info, ttl=1800)

            # Delete the request key
            self.redis_mgr.delete_request_key(key)

        await self.handle_requests("tgsentinel:participant_request:*", process_request)


class TelegramChatsHandler(BaseRequestHandler):
    """Handles Telegram chats discovery requests from UI."""

    async def run(self) -> None:
        """Run the chats handler loop (serves from Redis cache)."""
        # Outer loop: wait for each new auth/generation
        while True:
            # Wait for authorization before starting
            if not self.is_authorized():
                self.log.info("[CHATS-HANDLER] Waiting for authorization...")
                await self.auth_event.wait()

            # Wait for cache to be ready
            await self.cache_ready_event.wait()

            async def process_request(key: str, req: Dict[str, Any]) -> None:
                request_id = req.get("request_id")
                self.log.info("[CHATS-HANDLER] Processing request_id=%s", request_id)

                # Serve from cache instantly
                # Note: We've already passed cache_ready_event.wait() gate, so cache must be ready
                self.log.debug("[CHATS-HANDLER] Serving from Redis cache")
                chats = self.redis_mgr.get_cached_channels() or []
                self.log.info(
                    "[CHATS-HANDLER] ✓ Served %d channels from cache", len(chats)
                )

                # Send response
                response_key = f"tgsentinel:telegram_chats_response:{request_id}"
                response_data = {"status": "ok", "chats": chats}
                self.redis_mgr.set_response_with_ttl(
                    response_key, response_data, ttl=60
                )
                self.redis_mgr.delete_request_key(key)
                self.log.info(
                    "[CHATS-HANDLER] ✓ Completed request_id=%s: %d chats returned",
                    request_id,
                    len(chats),
                )

            await self.handle_requests(
                "tgsentinel:telegram_chats_request:*", process_request
            )


class TelegramDialogsHandler(BaseRequestHandler):
    """Handles Telegram dialogs requests from UI."""

    async def run(self) -> None:
        """Run the dialogs handler loop (serves from Redis cache)."""
        # Outer loop: wait for each new auth/generation
        while True:
            # Wait for authorization before starting
            if not self.is_authorized():
                self.log.info("[DIALOGS-HANDLER] Waiting for authorization...")
                await self.auth_event.wait()

            # Wait for cache to be ready
            await self.cache_ready_event.wait()

            async def process_request(key: str, req: Dict[str, Any]) -> None:
                request_id = req.get("request_id")
                self.log.info("[DIALOGS-HANDLER] Processing request_id=%s", request_id)

                # Serve from cache instantly
                # Note: We've already passed cache_ready_event.wait() gate, so cache must be ready
                self.log.debug("[DIALOGS-HANDLER] Serving from Redis cache")
                dialogs = self.redis_mgr.get_cached_channels() or []
                self.log.info(
                    "[DIALOGS-HANDLER] ✓ Served %d dialogs from cache", len(dialogs)
                )

                # Send response (match UI expected key format)
                response_key = f"tgsentinel:response:get_dialogs:{request_id}"
                response_data = {
                    "status": "ok",
                    "chats": dialogs,
                }  # UI expects 'chats' key
                self.redis_mgr.set_response_with_ttl(
                    response_key, response_data, ttl=60
                )
                self.redis_mgr.delete_request_key(key)
                self.log.info(
                    "[DIALOGS-HANDLER] ✓ Completed request_id=%s: %d dialogs returned",
                    request_id,
                    len(dialogs),
                )

            await self.handle_requests(
                "tgsentinel:request:get_dialogs:*", process_request
            )


class TelegramUsersHandler(BaseRequestHandler):
    """Handles Telegram users requests from UI."""

    async def run(self) -> None:
        """Run the users handler loop (serves from Redis cache)."""
        # Outer loop: wait for each new auth/generation
        while True:
            # Wait for authorization before starting
            if not self.is_authorized():
                self.log.info("[USERS-HANDLER] Waiting for authorization...")
                await self.auth_event.wait()

            # Wait for cache to be ready
            await self.cache_ready_event.wait()

            async def process_request(key: str, req: Dict[str, Any]) -> None:
                request_id = req.get("request_id")
                self.log.info("[USERS-HANDLER] Processing request_id=%s", request_id)

                # Serve from cache instantly
                # Note: We've already passed cache_ready_event.wait() gate, so cache must be ready
                self.log.debug("[USERS-HANDLER] Serving from Redis cache")
                users = self.redis_mgr.get_cached_users() or []
                self.log.info(
                    "[USERS-HANDLER] ✓ Served %d users from cache", len(users)
                )

                # Send response
                response_key = f"tgsentinel:telegram_users_response:{request_id}"
                response_data = {"status": "ok", "users": users}
                self.redis_mgr.set_response_with_ttl(
                    response_key, response_data, ttl=60
                )
                self.redis_mgr.delete_request_key(key)
                self.log.info(
                    "[USERS-HANDLER] ✓ Completed request_id=%s: %d users returned",
                    request_id,
                    len(users),
                )

            await self.handle_requests(
                "tgsentinel:telegram_users_request:*", process_request
            )


class TelegramTestMessageHandler(BaseRequestHandler):
    """Handles test message send requests from the Message Formats Editor."""

    def __init__(
        self,
        redis_client: Redis,
        redis_manager: RedisManager,
        handshake_gate: asyncio.Event,
        authorized_check: Callable[[], bool],
        auth_event: asyncio.Event,
        cache_ready_event: asyncio.Event,
        logout_event: asyncio.Event,
        get_session_generation: Callable[[], int],
        get_authorized_user_id: Callable[[], int | None],
        get_client: Callable[[], TelegramClient | None],
    ):
        """Initialize test message handler.

        Args:
            get_client: Function that returns the current Telegram client
        """
        super().__init__(
            redis_client=redis_client,
            redis_manager=redis_manager,
            handshake_gate=handshake_gate,
            authorized_check=authorized_check,
            auth_event=auth_event,
            cache_ready_event=cache_ready_event,
            logout_event=logout_event,
            get_session_generation=get_session_generation,
            get_authorized_user_id=get_authorized_user_id,
        )
        self.get_client = get_client

    async def run(self) -> None:
        """Run the test message handler loop."""
        # Outer loop: wait for each new auth/generation
        while True:
            # Wait for authorization before starting
            if not self.is_authorized():
                self.log.info("[TEST-MESSAGE-HANDLER] Waiting for authorization...")
                await self.auth_event.wait()

            # Capture current generation
            my_generation = self.get_session_generation()
            my_user_id = self.get_authorized_user_id()

            self.log.info(
                "[TEST-MESSAGE-HANDLER] Starting handler loop, generation=%d, user_id=%s",
                my_generation,
                my_user_id,
            )

            async def process_request(key: str, req: Dict[str, Any]) -> None:
                request_id = req.get("request_id")
                message = req.get("message", "")
                format_type = req.get("format_type", "unknown")

                self.log.info(
                    "[TEST-MESSAGE-HANDLER] Processing request_id=%s, format_type=%s",
                    request_id,
                    format_type,
                )

                response_key = f"tgsentinel:response:send_test_message:{request_id}"

                try:
                    client = self.get_client()
                    if not client:
                        self.log.warning(
                            "[TEST-MESSAGE-HANDLER] No Telegram client available"
                        )
                        self.redis_mgr.set_response_with_ttl(
                            response_key,
                            {
                                "status": "error",
                                "error": "Telegram client not available",
                            },
                            ttl=60,
                        )
                        self.redis_mgr.delete_request_key(key)
                        return

                    # Send message to Saved Messages
                    await client.send_message("me", message)

                    self.log.info(
                        "[TEST-MESSAGE-HANDLER] ✓ Test message sent successfully: %s",
                        request_id,
                    )

                    self.redis_mgr.set_response_with_ttl(
                        response_key,
                        {"status": "ok", "message": "Test message sent"},
                        ttl=60,
                    )

                except Exception as exc:
                    self.log.error(
                        "[TEST-MESSAGE-HANDLER] Error sending test message: %s", exc
                    )
                    self.redis_mgr.set_response_with_ttl(
                        response_key,
                        {"status": "error", "error": str(exc)},
                        ttl=60,
                    )

                self.redis_mgr.delete_request_key(key)

            await self.handle_requests(
                "tgsentinel:request:send_test_message:*", process_request
            )
