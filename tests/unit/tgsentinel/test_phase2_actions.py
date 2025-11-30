"""
Unit tests for Phase 2: FeedbackAggregator Actions

Tests the new action types:
- add_negative_sample (severe FPs)
- add_positive_sample (strong TPs)
"""

import pytest

from tgsentinel.feedback_aggregator import FeedbackAggregator


@pytest.mark.unit
class TestPhase2Actions:
    """Test Phase 2 feedback aggregation actions."""

    def test_severe_fp_triggers_add_negative_sample(self):
        """Test that 2 severe FPs trigger add_negative_sample action."""
        agg = FeedbackAggregator()

        # Record 2 severe false positives (score > threshold + 0.15)
        result1 = agg.record_feedback(
            "3000", "down", semantic_score=0.75, threshold=0.45
        )
        assert result1["action"] == "none"  # Not enough yet

        result2 = agg.record_feedback(
            "3000", "down", semantic_score=0.80, threshold=0.45
        )
        assert result2["action"] == "add_negative_sample"
        assert result2["reason"] == "2 severe false positives detected (threshold: 2)"

        # Stats should show 2 severe FPs
        stats = agg.get_stats("3000")
        assert stats is not None
        assert stats.severe_fp == 2

    def test_strong_tp_triggers_add_positive_sample(self):
        """Test that 2 strong TPs trigger add_positive_sample action."""
        agg = FeedbackAggregator()

        # Record 2 strong true positives (score > threshold + 0.15)
        result1 = agg.record_feedback("3000", "up", semantic_score=0.75, threshold=0.45)
        assert result1["action"] == "none"  # Not enough yet

        result2 = agg.record_feedback("3000", "up", semantic_score=0.80, threshold=0.45)
        assert result2["action"] == "add_positive_sample"
        assert result2["reason"] == "2 strong true positives detected (threshold: 2)"

        # Stats should show 2 strong TPs
        stats = agg.get_stats("3000")
        assert stats is not None
        assert stats.strong_tp == 2

    def test_phase2_actions_have_priority_over_threshold_raise(self):
        """Test that severe FP action takes priority over threshold raise."""
        agg = FeedbackAggregator()

        # Record 3 borderline FPs (enough for threshold raise)
        for _ in range(3):
            agg.record_feedback("3000", "down", semantic_score=0.52, threshold=0.45)

        # Now record 2 severe FPs
        agg.record_feedback("3000", "down", semantic_score=0.75, threshold=0.45)
        result = agg.record_feedback(
            "3000", "down", semantic_score=0.80, threshold=0.45
        )

        # Should recommend add_negative_sample, not raise_threshold
        assert result["action"] == "add_negative_sample"

    def test_reset_stats_for_add_negative_sample(self):
        """Test that reset_stats clears severe FP counter."""
        agg = FeedbackAggregator()

        # Record 2 severe FPs
        agg.record_feedback("3000", "down", semantic_score=0.75, threshold=0.45)
        agg.record_feedback("3000", "down", semantic_score=0.80, threshold=0.45)

        stats_before = agg.get_stats("3000")
        assert stats_before is not None
        assert stats_before.severe_fp == 2

        # Reset after action
        agg.reset_stats("3000", "add_negative_sample")

        stats_after = agg.get_stats("3000")
        assert stats_after is not None
        assert stats_after.severe_fp == 0
        assert len(stats_after.last_severe_fp) == 0

    def test_reset_stats_for_add_positive_sample(self):
        """Test that reset_stats clears strong TP counter."""
        agg = FeedbackAggregator()

        # Record 2 strong TPs
        agg.record_feedback("3000", "up", semantic_score=0.75, threshold=0.45)
        agg.record_feedback("3000", "up", semantic_score=0.80, threshold=0.45)

        stats_before = agg.get_stats("3000")
        assert stats_before is not None
        assert stats_before.strong_tp == 2

        # Reset after action
        agg.reset_stats("3000", "add_positive_sample")

        stats_after = agg.get_stats("3000")
        assert stats_after is not None
        assert stats_after.strong_tp == 0
        assert len(stats_after.last_strong_tp) == 0

    def test_borderline_fp_still_triggers_threshold_raise(self):
        """Test that borderline FPs still trigger threshold raise (Phase 1)."""
        agg = FeedbackAggregator()

        # Record 3 borderline FPs
        for _ in range(3):
            result = agg.record_feedback(
                "3000", "down", semantic_score=0.52, threshold=0.45
            )

        # Should still recommend threshold raise
        assert result["action"] == "raise_threshold"

    def test_mixed_feedback_tracking(self):
        """Test that different feedback types are tracked separately."""
        agg = FeedbackAggregator()

        # Record mix of feedback types
        agg.record_feedback(
            "3000", "down", semantic_score=0.52, threshold=0.45
        )  # Borderline FP
        agg.record_feedback(
            "3000", "down", semantic_score=0.75, threshold=0.45
        )  # Severe FP
        agg.record_feedback(
            "3000", "up", semantic_score=0.75, threshold=0.45
        )  # Strong TP

        stats = agg.get_stats("3000")
        assert stats is not None
        assert stats.borderline_fp == 1
        assert stats.severe_fp == 1
        assert stats.strong_tp == 1

    def test_no_action_with_insufficient_feedback(self):
        """Test that single feedbacks don't trigger actions."""
        agg = FeedbackAggregator()

        # Record 1 severe FP (not enough)
        result = agg.record_feedback(
            "3000", "down", semantic_score=0.75, threshold=0.45
        )
        assert result["action"] == "none"

        # Record 1 strong TP (not enough)
        result = agg.record_feedback("3000", "up", semantic_score=0.75, threshold=0.45)
        assert result["action"] == "none"

        # Record 2 borderline FPs (not enough, need 3)
        agg.record_feedback("3000", "down", semantic_score=0.52, threshold=0.45)
        result = agg.record_feedback(
            "3000", "down", semantic_score=0.52, threshold=0.45
        )
        assert result["action"] == "none"
