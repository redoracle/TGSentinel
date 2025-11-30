"""
Unit tests for Phase 2: Weighted Centroids

Tests the semantic scoring enhancements:
- Weighted centroid calculation
- Feedback sample downweighting
- Cache clearing
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tgsentinel.semantic import (
    _build_weighted_centroid,
    _profile_vectors,
    clear_profile_cache,
    load_profile_embeddings,
)


@pytest.mark.unit
class TestWeightedCentroids:
    """Test weighted centroid calculation."""

    def test_build_weighted_centroid_curated_only(self):
        """Test centroid with only curated samples (weight 1.0)."""
        # Mock model that returns fixed vectors
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array(
            [
                [1.0, 0.0, 0.0],  # Sample 1
                [0.0, 1.0, 0.0],  # Sample 2
            ]
        )

        centroid = _build_weighted_centroid(
            curated_samples=["sample1", "sample2"],
            feedback_samples=[],
            feedback_weight=0.4,
            model=mock_model,
        )

        assert centroid is not None
        # With equal weights, should be average of two vectors
        expected = np.array([0.5, 0.5, 0.0])
        # Normalize
        expected = expected / np.linalg.norm(expected)
        np.testing.assert_array_almost_equal(centroid, expected)

    def test_build_weighted_centroid_with_feedback(self):
        """Test centroid with curated + feedback samples."""
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array(
            [
                [1.0, 0.0, 0.0],  # Curated 1
                [1.0, 0.0, 0.0],  # Curated 2
                [0.0, 1.0, 0.0],  # Feedback 1 (should be downweighted)
            ]
        )

        centroid = _build_weighted_centroid(
            curated_samples=["curated1", "curated2"],
            feedback_samples=["feedback1"],
            feedback_weight=0.4,
            model=mock_model,
        )

        assert centroid is not None

        # Calculate expected:
        # (1.0 * [1,0,0] + 1.0 * [1,0,0] + 0.4 * [0,1,0]) / (1.0 + 1.0 + 0.4)
        # = ([2, 0.4, 0]) / 2.4
        # = [0.833, 0.167, 0]
        # Then normalized

        # The feedback sample should have less influence
        assert centroid[0] > centroid[1]  # X component should dominate

    def test_build_weighted_centroid_feedback_only(self):
        """Test centroid with only feedback samples."""
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array(
            [
                [1.0, 0.0, 0.0],  # Feedback 1
                [0.0, 1.0, 0.0],  # Feedback 2
            ]
        )

        centroid = _build_weighted_centroid(
            curated_samples=[],
            feedback_samples=["feedback1", "feedback2"],
            feedback_weight=0.4,
            model=mock_model,
        )

        assert centroid is not None
        # Even with downweight, should still compute centroid

    def test_build_weighted_centroid_empty(self):
        """Test centroid with no samples."""
        mock_model = MagicMock()

        centroid = _build_weighted_centroid(
            curated_samples=[],
            feedback_samples=[],
            feedback_weight=0.4,
            model=mock_model,
        )

        assert centroid is None

    @patch("tgsentinel.semantic._model")
    def test_load_profile_embeddings_with_feedback(self, mock_model):
        """Test loading profile with feedback samples."""
        # Mock model
        mock_model.encode.side_effect = [
            # Positive samples (curated + feedback)
            np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 0.5, 0.0]]),
            # Negative samples (curated + feedback)
            np.array([[0.0, 1.0, 0.0], [0.0, 0.5, 0.5]]),
        ]

        # Clear cache
        _profile_vectors.clear()

        load_profile_embeddings(
            profile_id="3000",
            positive_samples=["curated1", "curated2"],
            negative_samples=["neg_curated1"],
            threshold=0.45,
            positive_weight=1.0,
            negative_weight=0.18,
            feedback_positive_samples=["feedback_pos1"],
            feedback_negative_samples=["feedback_neg1"],
            feedback_sample_weight=0.4,
        )

        # Verify profile was loaded
        assert "3000" in _profile_vectors

        pos_vec, neg_vec, threshold, pos_weight, neg_weight = _profile_vectors["3000"]
        assert pos_vec is not None
        assert neg_vec is not None
        assert threshold == 0.45
        assert pos_weight == 1.0
        assert neg_weight == 0.18

    def test_clear_profile_cache_specific(self):
        """Test clearing cache for specific profile."""
        # Mock data
        _profile_vectors["3000"] = (np.array([1, 0, 0]), None, 0.45, 1.0, 0.18)
        _profile_vectors["3001"] = (np.array([0, 1, 0]), None, 0.50, 1.0, 0.18)

        clear_profile_cache("3000")

        assert "3000" not in _profile_vectors
        assert "3001" in _profile_vectors

    def test_clear_profile_cache_all(self):
        """Test clearing all profile caches."""
        # Mock data
        _profile_vectors["3000"] = (np.array([1, 0, 0]), None, 0.45, 1.0, 0.18)
        _profile_vectors["3001"] = (np.array([0, 1, 0]), None, 0.50, 1.0, 0.18)

        clear_profile_cache(None)

        assert len(_profile_vectors) == 0

    def test_downweighting_preserves_intent(self):
        """Test that feedback downweighting preserves original profile intent."""
        mock_model = MagicMock()

        # 2 curated samples strongly pointing in [1,0,0] direction
        # 1 feedback sample trying to pull toward [0,1,0]
        mock_model.encode.return_value = np.array(
            [
                [1.0, 0.0, 0.0],  # Curated (weight 1.0)
                [1.0, 0.0, 0.0],  # Curated (weight 1.0)
                [0.0, 1.0, 0.0],  # Feedback (weight 0.4)
            ]
        )

        centroid = _build_weighted_centroid(
            curated_samples=["c1", "c2"],
            feedback_samples=["f1"],
            feedback_weight=0.4,
            model=mock_model,
        )

        # The [1,0,0] direction should still dominate
        # because curated samples have total weight 2.0 vs 0.4 for feedback
        assert centroid[0] > 0.7  # Strong X component
        assert centroid[1] < 0.3  # Weak Y component
