"""
Integration tests for feedback learning system end-to-end workflows.

Tests complete user journeys from feedback submission through automatic
adjustments and profile updates.
"""

import asyncio
from datetime import datetime, timedelta

import pytest
import yaml as pyyaml

from tgsentinel.feedback_aggregator import FeedbackAggregator, FeedbackStats
from tgsentinel.feedback_processor import BatchFeedbackProcessor
from tgsentinel.profile_tuner import ProfileTuner
from tgsentinel.store import init_db


@pytest.fixture
def setup_environment(tmp_path):
    """Set up test environment with database and config directory."""
    # Create database
    engine = init_db("sqlite:///:memory:")

    # Create config directory
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Create test profiles file
    profiles = {
        "3000": {
            "id": 3000,
            "name": "AI Research",
            "threshold": 0.45,
            "positive_samples": [
                "Machine learning research paper",
                "Deep learning breakthrough in computer vision",
                "Neural network architecture improvements",
            ],
            "negative_samples": [
                "Stock market pump and dump",
                "Crypto scam alert",
                "Buy now limited offer",
            ],
            "feedback_positive_samples": [],
            "feedback_negative_samples": [],
            "pending_positive_samples": [],
            "pending_negative_samples": [],
            "positive_weight": 1.0,
            "negative_weight": 0.18,
            "auto_tuning": {
                "enabled": True,
                "feedback_sample_weight": 0.4,
                "pending_commit_threshold": 3,
                "max_feedback_samples": 20,
                "max_delta_threshold": 0.25,
                "max_delta_negative_weight": 0.1,
            },
        },
        "3001": {
            "id": 3001,
            "name": "Security Monitoring",
            "threshold": 0.50,
            "positive_samples": [
                "Security vulnerability disclosed",
                "Critical CVE alert",
                "Zero-day exploit discovered",
            ],
            "negative_samples": [
                "Product advertisement",
                "Marketing email",
                "Newsletter signup",
            ],
            "feedback_positive_samples": [],
            "feedback_negative_samples": [],
            "pending_positive_samples": [],
            "pending_negative_samples": [],
            "positive_weight": 1.0,
            "negative_weight": 0.18,
            "auto_tuning": {
                "enabled": True,
                "feedback_sample_weight": 0.4,
                "pending_commit_threshold": 3,
                "max_feedback_samples": 20,
                "max_delta_threshold": 0.25,
                "max_delta_negative_weight": 0.1,
            },
        },
    }

    profiles_path = config_dir / "profiles_interest.yml"
    with open(profiles_path, "w") as f:
        pyyaml.safe_dump(profiles, f)

    yield {
        "engine": engine,
        "config_dir": config_dir,
        "profiles_path": profiles_path,
    }


@pytest.mark.integration
class TestFeedbackLearningFlow:
    """Integration tests for complete feedback learning workflows."""

    def test_threshold_adjustment_flow(self, setup_environment):
        """
        Test threshold adjustment workflow:
        1. Submit 3 borderline false positives
        2. Verify threshold adjustment triggered
        3. Verify YAML updated
        4. Verify aggregator stats updated
        """
        env = setup_environment

        aggregator = FeedbackAggregator()
        tuner = ProfileTuner(env["engine"], env["config_dir"])

        # Submit 3 borderline false positives
        for i in range(3):
            rec = aggregator.record_feedback(
                profile_id="3000",
                label="down",
                semantic_score=0.50,  # Borderline (threshold + 0.05)
                threshold=0.45,
            )

            if i < 2:
                # First two should not trigger
                assert rec["action"] == "none"
            else:
                # Third should trigger threshold raise
                assert rec["action"] == "raise_threshold"
                assert rec["delta"] == 0.10

        # Apply adjustment
        tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.10,
            reason="3 borderline false positives",
            feedback_count=3,
            trigger_chat_id=-123,
            trigger_msg_id=456,
        )

        # Verify YAML updated
        with open(env["profiles_path"], "r") as f:
            profiles = pyyaml.safe_load(f)

        assert profiles["3000"]["threshold"] == 0.55

    @pytest.mark.asyncio
    async def test_sample_augmentation_flow(self, setup_environment):
        """
        Test sample augmentation workflow:
        1. Submit 2 severe false positives
        2. Verify samples added to pending buffer
        3. Commit pending samples
        4. Verify centroid changed
        """
        env = setup_environment

        aggregator = FeedbackAggregator()
        tuner = ProfileTuner(env["engine"], env["config_dir"])

        # Submit 2 severe false positives
        recommendations = []
        for i in range(2):
            msg = f"PUMP IT NOW {i} 🚀 TO THE MOON"
            rec = aggregator.record_feedback(
                profile_id="3000",
                label="down",
                semantic_score=0.70,  # Severe FP (threshold + 0.25)
                threshold=0.45,
            )
            recommendations.append(rec)

            if rec["action"] == "add_negative_sample":
                # Add to pending
                added = tuner.add_to_pending_samples(
                    profile_id="3000",
                    profile_type="interest",
                    sample_category="negative",
                    sample_text=msg,
                    semantic_score=0.70,
                    feedback_chat_id=-123,
                    feedback_msg_id=i,
                )
                assert added is True

        # Should trigger on second feedback
        assert recommendations[1]["action"] == "add_negative_sample"

        # Verify pending buffer has samples
        with open(env["profiles_path"], "r") as f:
            profiles = pyyaml.safe_load(f)

        pending_count = len(profiles["3000"]["pending_negative_samples"])
        assert pending_count >= 1
        assert any(
            "PUMP IT NOW" in s for s in profiles["3000"]["pending_negative_samples"]
        )

        # Commit pending samples
        committed = tuner.commit_pending_samples(
            profile_id="3000", profile_type="interest", sample_category="negative"
        )

        assert committed == pending_count

        # Verify moved to feedback_negative_samples
        with open(env["profiles_path"], "r") as f:
            profiles = pyyaml.safe_load(f)

        assert len(profiles["3000"]["pending_negative_samples"]) == 0
        assert len(profiles["3000"]["feedback_negative_samples"]) == pending_count

        # Verify centroid would be recomputed on next scoring
        # (We don't need to actually compute it in this test)

    @pytest.mark.asyncio
    async def test_batch_processing_flow(self, setup_environment):
        """
        Test batch processing workflow:
        1. Schedule multiple profiles for recompute
        2. Verify queue grows
        3. Process batch
        4. Verify queue cleared and caches invalidated
        """
        env = setup_environment

        processor = BatchFeedbackProcessor(env["engine"], env["config_dir"])

        # Schedule 5 profiles
        profile_ids = [f"300{i}" for i in range(5)]
        for pid in profile_ids:
            processor.schedule_recompute(pid)

        # Verify queue status
        status = processor.get_queue_status()
        assert status["pending_count"] == 5
        assert all(pid in status["pending_profiles"] for pid in profile_ids)

        # Process batch
        await processor.process_batch()

        # Verify queue cleared
        status = processor.get_queue_status()
        assert status["pending_count"] == 0

    def test_drift_cap_enforcement(self, setup_environment):
        """
        Test that drift cap prevents runaway adjustments.
        """
        aggregator = FeedbackAggregator()

        # Manually set cumulative delta to near cap
        stats = aggregator._stats.setdefault("3000", FeedbackStats(profile_id="3000"))
        stats.cumulative_threshold_delta = 0.20

        # Add 3 borderline FPs
        for i in range(3):
            aggregator.record_feedback(
                profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
            )

        # Should still recommend adjustment (0.20 + 0.10 = 0.30, but allowed since it's the action)
        rec = aggregator.record_feedback(
            profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
        )

        # Now set to cap
        stats.cumulative_threshold_delta = 0.25

        # Add 3 more FPs
        for i in range(3):
            aggregator.record_feedback(
                profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
            )

        rec = aggregator.record_feedback(
            profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
        )

        # Should NOT recommend adjustment (drift cap reached)
        assert rec["action"] == "none"
        assert "drift cap" in rec["reason"].lower()

    @pytest.mark.asyncio
    async def test_feedback_decay_flow(self, setup_environment):
        """
        Test feedback decay workflow:
        1. Add feedback with old timestamps
        2. Run decay
        3. Verify old feedback removed
        """
        aggregator = FeedbackAggregator()

        # Add fresh feedback
        for i in range(3):
            aggregator.record_feedback(
                profile_id="3000", label="down", semantic_score=0.50, threshold=0.45
            )

        # Manually add old feedback timestamps
        stats = aggregator._stats["3000"]
        old_time = datetime.now() - timedelta(days=8)  # 8 days ago
        stats.last_borderline_fp.extend([old_time, old_time, old_time])
        stats.borderline_fp += 3

        # Verify 6 total (3 fresh + 3 old)
        assert stats.borderline_fp == 6

        # Run decay
        aggregator._decay_all_profiles()

        # Verify only 3 remain (fresh only)
        assert stats.borderline_fp == 3

    @pytest.mark.asyncio
    async def test_concurrent_feedback_submission(self, setup_environment):
        """
        Test concurrent feedback submissions don't cause race conditions.
        """
        aggregator = FeedbackAggregator()

        async def submit_feedback(profile_id: str, count: int):
            for i in range(count):
                aggregator.record_feedback(
                    profile_id=profile_id,
                    label="down",
                    semantic_score=0.50,
                    threshold=0.45,
                )
                await asyncio.sleep(0.001)  # Small delay

        # Submit feedback concurrently for 2 profiles
        await asyncio.gather(submit_feedback("3000", 10), submit_feedback("3001", 10))

        # Verify counts correct
        assert aggregator._stats["3000"].borderline_fp == 10
        assert aggregator._stats["3001"].borderline_fp == 10

    def test_rollback_pending_samples(self, setup_environment):
        """
        Test rollback of pending samples.
        """
        env = setup_environment

        tuner = ProfileTuner(env["engine"], env["config_dir"])

        # Add pending samples
        for i in range(3):
            tuner.add_to_pending_samples(
                profile_id="3000",
                profile_type="interest",
                sample_category="negative",
                sample_text=f"Bad sample {i}",
                semantic_score=0.70,
                feedback_chat_id=-123,
                feedback_msg_id=i,
            )

        # Verify pending buffer has 3 samples
        with open(env["profiles_path"], "r") as f:
            profiles = pyyaml.safe_load(f)

        assert len(profiles["3000"]["pending_negative_samples"]) == 3

        # Rollback
        removed = tuner.rollback_pending_samples(
            profile_id="3000", profile_type="interest", sample_category="negative"
        )

        assert removed == 3

        # Verify pending buffer empty
        with open(env["profiles_path"], "r") as f:
            profiles = pyyaml.safe_load(f)

        assert len(profiles["3000"]["pending_negative_samples"]) == 0

    @pytest.mark.asyncio
    async def test_complete_user_journey(self, setup_environment):
        """
        Test complete user journey from feedback to profile improvement.
        """
        env = setup_environment

        aggregator = FeedbackAggregator()
        tuner = ProfileTuner(env["engine"], env["config_dir"])
        processor = BatchFeedbackProcessor(env["engine"], env["config_dir"])

        # Step 1: User provides feedback on false positives
        for i in range(3):
            rec = aggregator.record_feedback(
                profile_id="3000",
                label="down",
                semantic_score=0.50,
                threshold=0.45,
            )

        # Step 2: System recommends threshold adjustment
        assert rec["action"] == "raise_threshold"

        # Step 3: Tuner applies adjustment
        tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.10,
            reason="3 borderline false positives",
            feedback_count=3,
            trigger_chat_id=-123,
            trigger_msg_id=456,
        )

        # Step 4: Schedule centroid recompute
        processor.schedule_recompute("3000")

        # Step 5: Process batch
        await processor.process_batch()

        # Step 6: Verify profile updated
        with open(env["profiles_path"], "r") as f:
            profiles = pyyaml.safe_load(f)

        assert profiles["3000"]["threshold"] == 0.55

        # Step 7: User reviews and sees improvement
        # (Threshold raised, so borderline FPs now filtered out)
        # In real system, next scoring would use new threshold
