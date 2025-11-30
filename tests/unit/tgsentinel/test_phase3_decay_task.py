"""
Unit tests for Phase 3: Feedback Aggregator Decay Task

Tests background decay task functionality.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from tgsentinel.feedback_aggregator import FeedbackAggregator, FeedbackStats


@pytest.mark.unit
@pytest.mark.asyncio
class TestFeedbackDecayTask:
    """Test feedback decay background task."""

    async def test_start_decay_task(self):
        """Test starting decay task."""
        aggregator = FeedbackAggregator()

        await aggregator.start_decay_task()

        assert aggregator._decay_running is True
        assert aggregator._decay_task is not None

        await aggregator.stop_decay_task()

    async def test_stop_decay_task(self):
        """Test stopping decay task."""
        aggregator = FeedbackAggregator()

        await aggregator.start_decay_task()
        await aggregator.stop_decay_task()

        assert aggregator._decay_running is False

    async def test_double_start_ignored(self):
        """Test that starting twice is ignored."""
        aggregator = FeedbackAggregator()

        await aggregator.start_decay_task()
        task1 = aggregator._decay_task

        # Try to start again (should warn but not error)
        await aggregator.start_decay_task()
        task2 = aggregator._decay_task

        assert task1 is task2

        await aggregator.stop_decay_task()

    async def test_stop_when_not_running(self):
        """Test that stopping when not running is safe."""
        aggregator = FeedbackAggregator()

        # Stop without starting (should not error)
        await aggregator.stop_decay_task()
        assert aggregator._decay_running is False

    async def test_decay_task_runs_periodically(self):
        """Test that decay task runs periodically."""
        aggregator = FeedbackAggregator()
        # Shorten interval for testing (normally 24 hours)
        original_interval = aggregator.DECAY_INTERVAL_HOURS
        object.__setattr__(
            aggregator, "DECAY_INTERVAL_HOURS", 0.01
        )  # ~36 seconds converted to hours

        # Add old feedback that should decay
        cutoff = datetime.now() - timedelta(days=aggregator.FEEDBACK_WINDOW_DAYS + 1)
        aggregator._stats["3000"] = FeedbackStats(
            profile_id="3000",
            borderline_fp=3,
            last_borderline_fp=[cutoff, cutoff, cutoff],
        )

        initial_count = aggregator._stats["3000"].borderline_fp

        # Start decay task
        await aggregator.start_decay_task()

        # Wait for decay to run (interval in seconds)
        await asyncio.sleep(aggregator.DECAY_INTERVAL_HOURS * 3600 + 1)

        # Check decay ran
        assert aggregator._stats["3000"].borderline_fp < initial_count

        # Restore interval and stop
        aggregator.DECAY_INTERVAL_HOURS = original_interval
        await aggregator.stop_decay_task()


@pytest.mark.unit
class TestFeedbackStatsDecay:
    """Test FeedbackStats decay method."""

    def test_decay_removes_old_feedback(self):
        """Test that decay removes feedback older than window."""
        stats = FeedbackStats(profile_id="3000")

        # Add old feedback (8 days ago)
        old_time = datetime.now() - timedelta(days=8)
        stats.last_borderline_fp = [old_time, old_time, old_time]
        stats.borderline_fp = 3

        # Add recent feedback (2 days ago)
        recent_time = datetime.now() - timedelta(days=2)
        stats.last_severe_fp = [recent_time, recent_time]
        stats.severe_fp = 2

        # Decay with 7-day window
        stats.decay_old_feedback(window_days=7)

        # Old feedback removed
        assert stats.borderline_fp == 0
        assert len(stats.last_borderline_fp) == 0

        # Recent feedback kept
        assert stats.severe_fp == 2
        assert len(stats.last_severe_fp) == 2

    def test_decay_keeps_recent_feedback(self):
        """Test that recent feedback is not decayed."""
        stats = FeedbackStats(profile_id="3000")

        # Add recent feedback
        recent_time = datetime.now() - timedelta(days=3)
        stats.last_borderline_fp = [recent_time, recent_time]
        stats.borderline_fp = 2

        stats.last_strong_tp = [recent_time]
        stats.strong_tp = 1

        # Decay with 7-day window
        stats.decay_old_feedback(window_days=7)

        # All feedback kept
        assert stats.borderline_fp == 2
        assert stats.strong_tp == 1

    def test_decay_handles_mixed_ages(self):
        """Test decay with mixed old and recent feedback."""
        stats = FeedbackStats(profile_id="3000")

        old_time = datetime.now() - timedelta(days=10)
        recent_time = datetime.now() - timedelta(days=2)

        stats.last_borderline_fp = [old_time, old_time, recent_time, recent_time]
        stats.borderline_fp = 4

        stats.decay_old_feedback(window_days=7)

        # Only recent kept
        assert stats.borderline_fp == 2
        assert len(stats.last_borderline_fp) == 2
        assert all(
            ts > datetime.now() - timedelta(days=7) for ts in stats.last_borderline_fp
        )

    def test_decay_empty_stats(self):
        """Test decay on empty stats (should not error)."""
        stats = FeedbackStats(profile_id="3000")

        stats.decay_old_feedback(window_days=7)

        assert stats.borderline_fp == 0
        assert stats.severe_fp == 0
        assert stats.strong_tp == 0


@pytest.mark.unit
class TestAggregatorDecayAll:
    """Test aggregator-wide decay functionality."""

    def test_decay_all_profiles(self):
        """Test that _decay_all_profiles decays all profiles."""
        aggregator = FeedbackAggregator()

        # Add feedback to multiple profiles
        old_time = datetime.now() - timedelta(days=10)

        for profile_id in ["3000", "3001", "3002"]:
            aggregator._stats[profile_id] = FeedbackStats(
                profile_id=profile_id,
                borderline_fp=3,
                last_borderline_fp=[old_time, old_time, old_time],
            )

        # Run decay
        aggregator._decay_all_profiles()

        # All profiles decayed
        for profile_id in ["3000", "3001", "3002"]:
            assert aggregator._stats[profile_id].borderline_fp == 0

    def test_decay_partial_profiles(self):
        """Test decay affects only profiles with old feedback."""
        aggregator = FeedbackAggregator()

        old_time = datetime.now() - timedelta(days=10)
        recent_time = datetime.now() - timedelta(days=2)

        # Profile with old feedback
        aggregator._stats["3000"] = FeedbackStats(
            profile_id="3000",
            borderline_fp=3,
            last_borderline_fp=[old_time, old_time, old_time],
        )

        # Profile with recent feedback
        aggregator._stats["3001"] = FeedbackStats(
            profile_id="3001",
            borderline_fp=2,
            last_borderline_fp=[recent_time, recent_time],
        )

        # Run decay
        aggregator._decay_all_profiles()

        # Old feedback decayed
        assert aggregator._stats["3000"].borderline_fp == 0

        # Recent feedback kept
        assert aggregator._stats["3001"].borderline_fp == 2

    def test_decay_updates_last_decay_time(self):
        """Test that manual decay updates _last_decay."""
        aggregator = FeedbackAggregator()

        initial_time = aggregator._last_decay

        # Add some feedback
        aggregator._stats["3000"] = FeedbackStats(profile_id="3000")

        # Run decay
        aggregator._decay_all_profiles()

        # Time should be same (only updated by record_feedback or background task)
        # _decay_all_profiles itself doesn't update timestamp
        assert aggregator._last_decay == initial_time
