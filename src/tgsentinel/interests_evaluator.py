"""Semantic interest evaluation pipeline.

This module handles embedding-based scoring for interest profiles, enforcing the constraint:
"Interests = semantic analysis + semantic-scoring"

Separates interest (semantic) logic from alert (keyword) logic per the taxonomy:
- Semantic Type: interest_semantic
- Target Entity: profile (during evaluation) → feed (after storage)
- Delivery: determined by delivery_orchestrator based on profile config

Related architectural constraints:
- Constraint 2 (Concurrency): All functions are sync; called from async worker context
- Constraint 4 (Structured Logging): Uses handler tag [INTERESTS-EVALUATOR]
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import AppCfg, ProfileDefinition
from .semantic import score_text_for_profile

log = logging.getLogger(__name__)


@dataclass
class InterestEvaluationResult:
    """Result of semantic interest evaluation.

    Attributes:
        semantic_scores_json: JSON string with {profile_id: similarity_score}
        matched_profile_ids: List of interest profile IDs that exceeded threshold
        trigger_annotations: Dict with semantic match metadata
        should_include_in_feed: True if any profile threshold was met
        should_include_in_digest: True if message qualifies for digest batching
        delivery_recommendations: Per-profile delivery mode suggestions
    """

    semantic_scores_json: str
    matched_profile_ids: List[str]
    trigger_annotations: Dict[str, Any]
    should_include_in_feed: bool
    should_include_in_digest: bool
    delivery_recommendations: Dict[str, str]  # profile_id -> delivery_mode


def evaluate_interest_profiles(
    message_text: str,
    chat_title: str,
    sender_name: str,
    sender_id: int,
    resolved_profile: Any,  # ResolvedProfile
    cfg: AppCfg,
    request_id: Optional[str] = None,
) -> Optional[InterestEvaluationResult]:
    """Evaluate message against interest profiles using semantic scoring.

    This function implements the "Interests = semantic analysis + semantic-scoring" rule.
    It only processes profiles WITH positive_samples (semantic-based profiles).

    Args:
        message_text: Message content for semantic embedding
        chat_title: Chat/channel title for context
        sender_name: Sender display name
        sender_id: Telegram sender ID
        resolved_profile: Profile resolution result with matched_profile_ids
        cfg: Application configuration with global_profiles
        request_id: Optional correlation ID for logging

    Returns:
        InterestEvaluationResult with semantic scores and recommendations, or None if no
        semantic profiles matched

    Logs:
        - [INTERESTS-EVALUATOR] INFO: Profile matches and threshold comparisons
        - [INTERESTS-EVALUATOR] DEBUG: Semantic similarity scores per profile
    """
    extra = (
        {"request_id": request_id, "semantic_type": "interest_semantic"}
        if request_id
        else {}
    )

    # Filter to interest profiles only (those WITH positive_samples = semantic config)
    interest_profile_ids = []
    semantic_scores = {}
    triggering_feed_profiles = []  # Profiles that met threshold for feed inclusion
    triggering_digest_profiles = []  # Profiles that met threshold for digest
    delivery_recommendations = {}

    if (
        not resolved_profile
        or not resolved_profile.matched_profile_ids
        or not cfg.global_profiles
    ):
        return None

    for pid in resolved_profile.matched_profile_ids:
        profile_def = cfg.global_profiles.get(pid)
        if not profile_def:
            continue

        # Check if this is a semantic profile (has positive_samples)
        is_semantic = (
            hasattr(profile_def, "positive_samples") and profile_def.positive_samples
        )

        if not is_semantic:
            # Skip keyword-based profiles - they belong to alerts_evaluator
            continue

        # This is an interest profile (semantic-based)
        interest_profile_ids.append(pid)

        # Calculate semantic similarity score
        semantic_score = score_text_for_profile(message_text, pid)
        if semantic_score is None:
            log.warning(
                "[INTERESTS-EVALUATOR] Profile %s semantic scoring failed (embeddings unavailable?)",
                pid,
                extra=extra,
            )
            continue

        semantic_scores[pid] = semantic_score

        # Check if score meets profile's threshold
        threshold = getattr(profile_def, "threshold", 0.7)
        if semantic_score >= threshold:
            triggering_feed_profiles.append(pid)

            # Determine if this qualifies for digest based on profile config
            delivery_mode = _extract_delivery_mode(profile_def)
            delivery_recommendations[pid] = delivery_mode

            if delivery_mode in ("digest", "both"):
                triggering_digest_profiles.append(pid)

            log.info(
                "[INTERESTS-EVALUATOR] Profile %s matched: semantic_score=%.3f >= threshold=%.2f, delivery=%s",
                pid,
                semantic_score,
                threshold,
                delivery_mode,
                extra=extra,
            )
        else:
            log.debug(
                "[INTERESTS-EVALUATOR] Profile %s below threshold: semantic_score=%.3f < threshold=%.2f",
                pid,
                semantic_score,
                threshold,
                extra=extra,
            )

    # If no semantic profiles matched, return None
    if not interest_profile_ids:
        return None

    # Build annotations with semantic metadata
    annotations_dict = {
        "semantic_type": "interest_semantic",
        "interest_profile_ids": interest_profile_ids,
        "semantic_scores": semantic_scores,  # Store for analysis
        "triggering_feed_profiles": triggering_feed_profiles,
        "triggering_digest_profiles": triggering_digest_profiles,
    }

    semantic_scores_json = json.dumps(semantic_scores)

    log.info(
        "[INTERESTS-EVALUATOR] Evaluation complete: matched=%d, feed=%d, digest=%d",
        len(interest_profile_ids),
        len(triggering_feed_profiles),
        len(triggering_digest_profiles),
        extra=extra,
    )

    return InterestEvaluationResult(
        semantic_scores_json=semantic_scores_json,
        matched_profile_ids=triggering_feed_profiles,  # Only profiles that met threshold
        trigger_annotations=annotations_dict,
        should_include_in_feed=len(triggering_feed_profiles) > 0,
        should_include_in_digest=len(triggering_digest_profiles) > 0,
        delivery_recommendations=delivery_recommendations,
    )


def _extract_delivery_mode(profile_def: ProfileDefinition) -> str:
    """Extract delivery mode from profile configuration.

    Checks digest schedules for delivery settings, falling back to 'digest' default for interests.

    Args:
        profile_def: Profile definition with optional digest (ProfileDigestConfig)

    Returns:
        Delivery mode string: 'none', 'dm', 'digest', or 'both'
    """
    # For interest profiles, default to digest (batched delivery)
    # Check if profile has explicit digest configuration
    if profile_def.digest is not None and hasattr(profile_def.digest, "schedules"):
        schedules = profile_def.digest.schedules
        if schedules and len(schedules) > 0:
            # Use first schedule's delivery mode as recommendation
            first_schedule = schedules[0]
            if hasattr(first_schedule, "mode") and first_schedule.mode is not None:
                mode = first_schedule.mode
                # Handle DeliveryMode enum or string
                try:
                    # Try to access .value (enum)
                    return str(mode.value).lower()  # type: ignore
                except AttributeError:
                    # Fallback to string conversion
                    return str(mode).lower()

    # Default to digest for interest profiles (unlike alerts which default to DM)
    return "digest"
