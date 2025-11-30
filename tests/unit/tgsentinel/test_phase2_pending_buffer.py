"""
Unit tests for Phase 2: Pending Buffer System

Tests the pending buffer workflow:
- Adding samples to pending buffer
- Committing pending samples
- Rolling back pending samples
- Feedback sample caps
"""

import pytest
import yaml

from tgsentinel.profile_tuner import ProfileTuner
from tgsentinel.store import init_db


@pytest.mark.unit
class TestPendingBuffer:
    """Test pending buffer functionality."""

    def test_add_to_pending_buffer(self, tmp_path):
        """Test adding sample to pending buffer."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": [],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
                "pending_positive_samples": [],
                "pending_negative_samples": [],
                "auto_tuning": {
                    "enabled": True,
                    "max_feedback_samples": 20,
                },
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Add to pending negative samples
        success = tuner.add_to_pending_samples(
            profile_id="3000",
            profile_type="interest",
            sample_category="negative",
            sample_text="PUMP IT NOW 🚀",
            semantic_score=0.70,
            feedback_chat_id=-123,
            feedback_msg_id=456,
        )

        assert success

        # Verify in YAML
        with open(profiles_path, "r", encoding="utf-8") as f:
            updated = yaml.safe_load(f)

        assert len(updated["3000"]["pending_negative_samples"]) == 1
        assert updated["3000"]["pending_negative_samples"][0] == "PUMP IT NOW 🚀"

    def test_prevent_duplicate_pending_samples(self, tmp_path):
        """Test that duplicate samples are not added to pending."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": [],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
                "pending_positive_samples": [],
                "pending_negative_samples": [],
                "auto_tuning": {"enabled": True, "max_feedback_samples": 20},
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Add first sample
        success1 = tuner.add_to_pending_samples(
            profile_id="3000",
            profile_type="interest",
            sample_category="negative",
            sample_text="PUMP IT NOW 🚀",
            semantic_score=0.70,
        )

        # Try to add duplicate
        success2 = tuner.add_to_pending_samples(
            profile_id="3000",
            profile_type="interest",
            sample_category="negative",
            sample_text="PUMP IT NOW 🚀",
            semantic_score=0.72,
        )

        assert success1
        assert not success2  # Duplicate should be rejected

    def test_commit_pending_samples(self, tmp_path):
        """Test committing pending samples to feedback samples."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": [],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
                "pending_positive_samples": [],
                "pending_negative_samples": [],
                "auto_tuning": {"enabled": True, "max_feedback_samples": 20},
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Add 3 pending samples
        for i in range(3):
            tuner.add_to_pending_samples(
                profile_id="3000",
                profile_type="interest",
                sample_category="negative",
                sample_text=f"Sample {i}",
                semantic_score=0.70 + i * 0.01,
            )

        # Commit
        committed_count = tuner.commit_pending_samples("3000", "interest", "negative")

        assert committed_count == 3

        # Verify in YAML
        with open(profiles_path, "r", encoding="utf-8") as f:
            updated = yaml.safe_load(f)

        assert len(updated["3000"]["feedback_negative_samples"]) == 3
        assert len(updated["3000"]["pending_negative_samples"]) == 0

    def test_feedback_sample_cap(self, tmp_path):
        """Test that feedback samples respect cap."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": [],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
                "pending_positive_samples": [],
                "pending_negative_samples": [],
                "auto_tuning": {"enabled": True, "max_feedback_samples": 5},  # Cap at 5
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Add 3 samples to feedback directly
        profiles["3000"]["feedback_negative_samples"] = ["A", "B", "C"]
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        # Add 5 to pending
        for i in range(5):
            tuner.add_to_pending_samples(
                profile_id="3000",
                profile_type="interest",
                sample_category="negative",
                sample_text=f"Sample {i}",
                semantic_score=0.70 + i * 0.01,
            )

        # Commit (should only commit 2 due to cap)
        committed_count = tuner.commit_pending_samples("3000", "interest", "negative")

        assert committed_count == 2  # Cap is 5, already have 3, so only 2 more

        # Verify in YAML
        with open(profiles_path, "r", encoding="utf-8") as f:
            updated = yaml.safe_load(f)

        assert len(updated["3000"]["feedback_negative_samples"]) == 5  # Cap reached
        assert len(updated["3000"]["pending_negative_samples"]) == 3  # 3 still pending

    def test_rollback_pending_samples(self, tmp_path):
        """Test rolling back pending samples."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": [],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
                "pending_positive_samples": [],
                "pending_negative_samples": [],
                "auto_tuning": {"enabled": True, "max_feedback_samples": 20},
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Add 3 pending samples
        for i in range(3):
            tuner.add_to_pending_samples(
                profile_id="3000",
                profile_type="interest",
                sample_category="negative",
                sample_text=f"Sample {i}",
                semantic_score=0.70 + i * 0.01,
            )

        # Rollback
        rolled_back_count = tuner.rollback_pending_samples(
            "3000", "interest", "negative"
        )

        assert rolled_back_count == 3

        # Verify in YAML
        with open(profiles_path, "r", encoding="utf-8") as f:
            updated = yaml.safe_load(f)

        assert len(updated["3000"]["pending_negative_samples"]) == 0
        assert (
            len(updated["3000"]["feedback_negative_samples"]) == 0
        )  # Nothing committed

    def test_get_pending_samples(self, tmp_path):
        """Test retrieving pending samples with metadata."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "threshold": 0.45,
                "positive_samples": ["test"],
                "negative_samples": [],
                "feedback_positive_samples": [],
                "feedback_negative_samples": [],
                "pending_positive_samples": [],
                "pending_negative_samples": [],
                "auto_tuning": {"enabled": True, "max_feedback_samples": 20},
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Add samples
        tuner.add_to_pending_samples(
            profile_id="3000",
            profile_type="interest",
            sample_category="negative",
            sample_text="Sample A",
            semantic_score=0.70,
            feedback_chat_id=-123,
            feedback_msg_id=456,
        )

        tuner.add_to_pending_samples(
            profile_id="3000",
            profile_type="interest",
            sample_category="positive",
            sample_text="Sample B",
            semantic_score=0.80,
            feedback_chat_id=-123,
            feedback_msg_id=789,
        )

        # Get pending samples
        samples = tuner.get_pending_samples("3000", "interest")

        assert len(samples["negative"]) == 1
        assert len(samples["positive"]) == 1

        assert samples["negative"][0]["text"] == "Sample A"
        assert samples["negative"][0]["semantic_score"] == 0.70
        assert samples["negative"][0]["weight"] == 0.4

        assert samples["positive"][0]["text"] == "Sample B"
        assert samples["positive"][0]["semantic_score"] == 0.80
