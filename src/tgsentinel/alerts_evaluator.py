"""Heuristic alert evaluation pipeline.

This module handles keyword-based scoring for alert profiles, enforcing the constraint:
"Alerts = heuristic analysis + keyword-scoring"

Separates alert (keyword) logic from interest (semantic) logic per the taxonomy:
- Semantic Type: alert_keyword
- Target Entity: profile (during evaluation) → feed (after storage)
- Delivery: determined by delivery_orchestrator based on profile config

Related architectural constraints:
- Constraint 2 (Concurrency): All functions are sync; called from async worker context
- Constraint 4 (Structured Logging): Uses handler tag [ALERTS-EVALUATOR]
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import AppCfg, ProfileDefinition

log = logging.getLogger(__name__)


@dataclass
class AlertEvaluationResult:
    """Result of heuristic alert evaluation.

    Attributes:
        keyword_score: Normalized heuristic score (0.0-1.0+)
        matched_profile_ids: List of alert profile IDs that matched
        triggers: Comma-separated list of matched keywords/heuristics
        trigger_annotations: Dict with detailed match metadata
        should_alert: True if score meets minimum threshold for any profile
        delivery_recommendations: Per-profile delivery mode suggestions
    """

    keyword_score: float
    matched_profile_ids: List[str]
    triggers: str
    trigger_annotations: Dict[str, Any]
    should_alert: bool
    delivery_recommendations: Dict[str, str]  # profile_id -> delivery_mode


def evaluate_alert_profiles(
    message_text: str,
    chat_title: str,
    sender_name: str,
    sender_id: int,
    heuristic_result: Any,  # HeuristicResult from run_heuristics
    resolved_profile: Any,  # ResolvedProfile
    cfg: AppCfg,
    request_id: Optional[str] = None,
) -> AlertEvaluationResult:
    """Evaluate message against alert profiles using keyword-based scoring.

    This function implements the "Alerts = heuristic analysis + keyword-scoring" rule.
    It only processes profiles WITHOUT positive_samples (keyword-based profiles).

    Args:
        message_text: Message content for keyword matching
        chat_title: Chat/channel title for context
        sender_name: Sender display name
        sender_id: Telegram sender ID
        heuristic_result: Output from run_heuristics with pre_score, reasons, etc.
        resolved_profile: Profile resolution result with matched_profile_ids
        cfg: Application configuration with global_profiles
        request_id: Optional correlation ID for logging

    Returns:
        AlertEvaluationResult with keyword scores and alert recommendations

    Logs:
        - [ALERTS-EVALUATOR] INFO: Profile matches and threshold comparisons
        - [ALERTS-EVALUATOR] DEBUG: Detailed scoring breakdown per profile
    """
    extra = (
        {"request_id": request_id, "semantic_type": "alert_keyword"}
        if request_id
        else {}
    )

    # Base keyword score from heuristics pipeline
    keyword_score = heuristic_result.pre_score if heuristic_result else 0.0
    triggers = (
        ", ".join(heuristic_result.reasons)
        if heuristic_result and heuristic_result.reasons
        else ""
    )
    annotations_dict = (
        heuristic_result.trigger_annotations.copy()
        if heuristic_result and heuristic_result.trigger_annotations
        else {}
    )

    # Filter to alert profiles only (those WITHOUT positive_samples = semantic config)
    alert_profile_ids = []
    delivery_recommendations = {}
    should_alert = False

    log.info(
        "[ALERTS-EVALUATOR] Starting evaluation: resolved_profile=%s, matched_profile_ids=%s, keyword_score=%.3f",
        "present" if resolved_profile else "None",
        resolved_profile.matched_profile_ids if resolved_profile else [],
        keyword_score,
        extra=extra,
    )

    if (
        resolved_profile
        and resolved_profile.matched_profile_ids
        and cfg.global_profiles
    ):
        for pid in resolved_profile.matched_profile_ids:
            profile_def = cfg.global_profiles.get(pid)
            if not profile_def:
                log.warning(
                    "[ALERTS-EVALUATOR] Profile %s in matched_profile_ids but not found in global_profiles",
                    pid,
                    extra=extra,
                )
                continue

            # Check if this is a keyword-based profile (no semantic samples)
            is_semantic = (
                hasattr(profile_def, "positive_samples")
                and profile_def.positive_samples
            )

            log.info(
                "[ALERTS-EVALUATOR] Checking profile %s: is_semantic=%s, has_positive_samples=%s",
                pid,
                is_semantic,
                (
                    hasattr(profile_def, "positive_samples")
                    and bool(profile_def.positive_samples)
                    if hasattr(profile_def, "positive_samples")
                    else "no_attr"
                ),
                extra=extra,
            )

            if is_semantic:
                # Skip semantic profiles - they belong to interests_evaluator
                log.info(
                    "[ALERTS-EVALUATOR] Skipping semantic profile %s (has positive_samples)",
                    pid,
                    extra=extra,
                )
                continue

            # This is an alert profile (keyword-based)
            # Check if score meets profile's minimum threshold
            min_score = getattr(profile_def, "min_score", 0.0)
            if keyword_score >= min_score:
                # Only add to matched list if threshold is met
                alert_profile_ids.append(pid)
                should_alert = True
                log.info(
                    "[ALERTS-EVALUATOR] Profile %s matched: keyword_score=%.3f >= min_score=%.2f",
                    pid,
                    keyword_score,
                    min_score,
                    extra=extra,
                )

                # Extract delivery mode from profile's digest schedules or default
                delivery_mode = _extract_delivery_mode(profile_def)
                delivery_recommendations[pid] = delivery_mode
            else:
                log.info(
                    "[ALERTS-EVALUATOR] Profile %s below threshold: keyword_score=%.3f < min_score=%.2f",
                    pid,
                    keyword_score,
                    min_score,
                    extra=extra,
                )

    # Annotate with alert metadata
    annotations_dict["semantic_type"] = "alert_keyword"
    annotations_dict["alert_profile_ids"] = alert_profile_ids

    log.info(
        "[ALERTS-EVALUATOR] Evaluation complete: matched=%d, should_alert=%s, keyword_score=%.3f",
        len(alert_profile_ids),
        should_alert,
        keyword_score,
        extra=extra,
    )

    return AlertEvaluationResult(
        keyword_score=keyword_score,
        matched_profile_ids=alert_profile_ids,
        triggers=triggers,
        trigger_annotations=annotations_dict,
        should_alert=should_alert,
        delivery_recommendations=delivery_recommendations,
    )


def _extract_delivery_mode(profile_def: ProfileDefinition) -> str:
    """Extract delivery mode from profile configuration.

    Checks digest schedules for delivery settings, falling back to 'dm' default.

    Args:
        profile_def: Profile definition with optional digest (ProfileDigestConfig)

    Returns:
        Delivery mode string: 'none', 'dm', 'digest', or 'both'
    """
    # Check if profile has digest configuration
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

    # Default to immediate DM for alert profiles
    return "dm"
