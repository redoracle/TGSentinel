"""
Feedback aggregation to prevent single-feedback profile changes.

This module tracks feedback per profile and only triggers adjustments
when thresholds are met (e.g., 3+ borderline false positives).

Part of Phase 1: Stability Foundation - prevents "twitchy" behavior.
Phase 3: Adds background decay task for automatic cleanup.
"""

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class FeedbackStats:
    """Per-profile feedback counters with timestamps for decay."""

    profile_id: str

    # Counters for different score bands
    borderline_fp: int = 0  # thumbs down, score in [threshold, threshold + 0.20]
    severe_fp: int = 0  # thumbs down, score >= threshold + 0.20
    strong_tp: int = 0  # thumbs up, score >= threshold + 0.15
    marginal_tp: int = 0  # thumbs up, score < threshold + 0.15 (informational only)

    # Timestamps for decay (store individual feedback times)
    last_borderline_fp: List[datetime] = field(default_factory=list)
    last_severe_fp: List[datetime] = field(default_factory=list)
    last_strong_tp: List[datetime] = field(default_factory=list)
    last_marginal_tp: List[datetime] = field(default_factory=list)

    # Cumulative drift tracking (how much we've auto-adjusted)
    cumulative_threshold_delta: float = 0.0
    cumulative_negative_weight_delta: float = 0.0

    def decay_old_feedback(self, window_days: int = 7):
        """Remove feedback older than window_days."""
        cutoff = datetime.now() - timedelta(days=window_days)

        self.last_borderline_fp = [ts for ts in self.last_borderline_fp if ts > cutoff]
        self.last_severe_fp = [ts for ts in self.last_severe_fp if ts > cutoff]
        self.last_strong_tp = [ts for ts in self.last_strong_tp if ts > cutoff]
        self.last_marginal_tp = [ts for ts in self.last_marginal_tp if ts > cutoff]

        self.borderline_fp = len(self.last_borderline_fp)
        self.severe_fp = len(self.last_severe_fp)
        self.strong_tp = len(self.last_strong_tp)
        self.marginal_tp = len(self.last_marginal_tp)


class FeedbackAggregator:
    """
    Thread-safe in-memory feedback aggregator.

    Aggregates feedback per profile to prevent single-feedback profile changes.
    Only triggers adjustments when thresholds are met.
    """

    # Configuration constants (can be moved to config later)
    BORDERLINE_FP_THRESHOLD = 3  # Need 3 borderline FPs before raising threshold
    SEVERE_FP_THRESHOLD = 2  # Need 2 severe FPs before adding negative sample (Phase 2)
    STRONG_TP_THRESHOLD = 2  # Need 2 strong TPs before adding positive sample (Phase 2)

    FEEDBACK_WINDOW_DAYS = 7  # Only count feedback from last 7 days
    DECAY_INTERVAL_HOURS = 24  # Run decay every 24 hours

    # Drift caps per profile (prevent runaway adjustments)
    MAX_THRESHOLD_DRIFT = 0.25
    MAX_NEGATIVE_WEIGHT_DRIFT = 0.1

    def __init__(self):
        self._stats: Dict[str, FeedbackStats] = {}
        self._lock = threading.RLock()
        self._last_decay = datetime.now()

        # Phase 3: Background decay task
        self._decay_task: Optional[asyncio.Task] = None
        self._decay_running = False

    def record_feedback(
        self, profile_id: str, label: str, semantic_score: float, threshold: float
    ) -> Dict[str, Any]:
        """
        Record feedback and return recommended action (if any).

        Args:
            profile_id: Profile identifier (e.g., "3000")
            label: "up" or "down"
            semantic_score: Message's semantic similarity score
            threshold: Profile's current threshold

        Returns:
            {
                "action": "raise_threshold" | "none",
                "reason": str,
                "stats": FeedbackStats,
                "delta": float (if action is raise_threshold)
            }
        """
        with self._lock:
            # Periodic decay (every 24 hours)
            if datetime.now() - self._last_decay > timedelta(
                hours=self.DECAY_INTERVAL_HOURS
            ):
                self._decay_all_profiles()
                self._last_decay = datetime.now()

            # Get or create stats for profile
            if profile_id not in self._stats:
                self._stats[profile_id] = FeedbackStats(profile_id=profile_id)

            stats = self._stats[profile_id]
            now = datetime.now()

            # Classify and record feedback
            if label == "down":
                if semantic_score >= (threshold + 0.20):
                    # Severe false positive (Phase 2: add to negative samples)
                    stats.severe_fp += 1
                    stats.last_severe_fp.append(now)
                    log.debug(
                        f"[FEEDBACK-AGG] {profile_id}: severe FP recorded "
                        f"(score {semantic_score:.3f}, total: {stats.severe_fp})"
                    )
                elif semantic_score >= threshold:
                    # Borderline false positive (Phase 1: raise threshold)
                    stats.borderline_fp += 1
                    stats.last_borderline_fp.append(now)
                    log.debug(
                        f"[FEEDBACK-AGG] {profile_id}: borderline FP recorded "
                        f"(score {semantic_score:.3f}, total: {stats.borderline_fp})"
                    )

            elif label == "up":
                if semantic_score >= (threshold + 0.15):
                    # Strong true positive (Phase 2: add to positive samples)
                    stats.strong_tp += 1
                    stats.last_strong_tp.append(now)
                    log.debug(
                        f"[FEEDBACK-AGG] {profile_id}: strong TP recorded "
                        f"(score {semantic_score:.3f}, total: {stats.strong_tp})"
                    )
                else:
                    # Marginal true positive (informational only, no action triggered)
                    stats.marginal_tp += 1
                    stats.last_marginal_tp.append(now)
                    log.debug(
                        f"[FEEDBACK-AGG] {profile_id}: marginal TP recorded "
                        f"(score {semantic_score:.3f}, total: {stats.marginal_tp})"
                    )

            # Evaluate if action should be triggered
            return self._evaluate_action(stats, threshold)

    def _evaluate_action(
        self, stats: FeedbackStats, threshold: float
    ) -> Dict[str, Any]:
        """
        Determine if aggregated feedback warrants action.

        Phase 1: Threshold adjustments
        Phase 2: Sample augmentation
        """
        # Check drift caps first
        if stats.cumulative_threshold_delta >= self.MAX_THRESHOLD_DRIFT:
            log.warning(
                f"[FEEDBACK-AGG] {stats.profile_id}: drift cap reached "
                f"({stats.cumulative_threshold_delta:.2f}), manual review required"
            )
            return {
                "action": "none",
                "reason": f"Drift cap reached ({stats.cumulative_threshold_delta:.2f}), manual review required",
                "stats": stats,
            }

        # Phase 2: Severe false positives → add to negative samples
        if stats.severe_fp >= self.SEVERE_FP_THRESHOLD:
            log.info(
                f"[FEEDBACK-AGG] {stats.profile_id}: add negative sample recommended "
                f"({stats.severe_fp} severe FPs, threshold={self.SEVERE_FP_THRESHOLD})"
            )
            return {
                "action": "add_negative_sample",
                "reason": f"{stats.severe_fp} severe false positives detected (threshold: {self.SEVERE_FP_THRESHOLD})",
                "stats": stats,
            }

        # Phase 2: Strong true positives → add to positive samples
        if stats.strong_tp >= self.STRONG_TP_THRESHOLD:
            log.info(
                f"[FEEDBACK-AGG] {stats.profile_id}: add positive sample recommended "
                f"({stats.strong_tp} strong TPs, threshold={self.STRONG_TP_THRESHOLD})"
            )
            return {
                "action": "add_positive_sample",
                "reason": f"{stats.strong_tp} strong true positives detected (threshold: {self.STRONG_TP_THRESHOLD})",
                "stats": stats,
            }

        # Phase 1: Borderline false positives → raise threshold
        if stats.borderline_fp >= self.BORDERLINE_FP_THRESHOLD:
            # Check if we have room for adjustment
            if (stats.cumulative_threshold_delta + 0.1) <= self.MAX_THRESHOLD_DRIFT:
                log.info(
                    f"[FEEDBACK-AGG] {stats.profile_id}: threshold raise recommended "
                    f"({stats.borderline_fp} borderline FPs)"
                )
                return {
                    "action": "raise_threshold",
                    "reason": (
                        f"{stats.borderline_fp} borderline false positives detected "
                        f"in last {self.FEEDBACK_WINDOW_DAYS} days"
                    ),
                    "stats": stats,
                    "delta": 0.1,
                }

        # No action needed
        return {
            "action": "none",
            "reason": "Insufficient feedback for action",
            "stats": stats,
        }

    def reset_stats(self, profile_id: str, action_type: str):
        """
        Reset counters after action is taken.

        Args:
            profile_id: Profile that was adjusted
            action_type: "raise_threshold" | "add_negative_sample" | "add_positive_sample"
        """
        with self._lock:
            if profile_id not in self._stats:
                return

            stats = self._stats[profile_id]

            if action_type == "raise_threshold":
                stats.borderline_fp = 0
                stats.last_borderline_fp.clear()
                stats.cumulative_threshold_delta += 0.1
                log.debug(
                    f"[FEEDBACK-AGG] {profile_id}: reset borderline FP counter "
                    f"(cumulative delta: {stats.cumulative_threshold_delta:.2f})"
                )
            elif action_type == "add_negative_sample":
                stats.severe_fp = 0
                stats.last_severe_fp.clear()
                log.debug(f"[FEEDBACK-AGG] {profile_id}: reset severe FP counter")
            elif action_type == "add_positive_sample":
                stats.strong_tp = 0
                stats.last_strong_tp.clear()
                log.debug(f"[FEEDBACK-AGG] {profile_id}: reset strong TP counter")

    def _decay_all_profiles(self):
        """Decay old feedback across all profiles (called every 24h)."""
        log.info(
            f"[FEEDBACK-AGG] Running feedback decay (window: {self.FEEDBACK_WINDOW_DAYS} days)"
        )
        decayed_count = 0
        with self._lock:
            for stats in self._stats.values():
                before_count = stats.borderline_fp + stats.severe_fp + stats.strong_tp
                stats.decay_old_feedback(self.FEEDBACK_WINDOW_DAYS)
                after_count = stats.borderline_fp + stats.severe_fp + stats.strong_tp

                if before_count != after_count:
                    decayed_count += 1
                    log.debug(
                        f"[FEEDBACK-AGG] {stats.profile_id}: decayed "
                        f"{before_count - after_count} old feedback items"
                    )

        log.info(f"[FEEDBACK-AGG] Decay complete: {decayed_count} profiles updated")

    def get_stats(self, profile_id: str) -> Optional[FeedbackStats]:
        """Get current stats for a profile."""
        with self._lock:
            return self._stats.get(profile_id)

    def get_all_stats(self) -> Dict[str, FeedbackStats]:
        """Get stats for all profiles (for monitoring/debugging)."""
        with self._lock:
            return dict(self._stats)

    # Phase 3: Background decay task methods

    async def start_decay_task(self):
        """Start background task for periodic feedback decay."""
        if self._decay_running:
            log.warning("[FEEDBACK-AGG] Decay task already running")
            return

        self._decay_running = True
        self._decay_task = asyncio.create_task(self._run_periodic_decay())
        log.info(
            f"[FEEDBACK-AGG] Started decay task "
            f"(interval: {self.DECAY_INTERVAL_HOURS}h, window: {self.FEEDBACK_WINDOW_DAYS}d)"
        )

    async def stop_decay_task(self):
        """Stop background decay task gracefully."""
        if not self._decay_running:
            return

        self._decay_running = False
        if self._decay_task:
            self._decay_task.cancel()
            try:
                await self._decay_task
            except asyncio.CancelledError:
                pass

        log.info("[FEEDBACK-AGG] Stopped decay task")

    async def _run_periodic_decay(self):
        """Background task that runs decay periodically."""
        log.info("[FEEDBACK-AGG] Periodic decay task started")

        while self._decay_running:
            try:
                # Wait for interval (convert hours to seconds)
                await asyncio.sleep(self.DECAY_INTERVAL_HOURS * 3600)

                # Run decay (timestamp updated after we release the main lock)
                log.info("[FEEDBACK-AGG] Running scheduled decay")
                self._decay_all_profiles()
                with self._lock:
                    self._last_decay = datetime.now()

            except asyncio.CancelledError:
                log.info("[FEEDBACK-AGG] Decay task cancelled")
                break
            except Exception as e:
                log.error(f"[FEEDBACK-AGG] Error in decay task: {e}", exc_info=True)
                # Continue running despite errors


# Global singleton instance
_aggregator: Optional[FeedbackAggregator] = None
_aggregator_lock = threading.Lock()


def get_feedback_aggregator() -> FeedbackAggregator:
    """Get or create global feedback aggregator instance (thread-safe)."""
    global _aggregator

    # Fast path: check without lock first
    if _aggregator is not None:
        return _aggregator

    # Slow path: acquire lock and create if still None (double-checked locking)
    with _aggregator_lock:
        if _aggregator is None:
            _aggregator = FeedbackAggregator()
        return _aggregator
