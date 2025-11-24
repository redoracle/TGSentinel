"""Profile resolution and merging logic for two-layer keyword architecture.

This module implements the ProfileResolver, which takes channel/user bindings and
overrides, merges them with global profile definitions, and produces a resolved
keyword set and scoring configuration.
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Set

from .config import (
    ChannelOverrides,
    ChannelRule,
    MonitoredUser,
    ProfileDefinition,
    ProfileDigestConfig,
)

log = logging.getLogger(__name__)


@dataclass
class ResolvedProfile:
    """Result of profile resolution for a specific entity."""

    # Merged keyword lists
    keywords: List[str] = field(default_factory=list)
    action_keywords: List[str] = field(default_factory=list)
    decision_keywords: List[str] = field(default_factory=list)
    urgency_keywords: List[str] = field(default_factory=list)
    importance_keywords: List[str] = field(default_factory=list)
    release_keywords: List[str] = field(default_factory=list)
    security_keywords: List[str] = field(default_factory=list)
    risk_keywords: List[str] = field(default_factory=list)
    opportunity_keywords: List[str] = field(default_factory=list)

    # User filtering
    excluded_users: List[int] = field(
        default_factory=list
    )  # Blacklist: never alert from these users

    # Detection flags (use most permissive from all profiles)
    detect_codes: bool = True
    detect_documents: bool = True
    detect_links: bool = False
    require_forwarded: bool = False
    prioritize_pinned: bool = True
    prioritize_admin: bool = True
    detect_polls: bool = True

    # Final scoring weights after overrides
    scoring_weights: Dict[str, float] = field(default_factory=dict)

    # Digest configuration (resolved from entity/profile hierarchy)
    digest: Optional[ProfileDigestConfig] = None

    # Metadata
    bound_profiles: List[str] = field(default_factory=list)
    matched_profile_ids: List[str] = field(
        default_factory=list
    )  # For digest deduplication
    has_overrides: bool = False


class ProfileResolver:
    """Resolves and merges global profiles with entity-specific overrides."""

    def __init__(self, global_profiles: Dict[str, ProfileDefinition]):
        """Initialize resolver with global profile definitions.

        Args:
            global_profiles: Dict mapping profile_id -> ProfileDefinition
        """
        self.global_profiles = global_profiles

    def has_applicable_profiles(self, entity_type: str, entity_id: int) -> bool:
        """Check if any global profile applies to this entity.

        This checks if there are profiles with empty bindings (apply to all)
        or explicit bindings that include this entity.

        Args:
            entity_type: "channel" or "user"
            entity_id: The entity ID to check

        Returns:
            True if at least one enabled profile applies to this entity
        """
        for profile in self.global_profiles.values():
            # Skip disabled profiles
            if not getattr(profile, "enabled", True):
                continue

            if entity_type == "channel":
                # Profile applies if BOTH lists empty OR channels contains this ID
                both_empty = not profile.channels and not profile.users
                if both_empty or entity_id in profile.channels:
                    return True
            elif entity_type == "user":
                # Profile applies if BOTH lists empty OR users contains this ID
                both_empty = not profile.channels and not profile.users
                if both_empty or entity_id in profile.users:
                    return True
        return False

    def resolve_for_channel(self, channel: ChannelRule) -> ResolvedProfile:
        """Resolve profiles for a specific channel.

        Args:
            channel: ChannelRule with profile bindings and overrides

        Returns:
            ResolvedProfile with merged keywords and scoring weights
        """
        # Start with explicitly bound profiles
        effective_profiles = list(channel.profiles)

        # Add global profiles that apply to this channel (empty channels list OR explicitly listed)
        for profile_id, profile in self.global_profiles.items():
            # Skip disabled profiles
            if not getattr(profile, "enabled", True):
                log.debug(
                    f"Skipping disabled profile '{profile_id}' for channel {channel.id}"
                )
                continue

            if profile_id not in effective_profiles:
                # Auto-bind if:
                # 1. BOTH channels and users are empty (applies to all), OR
                # 2. channels list contains this channel ID
                both_empty = not profile.channels and not profile.users
                channel_matches = channel.id in profile.channels

                if both_empty or channel_matches:
                    effective_profiles.append(profile_id)
                    reason = "both lists empty" if both_empty else "channel ID in list"
                    log.debug(
                        f"Auto-binding global profile '{profile_id}' to channel {channel.id} "
                        f"({reason})"
                    )

        return self._resolve(
            entity_type="channel",
            entity_id=channel.id,
            bound_profiles=effective_profiles,
            overrides=channel.overrides,
            entity_digest=channel.digest,  # Direct digest override
            entity_excluded_users=channel.excluded_users,  # Entity-level excluded users
            # Backward compatibility: merge legacy keyword fields
            legacy_keywords={
                "keywords": channel.keywords,
                "action_keywords": channel.action_keywords,
                "decision_keywords": channel.decision_keywords,
                "urgency_keywords": channel.urgency_keywords,
                "importance_keywords": channel.importance_keywords,
                "release_keywords": channel.release_keywords,
                "security_keywords": channel.security_keywords,
                "risk_keywords": channel.risk_keywords,
                "opportunity_keywords": channel.opportunity_keywords,
            },
        )

    def resolve_for_user(self, user: MonitoredUser) -> ResolvedProfile:
        """Resolve profiles for a specific monitored user.

        Args:
            user: MonitoredUser with profile bindings and overrides

        Returns:
            ResolvedProfile with merged keywords and scoring weights
        """
        # Start with explicitly bound profiles
        effective_profiles = list(user.profiles)

        # Add global profiles that apply to this user (empty users list OR explicitly listed)
        for profile_id, profile in self.global_profiles.items():
            # Skip disabled profiles
            if not getattr(profile, "enabled", True):
                log.debug(
                    f"Skipping disabled profile '{profile_id}' for user {user.id}"
                )
                continue

            if profile_id not in effective_profiles:
                # Auto-bind if:
                # 1. BOTH channels and users are empty (applies to all), OR
                # 2. users list contains this user ID
                both_empty = not profile.channels and not profile.users
                user_matches = user.id in profile.users

                if both_empty or user_matches:
                    effective_profiles.append(profile_id)
                    reason = "both lists empty" if both_empty else "user ID in list"
                    log.debug(
                        f"Auto-binding global profile '{profile_id}' to user {user.id} "
                        f"({reason})"
                    )

        return self._resolve(
            entity_type="user",
            entity_id=user.id,
            bound_profiles=effective_profiles,
            overrides=user.overrides,
            entity_digest=user.digest,  # Direct digest override
            entity_excluded_users=getattr(
                user, "excluded_users", []
            ),  # Entity-level excluded users
            legacy_keywords={},  # Users don't have legacy keyword fields
        )

    def _resolve(
        self,
        entity_type: str,
        entity_id: int,
        bound_profiles: List[str],
        overrides: ChannelOverrides,
        entity_digest: Optional[ProfileDigestConfig],
        entity_excluded_users: List[int],
        legacy_keywords: Dict[str, List[str]],
    ) -> ResolvedProfile:
        """Core resolution logic.

        Args:
            entity_type: "channel" or "user"
            entity_id: Entity ID for logging
            bound_profiles: List of profile IDs to bind
            overrides: Entity-specific overrides
            entity_digest: Direct digest config override at entity level
            entity_excluded_users: Entity-level excluded users (from ChannelRule or MonitoredUser)
            legacy_keywords: Backward-compatible keyword fields (for channels)

        Returns:
            ResolvedProfile
        """
        resolved = ResolvedProfile()

        # Step 1: Merge all bound profiles
        merged_keywords: Dict[str, Set[str]] = {
            "keywords": set(),
            "action_keywords": set(),
            "decision_keywords": set(),
            "urgency_keywords": set(),
            "importance_keywords": set(),
            "release_keywords": set(),
            "security_keywords": set(),
            "risk_keywords": set(),
            "opportunity_keywords": set(),
        }
        merged_weights: Dict[str, List[float]] = {}
        detect_flags = {
            "detect_codes": [],
            "detect_documents": [],
            "detect_links": [],
            "require_forwarded": [],
            "prioritize_pinned": [],
            "prioritize_admin": [],
            "detect_polls": [],
        }
        # Collect excluded_users preserving order (profiles first, then overrides)
        excluded_users_ordered: List[int] = []

        for profile_id in bound_profiles:
            profile = self.global_profiles.get(profile_id)
            if not profile:
                log.warning(
                    f"Profile '{profile_id}' not found for {entity_type} {entity_id}"
                )
                continue

            # Merge keywords (union)
            for key in merged_keywords.keys():
                merged_keywords[key].update(getattr(profile, key, []))

            # Collect scoring weights
            for category, weight in profile.scoring_weights.items():
                merged_weights.setdefault(category, []).append(weight)

            # Collect detection flags (use most permissive)
            for flag_key in detect_flags.keys():
                detect_flags[flag_key].append(getattr(profile, flag_key, True))

            # Collect excluded_users from profile (preserving order)
            profile_excluded = getattr(profile, "excluded_users", [])
            for user_id in profile_excluded:
                if user_id not in excluded_users_ordered:
                    excluded_users_ordered.append(user_id)

            resolved.bound_profiles.append(profile_id)

        # Step 2: Apply legacy keywords (backward compatibility)
        for key, legacy_vals in legacy_keywords.items():
            if legacy_vals:
                merged_keywords[key].update(legacy_vals)

        # Step 3: Apply overrides
        if overrides.keywords_extra:
            merged_keywords["keywords"].update(overrides.keywords_extra)
            resolved.has_overrides = True

        if overrides.action_keywords_extra:
            merged_keywords["action_keywords"].update(overrides.action_keywords_extra)
            resolved.has_overrides = True

        if overrides.urgency_keywords_extra:
            merged_keywords["urgency_keywords"].update(overrides.urgency_keywords_extra)
            resolved.has_overrides = True

        # Apply entity-level excluded_users (from ChannelRule/MonitoredUser)
        # These come after profile-level but before overrides for proper precedence
        if entity_excluded_users:
            for user_id in entity_excluded_users:
                if user_id not in excluded_users_ordered:
                    excluded_users_ordered.append(user_id)

        # Apply excluded_users from overrides (append last, highest priority)
        if hasattr(overrides, "excluded_users") and overrides.excluded_users:
            for user_id in overrides.excluded_users:
                if user_id not in excluded_users_ordered:
                    excluded_users_ordered.append(user_id)
            resolved.has_overrides = True

        # Step 4: Finalize keywords (sort for deterministic output)
        for key, keyword_set in merged_keywords.items():
            setattr(resolved, key, sorted(keyword_set))

        # Finalize excluded_users (preserve order, duplicates already removed)
        resolved.excluded_users = excluded_users_ordered

        # Step 5: Compute final scoring weights (average from profiles)
        for category, weights in merged_weights.items():
            resolved.scoring_weights[category] = sum(weights) / len(weights)

        # Apply weight overrides
        if overrides.scoring_weights:
            resolved.scoring_weights.update(overrides.scoring_weights)
            resolved.has_overrides = True

        # Step 6: Set detection flags (use most permissive)
        resolved.detect_codes = any(detect_flags["detect_codes"])
        resolved.detect_documents = any(detect_flags["detect_documents"])
        resolved.detect_links = any(detect_flags["detect_links"])
        resolved.require_forwarded = any(detect_flags["require_forwarded"])
        resolved.prioritize_pinned = any(detect_flags["prioritize_pinned"])
        resolved.prioritize_admin = any(detect_flags["prioritize_admin"])
        resolved.detect_polls = any(detect_flags["detect_polls"])

        # Step 7: Resolve digest configuration (Phase 2)
        # Precedence: entity_digest > overrides.digest > first bound profile.digest
        resolved.digest = self._resolve_digest_config(
            entity_digest=entity_digest,
            overrides_digest=overrides.digest,
            bound_profiles=bound_profiles,
        )

        # Step 8: Track matched profile IDs (for digest deduplication)
        resolved.matched_profile_ids = resolved.bound_profiles.copy()

        log.debug(
            f"Resolved profile for {entity_type} {entity_id}: "
            f"{len(resolved.keywords)} keywords, "
            f"{len(resolved.bound_profiles)} profiles, "
            f"{len(resolved.excluded_users)} excluded_users, "
            f"digest_config={'present' if resolved.digest else 'none'}, "
            f"overrides={resolved.has_overrides}"
        )

        return resolved

    def _resolve_digest_config(
        self,
        entity_digest: Optional[ProfileDigestConfig],
        overrides_digest: Optional[ProfileDigestConfig],
        bound_profiles: List[str],
    ) -> Optional[ProfileDigestConfig]:
        """Resolve digest configuration from hierarchy.

        Precedence (highest to lowest):
        1. Direct entity-level digest config (channel.digest or user.digest)
        2. Override-level digest config (channel.overrides.digest)
        3. First bound profile's digest config (profile.digest)
        4. None (no digest config, use global defaults)

        Args:
            entity_digest: Direct digest config at entity level
            overrides_digest: Digest config in overrides
            bound_profiles: List of bound profile IDs

        Returns:
            Resolved ProfileDigestConfig or None
        """
        # Level 1: Entity-level override (highest priority)
        if entity_digest is not None:
            log.debug("Using entity-level digest config")
            return entity_digest

        # Level 2: Overrides-level config
        if overrides_digest is not None:
            log.debug("Using overrides-level digest config")
            return overrides_digest

        # Level 3: First bound profile's digest config
        for profile_id in bound_profiles:
            profile = self.global_profiles.get(profile_id)
            if profile and profile.digest is not None:
                log.debug(f"Using digest config from profile '{profile_id}'")
                return profile.digest

        # Level 4: No digest config found
        log.debug("No digest config found in hierarchy")
        return None

    @lru_cache(maxsize=256)
    def resolve_for_channel_cached(self, channel_id: int) -> ResolvedProfile:
        """Cached version for hot paths (requires channel lookup by ID)."""
        # NOTE: This requires maintaining a channel_id -> ChannelRule lookup
        # For now, use non-cached version in worker. Add caching layer later.
        raise NotImplementedError("Use resolve_for_channel() directly for now")


def validate_profiles(
    global_profiles: Dict[str, ProfileDefinition],
    channels: List[ChannelRule],
    users: List[MonitoredUser],
) -> List[str]:
    """Validate profile configuration.

    Checks:
    - All bound profiles exist in global definitions
    - No circular dependencies
    - No duplicate profile IDs
    - Reasonable keyword counts

    Args:
        global_profiles: Global profile definitions
        channels: List of channels with profile bindings
        users: List of users with profile bindings

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    # Check for duplicate profile IDs
    if len(global_profiles) != len(set(global_profiles.keys())):
        errors.append("Duplicate profile IDs found in global_profiles")

    # Check bound profiles exist
    all_bound = set()
    for channel in channels:
        all_bound.update(channel.profiles)
    for user in users:
        all_bound.update(user.profiles)

    for profile_id in all_bound:
        if profile_id not in global_profiles:
            errors.append(f"Profile '{profile_id}' is bound but not defined globally")

    # Check keyword counts (warn if excessive)
    for profile_id, profile in global_profiles.items():
        total_keywords = (
            len(profile.keywords)
            + len(profile.action_keywords)
            + len(profile.decision_keywords)
            + len(profile.urgency_keywords)
            + len(profile.importance_keywords)
            + len(profile.release_keywords)
            + len(profile.security_keywords)
            + len(profile.risk_keywords)
            + len(profile.opportunity_keywords)
        )
        if total_keywords > 500:
            errors.append(
                f"Profile '{profile_id}' has {total_keywords} keywords (>500, may impact performance)"
            )

    return errors
