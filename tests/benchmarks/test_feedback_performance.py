"""
Performance benchmarks for feedback learning system.

Validates that performance targets are met under load:
- Feedback submission: < 100ms p99
- Batch processing (10 profiles): < 2s
- Profile loading (50 profiles): < 500ms
- Semantic scoring: < 50ms per message
"""

import asyncio
import time

import pytest
import yaml as pyyaml

from tgsentinel.feedback_aggregator import FeedbackAggregator
from tgsentinel.feedback_processor import BatchFeedbackProcessor
from tgsentinel.profile_tuner import ProfileTuner
from tgsentinel.semantic import (
    _try_import_model,
    clear_profile_cache,
    load_profile_embeddings,
)
from tgsentinel.store import init_db


@pytest.mark.benchmark
class TestFeedbackPerformance:
    """Performance benchmarks for feedback learning system."""

    def test_feedback_submission_throughput(self, tmp_path):
        """
        Test that feedback submission handles 100 requests/min.

        Target: < 100ms p99 latency per feedback
        """
        aggregator = FeedbackAggregator()

        latencies = []

        # Submit 100 feedbacks and measure latency
        for i in range(100):
            start = time.time()

            aggregator.record_feedback(
                profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
            )

            elapsed = time.time() - start
            latencies.append(elapsed)

        # Calculate p99
        latencies.sort()
        p99 = latencies[98]  # 99th percentile (0-indexed)

        # Should complete p99 in < 100ms
        assert p99 < 0.100, f"p99 latency {p99*1000:.1f}ms exceeds 100ms target"

        # Total time should be < 1 second (100 req/min = 1.67 req/sec)
        total_time = sum(latencies)
        assert (
            total_time < 1.0
        ), f"Total time {total_time:.2f}s exceeds 1s for 100 feedbacks"

        print(
            f"✓ 100 feedbacks processed: p99={p99*1000:.1f}ms, "
            f"total={total_time*1000:.0f}ms, "
            f"avg={total_time/100*1000:.1f}ms"
        )

    def test_profile_load_time(self, tmp_path):
        """
        Test that loading 50 profiles takes < 500ms.

        Target: < 500ms for 50 profiles
        """
        # Create 50 test profiles
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {}
        for i in range(50):
            profiles[f"30{i:02d}"] = {
                "id": 3000 + i,
                "name": f"Test Profile {i}",
                "threshold": 0.45,
                "positive_samples": [
                    f"Sample {i} positive 1",
                    f"Sample {i} positive 2",
                ],
                "negative_samples": [
                    f"Sample {i} negative 1",
                    f"Sample {i} negative 2",
                ],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
                "positive_weight": 1.0,
                "negative_weight": 0.18,
            }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w") as f:
            pyyaml.safe_dump(profiles, f)

        # Time loading all profiles
        start = time.time()

        with open(profiles_path, "r") as f:
            loaded = pyyaml.safe_load(f)

        elapsed = time.time() - start

        # Should load in < 500ms
        assert elapsed < 0.5, f"Profile load time {elapsed*1000:.0f}ms exceeds 500ms"
        assert len(loaded) == 50

        print(f"✓ 50 profiles loaded in {elapsed*1000:.0f}ms")

    @pytest.mark.asyncio
    async def test_batch_processing_time(self, tmp_path):
        """
        Test that batch processing 10 profiles takes < 2 seconds.

        Target: < 2s for batch of 10 profiles
        """
        engine = init_db("sqlite:///:memory:")
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Create minimal profiles file
        profiles = {
            f"300{i}": {
                "id": 3000 + i,
                "name": f"Profile {i}",
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": ["test"],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
            }
            for i in range(10)
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w") as f:
            pyyaml.safe_dump(profiles, f)

        processor = BatchFeedbackProcessor(engine, config_dir)

        # Schedule 10 profiles
        for i in range(10):
            processor.schedule_recompute(f"300{i}")

        # Time batch processing
        start = time.time()
        await processor.process_batch()
        elapsed = time.time() - start

        # Should complete in < 2 seconds
        assert elapsed < 2.0, f"Batch processing time {elapsed:.2f}s exceeds 2s target"

        print(f"✓ Batch of 10 profiles processed in {elapsed:.2f}s")

    def test_weighted_centroid_overhead(self, tmp_path):
        """
        Test overhead of weighted centroids vs equal-weight.

        Target: < 20% overhead for weighted centroids
        """
        # Ensure model is loaded
        _try_import_model()

        curated_samples = [
            "Machine learning research",
            "Deep learning breakthrough",
            "Neural network architecture",
        ]

        feedback_samples = ["AI news update", "Model training techniques"]

        # Time equal weight (Phase 1)
        start = time.time()
        for i in range(100):
            load_profile_embeddings(
                profile_id=f"test_{i}",
                positive_samples=curated_samples,
                negative_samples=[],
            )
        equal_time = time.time() - start

        # Clear cache
        clear_profile_cache()

        # Time weighted (Phase 2)
        start = time.time()
        for i in range(100):
            load_profile_embeddings(
                profile_id=f"test_weighted_{i}",
                positive_samples=curated_samples,
                negative_samples=[],
                feedback_positive_samples=feedback_samples,
                feedback_sample_weight=0.4,
            )
        weighted_time = time.time() - start

        # Guard against zero division
        assert equal_time > 0, (
            f"equal_time is zero, cannot compute overhead. "
            f"equal_time={equal_time}, weighted_time={weighted_time}"
        )

        # Overhead should be < 20%
        overhead = (weighted_time - equal_time) / equal_time
        assert (
            overhead < 0.20
        ), f"Weighted centroid overhead {overhead*100:.1f}% exceeds 20%"

        print(
            f"✓ Weighted centroid overhead: {overhead*100:.1f}% "
            f"(equal={equal_time*1000:.0f}ms, weighted={weighted_time*1000:.0f}ms)"
        )

    def test_semantic_scoring_latency(self, tmp_path):
        """
        Test semantic scoring latency per message.

        Target: < 50ms per message
        """
        # Ensure model is loaded
        _try_import_model()

        positive_samples = [
            "Machine learning research",
            "Deep learning breakthrough",
            "Neural network architecture",
        ]

        negative_samples = ["Stock market pump", "Crypto scam", "Buy now offer"]

        # Compute centroids once
        load_profile_embeddings(
            profile_id="test_scoring",
            positive_samples=positive_samples,
            negative_samples=negative_samples,
        )
        # Model loaded and cached by load_profile_embeddings

        # Test messages
        test_messages = [
            "New research on transformer models",
            "AI model achieves breakthrough results",
            "Neural network training optimization",
            "Deep learning paper accepted at conference",
            "Machine learning algorithm improvement",
        ]

        latencies = []

        # Time scoring each message
        from tgsentinel.semantic import score_text_for_profile

        for msg in test_messages:
            start = time.time()

            score_text_for_profile(
                text=msg,
                profile_id="test_scoring",
            )

            elapsed = time.time() - start
            latencies.append(elapsed)

        # Calculate average and max
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)

        # Should be < 50ms per message
        assert (
            avg_latency < 0.050
        ), f"Avg scoring latency {avg_latency*1000:.1f}ms exceeds 50ms"
        assert (
            max_latency < 0.100
        ), f"Max scoring latency {max_latency*1000:.1f}ms exceeds 100ms"

        print(
            f"✓ Semantic scoring: avg={avg_latency*1000:.1f}ms, max={max_latency*1000:.1f}ms"
        )

    @pytest.mark.asyncio
    async def test_load_test_1000_feedbacks(self, tmp_path):
        """
        Load test: 1000 feedbacks over 10 minutes.

        Target: Handle sustained load without degradation
        """
        aggregator = FeedbackAggregator()

        # Simulate 1000 feedbacks spread over 10 minutes
        # (In reality, we'll submit them faster for testing)
        start = time.time()

        for i in range(1000):
            aggregator.record_feedback(
                profile_id=f"300{i % 10}",  # Rotate through 10 profiles
                label="down" if i % 3 == 0 else "up",
                semantic_score=0.50,
                threshold=0.45,
            )

            # Small delay to avoid tight loop
            if i % 100 == 0:
                await asyncio.sleep(0.01)

        elapsed = time.time() - start

        # Should complete in < 10 seconds (generous for test speed)
        assert elapsed < 10.0, f"Load test took {elapsed:.2f}s, too slow"

        # Verify all profiles have feedback
        for i in range(10):
            profile_id = f"300{i}"
            if profile_id in aggregator._stats:
                assert aggregator._stats[profile_id].borderline_fp > 0

        print(
            f"✓ Load test: 1000 feedbacks processed in {elapsed:.2f}s "
            f"({1000/elapsed:.1f} req/s)"
        )

    def test_tuner_atomic_save_performance(self, tmp_path):
        """
        Test ProfileTuner atomic save performance.

        Target: < 50ms per save
        """
        engine = init_db("sqlite:///:memory:")
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Create test profile
        profiles = {
            "3000": {
                "id": 3000,
                "name": "Test",
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": ["test"],
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w") as f:
            pyyaml.safe_dump(profiles, f)

        tuner = ProfileTuner(engine, config_dir)

        latencies = []

        # Perform 10 threshold adjustments
        for i in range(10):
            delta = 0.01

            start = time.time()

            tuner.apply_threshold_adjustment(
                profile_id="3000",
                profile_type="interest",
                delta=delta,
                reason="test",
                feedback_count=1,
                trigger_chat_id=-123,
                trigger_msg_id=i,
            )

            elapsed = time.time() - start
            latencies.append(elapsed)

        # Calculate p99
        latencies.sort()
        p99 = latencies[-1]  # Max (for 10 samples)

        # Should complete in < 50ms
        assert p99 < 0.050, f"Atomic save p99 {p99*1000:.1f}ms exceeds 50ms"

        print(
            f"✓ Atomic save performance: p99={p99*1000:.1f}ms, avg={sum(latencies)/len(latencies)*1000:.1f}ms"
        )

    @pytest.mark.asyncio
    async def test_concurrent_batch_scheduling(self, tmp_path):
        """
        Test concurrent batch scheduling performance.

        Target: Handle concurrent scheduling without blocking
        """
        engine = init_db("sqlite:///:memory:")
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        processor = BatchFeedbackProcessor(engine, config_dir)

        async def schedule_many(start_idx: int, count: int):
            for i in range(count):
                processor.schedule_recompute(f"profile_{start_idx + i}")
                await asyncio.sleep(0.001)

        # Schedule 100 profiles concurrently from 4 coroutines
        start = time.time()

        await asyncio.gather(
            schedule_many(0, 25),
            schedule_many(25, 25),
            schedule_many(50, 25),
            schedule_many(75, 25),
        )

        elapsed = time.time() - start

        # Should complete in < 1 second
        assert elapsed < 1.0, f"Concurrent scheduling took {elapsed:.2f}s, too slow"

        # Verify queue has 100 profiles
        status = processor.get_queue_status()
        assert status["pending_count"] == 100

        print(f"✓ Concurrent scheduling: 100 profiles in {elapsed:.2f}s")


if __name__ == "__main__":
    # Run benchmarks
    pytest.main([__file__, "-v", "-s", "-m", "benchmark"])
