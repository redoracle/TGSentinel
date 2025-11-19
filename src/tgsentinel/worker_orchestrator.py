"""
Worker orchestration for TG Sentinel.

Manages background workers, periodic tasks, and request handlers.
Coordinates digest generation, metrics logging, and status updates.
"""

import asyncio
import logging
from typing import Any, Callable

from sqlalchemy.engine import Engine
from telethon import TelegramClient

from .config import AppCfg
from .digest import send_digest
from .metrics import dump
from .redis_operations import RedisManager
from .telegram_request_handlers import (
    ParticipantInfoHandler,
    TelegramChatsHandler,
    TelegramDialogsHandler,
    TelegramUsersHandler,
)
from .worker import process_loop

log = logging.getLogger(__name__)


class WorkerOrchestrator:
    """Orchestrates all background workers and handlers."""

    def __init__(
        self,
        cfg: AppCfg,
        client_ref: Callable[[], TelegramClient],
        engine,
        redis_manager: RedisManager,
        handshake_gate: asyncio.Event,
        authorized_check: Callable[[], bool],
        participant_handler: ParticipantInfoHandler,
        chats_handler: TelegramChatsHandler,
        dialogs_handler: TelegramDialogsHandler,
        users_handler: TelegramUsersHandler,
    ):
        """
        Initialize worker orchestrator.

        Args:
            cfg: Application configuration
            client_ref: Callable returning current Telegram client instance (dynamic lookup)
            engine: SQLAlchemy engine
            redis_manager: Redis operations manager
            handshake_gate: Event to control worker activation
            authorized_check: Function returning current authorization status
            participant_handler: Handler for participant info requests
            chats_handler: Handler for chat discovery requests
            dialogs_handler: Handler for dialogs requests
            users_handler: Handler for users requests
        """
        self.cfg = cfg
        self.client_ref = client_ref
        self.engine = engine
        self.redis_mgr = redis_manager
        self.handshake_gate = handshake_gate
        self.authorized_check = authorized_check
        self.participant_handler = participant_handler
        self.chats_handler = chats_handler
        self.dialogs_handler = dialogs_handler
        self.users_handler = users_handler

    async def worker(self) -> None:
        """Main message processing worker."""
        # Get current client dynamically (handles session imports)
        current_client = self.client_ref()
        await process_loop(self.cfg, current_client, self.engine, self.handshake_gate)

    async def periodic_digest(self) -> None:
        """Send hourly digests periodically."""
        while True:
            if self.cfg.alerts.digest.hourly:
                await self.handshake_gate.wait()
                log.info("Sending hourly digest...")
                current_client = self.client_ref()
                await send_digest(
                    self.engine,
                    current_client,
                    since_hours=1,
                    top_n=self.cfg.alerts.digest.top_n,
                    mode=self.cfg.alerts.mode,
                    channel=self.cfg.alerts.target_channel,
                    channels_config=self.cfg.channels,
                    min_score=0.0,
                )
            await asyncio.sleep(3600)  # Every hour

    async def daily_digest(self) -> None:
        """Send daily digests periodically."""
        while True:
            if self.cfg.alerts.digest.daily:
                await self.handshake_gate.wait()
                log.info("Sending daily digest...")
                current_client = self.client_ref()
                await send_digest(
                    self.engine,
                    current_client,
                    since_hours=24,
                    top_n=self.cfg.alerts.digest.top_n,
                    mode=self.cfg.alerts.mode,
                    channel=self.cfg.alerts.target_channel,
                    channels_config=self.cfg.channels,
                    min_score=0.0,
                )
            await asyncio.sleep(86400)  # Every 24 hours

    async def metrics_logger(self) -> None:
        """Log metrics periodically."""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            log.info("Sentinel heartbeat - monitoring active")
            dump()

    async def worker_status_refresher(self) -> None:
        """Refresh worker status in Redis periodically to prevent expiration."""
        while True:
            await asyncio.sleep(600)  # Every 10 minutes
            if self.authorized_check():
                self.redis_mgr.publish_worker_status(
                    authorized=True, status="authorized", ttl=3600
                )
                log.debug("[HEARTBEAT] Worker status refreshed in Redis")

    async def run_all_workers(
        self,
        session_persistence_handler_func: Callable[..., Any],
        cache_refresher_func: Callable[..., Any],
    ) -> None:
        """
        Run all background workers concurrently.

        Args:
            session_persistence_handler_func: Session persistence handler
            cache_refresher_func: Cache refresher function
        """
        worker_names = [
            "worker",
            "periodic_digest",
            "daily_digest",
            "metrics_logger",
            "worker_status_refresher",
            "participant_info_handler",
            "telegram_chats_handler",
            "telegram_dialogs_handler",
            "telegram_users_handler",
            "session_persistence_handler",
            "channels_users_cache_refresher",
        ]

        results = await asyncio.gather(
            self.worker(),
            self.periodic_digest(),
            self.daily_digest(),
            self.metrics_logger(),
            self.worker_status_refresher(),
            self.participant_handler.run(),
            self.chats_handler.run(),
            self.dialogs_handler.run(),
            self.users_handler.run(),
            session_persistence_handler_func(),
            cache_refresher_func(),
            return_exceptions=True,
        )

        # Check for exceptions in worker results
        for worker_name, result in zip(worker_names, results):
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                log.error(
                    "Worker '%s' failed with exception: %s",
                    worker_name,
                    result,
                    exc_info=result,
                )
