"""Utility modules for TG Sentinel UI.

This package re-exports utilities from utils_legacy.py
along with new modular utilities for backward compatibility.
"""

# Import from legacy utils_legacy.py module (sibling to utils/ package)
try:
    from ..utils_legacy import (
        format_timestamp,
        truncate,
        mask_phone,
        normalize_phone,
        format_display_phone,
        normalize_tags,
        fallback_username,
        fallback_avatar,
    )
except ImportError:
    # Fallback for absolute imports
    from utils_legacy import (
        format_timestamp,
        truncate,
        mask_phone,
        normalize_phone,
        format_display_phone,
        normalize_tags,
        fallback_username,
        fallback_avatar,
    )

# Import from new modular utilities
from .serializers import serialize_channels, serialize_entity
from .validators import (
    validate_config_payload,
    validate_profile_structure,
    validate_alert_rule,
)

__all__ = [
    # Legacy utils
    "format_timestamp",
    "truncate",
    "mask_phone",
    "normalize_phone",
    "format_display_phone",
    "normalize_tags",
    "fallback_username",
    "fallback_avatar",
    # New modular utils
    "serialize_channels",
    "serialize_entity",
    "validate_config_payload",
    "validate_profile_structure",
    "validate_alert_rule",
]
