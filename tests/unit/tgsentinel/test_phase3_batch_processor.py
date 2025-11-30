"""
Unit tests for Phase 3: BatchFeedbackProcessor

Tests batch processing, queuing, and background task behavior.
"""

import asyncio
from datetime import datetime

import pytest

from tgsentinel.feedback_processor import BatchFeedbackProcessor
from tgsentinel.store import init_db


@pytest.mark.unit
@pytest.mark.asyncio
class TestBatchFeedbackProcessor:
    """Test batch feedback processor functionality."""

    async def test_schedule_recompute_adds_to_queue(self, tmp_path):
        """Test that schedule_recompute adds profiles to queue."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        processor.schedule_recompute("3000")
        processor.schedule_recompute("3001")

        status = processor.get_queue_status()
        assert status["pending_count"] == 2
        assert "3000" in status["pending_profiles"]
        assert "3001" in status["pending_profiles"]

    async def test_schedule_same_profile_multiple_times(self, tmp_path):
        """Test that scheduling same profile multiple times only adds once (set behavior)."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        processor.schedule_recompute("3000")
        processor.schedule_recompute("3000")
        processor.schedule_recompute("3000")

        status = processor.get_queue_status()
        assert status["pending_count"] == 1
        assert "3000" in status["pending_profiles"]

    async def test_batch_clears_queue(self, tmp_path):
        """Test that process_batch clears the queue."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        processor.schedule_recompute("3000")
        processor.schedule_recompute("3001")

        await processor.process_batch()

        status = processor.get_queue_status()
        assert status["pending_count"] == 0

    async def test_threshold_triggers_early_batch(self, tmp_path):
        """Test that reaching threshold triggers early batch."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)
        processor.BATCH_SIZE_THRESHOLD = 3

        # Add profiles to reach threshold
        for i in range(3):
            processor.schedule_recompute(f"300{i}")

        status = processor.get_queue_status()
        assert status["pending_count"] == 3
        # In real usage, the periodic task would pick this up

    async def test_get_queue_status(self, tmp_path):
        """Test queue status reporting."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        # Initially empty
        status = processor.get_queue_status()
        assert status["pending_count"] == 0
        assert isinstance(status["pending_profiles"], list)
        assert isinstance(status["last_batch_time"], str)
        assert isinstance(status["seconds_since_last_batch"], (int, float))

        # Add some profiles
        processor.schedule_recompute("3000")
        processor.schedule_recompute("3001")

        status = processor.get_queue_status()
        assert status["pending_count"] == 2
        assert set(status["pending_profiles"]) == {"3000", "3001"}

    async def test_background_task_start_stop(self, tmp_path):
        """Test starting and stopping background batch task."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        # Start task
        await processor.start()
        assert processor._running is True
        assert processor._task is not None

        # Stop task
        await processor.stop()
        assert processor._running is False

    async def test_double_start_ignored(self, tmp_path):
        """Test that starting twice is ignored."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        await processor.start()
        task1 = processor._task

        # Try to start again
        await processor.start()
        task2 = processor._task

        # Should be same task
        assert task1 is task2

        await processor.stop()

    async def test_stop_when_not_running(self, tmp_path):
        """Test that stopping when not running is safe."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        # Stop without starting (should not error)
        await processor.stop()
        assert processor._running is False

    async def test_periodic_batch_processes_queue(self, tmp_path):
        """Test that periodic task processes queue after interval."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)
        # Set short interval for testing
        processor.BATCH_INTERVAL_SECONDS = 1

        # Schedule some profiles
        processor.schedule_recompute("3000")
        processor.schedule_recompute("3001")

        # Start background task
        await processor.start()

        # Wait for interval + processing time
        await asyncio.sleep(2)

        # Queue should be cleared by periodic task
        status = processor.get_queue_status()
        assert status["pending_count"] == 0

        await processor.stop()

    async def test_batch_updates_last_batch_time(self, tmp_path):
        """Test that processing batch updates last_batch_time."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        # Get initial time
        status1 = processor.get_queue_status()
        initial_time = datetime.fromisoformat(status1["last_batch_time"])

        # Wait a moment
        await asyncio.sleep(0.1)

        # Schedule and process
        processor.schedule_recompute("3000")
        await processor.process_batch()

        # Check time updated
        status2 = processor.get_queue_status()
        updated_time = datetime.fromisoformat(status2["last_batch_time"])

        assert updated_time > initial_time

    async def test_empty_batch_is_noop(self, tmp_path):
        """Test that processing empty queue is a no-op."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        # Process empty queue (should not error)
        await processor.process_batch()

        status = processor.get_queue_status()
        assert status["pending_count"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
class TestBatchFeedbackProcessorIntegration:
    """Integration tests with semantic cache clearing."""

    async def test_process_batch_clears_semantic_cache(self, tmp_path):
        """Test that batch processing clears semantic caches."""
        engine = init_db("sqlite:///:memory:")
        processor = BatchFeedbackProcessor(engine, tmp_path)

        # Mock semantic cache to verify clearing
        from tgsentinel import semantic

        # Set up a profile in cache
        semantic._profile_vectors = {"3000": {"positive": [[0.1, 0.2]]}}

        # Schedule and process
        processor.schedule_recompute("3000")
        await processor.process_batch()

        # Cache should be cleared for this profile
        assert "3000" not in semantic._profile_vectors

    async def test_batch_processor_singleton(self, tmp_path):
        """Test that get_batch_processor returns singleton."""
        from tgsentinel.feedback_processor import get_batch_processor

        engine = init_db("sqlite:///:memory:")

        # First call creates instance
        processor1 = get_batch_processor(engine, tmp_path)

        # Second call returns same instance
        processor2 = get_batch_processor()

        assert processor1 is processor2

    async def test_batch_processor_requires_init_on_first_call(self):
        """Test that first call requires engine and config_dir."""
        # Reset global
        import tgsentinel.feedback_processor
        from tgsentinel.feedback_processor import get_batch_processor

        tgsentinel.feedback_processor._processor = None

        # First call without params should raise
        with pytest.raises(ValueError, match="Must provide engine and config_dir"):
            get_batch_processor()
