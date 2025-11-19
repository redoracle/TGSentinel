"""Shutdown coordinator for TG Sentinel.

This module handles graceful shutdown of the Telegram client and background workers,
including signal handling and cleanup operations.
"""

import asyncio
import logging
import signal
from typing import Callable, List, Optional

from telethon import TelegramClient


class ShutdownCoordinator:
    """Coordinates graceful shutdown of the application."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        """Initialize shutdown coordinator.

        Args:
            loop: The asyncio event loop
        """
        self.loop = loop
        self.shutdown_event = asyncio.Event()
        self.log = logging.getLogger(__name__)
        self._signal_handlers_registered = False

    def register_signal_handlers(self) -> None:
        """Register SIGTERM and SIGINT signal handlers for graceful shutdown."""
        if self._signal_handlers_registered:
            return

        def _signal_handler():
            """Signal handler that safely triggers shutdown from signal context."""
            self.loop.call_soon_threadsafe(self.shutdown_event.set)

        try:
            self.loop.add_signal_handler(signal.SIGTERM, _signal_handler)
            self.loop.add_signal_handler(signal.SIGINT, _signal_handler)
            self._signal_handlers_registered = True
            self.log.debug("Registered SIGTERM and SIGINT handlers")
        except NotImplementedError:
            # Signals not available (e.g., on Windows); ignore
            self.log.debug("Signal handlers not available on this platform")

    async def graceful_shutdown(
        self, client: TelegramClient, timeout: int = 15
    ) -> None:
        """Perform graceful shutdown of Telegram client.

        Args:
            client: Telegram client to disconnect
            timeout: Timeout in seconds for disconnect operation
        """
        try:
            self.log.info(
                "Shutting down; disconnecting Telegram client to flush session..."
            )
            try:
                # Explicitly save session before disconnect
                try:
                    if hasattr(client, "session") and hasattr(client.session, "save"):
                        client.session.save()  # type: ignore[attr-defined]
                        self.log.debug("Session saved before disconnect")
                except Exception as save_exc:
                    self.log.debug("Failed to save session: %s", save_exc)

                await asyncio.wait_for(client.disconnect(), timeout=timeout)  # type: ignore[arg-type]
                self.log.info("✓ Telegram client disconnected successfully")
            except asyncio.TimeoutError:
                self.log.warning(
                    "Client disconnect timed out after %ds; proceeding with shutdown",
                    timeout,
                )
            except Exception as disc_exc:
                self.log.debug("Error during client disconnect: %s", disc_exc)
        except Exception as exc:
            self.log.error("Error during graceful shutdown: %s", exc, exc_info=True)

    async def wait_for_shutdown_or_completion(
        self,
        workers_task: asyncio.Task,
        background_tasks: Optional[List[asyncio.Task]] = None,
    ) -> None:
        """Wait for either shutdown signal or workers completion.

        Args:
            workers_task: Main workers task to wait for
            background_tasks: Optional list of background tasks to cancel on shutdown
        """
        # Wait for either the workers to complete or shutdown signal
        shutdown_task = asyncio.create_task(self.shutdown_event.wait())
        done, pending = await asyncio.wait(
            [workers_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If shutdown was triggered, cancel all tasks
        if shutdown_task in done:
            self.log.info("Shutdown signal received, cancelling workers...")
            workers_task.cancel()

            # Cancel background tasks if provided
            if background_tasks:
                for task in background_tasks:
                    task.cancel()
                    self.log.debug("Cancelled background task: %s", task.get_name())

            # Wait for workers to finish cancellation
            try:
                await workers_task
            except asyncio.CancelledError:
                self.log.debug("Workers task cancelled successfully")

            # Wait for background tasks to finish cancellation
            if background_tasks:
                for task in background_tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        self.log.debug("Background task cancelled: %s", task.get_name())
        else:
            # Workers completed (shouldn't happen normally), cancel shutdown task
            shutdown_task.cancel()
            self.log.info("Workers completed normally")

    async def cancel_tasks_by_pattern(self, pattern: str, timeout: float = 2.0) -> int:
        """Cancel tasks matching a name pattern.

        Args:
            pattern: String pattern to match in task coroutine names
            timeout: Timeout in seconds to wait for cancellation

        Returns:
            Number of tasks successfully cancelled
        """
        all_tasks = asyncio.all_tasks()
        matching_tasks = []

        for t in all_tasks:
            if t.done():
                continue

            # Prefer task name, fall back to coroutine string representation
            task_name = t.get_name()
            if task_name and pattern in task_name:
                matching_tasks.append(t)
            elif not task_name and pattern in str(t.get_coro()):
                matching_tasks.append(t)

        if not matching_tasks:
            return 0

        self.log.info(
            "Cancelling %d task(s) matching '%s'", len(matching_tasks), pattern
        )

        # Cancel all matching tasks
        for task in matching_tasks:
            task.cancel()

        # Wait for tasks to complete cancellation with timeout
        done, pending = await asyncio.wait(matching_tasks, timeout=timeout)

        # Log successfully cancelled tasks
        self.log.debug("Cancelled %d task(s) successfully", len(done))

        # Warn about tasks that didn't complete within timeout
        if pending:
            self.log.warning(
                "Timeout waiting for %d task(s) to cancel after %s seconds",
                len(pending),
                timeout,
            )
            for task in pending:
                task_name = task.get_name() or str(task.get_coro())
                self.log.warning("Task still pending: %s", task_name)

            # Attempt second cancellation for pending tasks
            for task in pending:
                task.cancel()

            # Give them a brief grace period
            done2, still_pending = await asyncio.wait(pending, timeout=1.0)

            if still_pending:
                self.log.error(
                    "Failed to cancel %d task(s) even after retry", len(still_pending)
                )
                for task in still_pending:
                    task_name = task.get_name() or str(task.get_coro())
                    self.log.error("Task still running: %s", task_name)

            # Return total successfully cancelled (first attempt + retry)
            return len(done) + len(done2)

        return len(done)
