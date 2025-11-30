"""
Unit tests for batch feedback processor with Redis persistence.

Tests verify that queue state and last batch time are correctly
persisted to Redis and restored on restart.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine

from src.tgsentinel.feedback_processor import (
    BATCH_LAST_TIME_KEY,
    BATCH_QUEUE_KEY,
    BatchFeedbackProcessor,
)


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    redis = MagicMock()
    redis.get = MagicMock(return_value=None)
    redis.set = MagicMock()
    return redis


@pytest.fixture
def engine():
    """In-memory SQLite engine for testing."""
    return create_engine("sqlite:///:memory:")


@pytest.fixture
def config_dir(tmp_path):
    """Temporary config directory."""
    return tmp_path / "config"


@pytest.mark.unit
def test_processor_initialization_without_redis(engine, config_dir):
    """Test processor can initialize without Redis client."""
    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=None)

    assert processor.redis is None
    assert len(processor._queue.profiles_pending) == 0


@pytest.mark.unit
def test_processor_loads_empty_state_from_redis(mock_redis, engine, config_dir):
    """Test processor handles empty Redis state gracefully."""
    mock_redis.get.return_value = None

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Should have called get for both keys
    assert mock_redis.get.call_count == 2
    mock_redis.get.assert_any_call(BATCH_QUEUE_KEY)
    mock_redis.get.assert_any_call(BATCH_LAST_TIME_KEY)

    # Queue should be empty
    assert len(processor._queue.profiles_pending) == 0


@pytest.mark.unit
def test_processor_restores_queue_from_redis(mock_redis, engine, config_dir):
    """Test processor restores pending profiles from Redis."""
    # Mock Redis with persisted queue
    test_profiles = ["3000", "3001", "3002"]
    mock_redis.get.side_effect = lambda key: (
        json.dumps(test_profiles) if key == BATCH_QUEUE_KEY else None
    )

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Queue should be restored
    assert len(processor._queue.profiles_pending) == 3
    assert processor._queue.profiles_pending == set(test_profiles)


@pytest.mark.unit
def test_processor_restores_last_batch_time_from_redis(mock_redis, engine, config_dir):
    """Test processor restores last batch time from Redis."""
    # Mock Redis with persisted timestamp
    test_time = datetime(2025, 12, 7, 12, 0, 0)
    mock_redis.get.side_effect = lambda key: (
        test_time.isoformat() if key == BATCH_LAST_TIME_KEY else None
    )

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Time should be restored
    assert processor._queue.last_batch_time == test_time


@pytest.mark.unit
def test_processor_saves_state_after_schedule(mock_redis, engine, config_dir):
    """Test processor saves state to Redis after scheduling a profile."""
    mock_redis.get.return_value = None  # Start with empty state

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Schedule a profile
    processor.schedule_recompute("3000")

    # Should have saved to Redis
    assert mock_redis.set.call_count >= 2  # queue + last_batch_time

    # Verify queue was saved
    calls = [call for call in mock_redis.set.call_args_list]
    queue_calls = [call for call in calls if call[0][0] == BATCH_QUEUE_KEY]
    assert len(queue_calls) > 0

    # Parse the saved queue
    saved_queue = json.loads(queue_calls[-1][0][1])
    assert "3000" in saved_queue


@pytest.mark.unit
@pytest.mark.asyncio
async def test_processor_saves_state_after_batch(mock_redis, engine, config_dir):
    """Test processor saves cleared queue and updated time after batch processing."""
    mock_redis.get.return_value = None

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Schedule profiles
    processor.schedule_recompute("3000")
    processor.schedule_recompute("3001")

    # Mock semantic cache clearing (it's imported inside process_batch)
    with patch("src.tgsentinel.semantic.clear_profile_cache"):
        # Process batch
        await processor.process_batch(trigger_type="manual")

    # Queue should be empty after batch
    assert len(processor._queue.profiles_pending) == 0

    # Should have saved empty queue to Redis
    calls = [call for call in mock_redis.set.call_args_list]
    queue_calls = [call for call in calls if call[0][0] == BATCH_QUEUE_KEY]

    # Get the most recent queue save (after batch)
    final_queue = json.loads(queue_calls[-1][0][1])
    assert len(final_queue) == 0


@pytest.mark.unit
def test_processor_handles_redis_save_errors_gracefully(mock_redis, engine, config_dir):
    """Test processor continues operating if Redis save fails."""
    mock_redis.get.return_value = None
    mock_redis.set.side_effect = Exception("Redis connection failed")

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Should not raise exception
    processor.schedule_recompute("3000")

    # Profile should still be in queue
    assert "3000" in processor._queue.profiles_pending


@pytest.mark.unit
def test_processor_handles_redis_load_errors_gracefully(mock_redis, engine, config_dir):
    """Test processor continues operating if Redis load fails."""
    mock_redis.get.side_effect = Exception("Redis connection failed")

    # Should not raise exception during initialization
    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Should have empty queue (fallback to default)
    assert len(processor._queue.profiles_pending) == 0


@pytest.mark.unit
def test_processor_restores_full_state(mock_redis, engine, config_dir):
    """Test processor restores both queue and timestamp together."""
    test_profiles = ["3000", "3001"]
    test_time = datetime(2025, 12, 7, 10, 30, 0)

    mock_redis.get.side_effect = lambda key: (
        json.dumps(test_profiles)
        if key == BATCH_QUEUE_KEY
        else test_time.isoformat() if key == BATCH_LAST_TIME_KEY else None
    )

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    # Both should be restored
    assert processor._queue.profiles_pending == set(test_profiles)
    assert processor._queue.last_batch_time == test_time


@pytest.mark.unit
def test_queue_status_reflects_persisted_state(mock_redis, engine, config_dir):
    """Test get_queue_status returns correct state after restoration."""
    test_profiles = ["3000", "3001", "3002"]
    test_time = datetime.now() - timedelta(minutes=15)

    mock_redis.get.side_effect = lambda key: (
        json.dumps(test_profiles)
        if key == BATCH_QUEUE_KEY
        else test_time.isoformat() if key == BATCH_LAST_TIME_KEY else None
    )

    processor = BatchFeedbackProcessor(engine, config_dir, redis_client=mock_redis)

    status = processor.get_queue_status()

    assert status["pending_count"] == 3
    assert set(status["pending_profiles"]) == set(test_profiles)
    assert status["last_batch_time"] == test_time.isoformat()
    # Should be approximately 15 minutes (900 seconds)
    assert 890 < status["seconds_since_last_batch"] < 910
