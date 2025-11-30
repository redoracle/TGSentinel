"""
Unit tests for FeedbackAggregator (Phase 1: Stability Foundation)

Tests the core feedback aggregation logic including:
- Single feedback should not trigger actions
- Three borderline FPs trigger threshold raise
- Drift cap enforcement
- Feedback decay
"""

from datetime import datetime, timedelta

import pytest

from tgsentinel.feedback_aggregator import FeedbackAggregator, FeedbackStats


@pytest.mark.unit
class TestFeedbackAggregator:
    """Test FeedbackAggregator behavior."""

    def test_single_feedback_no_action(self):
        """Single negative feedback should not trigger action."""
        agg = FeedbackAggregator()
        result = agg.record_feedback(
            profile_id="3000",
            label="down",
            semantic_score=0.50,  # Borderline FP (threshold=0.45)
            threshold=0.45,
        )

        assert result["action"] == "none"
        assert "Insufficient" in result["reason"]

    def test_two_feedbacks_no_action(self):
        """Two negative feedbacks should not trigger action (need 3)."""
        agg = FeedbackAggregator()

        # First feedback
        result = agg.record_feedback(
            profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
        )
        assert result["action"] == "none"

        # Second feedback
        result = agg.record_feedback(
            profile_id="3000", label="down", semantic_score=0.52, threshold=0.45
        )
        assert result["action"] == "none"

    def test_three_borderline_fp_triggers_threshold_raise(self):
        """Three borderline FPs should trigger threshold raise."""
        agg = FeedbackAggregator()

        # First two feedbacks: no action
        for i in range(2):
            result = agg.record_feedback(
                profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
            )
            assert result["action"] == "none"

        # Third feedback: should trigger
        result = agg.record_feedback(
            profile_id="3000", label="down", semantic_score=0.52, threshold=0.45
        )

        assert result["action"] == "raise_threshold"
        assert result["delta"] == 0.1
        assert "3 borderline" in result["reason"]

    def test_drift_cap_prevents_adjustment(self):
        """Drift cap should prevent further adjustments."""
        agg = FeedbackAggregator()

        # Manually set cumulative delta to cap
        if "3000" not in agg._stats:
            agg._stats["3000"] = FeedbackStats(profile_id="3000")

        stats = agg._stats["3000"]
        stats.cumulative_threshold_delta = 0.25  # At cap

        # Add 3 borderline FPs
        for i in range(3):
            agg.record_feedback(
                profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
            )

        result = agg.record_feedback(
            profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
        )

        assert result["action"] == "none"
        assert "drift cap" in result["reason"].lower()

    def test_feedback_decay(self):
        """Old feedback should decay after window expires."""
        stats = FeedbackStats(profile_id="3000")

        # Add feedback with old timestamps
        old_time = datetime.now() - timedelta(days=8)
        stats.last_borderline_fp = [old_time, old_time, old_time]
        stats.borderline_fp = 3

        # Run decay
        stats.decay_old_feedback(window_days=7)

        assert stats.borderline_fp == 0
        assert len(stats.last_borderline_fp) == 0

    def test_feedback_decay_partial(self):
        """Decay should only remove old feedback, keep recent."""
        stats = FeedbackStats(profile_id="3000")

        # Add mix of old and recent feedback
        old_time = datetime.now() - timedelta(days=8)
        recent_time = datetime.now() - timedelta(days=2)
        stats.last_borderline_fp = [old_time, old_time, recent_time, recent_time]
        stats.borderline_fp = 4

        # Run decay
        stats.decay_old_feedback(window_days=7)

        assert stats.borderline_fp == 2
        assert len(stats.last_borderline_fp) == 2

    def test_reset_stats_clears_counters(self):
        """Reset should clear borderline FP counters after adjustment."""
        agg = FeedbackAggregator()

        # Record 3 feedbacks to reach threshold
        for i in range(3):
            agg.record_feedback(
                profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
            )

        # Reset stats
        agg.reset_stats("3000", "raise_threshold")

        # Check counters cleared
        stats = agg.get_stats("3000")
        assert stats is not None
        assert stats.borderline_fp == 0
        assert len(stats.last_borderline_fp) == 0
        assert stats.cumulative_threshold_delta == 0.1  # Updated

    def test_severe_fp_classification(self):
        """Severe FPs (score >= threshold + 0.20) should be counted separately."""
        agg = FeedbackAggregator()

        # Severe FP: score much higher than threshold
        result = agg.record_feedback(
            profile_id="3000",
            label="down",
            semantic_score=0.70,  # threshold=0.45, so 0.70 >= 0.65
            threshold=0.45,
        )

        stats = result["stats"]
        assert stats.severe_fp == 1
        assert stats.borderline_fp == 0

    def test_strong_tp_classification(self):
        """Strong TPs (score >= threshold + 0.15) should be counted separately."""
        agg = FeedbackAggregator()

        # Strong TP: score well above threshold
        result = agg.record_feedback(
            profile_id="3000",
            label="up",
            semantic_score=0.65,  # threshold=0.45, so 0.65 >= 0.60
            threshold=0.45,
        )

        stats = result["stats"]
        assert stats.strong_tp == 1
        assert stats.marginal_tp == 0

    def test_marginal_tp_classification(self):
        """Marginal TPs (score < threshold + 0.15) should be counted separately."""
        agg = FeedbackAggregator()

        # Marginal TP: score just above threshold
        result = agg.record_feedback(
            profile_id="3000",
            label="up",
            semantic_score=0.50,  # threshold=0.45, so 0.50 < 0.60
            threshold=0.45,
        )

        stats = result["stats"]
        assert stats.strong_tp == 0
        assert stats.marginal_tp == 1

    def test_get_stats_nonexistent_profile(self):
        """Getting stats for non-existent profile should return None."""
        agg = FeedbackAggregator()
        stats = agg.get_stats("9999")
        assert stats is None

    def test_get_all_stats(self):
        """Should return all profile stats."""
        agg = FeedbackAggregator()

        # Record feedback for multiple profiles
        agg.record_feedback("3000", "down", 0.50, 0.45)
        agg.record_feedback("3001", "up", 0.65, 0.45)

        all_stats = agg.get_all_stats()
        assert "3000" in all_stats
        assert "3001" in all_stats
