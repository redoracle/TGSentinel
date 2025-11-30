"""
Batch feedback processor for deferred centroid recomputation.

This module batches profile updates to avoid thrashing on rapid feedback.
Instead of recomputing centroids on every feedback, we queue profiles
and recompute in batches (every 10 minutes or when 5+ profiles changed).

State Persistence:
    - Queue and last batch time are persisted to Redis only
    - Restores state on restart, preventing loss of pending profiles
    - Redis keys: tgsentinel:batch_processor:queue and :last_batch_time
    - Gracefully handles Redis unavailability

Part of Phase 3: Performance optimization through batching.
"""

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# Redis keys for batch processor state persistence
BATCH_QUEUE_KEY = "tgsentinel:batch_processor:queue"
BATCH_LAST_TIME_KEY = "tgsentinel:batch_processor:last_batch_time"


@dataclass
class RecomputeQueue:
    """Queue of profiles needing centroid recomputation."""

    profiles_pending: Set[str] = field(default_factory=set)
    last_batch_time: datetime = field(default_factory=datetime.now)
    lock: threading.RLock = field(default_factory=threading.RLock)


class BatchFeedbackProcessor:
    """
    Batch processor for profile centroid recomputation.

    Collects profiles that need recomputation and processes them
    in batches to avoid thrashing under rapid feedback.

    Thread-safe and async-compatible for integration with FastAPI/Starlette.
    """

    # Configuration
    BATCH_INTERVAL_SECONDS = 600  # 10 minutes
    BATCH_SIZE_THRESHOLD = 5  # Process when 5+ profiles pending

    def __init__(
        self, engine: Engine, config_dir: Path, redis_client: Optional[Redis] = None
    ):
        """
        Initialize batch processor.

        Args:
            engine: SQLAlchemy engine for database access
            config_dir: Path to config directory (for profile YAMLs)
            redis_client: Redis client for state persistence (optional)
        """
        self.engine = engine
        self.config_dir = config_dir
        self.redis = redis_client
        self._queue = RecomputeQueue()
        self._state_lock = threading.Lock()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Load persisted state from Redis if available
        if self.redis:
            self._load_state_from_redis()

    def _load_state_from_redis(self):
        """
        Load persisted queue state from Redis.

        Restores pending profiles and last batch time across restarts.
        """
        if not self.redis:
            return

        try:
            # Load pending profiles
            queue_data = self.redis.get(BATCH_QUEUE_KEY)
            if queue_data and isinstance(queue_data, (bytes, str)):
                # Decode bytes to UTF-8 if needed
                if isinstance(queue_data, bytes):
                    queue_data = queue_data.decode("utf-8")
                profile_list = json.loads(queue_data)
                with self._queue.lock:
                    self._queue.profiles_pending = set(profile_list)
                log.info(
                    f"[BATCH-PROCESSOR] Restored {len(profile_list)} profiles from Redis"
                )

            # Load last batch time
            last_time_data = self.redis.get(BATCH_LAST_TIME_KEY)
            if last_time_data and isinstance(last_time_data, (bytes, str)):
                # Decode bytes to UTF-8 if needed
                if isinstance(last_time_data, bytes):
                    last_time_data = last_time_data.decode("utf-8")
                with self._queue.lock:
                    self._queue.last_batch_time = datetime.fromisoformat(last_time_data)
                log.info(
                    f"[BATCH-PROCESSOR] Restored last batch time: {last_time_data}"
                )
        except Exception as e:
            log.warning(
                f"[BATCH-PROCESSOR] Failed to load state from Redis: {e}", exc_info=True
            )

    def _save_state_to_redis(self):
        """
        Persist current queue state to Redis.

        Saves pending profiles and last batch time for recovery after restarts.
        """
        if not self.redis:
            return

        try:
            with self._queue.lock:
                # Save pending profiles as JSON array
                profile_list = list(self._queue.profiles_pending)
                self.redis.set(BATCH_QUEUE_KEY, json.dumps(profile_list))

                # Save last batch time as ISO format string
                self.redis.set(
                    BATCH_LAST_TIME_KEY, self._queue.last_batch_time.isoformat()
                )
        except Exception as e:
            log.warning(
                f"[BATCH-PROCESSOR] Failed to save state to Redis: {e}", exc_info=True
            )

    def schedule_recompute(self, profile_id: str):
        """
        Schedule a profile for centroid recomputation.

        Does NOT recompute immediately - adds to queue for batch processing.

        Args:
            profile_id: Profile identifier (e.g., "3000")
        """
        with self._queue.lock:
            self._queue.profiles_pending.add(profile_id)

            count = len(self._queue.profiles_pending)
            log.debug(
                f"[BATCH-PROCESSOR] Scheduled {profile_id} for recompute "
                f"({count} profiles pending)"
            )

            # If threshold reached, log (actual processing happens in periodic task)
            if count >= self.BATCH_SIZE_THRESHOLD:
                log.info(
                    f"[BATCH-PROCESSOR] Batch threshold reached ({count} profiles), "
                    f"will process on next iteration"
                )

        # Persist state to Redis after updating queue
        self._save_state_to_redis()

    async def start(self):
        """Start the background batch processing task."""
        with self._state_lock:
            if self._running:
                log.warning("[BATCH-PROCESSOR] Already running")
                return
            self._running = True

        self._task = asyncio.create_task(self._run_periodic_batch())
        log.info(
            f"[BATCH-PROCESSOR] Started (interval: {self.BATCH_INTERVAL_SECONDS}s, "
            f"threshold: {self.BATCH_SIZE_THRESHOLD} profiles)"
        )

    async def stop(self):
        """Stop the background processing task gracefully."""
        with self._state_lock:
            if not self._running:
                return
            self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        log.info("[BATCH-PROCESSOR] Stopped")

    async def _run_periodic_batch(self):
        """Background task that processes batches periodically."""
        log.info("[BATCH-PROCESSOR] Periodic batch task started")

        while self._running:
            try:
                # Wait for interval
                await asyncio.sleep(self.BATCH_INTERVAL_SECONDS)

                # Check if batch needed
                with self._queue.lock:
                    count = len(self._queue.profiles_pending)
                    time_since_last = datetime.now() - self._queue.last_batch_time

                # Process if: time interval passed OR threshold reached
                should_process = (
                    time_since_last.total_seconds() >= self.BATCH_INTERVAL_SECONDS
                    or count >= self.BATCH_SIZE_THRESHOLD
                )

                if should_process and count > 0:
                    log.info(
                        f"[BATCH-PROCESSOR] Processing batch "
                        f"({count} profiles, {time_since_last.total_seconds():.0f}s elapsed)"
                    )
                    await self.process_batch()
                else:
                    log.debug(
                        f"[BATCH-PROCESSOR] No batch needed "
                        f"({count} profiles pending, {time_since_last.total_seconds():.0f}s elapsed)"
                    )

            except asyncio.CancelledError:
                log.info("[BATCH-PROCESSOR] Batch task cancelled")
                break
            except Exception as e:
                log.error(
                    f"[BATCH-PROCESSOR] Error in periodic batch: {e}", exc_info=True
                )
                # Continue running despite errors

    async def process_batch(self, trigger_type: str = "automatic"):
        """
        Process a batch of profiles needing recomputation.

        This clears semantic caches for all pending profiles,
        forcing fresh centroid calculation on next scoring.

        Args:
            trigger_type: "automatic" or "manual" to track how batch was triggered
        """
        with self._queue.lock:
            profiles_to_process = list(self._queue.profiles_pending)
            self._queue.profiles_pending.clear()
            self._queue.last_batch_time = datetime.now()

        # Persist state to Redis after clearing queue and updating time
        self._save_state_to_redis()

        if not profiles_to_process:
            return

        log.info(
            f"[BATCH-PROCESSOR] Processing batch of {len(profiles_to_process)} profiles (trigger: {trigger_type})"
        )

        start_time = datetime.now()

        # Clear semantic caches for all profiles in batch
        # Offload blocking cache-clearing to thread pool to avoid blocking event loop
        from tgsentinel.semantic import clear_profile_cache

        # Create tasks for parallel cache clearing in thread pool
        tasks = [
            asyncio.to_thread(clear_profile_cache, profile_id)
            for profile_id in profiles_to_process
        ]

        # Await all cache clearing operations concurrently
        await asyncio.gather(*tasks)

        for profile_id in profiles_to_process:
            log.debug(f"[BATCH-PROCESSOR] Cleared cache for {profile_id}")

        elapsed = (datetime.now() - start_time).total_seconds()

        # Record batch history in database
        try:
            with self.engine.connect() as con:
                con.execute(
                    text(
                        """
                        INSERT INTO batch_history
                        (started_at, completed_at, profiles_processed,
                         profile_ids, elapsed_seconds, trigger_type, status)
                        VALUES (:started_at, :completed_at, :profiles_processed,
                                :profile_ids, :elapsed_seconds, :trigger_type, :status)
                        """
                    ),
                    {
                        "started_at": start_time.isoformat(),
                        "completed_at": datetime.now().isoformat(),
                        "profiles_processed": len(profiles_to_process),
                        "profile_ids": ",".join(profiles_to_process),
                        "elapsed_seconds": elapsed,
                        "trigger_type": trigger_type,
                        "status": "completed",
                    },
                )
                con.commit()
        except Exception as e:
            log.warning(f"[BATCH-PROCESSOR] Failed to record batch history: {e}")

        log.info(
            "[BATCH-PROCESSOR] Batch complete",
            extra={
                "profiles_processed": len(profiles_to_process),
                "elapsed_seconds": round(elapsed, 2),
                "profiles": profiles_to_process[:10],  # Log first 10 for debugging
            },
        )

    def get_queue_status(self) -> dict:
        """Get current queue status (for monitoring/debugging)."""
        with self._queue.lock:
            return {
                "pending_count": len(self._queue.profiles_pending),
                "pending_profiles": list(self._queue.profiles_pending),
                "last_batch_time": self._queue.last_batch_time.isoformat(),
                "seconds_since_last_batch": (
                    datetime.now() - self._queue.last_batch_time
                ).total_seconds(),
            }


# Global singleton instance
_processor: Optional[BatchFeedbackProcessor] = None
_processor_lock = threading.Lock()


def get_batch_processor(
    engine: Optional[Engine] = None,
    config_dir: Optional[Path] = None,
    redis_client: Optional[Redis] = None,
) -> BatchFeedbackProcessor:
    """
    Get or create global batch processor instance (thread-safe).

    Uses double-checked locking to ensure only one instance is created
    even under concurrent calls.

    Args:
        engine: SQLAlchemy engine (required on first call)
        config_dir: Config directory path (required on first call)
        redis_client: Redis client for state persistence (optional, recommended)

    Returns:
        Global BatchFeedbackProcessor instance

    Raises:
        ValueError: If engine/config_dir not provided on first call
    """
    global _processor

    # First check (without lock) - fast path for already-initialized singleton
    if _processor is None:
        # Acquire lock for initialization
        with _processor_lock:
            # Second check (with lock) - ensure another thread didn't initialize
            if _processor is None:
                if engine is None or config_dir is None:
                    raise ValueError("Must provide engine and config_dir on first call")
                _processor = BatchFeedbackProcessor(engine, config_dir, redis_client)

    return _processor
