"""
Alert feedback aggregation for keyword-based alert profiles.

This module handles feedback for alert profiles (min_score adjustments)
separately from interest profiles (threshold/semantic adjustments).

Alerts use keyword matching with min_score thresholds. Feedback drives:
- Thumbs down (👎) → raises min_score (fewer false positives)
- Thumbs up (👍) → logged for approval rate stats only (no auto-adjustment)
"""

import copy
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


@dataclass
class AlertFeedbackStats:
    """Per-alert-profile feedback counters with timestamps."""

    profile_id: str

    # Counters
    negative_feedback: int = 0  # thumbs down (false positives)
    positive_feedback: int = 0  # thumbs up (useful alerts)

    # Timestamps for decay
    last_negative: list[datetime] = field(default_factory=list)
    last_positive: list[datetime] = field(default_factory=list)

    # Drift tracking
    cumulative_min_score_delta: float = 0.0

    def decay_old_feedback(self, window_days: int = 7):
        """Remove feedback older than window_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

        self.last_negative = [ts for ts in self.last_negative if ts > cutoff]
        self.last_positive = [ts for ts in self.last_positive if ts > cutoff]

        self.negative_feedback = len(self.last_negative)
        self.positive_feedback = len(self.last_positive)


class AlertFeedbackAggregator:
    """
    Thread-safe aggregator for alert profile feedback.

    Tracks negative feedback and recommends min_score increases when
    false positive rate exceeds thresholds.

    Separate from InterestFeedbackAggregator to avoid conflicts.
    """

    # Configuration constants
    MIN_NEGATIVE_FEEDBACK = 3  # Need 3 false positives to trigger adjustment
    NEGATIVE_RATE_THRESHOLD = 0.30  # 30% negative rate threshold
    MIN_SCORE_DELTA = 0.1  # How much to raise min_score
    MAX_MIN_SCORE_DELTA = 0.5  # Maximum cumulative drift allowed
    FEEDBACK_WINDOW_DAYS = 7  # Consider feedback from last 7 days
    DECAY_INTERVAL = timedelta(
        hours=1
    )  # Run decay every hour to prevent unbounded accumulation

    def __init__(self):
        self._stats: Dict[str, AlertFeedbackStats] = {}
        self._lock = threading.RLock()
        self._last_decay: Optional[datetime] = None

    def _check_and_decay_if_needed(self):
        """
        Check if decay is needed and run it if interval elapsed.
        Must be called under self._lock.
        """
        now = datetime.now(timezone.utc)
        if self._last_decay is None or (now - self._last_decay) >= self.DECAY_INTERVAL:
            self._decay_all_profiles()

    def record_feedback(
        self, profile_id: str, label: str, min_score: float
    ) -> Dict[str, Any]:
        """
        Record feedback for an alert profile.

        Args:
            profile_id: Alert profile ID
            label: "up" (useful) or "down" (false positive)
            min_score: Current min_score threshold

        Returns:
            Dict with action recommendation:
            {
                "action": "raise_min_score" | "none",
                "delta": float (if action != none),
                "reason": str,
                "current_stats": {...}
            }
        """
        with self._lock:
            # Trigger decay if interval elapsed
            self._check_and_decay_if_needed()

            stats = self._stats.setdefault(
                profile_id, AlertFeedbackStats(profile_id=profile_id)
            )

            now = datetime.now(timezone.utc)

            # Record feedback
            if label == "down":
                stats.negative_feedback += 1
                stats.last_negative.append(now)
            elif label == "up":
                stats.positive_feedback += 1
                stats.last_positive.append(now)

            # Evaluate if adjustment needed
            total_feedback = stats.negative_feedback + stats.positive_feedback
            negative_rate = stats.negative_feedback / total_feedback

            # Check if drift cap reached
            if stats.cumulative_min_score_delta >= self.MAX_MIN_SCORE_DELTA:
                return {
                    "action": "none",
                    "reason": f"Drift cap reached ({stats.cumulative_min_score_delta:.2f})",
                    "current_stats": self._get_stats_dict(stats),
                }

            # Recommend adjustment if enough negative feedback
            if (
                stats.negative_feedback >= self.MIN_NEGATIVE_FEEDBACK
                and negative_rate >= self.NEGATIVE_RATE_THRESHOLD
            ):
                # Check if we can still adjust without exceeding cap
                potential_new_delta = (
                    stats.cumulative_min_score_delta + self.MIN_SCORE_DELTA
                )
                if potential_new_delta > self.MAX_MIN_SCORE_DELTA:
                    # Allow adjustment up to cap
                    remaining = (
                        self.MAX_MIN_SCORE_DELTA - stats.cumulative_min_score_delta
                    )
                    if remaining > 0.01:  # At least 0.01 adjustment
                        return {
                            "action": "raise_min_score",
                            "delta": round(remaining, 2),
                            "reason": (
                                f"{stats.negative_feedback} false positives "
                                f"({negative_rate*100:.1f}% rate), "
                                f"approaching drift cap"
                            ),
                            "current_stats": self._get_stats_dict(stats),
                        }
                    else:
                        return {
                            "action": "none",
                            "reason": (
                                f"Drift cap reached "
                                f"({stats.cumulative_min_score_delta:.2f})"
                            ),
                            "current_stats": self._get_stats_dict(stats),
                        }

                # Capture negative count before resetting
                negative_count = stats.negative_feedback

                # Reset counter after recommendation (will be updated when adjustment applied)
                stats.negative_feedback = 0
                stats.last_negative = []

                return {
                    "action": "raise_min_score",
                    "delta": self.MIN_SCORE_DELTA,
                    "reason": (
                        f"{negative_count} "
                        f"false positives ({negative_rate*100:.1f}% rate)"
                    ),
                    "current_stats": self._get_stats_dict(stats),
                }

            return {
                "action": "none",
                "reason": (
                    f"Insufficient negative feedback "
                    f"({stats.negative_feedback}/{self.MIN_NEGATIVE_FEEDBACK}, "
                    f"{negative_rate*100:.1f}% rate)"
                ),
                "current_stats": self._get_stats_dict(stats),
            }

    def update_cumulative_delta(self, profile_id: str, delta: float):
        """Update cumulative drift after an adjustment is applied."""
        with self._lock:
            if profile_id in self._stats:
                self._stats[profile_id].cumulative_min_score_delta += delta

    def get_stats(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Get current stats for a profile."""
        with self._lock:
            # Trigger decay if interval elapsed
            self._check_and_decay_if_needed()

            if profile_id not in self._stats:
                return None
            return self._get_stats_dict(self._stats[profile_id])

    def get_all_stats(self) -> Dict[str, AlertFeedbackStats]:
        """Get stats for all profiles (for monitoring/debugging)."""
        with self._lock:
            # Trigger decay if interval elapsed
            self._check_and_decay_if_needed()

            return copy.deepcopy(self._stats)

    def _get_stats_dict(self, stats: AlertFeedbackStats) -> Dict[str, Any]:
        """Convert stats to dict."""
        total = stats.negative_feedback + stats.positive_feedback
        return {
            "profile_id": stats.profile_id,
            "negative_feedback": stats.negative_feedback,
            "positive_feedback": stats.positive_feedback,
            "total_feedback": total,
            "negative_rate": (stats.negative_feedback / total if total > 0 else 0.0),
            "cumulative_drift": stats.cumulative_min_score_delta,
        }

    def _decay_all_profiles(self):
        """
        Decay old feedback for all profiles.
        Must be called under self._lock.
        Updates _last_decay timestamp to track when decay last ran.
        """
        for stats in self._stats.values():
            stats.decay_old_feedback(self.FEEDBACK_WINDOW_DAYS)

        self._last_decay = datetime.now(timezone.utc)
        profile_count = len(self._stats)

        log.info(f"[ALERT-FEEDBACK-AGG] Decayed feedback for {profile_count} profiles")


# Global singleton instance
_alert_aggregator: Optional[AlertFeedbackAggregator] = None
_alert_aggregator_lock = threading.Lock()


def get_alert_feedback_aggregator() -> AlertFeedbackAggregator:
    """Get or create global alert feedback aggregator instance."""
    global _alert_aggregator
    if _alert_aggregator is None:
        with _alert_aggregator_lock:
            if _alert_aggregator is None:
                _alert_aggregator = AlertFeedbackAggregator()
    return _alert_aggregator
