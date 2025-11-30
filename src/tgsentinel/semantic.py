import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # package optional at runtime
    SentenceTransformer = None  # type: ignore

_model = None
_profile_vectors: Dict[
    str, Tuple[np.ndarray, Optional[np.ndarray], float, float, float]
] = (
    {}
)  # profile_id -> (positive_vec, negative_vec, threshold, positive_weight, negative_weight)
_profile_vectors_lock = (
    threading.RLock()
)  # Protect _profile_vectors from concurrent access

# Negative similarity margin: only penalize if negative_sim exceeds this threshold
# This prevents small incidental similarities from over-penalizing good matches
NEGATIVE_MARGIN = 0.3


def _build_normalized_centroid(vectors: np.ndarray) -> np.ndarray:
    """Build a normalized centroid from a set of vectors.

    The centroid is the average of all vectors, then normalized to unit length.
    This ensures that dot products with other normalized vectors produce
    true cosine similarity values in [-1, 1].

    Args:
        vectors: Array of embedding vectors (already normalized individually)

    Returns:
        Normalized centroid vector
    """
    centroid = vectors.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm == 0:
        return centroid
    return centroid / norm


def _try_import_model():
    """Lazy load the sentence transformer model.

    Returns None if embeddings are disabled or model loading fails.
    This is called automatically during module initialization if EMBEDDINGS_MODEL is set.
    """
    global _model
    try:
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers not available")
        name = os.getenv("EMBEDDINGS_MODEL")
        if not name:
            return None
        log.info(
            "[SEMANTIC] Loading embeddings model: %s (this happens once at boot)", name
        )
        _model = SentenceTransformer(name)
        log.info("[SEMANTIC] ✓ Embeddings model loaded successfully")
        return _model
    except Exception as e:
        log.warning(f"[SEMANTIC] Embeddings disabled: {e}")
        return None


# NOTE: Model loading is deferred until explicitly called from main.py
# This ensures logging is configured before model loading messages appear
# The _try_import_model() function should be called after setup_logging()


def load_profile_embeddings(
    profile_id: str,
    positive_samples: List[str],
    negative_samples: List[str],
    threshold: float = 0.4,
    positive_weight: float = 1.0,
    negative_weight: float = 0.15,
    feedback_positive_samples: Optional[List[str]] = None,
    feedback_negative_samples: Optional[List[str]] = None,
    feedback_sample_weight: float = 0.4,
):
    """Load and encode positive/negative samples for a semantic profile with weighted centroids.

    Args:
        profile_id: Unique identifier for this profile
        positive_samples: Curated example messages that should match (weight 1.0)
        negative_samples: Curated example messages that should NOT match (weight 1.0)
        threshold: Similarity threshold for this profile (0.0-1.0)
        positive_weight: Multiplier for positive similarity (0.1-2.0)
        negative_weight: Penalty multiplier for negative similarity (0.0-0.5)
        feedback_positive_samples: User feedback samples to add (downweighted)
        feedback_negative_samples: User feedback samples to add (downweighted)
        feedback_sample_weight: Weight for feedback samples (default 0.4 vs 1.0 for curated)
    """
    if _model is None:
        log.debug("[SEMANTIC] Model not available, skipping profile %s", profile_id)
        return

    if not positive_samples:
        log.debug("[SEMANTIC] No positive samples for profile %s, skipping", profile_id)
        return

    feedback_pos = feedback_positive_samples or []
    feedback_neg = feedback_negative_samples or []

    log.info(
        "[SEMANTIC] Encoding profile %s: %d+%d positive, %d+%d negative samples (feedback weighted at %.2f)",
        profile_id,
        len(positive_samples),
        len(feedback_pos),
        len(negative_samples),
        len(feedback_neg),
        feedback_sample_weight,
    )

    # Build weighted positive centroid
    positive_vec = _build_weighted_centroid(
        curated_samples=positive_samples,
        feedback_samples=feedback_pos,
        feedback_weight=feedback_sample_weight,
        model=_model,
    )

    # Guaranteed non-None since positive_samples is non-empty (checked above)
    assert positive_vec is not None, "positive_vec should never be None here"

    # Build weighted negative centroid (optional)
    negative_vec = None
    if negative_samples or feedback_neg:
        negative_vec = _build_weighted_centroid(
            curated_samples=negative_samples,
            feedback_samples=feedback_neg,
            feedback_weight=feedback_sample_weight,
            model=_model,
        )

    # Acquire lock only for the dict write operation (minimal duration)
    with _profile_vectors_lock:
        _profile_vectors[profile_id] = (
            positive_vec,
            negative_vec,
            threshold,
            positive_weight,
            negative_weight,
        )

    log.info(
        "[SEMANTIC] ✓ Profile %s vectors computed (threshold=%.2f, pos_weight=%.2f, neg_weight=%.2f)",
        profile_id,
        threshold,
        positive_weight,
        negative_weight,
    )


def _build_weighted_centroid(
    curated_samples: List[str],
    feedback_samples: List[str],
    feedback_weight: float,
    model,
) -> Optional[np.ndarray]:
    """
    Build weighted centroid from curated and feedback samples.

    Curated samples have weight 1.0, feedback samples have feedback_weight (typically 0.4).
    This prevents feedback samples from overriding the original intent.

    Args:
        curated_samples: Original curated samples (weight 1.0)
        feedback_samples: User feedback samples (downweighted)
        feedback_weight: Weight for feedback samples (0.0-1.0)
        model: SentenceTransformer model instance

    Returns:
        Normalized weighted centroid, or None if no samples
    """
    if not curated_samples and not feedback_samples:
        return None

    all_samples = []
    weights = []

    # Add curated samples with weight 1.0
    for sample in curated_samples:
        all_samples.append(sample)
        weights.append(1.0)

    # Add feedback samples with downweight
    for sample in feedback_samples:
        all_samples.append(sample)
        weights.append(feedback_weight)

    # Encode all samples
    encoded = model.encode(all_samples, normalize_embeddings=True)
    vectors = np.asarray(encoded)

    # Compute weighted sum
    weights_array = np.array(weights).reshape(-1, 1)
    weighted_sum = np.sum(vectors * weights_array, axis=0)

    # Normalize by total weight
    total_weight = sum(weights)
    if total_weight == 0:
        return None

    centroid = weighted_sum / total_weight

    # Normalize to unit length
    norm = np.linalg.norm(centroid)
    if norm == 0:
        return centroid

    return centroid / norm


def score_text_for_profile(text: str, profile_id: str) -> Optional[float]:
    """Score text against a specific semantic profile.

    Uses normalized centroids and proper cosine similarity mapping:
    1. Compute cosine similarity to positive centroid (in [-1, 1])
    2. Compute cosine similarity to negative centroid (in [-1, 1])
    3. Apply negative penalty only if similarity exceeds margin threshold
    4. Map final score from [-1, 1] to [0, 1] for consistent interpretation

    Score ranges (with normalized centroids):
        0.00 - 0.30: Very weakly related / noise
        0.30 - 0.50: Somewhat related
        0.50 - 0.70: Moderately similar (potentially relevant)
        0.70 - 0.85: Strongly similar (highly relevant)
        0.85 - 1.00: Extremely close (almost exact semantic match)

    Args:
        text: Message text to score
        profile_id: Profile ID to score against

    Returns:
        Similarity score (0.0-1.0) or None if profile not found or model unavailable
    """
    if not text or _model is None:
        return None

    # Acquire lock only for reading profile data (minimal duration)
    with _profile_vectors_lock:
        profile_data = _profile_vectors.get(profile_id)

    if profile_data is None:
        return None

    positive_vec, negative_vec, threshold, positive_weight, negative_weight = (
        profile_data
    )

    # Encode message (normalized for true cosine similarity)
    msg_vec = _model.encode([text], normalize_embeddings=True)[0]

    # Calculate cosine similarity to positive centroid (both normalized → value in [-1, 1])
    positive_sim = float(np.dot(msg_vec, positive_vec))

    # Calculate raw score (start with positive similarity)
    raw_score = positive_sim * positive_weight

    # If negative samples exist, apply penalty with margin
    # Only penalize if negative similarity exceeds the margin threshold
    # This prevents small incidental similarities from over-penalizing good matches
    if negative_vec is not None:
        negative_sim = float(np.dot(msg_vec, negative_vec))
        # Apply penalty only if negative similarity exceeds margin
        if negative_sim > NEGATIVE_MARGIN:
            penalty = (negative_sim - NEGATIVE_MARGIN) * negative_weight
            raw_score = raw_score - penalty

    # Map from [-1, 1] to [0, 1] for consistent interpretation
    # This gives more meaningful score ranges
    score = (raw_score + 1.0) / 2.0

    # Clamp to [0, 1] to handle edge cases
    score = max(0.0, min(1.0, score))

    return score


def compute_max_sample_similarity(
    text: str, positive_samples: List[str]
) -> Optional[float]:
    """Compute the maximum similarity between text and any individual positive sample.

    This is useful for the Similarity Tester to show when a text exactly matches
    one of the training samples (score = 1.0).

    Args:
        text: Message text to test
        positive_samples: List of positive training samples

    Returns:
        Maximum cosine similarity to any individual sample (0.0-1.0), or None if model unavailable
    """
    if not text or not positive_samples or _model is None:
        return None

    # Encode the test text
    text_vec = _model.encode([text], normalize_embeddings=True)[0]

    # Encode all positive samples
    sample_vecs = _model.encode(positive_samples, normalize_embeddings=True)

    # Calculate similarity to each sample and return the maximum
    max_sim = 0.0
    for sample_vec in sample_vecs:
        sim = float(np.dot(text_vec, sample_vec))
        if sim > max_sim:
            max_sim = sim

    return max_sim


def get_model_status() -> dict:
    """Get semantic scoring status for health checks."""
    with _profile_vectors_lock:
        profile_count = len(_profile_vectors)
        profiles = list(_profile_vectors.keys())

    return {
        "model_loaded": _model is not None,
        "model_name": os.getenv("EMBEDDINGS_MODEL", "not configured"),
        "profile_count": profile_count,
        "profiles": profiles,
    }


def clear_profile_cache(profile_id: Optional[str] = None):
    """
    Clear cached profile embeddings to force recomputation.

    This should be called after:
    - Committing pending samples to feedback samples
    - Manual profile edits
    - Threshold adjustments that affect scoring

    Args:
        profile_id: Specific profile to clear, or None to clear all
    """
    with _profile_vectors_lock:
        if profile_id is None:
            # Clear all profiles
            count = len(_profile_vectors)
            _profile_vectors.clear()
            log.info(f"[SEMANTIC] Cleared all profile caches ({count} profiles)")
        else:
            # Clear specific profile
            if profile_id in _profile_vectors:
                del _profile_vectors[profile_id]
                log.info(f"[SEMANTIC] Cleared cache for profile {profile_id}")
            else:
                log.debug(f"[SEMANTIC] Profile {profile_id} not in cache")
