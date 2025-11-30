"""
Unit tests for ProfileTuner (Phase 1: Stability Foundation)

Tests the profile threshold adjustment logic including:
- Threshold adjustment for interest profiles
- Atomic YAML saves
- Database audit trail
- Adjustment history retrieval
"""

import pytest
import yaml

from tgsentinel.profile_tuner import ProfileTuner
from tgsentinel.store import init_db


@pytest.mark.unit
class TestProfileTuner:
    """Test ProfileTuner behavior."""

    def test_threshold_adjustment_interest_profile(self, tmp_path):
        """Test threshold adjustment for interest profile."""
        # Create temp config directory
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Create test profile
        profiles = {
            "3000": {
                "id": 3000,
                "name": "Test Profile",
                "threshold": 0.45,
                "positive_samples": ["test sample"],
                "negative_samples": [],
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        # Initialize database
        engine = init_db("sqlite:///:memory:")

        # Create tuner
        tuner = ProfileTuner(engine, config_dir)

        # Apply adjustment
        adjustment = tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.1,
            reason="test",
            feedback_count=3,
        )

        assert adjustment is not None
        assert adjustment.old_value == 0.45
        assert adjustment.new_value == 0.55
        assert adjustment.feedback_count == 3

        # Verify YAML was updated
        with open(profiles_path, "r", encoding="utf-8") as f:
            updated_profiles = yaml.safe_load(f)

        assert updated_profiles["3000"]["threshold"] == 0.55

    def test_threshold_adjustment_alert_profile(self, tmp_path):
        """Test threshold adjustment for alert profile."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "1000": {
                "id": 1000,
                "name": "Test Alert",
                "min_score": 2.0,
                "keywords": ["urgent"],
            }
        }

        profiles_path = config_dir / "profiles_alert.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        adjustment = tuner.apply_threshold_adjustment(
            profile_id="1000",
            profile_type="alert",
            delta=0.5,
            reason="negative_feedback",
            feedback_count=3,
        )

        assert adjustment is not None
        assert adjustment.old_value == 2.0
        assert adjustment.new_value == 2.5

        # Verify YAML was updated
        with open(profiles_path, "r", encoding="utf-8") as f:
            updated_profiles = yaml.safe_load(f)

        assert updated_profiles["1000"]["min_score"] == 2.5

    def test_threshold_cap_enforcement(self, tmp_path):
        """Test that threshold caps are enforced."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "name": "Test Profile",
                "threshold": 0.90,  # Close to cap
                "positive_samples": ["test"],
                "negative_samples": [],
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Try to raise beyond cap (0.95)
        adjustment = tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.2,  # Would result in 1.1, but capped at 0.95
            reason="test",
            feedback_count=1,
        )

        assert adjustment is not None
        assert adjustment.new_value == 0.95  # Capped

    def test_no_adjustment_when_at_cap(self, tmp_path):
        """Test that no adjustment is made when already at cap."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {
            "3000": {
                "id": 3000,
                "name": "Test Profile",
                "threshold": 0.95,  # At cap
                "positive_samples": ["test"],
                "negative_samples": [],
            }
        }

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Try to raise further
        adjustment = tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.1,
            reason="test",
            feedback_count=1,
        )

        # Should return None since no change needed
        assert adjustment is None

    def test_atomic_save_prevents_corruption(self, tmp_path):
        """Test that atomic save doesn't corrupt file on error."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles_path = config_dir / "profiles_interest.yml"
        original_content = {"3000": {"id": 3000, "threshold": 0.45}}

        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(original_content, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Simulate error during save (invalid YAML)
        invalid_profiles = {"3000": object()}  # Can't serialize

        with pytest.raises(Exception):
            tuner._save_profiles_atomic(profiles_path, invalid_profiles)

        # Original file should still be intact
        with open(profiles_path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)

        assert content == original_content

    def test_dry_run_mode(self, tmp_path):
        """Test that dry_run mode doesn't persist changes."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {"3000": {"id": 3000, "threshold": 0.45}}

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Apply adjustment in dry_run mode
        adjustment = tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.1,
            reason="test",
            feedback_count=3,
            dry_run=True,
        )

        assert adjustment is not None
        assert adjustment.new_value == 0.55

        # Verify YAML was NOT updated
        with open(profiles_path, "r", encoding="utf-8") as f:
            updated_profiles = yaml.safe_load(f)

        assert updated_profiles["3000"]["threshold"] == 0.45  # Unchanged

    def test_adjustment_recorded_in_database(self, tmp_path):
        """Test that adjustments are recorded in database."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {"3000": {"id": 3000, "threshold": 0.45}}

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Apply adjustment
        adjustment = tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.1,
            reason="negative_feedback",
            feedback_count=3,
            trigger_chat_id=-123,
            trigger_msg_id=456,
        )

        assert adjustment is not None

        # Verify database record
        from sqlalchemy import text

        with engine.connect() as con:
            result = con.execute(
                text(
                    """
                    SELECT profile_id, old_value, new_value, adjustment_reason, feedback_count
                    FROM profile_adjustments
                    WHERE profile_id = '3000'
                """
                )
            )
            row = result.fetchone()

        assert row is not None
        assert row[0] == "3000"
        assert row[1] == 0.45
        assert row[2] == 0.55
        assert row[3] == "negative_feedback"
        assert row[4] == 3

    def test_get_adjustment_history(self, tmp_path):
        """Test retrieval of adjustment history."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {"3000": {"id": 3000, "threshold": 0.45}}

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Apply multiple adjustments
        tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.05,
            reason="negative_feedback",
            feedback_count=3,
        )

        tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.05,
            reason="negative_feedback",
            feedback_count=3,
        )

        # Get history
        history = tuner.get_adjustment_history("3000", limit=10)

        assert len(history) == 2
        # History is returned newest first (DESC order by created_at)
        # But if timestamps are identical, order may vary - check both records exist
        old_values = {h["old_value"] for h in history}
        new_values = {h["new_value"] for h in history}
        assert old_values == {0.45, 0.50}
        assert new_values == {0.50, 0.55}

    def test_nonexistent_profile_file(self, tmp_path):
        """Test handling of non-existent profile file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Try to adjust non-existent profile file
        adjustment = tuner.apply_threshold_adjustment(
            profile_id="3000",
            profile_type="interest",
            delta=0.1,
            reason="test",
            feedback_count=1,
        )

        # Should return None
        assert adjustment is None

    def test_nonexistent_profile_id(self, tmp_path):
        """Test handling of non-existent profile ID."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        profiles = {"3000": {"id": 3000, "threshold": 0.45}}

        profiles_path = config_dir / "profiles_interest.yml"
        with open(profiles_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(profiles, f)

        engine = init_db("sqlite:///:memory:")
        tuner = ProfileTuner(engine, config_dir)

        # Try to adjust non-existent profile
        adjustment = tuner.apply_threshold_adjustment(
            profile_id="9999",
            profile_type="interest",
            delta=0.1,
            reason="test",
            feedback_count=1,
        )

        # Should return None
        assert adjustment is None
