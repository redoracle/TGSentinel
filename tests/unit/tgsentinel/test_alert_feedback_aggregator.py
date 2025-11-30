"""
Unit tests for AlertFeedbackAggregator

Tests the alert-specific feedback aggregation logic including:
- Negative feedback (false positives) triggering min_score raises
- Positive feedback (useful alerts) logged only (no auto-adjustment)
- Drift cap enforcement
- Feedback decay
- Thread safety
- Separation from InterestFeedbackAggregator
"""

from datetime import datetime, timedelta, timezone

import pytest

from tgsentinel.alert_feedback_aggregator import (
    AlertFeedbackAggregator,
    AlertFeedbackStats,
    get_alert_feedback_aggregator,
)


@pytest.mark.unit
class TestAlertFeedbackAggregator:
    """Test AlertFeedbackAggregator behavior."""

    def test_single_negative_feedback_no_action(self):
        """Single negative feedback should not trigger action."""
        agg = AlertFeedbackAggregator()
        result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)

        assert result["action"] == "none"
        assert "Insufficient" in result["reason"]
        assert result["current_stats"]["negative_feedback"] == 1

    def test_positive_feedback_no_action(self):
        """Positive feedback should be logged but not trigger action."""
        agg = AlertFeedbackAggregator()

        # Multiple positive feedbacks
        for _ in range(5):
            result = agg.record_feedback(profile_id="1000", label="up", min_score=1.0)
            assert result["action"] == "none"

        stats = agg.get_stats("1000")
        assert stats is not None
        assert stats["positive_feedback"] == 5
        assert stats["negative_feedback"] == 0

    def test_two_negative_feedbacks_no_action(self):
        """Two negative feedbacks should not trigger action (need 3)."""
        agg = AlertFeedbackAggregator()

        # First feedback
        result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)
        assert result["action"] == "none"

        # Second feedback
        result = agg.record_feedback(profile_id="1000", label="down", min_score=1.5)
        assert result["action"] == "none"
        assert result["current_stats"]["negative_feedback"] == 2

    def test_three_negative_fp_triggers_min_score_raise(self):
        """Three false positives should trigger min_score raise."""
        agg = AlertFeedbackAggregator()

        # First two feedbacks: no action
        for i in range(2):
            result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)
            assert result["action"] == "none"

        # Third feedback: should trigger
        result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)

        assert result["action"] == "raise_min_score"
        assert result["delta"] == 0.1
        assert "false positives" in result["reason"]
        # Counters should be reset after recommendation
        assert result["current_stats"]["negative_feedback"] == 0

    def test_negative_rate_threshold_enforcement(self):
        """Min_score raise requires 30%+ negative rate."""
        agg = AlertFeedbackAggregator()

        # Add 10 positive feedbacks
        for _ in range(10):
            agg.record_feedback(profile_id="1000", label="up", min_score=1.0)

        # Add 2 negative feedbacks (2/12 = 16.7% < 30%)
        for _ in range(2):
            result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)
            assert result["action"] == "none"

        # Add 1 more negative (3/13 = 23% < 30%, and now we have 3 negatives)
        # Should still be "none" because rate < 30%
        result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)
        assert result["action"] == "none"

        # Start fresh with cleaner test
        agg2 = AlertFeedbackAggregator()

        # Add 2 positive, 6 negative = 6/8 = 75% negative rate
        agg2.record_feedback(profile_id="1001", label="up", min_score=1.0)
        agg2.record_feedback(profile_id="1001", label="up", min_score=1.0)

        for i in range(2):
            result = agg2.record_feedback(
                profile_id="1001", label="down", min_score=1.0
            )
            assert result["action"] == "none"

        # 3rd negative with 2 positive = 3/5 = 60% > 30%
        result = agg2.record_feedback(profile_id="1001", label="down", min_score=1.0)
        assert result["action"] == "raise_min_score"

    def test_drift_cap_prevents_adjustment(self):
        """Drift cap should prevent further adjustments."""
        agg = AlertFeedbackAggregator()

        # Manually set cumulative delta to cap
        if "1000" not in agg._stats:
            agg._stats["1000"] = AlertFeedbackStats(profile_id="1000")

        stats = agg._stats["1000"]
        stats.cumulative_min_score_delta = 0.5  # At cap

        # Add 3 negative feedbacks
        for i in range(3):
            result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)

        assert result["action"] == "none"
        assert "drift cap" in result["reason"].lower()

    def test_drift_cap_allows_partial_adjustment(self):
        """Near drift cap, should allow adjustment up to cap."""
        agg = AlertFeedbackAggregator()

        # Set cumulative delta near cap
        if "1000" not in agg._stats:
            agg._stats["1000"] = AlertFeedbackStats(profile_id="1000")

        stats = agg._stats["1000"]
        stats.cumulative_min_score_delta = 0.45  # 0.05 remaining

        # Add 3 negative feedbacks to trigger
        for i in range(3):
            agg.record_feedback(profile_id="1000", label="down", min_score=1.0)

        result = agg.record_feedback(profile_id="1000", label="down", min_score=1.0)

        assert result["action"] == "raise_min_score"
        assert result["delta"] == 0.05  # Only remaining amount
        assert "approaching drift cap" in result["reason"]

    def test_feedback_decay(self):
        """Old feedback should decay after window expires."""
        stats = AlertFeedbackStats(profile_id="1000")

        # Add feedback with old timestamps
        old_time = datetime.now(timezone.utc) - timedelta(days=8)
        stats.last_negative = [old_time, old_time, old_time]
        stats.last_positive = [old_time]
        stats.negative_feedback = 3
        stats.positive_feedback = 1

        # Run decay
        stats.decay_old_feedback(window_days=7)

        assert stats.negative_feedback == 0
        assert stats.positive_feedback == 0
        assert len(stats.last_negative) == 0
        assert len(stats.last_positive) == 0

    def test_feedback_decay_partial(self):
        """Decay should only remove old feedback, keep recent."""
        stats = AlertFeedbackStats(profile_id="1000")

        # Add mix of old and recent feedback
        old_time = datetime.now(timezone.utc) - timedelta(days=8)
        recent_time = datetime.now(timezone.utc) - timedelta(days=2)
        stats.last_negative = [old_time, old_time, recent_time, recent_time]
        stats.last_positive = [old_time, recent_time]
        stats.negative_feedback = 4
        stats.positive_feedback = 2

        # Run decay
        stats.decay_old_feedback(window_days=7)

        assert stats.negative_feedback == 2
        assert stats.positive_feedback == 1
        assert len(stats.last_negative) == 2
        assert len(stats.last_positive) == 1

    def test_update_cumulative_delta(self):
        """Update cumulative delta after adjustment applied."""
        agg = AlertFeedbackAggregator()

        # Initialize stats
        agg.record_feedback(profile_id="1000", label="down", min_score=1.0)

        # Update cumulative delta
        agg.update_cumulative_delta("1000", 0.1)

        stats = agg.get_stats("1000")
        assert stats is not None
        assert stats["cumulative_drift"] == 0.1

        # Update again
        agg.update_cumulative_delta("1000", 0.15)
        stats = agg.get_stats("1000")
        assert stats is not None
        assert stats["cumulative_drift"] == 0.25

    def test_get_stats_nonexistent_profile(self):
        """Get stats for nonexistent profile should return None."""
        agg = AlertFeedbackAggregator()
        stats = agg.get_stats("9999")
        assert stats is None

    def test_get_stats_returns_dict(self):
        """Get stats should return dict with correct structure."""
        agg = AlertFeedbackAggregator()

        # Add some feedback
        agg.record_feedback(profile_id="1000", label="down", min_score=1.0)
        agg.record_feedback(profile_id="1000", label="up", min_score=1.0)

        stats = agg.get_stats("1000")

        assert stats is not None
        assert stats["profile_id"] == "1000"
        assert stats["negative_feedback"] == 1
        assert stats["positive_feedback"] == 1
        assert stats["total_feedback"] == 2
        assert stats["negative_rate"] == 0.5
        assert stats["cumulative_drift"] == 0.0

    def test_thread_safety(self):
        """Test concurrent feedback submission is thread-safe."""
        import threading

        agg = AlertFeedbackAggregator()
        errors = []

        def submit_feedback(profile_id: str, count: int):
            try:
                for _ in range(count):
                    agg.record_feedback(
                        profile_id=profile_id, label="down", min_score=1.0
                    )
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = []
        for i in range(5):
            t = threading.Thread(target=submit_feedback, args=("1000", 10))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Check no errors and stats are consistent
        assert len(errors) == 0

        stats = agg.get_stats("1000")
        assert stats is not None
        # Should have 50 feedbacks total (5 threads * 10 feedbacks)
        # But some may have triggered adjustments and been reset
        assert stats["negative_feedback"] <= 50

    def test_singleton_returns_same_instance(self):
        """get_alert_feedback_aggregator should return singleton."""
        agg1 = get_alert_feedback_aggregator()
        agg2 = get_alert_feedback_aggregator()
        assert agg1 is agg2

    def test_separation_from_interest_aggregator(self):
        """Alert aggregator should be separate from interest aggregator."""
        from tgsentinel.feedback_aggregator import get_feedback_aggregator

        interest_agg = get_feedback_aggregator()
        alert_agg = get_alert_feedback_aggregator()

        # Should be different instances
        assert interest_agg is not alert_agg
        assert type(interest_agg).__name__ == "FeedbackAggregator"
        assert type(alert_agg).__name__ == "AlertFeedbackAggregator"

        # Add feedback to alert aggregator
        alert_agg.record_feedback(profile_id="1000", label="down", min_score=1.0)

        # Interest aggregator should be unaffected
        interest_stats = interest_agg.get_stats("1000")
        assert interest_stats is None  # No feedback recorded for interest

    def test_decay_all_profiles(self):
        """Test decay across all profiles."""
        agg = AlertFeedbackAggregator()

        # Add feedback to multiple profiles
        old_time = datetime.now(timezone.utc) - timedelta(days=8)
        recent_time = datetime.now(timezone.utc) - timedelta(days=2)

        for profile_id in ["1000", "1001", "1002"]:
            agg._stats[profile_id] = AlertFeedbackStats(profile_id=profile_id)
            agg._stats[profile_id].last_negative = [old_time, recent_time]
            agg._stats[profile_id].negative_feedback = 2

        # Run decay for all profiles
        agg._decay_all_profiles()

        # Check all profiles decayed
        for profile_id in ["1000", "1001", "1002"]:
            stats = agg.get_stats(profile_id)
            assert stats is not None
            assert stats["negative_feedback"] == 1  # Only recent feedback remains
