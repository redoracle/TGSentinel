"""
Profile auto-tuning based on aggregated user feedback.

Phase 1: Threshold adjustment only (no sample augmentation).

This module applies threshold adjustments atomically to prevent YAML
corruption and maintains full audit trail in database.
"""

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


@dataclass
class ThresholdAdjustment:
    """Record of a threshold adjustment."""

    profile_id: str
    profile_type: str  # 'interest' or 'alert'
    adjustment_type: str  # 'threshold' or 'min_score'
    old_value: float
    new_value: float
    adjustment_reason: str
    feedback_count: int
    trigger_chat_id: Optional[int] = None
    trigger_msg_id: Optional[int] = None


class ProfileTuner:
    """Handles profile threshold adjustments based on aggregated feedback."""

    # Safety caps
    MAX_THRESHOLD_INTEREST = 0.95
    MAX_THRESHOLD_ALERT = 10.0

    def __init__(self, engine: Engine, config_dir: Path):
        self.engine = engine
        self.config_dir = config_dir

    def apply_threshold_adjustment(
        self,
        profile_id: str,
        profile_type: str,
        delta: float,
        reason: str = "negative_feedback",
        feedback_count: int = 1,
        trigger_chat_id: Optional[int] = None,
        trigger_msg_id: Optional[int] = None,
        dry_run: bool = False,
    ) -> Optional[ThresholdAdjustment]:
        """
        Apply threshold adjustment to a profile.

        Args:
            profile_id: Profile identifier (e.g., "3000")
            profile_type: "interest" or "alert"
            delta: Amount to adjust (positive = raise threshold)
            reason: "negative_feedback" | "manual" | "auto_tune"
            feedback_count: How many feedbacks triggered this
            trigger_chat_id: Optional message that triggered adjustment
            trigger_msg_id: Optional message that triggered adjustment
            dry_run: If True, don't persist changes

        Returns:
            ThresholdAdjustment record or None if adjustment failed
        """
        # Load profile YAML
        profiles_path = self.config_dir / f"profiles_{profile_type}.yml"
        if not profiles_path.exists():
            log.error(f"[TUNER] Profile file not found: {profiles_path}")
            return None

        with open(profiles_path, "r", encoding="utf-8") as f:
            profiles = yaml.safe_load(f) or {}

        if profile_id not in profiles:
            log.error(f"[TUNER] Profile {profile_id} not found in {profiles_path}")
            return None

        profile = profiles[profile_id]

        # Determine threshold field name
        threshold_field = "threshold" if profile_type == "interest" else "min_score"
        old_threshold = profile.get(
            threshold_field, 0.42 if profile_type == "interest" else 1.0
        )
        new_threshold = old_threshold + delta

        # Apply caps
        max_threshold = (
            self.MAX_THRESHOLD_INTEREST
            if profile_type == "interest"
            else self.MAX_THRESHOLD_ALERT
        )
        new_threshold = min(new_threshold, max_threshold)
        new_threshold = max(new_threshold, 0.0)  # No negative thresholds

        # Round to 2 decimal places
        new_threshold = round(new_threshold, 2)

        if new_threshold == old_threshold:
            log.info(
                f"[TUNER] No adjustment needed for {profile_id}: "
                f"threshold already at {old_threshold}"
            )
            return None

        adjustment = ThresholdAdjustment(
            profile_id=profile_id,
            profile_type=profile_type,
            adjustment_type=threshold_field,
            old_value=old_threshold,
            new_value=new_threshold,
            adjustment_reason=reason,
            feedback_count=feedback_count,
            trigger_chat_id=trigger_chat_id,
            trigger_msg_id=trigger_msg_id,
        )

        if dry_run:
            log.info(
                f"[TUNER] [DRY RUN] Would adjust {profile_id} {threshold_field}: "
                f"{old_threshold} → {new_threshold}"
            )
            return adjustment

        # Update profile in memory
        profile[threshold_field] = new_threshold
        profiles[profile_id] = profile

        # Atomic save to YAML
        try:
            self._save_profiles_atomic(profiles_path, profiles)
        except Exception as e:
            log.error(f"[TUNER] Failed to save profiles: {e}", exc_info=True)
            return None

        # Record adjustment in database
        self._record_adjustment(adjustment)

        # Structured logging
        self._log_adjustment(adjustment)

        log.info(
            f"[TUNER] ✓ Adjusted {profile_id} {threshold_field}: "
            f"{old_threshold} → {new_threshold} (reason: {reason})"
        )

        return adjustment

    def _save_profiles_atomic(self, profiles_path: Path, profiles: dict):
        """
        Atomically save profiles to YAML using temp file + rename.

        This prevents corruption from partial writes or crashes.
        """
        # Write to temp file first
        temp_fd, temp_path = tempfile.mkstemp(
            suffix=".yml",
            prefix=f"profiles_{profiles_path.stem}_",
            dir=profiles_path.parent,
        )

        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(profiles, f, default_flow_style=False, sort_keys=False)

            # Atomic rename (POSIX guarantees atomicity)
            os.replace(temp_path, profiles_path)

            log.debug(f"[TUNER] ✓ Atomically saved {profiles_path}")

        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            log.error(f"[TUNER] Failed to save {profiles_path}: {e}", exc_info=True)
            raise

    def _load_profiles(self, profiles_path: Path) -> dict:
        """
        Load profiles from YAML file.

        Args:
            profiles_path: Path to profiles YAML file

        Returns:
            Dictionary of profiles
        """
        if not profiles_path.exists():
            log.error(f"[TUNER] Profile file not found: {profiles_path}")
            return {}

        with open(profiles_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _record_adjustment(self, adjustment: ThresholdAdjustment):
        """Record adjustment in database for audit trail."""
        try:
            with self.engine.begin() as con:
                con.execute(
                    text(
                        """
                        INSERT INTO profile_adjustments(
                            profile_id, profile_type, adjustment_type,
                            old_value, new_value, adjustment_reason,
                            feedback_count, trigger_chat_id, trigger_msg_id
                        ) VALUES(
                            :pid, :ptype, :atype,
                            :old, :new, :reason,
                            :count, :chat_id, :msg_id
                        )
                    """
                    ),
                    {
                        "pid": adjustment.profile_id,
                        "ptype": adjustment.profile_type,
                        "atype": adjustment.adjustment_type,
                        "old": adjustment.old_value,
                        "new": adjustment.new_value,
                        "reason": adjustment.adjustment_reason,
                        "count": adjustment.feedback_count,
                        "chat_id": adjustment.trigger_chat_id,
                        "msg_id": adjustment.trigger_msg_id,
                    },
                )
            log.debug(
                f"[TUNER] ✓ Recorded adjustment in database: {adjustment.profile_id}"
            )
        except Exception as e:
            log.error(f"[TUNER] Failed to record adjustment: {e}", exc_info=True)

    def _log_adjustment(self, adjustment: ThresholdAdjustment):
        """Structured logging for adjustment (enables debugging)."""
        log.info(
            "[AUTO-TUNING] Profile adjustment applied",
            extra={
                "profile_id": adjustment.profile_id,
                "profile_type": adjustment.profile_type,
                "adjustment_type": adjustment.adjustment_type,
                "old_value": round(adjustment.old_value, 3),
                "new_value": round(adjustment.new_value, 3),
                "delta": round(adjustment.new_value - adjustment.old_value, 3),
                "reason": adjustment.adjustment_reason,
                "feedback_count": adjustment.feedback_count,
                "trigger_chat_id": adjustment.trigger_chat_id,
                "trigger_msg_id": adjustment.trigger_msg_id,
            },
        )

    def get_adjustment_history(
        self, profile_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get adjustment history for a profile.

        Returns:
            List of adjustment records (most recent first)
        """
        try:
            with self.engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT
                            adjustment_type,
                            old_value,
                            new_value,
                            adjustment_reason,
                            feedback_count,
                            created_at
                        FROM profile_adjustments
                        WHERE profile_id = :pid
                        ORDER BY created_at DESC
                        LIMIT :limit
                    """
                    ),
                    {"pid": profile_id, "limit": limit},
                )

                return [
                    {
                        "adjustment_type": row[0],
                        "old_value": row[1],
                        "new_value": row[2],
                        "adjustment_reason": row[3],
                        "feedback_count": row[4],
                        "created_at": row[5],
                    }
                    for row in result
                ]
        except Exception as e:
            log.error(f"[TUNER] Failed to get adjustment history: {e}", exc_info=True)
            return []

    # ===== Phase 2: Sample Management Methods =====

    def add_to_pending_samples(
        self,
        profile_id: str,
        profile_type: str,
        sample_category: str,
        sample_text: str,
        semantic_score: float,
        feedback_chat_id: Optional[int] = None,
        feedback_msg_id: Optional[int] = None,
        sample_weight: float = 0.4,
    ) -> bool:
        """
        Add a sample to the pending buffer.

        Samples remain in pending until explicitly committed or rolled back.

        Args:
            profile_id: Profile identifier
            profile_type: 'interest' or 'alert'
            sample_category: 'positive' or 'negative'
            sample_text: Message text to add as sample
            semantic_score: Score that triggered this addition
            feedback_chat_id: Source chat ID
            feedback_msg_id: Source message ID
            sample_weight: Weight for this sample (default 0.4 for feedback)

        Returns:
            True if added successfully
        """
        try:
            # Check for duplicates in pending
            if self._is_duplicate_pending_sample(
                profile_id, sample_category, sample_text
            ):
                log.info(
                    f"[TUNER] Sample already in pending buffer for {profile_id}/{sample_category}, skipping"
                )
                return False

            # Record in database
            self._record_sample_addition(
                profile_id=profile_id,
                profile_type=profile_type,
                sample_category=sample_category,
                sample_text=sample_text,
                sample_weight=sample_weight,
                sample_status="pending",
                feedback_chat_id=feedback_chat_id,
                feedback_msg_id=feedback_msg_id,
                semantic_score=semantic_score,
            )

            # Add to pending buffer in YAML
            profiles_path = self.config_dir / f"profiles_{profile_type}.yml"
            profiles = self._load_profiles(profiles_path)

            if profile_id not in profiles:
                log.error(f"[TUNER] Profile {profile_id} not found in {profiles_path}")
                return False

            profile = profiles[profile_id]

            # Ensure pending fields exist
            pending_key = f"pending_{sample_category}_samples"
            if pending_key not in profile:
                profile[pending_key] = []

            # Add to pending buffer
            profile[pending_key].append(sample_text)

            # Atomic save
            self._save_profiles_atomic(profiles_path, profiles)

            log.info(
                f"[TUNER] Added sample to pending buffer: {profile_id}/{sample_category} "
                f"(score={semantic_score:.3f}, pending_count={len(profile[pending_key])})"
            )
            return True

        except Exception as e:
            log.error(
                f"[TUNER] Failed to add pending sample for {profile_id}: {e}",
                exc_info=True,
            )
            return False

    def commit_pending_samples(
        self, profile_id: str, profile_type: str, sample_category: str
    ) -> int:
        """
        Commit pending samples to feedback samples.

        Moves samples from pending_*_samples to feedback_*_samples and
        updates database status to 'committed'.

        Args:
            profile_id: Profile identifier
            profile_type: 'interest' or 'alert'
            sample_category: 'positive' or 'negative'

        Returns:
            Number of samples committed
        """
        try:
            profiles_path = self.config_dir / f"profiles_{profile_type}.yml"
            profiles = self._load_profiles(profiles_path)

            if profile_id not in profiles:
                log.error(f"[TUNER] Profile {profile_id} not found")
                return 0

            profile = profiles[profile_id]

            pending_key = f"pending_{sample_category}_samples"
            feedback_key = f"feedback_{sample_category}_samples"

            # Get pending samples
            pending_samples = profile.get(pending_key, [])
            if not pending_samples:
                log.info(
                    f"[TUNER] No pending {sample_category} samples for {profile_id}"
                )
                return 0

            # Ensure feedback list exists
            if feedback_key not in profile:
                profile[feedback_key] = []

            # Check feedback sample cap
            auto_tuning = profile.get("auto_tuning", {})
            max_feedback_samples = auto_tuning.get("max_feedback_samples", 20)

            feedback_samples = profile[feedback_key]
            available_slots = max_feedback_samples - len(feedback_samples)

            if available_slots <= 0:
                log.warning(
                    f"[TUNER] Feedback sample cap reached for {profile_id}/{sample_category} "
                    f"(max={max_feedback_samples})"
                )
                return 0

            # Move samples (respecting cap)
            samples_to_commit = pending_samples[:available_slots]
            profile[feedback_key].extend(samples_to_commit)
            profile[pending_key] = pending_samples[available_slots:]

            # Atomic save
            self._save_profiles_atomic(profiles_path, profiles)

            # Update database status
            committed_count = len(samples_to_commit)
            self._update_sample_status(
                profile_id=profile_id,
                sample_category=sample_category,
                old_status="pending",
                new_status="committed",
                limit=committed_count,
            )

            log.info(
                f"[TUNER] Committed {committed_count} {sample_category} samples for {profile_id} "
                f"(feedback_count={len(profile[feedback_key])}/{max_feedback_samples})"
            )

            return committed_count

        except Exception as e:
            log.error(
                f"[TUNER] Failed to commit pending samples for {profile_id}: {e}",
                exc_info=True,
            )
            return 0

    def rollback_pending_samples(
        self, profile_id: str, profile_type: str, sample_category: str
    ) -> int:
        """
        Rollback (discard) pending samples without committing.

        Args:
            profile_id: Profile identifier
            profile_type: 'interest' or 'alert'
            sample_category: 'positive' or 'negative'

        Returns:
            Number of samples rolled back
        """
        try:
            profiles_path = self.config_dir / f"profiles_{profile_type}.yml"
            profiles = self._load_profiles(profiles_path)

            if profile_id not in profiles:
                log.error(f"[TUNER] Profile {profile_id} not found")
                return 0

            profile = profiles[profile_id]

            pending_key = f"pending_{sample_category}_samples"
            pending_samples = profile.get(pending_key, [])

            if not pending_samples:
                log.info(
                    f"[TUNER] No pending {sample_category} samples to rollback for {profile_id}"
                )
                return 0

            rollback_count = len(pending_samples)

            # Clear pending buffer
            profile[pending_key] = []

            # Atomic save
            self._save_profiles_atomic(profiles_path, profiles)

            # Update database status
            self._update_sample_status(
                profile_id=profile_id,
                sample_category=sample_category,
                old_status="pending",
                new_status="rolled_back",
            )

            log.info(
                f"[TUNER] Rolled back {rollback_count} {sample_category} samples for {profile_id}"
            )

            return rollback_count

        except Exception as e:
            log.error(
                f"[TUNER] Failed to rollback pending samples for {profile_id}: {e}",
                exc_info=True,
            )
            return 0

    def get_pending_samples(
        self, profile_id: str, profile_type: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get pending samples for a profile.

        Returns:
            Dict with 'positive' and 'negative' keys, each containing list of sample dicts
        """
        try:
            # Get from database (includes metadata)
            with self.engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT
                            sample_category,
                            sample_text,
                            sample_weight,
                            semantic_score,
                            feedback_chat_id,
                            feedback_msg_id,
                            created_at
                        FROM profile_sample_additions
                        WHERE profile_id = :pid
                          AND profile_type = :ptype
                          AND sample_status = 'pending'
                        ORDER BY created_at ASC
                    """
                    ),
                    {"pid": profile_id, "ptype": profile_type},
                )

                samples: Dict[str, List[Dict[str, Any]]] = {
                    "positive": [],
                    "negative": [],
                }

                for row in result:
                    category = row[0]
                    if category in samples:
                        samples[category].append(
                            {
                                "text": row[1],
                                "weight": row[2],
                                "semantic_score": row[3],
                                "chat_id": row[4],
                                "msg_id": row[5],
                                "created_at": row[6],
                            }
                        )

                return samples

        except Exception as e:
            log.error(
                f"[TUNER] Failed to get pending samples for {profile_id}: {e}",
                exc_info=True,
            )
            return {"positive": [], "negative": []}

    # ===== Helper Methods for Phase 2 =====

    def _is_duplicate_pending_sample(
        self, profile_id: str, sample_category: str, sample_text: str
    ) -> bool:
        """Check if sample already exists in pending buffer."""
        try:
            with self.engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM profile_sample_additions
                        WHERE profile_id = :pid
                          AND sample_category = :cat
                          AND sample_text = :text
                          AND sample_status = 'pending'
                    """
                    ),
                    {"pid": profile_id, "cat": sample_category, "text": sample_text},
                )
                count = result.scalar()
                return count is not None and count > 0
        except Exception:
            return False

    def _record_sample_addition(
        self,
        profile_id: str,
        profile_type: str,
        sample_category: str,
        sample_text: str,
        sample_weight: float,
        sample_status: str,
        feedback_chat_id: Optional[int],
        feedback_msg_id: Optional[int],
        semantic_score: float,
    ):
        """Record sample addition in database."""
        try:
            with self.engine.connect() as con:
                con.execute(
                    text(
                        """
                        INSERT INTO profile_sample_additions (
                            profile_id, profile_type, sample_category,
                            sample_text, sample_weight, sample_status,
                            feedback_chat_id, feedback_msg_id, semantic_score
                        ) VALUES (
                            :pid, :ptype, :cat, :text, :weight, :status,
                            :chat_id, :msg_id, :score
                        )
                    """
                    ),
                    {
                        "pid": profile_id,
                        "ptype": profile_type,
                        "cat": sample_category,
                        "text": sample_text,
                        "weight": sample_weight,
                        "status": sample_status,
                        "chat_id": feedback_chat_id,
                        "msg_id": feedback_msg_id,
                        "score": semantic_score,
                    },
                )
                con.commit()
        except Exception as e:
            log.error(f"[TUNER] Failed to record sample addition: {e}", exc_info=True)

    def _update_sample_status(
        self,
        profile_id: str,
        sample_category: str,
        old_status: str,
        new_status: str,
        limit: Optional[int] = None,
    ):
        """Update status of sample additions in database."""
        try:
            with self.engine.connect() as con:
                if limit:
                    # Update only the oldest N samples
                    con.execute(
                        text(
                            """
                            UPDATE profile_sample_additions
                            SET sample_status = :new_status,
                                committed_at = CURRENT_TIMESTAMP
                            WHERE id IN (
                                SELECT id FROM profile_sample_additions
                                WHERE profile_id = :pid
                                  AND sample_category = :cat
                                  AND sample_status = :old_status
                                ORDER BY created_at ASC
                                LIMIT :limit
                            )
                        """
                        ),
                        {
                            "new_status": new_status,
                            "pid": profile_id,
                            "cat": sample_category,
                            "old_status": old_status,
                            "limit": limit,
                        },
                    )
                else:
                    # Update all matching samples
                    con.execute(
                        text(
                            """
                            UPDATE profile_sample_additions
                            SET sample_status = :new_status,
                                committed_at = CURRENT_TIMESTAMP
                            WHERE profile_id = :pid
                              AND sample_category = :cat
                              AND sample_status = :old_status
                        """
                        ),
                        {
                            "new_status": new_status,
                            "pid": profile_id,
                            "cat": sample_category,
                            "old_status": old_status,
                        },
                    )
                con.commit()
        except Exception as e:
            log.error(f"[TUNER] Failed to update sample status: {e}", exc_info=True)

    # ========================================
    # Alert-specific methods
    # ========================================

    def apply_alert_min_score_adjustment(
        self,
        profile_id: str,
        delta: float,
        reason: str = "negative_feedback",
        feedback_count: int = 1,
        trigger_chat_id: Optional[int] = None,
        trigger_msg_id: Optional[int] = None,
        dry_run: bool = False,
    ) -> Optional[ThresholdAdjustment]:
        """
        Apply min_score adjustment to an alert profile.

        This is a convenience wrapper around apply_threshold_adjustment
        specifically for alert profiles.

        Args:
            profile_id: Alert profile identifier
            delta: Amount to adjust min_score (positive = stricter)
            reason: Reason for adjustment
            feedback_count: Number of feedbacks that triggered this
            trigger_chat_id: Optional message that triggered adjustment
            trigger_msg_id: Optional message that triggered adjustment
            dry_run: If True, don't persist changes

        Returns:
            ThresholdAdjustment record or None if adjustment failed
        """
        return self.apply_threshold_adjustment(
            profile_id=profile_id,
            profile_type="alert",
            delta=delta,
            reason=reason,
            feedback_count=feedback_count,
            trigger_chat_id=trigger_chat_id,
            trigger_msg_id=trigger_msg_id,
            dry_run=dry_run,
        )

    def get_alert_feedback_stats(
        self, profile_id: str, days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Get feedback statistics for an alert profile.

        Args:
            profile_id: Alert profile ID
            days: Number of days to look back

        Returns:
            Dict with stats or None if no feedback found
        """
        semantic_type = "alert_keyword"

        try:
            with self.engine.connect() as con:
                result = con.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) as total_feedback,
                            SUM(CASE WHEN f.label = 1 THEN 1 ELSE 0 END) as positive,
                            SUM(CASE WHEN f.label = 0 THEN 1 ELSE 0 END) as negative
                        FROM feedback f
                        JOIN feedback_profiles fp
                            ON f.chat_id = fp.chat_id AND f.msg_id = fp.msg_id
                        WHERE fp.profile_id = :pid
                          AND f.semantic_type = :stype
                          AND f.created_at >= datetime('now', :days_back)
                    """
                    ),
                    {
                        "pid": profile_id,
                        "stype": semantic_type,
                        "days_back": f"-{days} days",
                    },
                )
                row = result.fetchone()

                if not row or row[0] == 0:
                    return None

                total_feedback = row[0]
                positive = row[1] or 0
                negative = row[2] or 0
                approval_rate = (
                    (positive / total_feedback * 100) if total_feedback > 0 else 0.0
                )

                # Get current min_score
                current_min_score = self._get_current_min_score(profile_id)

                return {
                    "profile_id": profile_id,
                    "profile_type": "alert",
                    "total_feedback": total_feedback,
                    "positive_feedback": positive,
                    "negative_feedback": negative,
                    "approval_rate": round(approval_rate, 2),
                    "current_min_score": current_min_score,
                }
        except Exception as e:
            log.error(
                f"[TUNER] Failed to get alert feedback stats for {profile_id}: {e}",
                exc_info=True,
            )
            return None

    def _get_current_min_score(self, profile_id: str) -> float:
        """Get current min_score for an alert profile."""
        profiles_path = self.config_dir / "profiles_alert.yml"
        if not profiles_path.exists():
            return 1.0

        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                profiles = yaml.safe_load(f) or {}

            if profile_id not in profiles:
                return 1.0

            return profiles[profile_id].get("min_score", 1.0)
        except Exception as e:
            log.error(f"[TUNER] Failed to read min_score for {profile_id}: {e}")
            return 1.0
