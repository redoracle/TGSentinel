"""
Worker orchestration for TG Sentinel.

Manages background workers, periodic tasks, and request handlers.
Coordinates digest generation, metrics logging, and status updates.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.engine import Engine
from telethon import TelegramClient

from .config import AppCfg
from .digest import send_digest
from .digest_scheduler import DigestScheduler
from .digest_worker import UnifiedDigestWorker
from .metrics import dump
from .redis_operations import RedisManager
from .store import cleanup_old_messages, vacuum_database
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

        # Initialize digest scheduler and unified worker
        self.digest_scheduler = DigestScheduler(cfg)
        self.unified_digest = UnifiedDigestWorker(
            cfg, engine, self.digest_scheduler, redis_manager
        )

    async def worker(self) -> None:
        """Main message processing worker."""
        # Get current client dynamically (handles session imports)
        current_client = self.client_ref()
        await process_loop(self.cfg, current_client, self.engine, self.handshake_gate)

    async def periodic_digest(self) -> None:
        """Send hourly digests periodically (DEPRECATED - use unified_digest_worker).

        Kept for backward compatibility during migration.
        Will be removed once unified digest worker is fully validated.
        """
        # Only run if hourly digests are enabled
        if not self.cfg.alerts.digest.hourly:
            log.info("[DIGEST] Hourly digests disabled, worker sleeping")
            while True:
                await asyncio.sleep(3600)  # Check config every hour in case it changes
                if self.cfg.alerts.digest.hourly:
                    break

        while True:
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
        """Send daily digests periodically (DEPRECATED - use unified_digest_worker).

        Kept for backward compatibility during migration.
        Will be removed once unified digest worker is fully validated.
        """
        # Only run if daily digests are enabled
        if not self.cfg.alerts.digest.daily:
            log.info("[DIGEST] Daily digests disabled, worker sleeping")
            while True:
                await asyncio.sleep(86400)  # Check config every 24h in case it changes
                if self.cfg.alerts.digest.daily:
                    break

        while True:
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

    async def unified_digest_worker(self) -> None:
        """Unified digest worker using schedule-driven architecture.

        Replaces periodic_digest() and daily_digest() with a single worker that:
        - Discovers due schedules every 5 minutes
        - Collects messages per schedule with deduplication
        - Formats and delivers digests with profile badges
        """
        current_client = self.client_ref()
        await self.unified_digest.run(current_client, self.handshake_gate)

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
                # Use atomic TTL refresh to prevent TOCTOU races
                # This preserves current status (e.g., "ready") without overwriting concurrent updates
                await asyncio.to_thread(
                    self.redis_mgr.refresh_worker_status_ttl, ttl=3600
                )

    async def database_cleanup_worker(self) -> None:
        """Periodic database cleanup to enforce retention policy."""
        # Wait for initial authorization before starting cleanup cycle
        await self.handshake_gate.wait()

        while True:
            if not self.cfg.system.database.cleanup_enabled:
                # If cleanup disabled, just sleep and check again
                await asyncio.sleep(3600)  # Check every hour
                continue

            try:
                log.info(
                    "[DATABASE-CLEANUP] Starting cleanup: retention_days=%d, max_messages=%d",
                    self.cfg.system.database.retention_days,
                    self.cfg.system.database.max_messages,
                )

                # Run cleanup
                stats = await asyncio.to_thread(
                    cleanup_old_messages,
                    self.engine,
                    retention_days=self.cfg.system.database.retention_days,
                    max_messages=self.cfg.system.database.max_messages,
                )

                log.info(
                    "[DATABASE-CLEANUP] Deleted %d messages (age-based: %d, count-based: %d), remaining: %d",
                    stats["total_deleted"],
                    stats["deleted_by_age"],
                    stats["deleted_by_count"],
                    stats["remaining_count"],
                )

                # Run VACUUM if enabled and it's the right hour
                if self.cfg.system.database.vacuum_on_cleanup:
                    current_hour = datetime.now().hour
                    preferred_hour = self.cfg.system.database.vacuum_hour

                    # Run VACUUM if within 1 hour of preferred time or if significant cleanup happened
                    # Use wraparound-aware comparison for midnight boundary (e.g. 23→0)
                    hour_delta = (current_hour - preferred_hour) % 24
                    within_hour_window = min(hour_delta, 24 - hour_delta) <= 1

                    should_vacuum = (
                        within_hour_window
                        or stats["total_deleted"]
                        > 100  # Force VACUUM after large cleanup
                    )

                    if should_vacuum:
                        log.info("[DATABASE-CLEANUP] Running VACUUM...")
                        vacuum_stats = await asyncio.to_thread(
                            vacuum_database, self.engine
                        )

                        if vacuum_stats["success"]:
                            log.info(
                                "[DATABASE-CLEANUP] VACUUM completed in %.2fs",
                                vacuum_stats["duration_seconds"],
                            )
                        else:
                            log.warning(
                                "[DATABASE-CLEANUP] VACUUM failed: %s",
                                vacuum_stats.get("error"),
                            )
                    else:
                        log.debug(
                            "[DATABASE-CLEANUP] Skipping VACUUM (current hour: %d, preferred: %d)",
                            current_hour,
                            preferred_hour,
                        )

                log.info("[DATABASE-CLEANUP] Cleanup complete")

            except Exception as e:
                log.error("[DATABASE-CLEANUP] Cleanup failed: %s", e, exc_info=True)

            # Sleep until next cleanup cycle
            await asyncio.sleep(self.cfg.system.database.cleanup_interval_hours * 3600)

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
            "unified_digest_worker",  # New schedule-driven digest worker
            "metrics_logger",
            "worker_status_refresher",
            "database_cleanup_worker",
            "participant_info_handler",
            "telegram_chats_handler",
            "telegram_dialogs_handler",
            "telegram_users_handler",
            "session_persistence_handler",
            "channels_users_cache_refresher",
        ]

        results = await asyncio.gather(
            self.worker(),
            self.unified_digest_worker(),  # Replaces periodic_digest + daily_digest
            self.metrics_logger(),
            self.worker_status_refresher(),
            self.database_cleanup_worker(),
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
