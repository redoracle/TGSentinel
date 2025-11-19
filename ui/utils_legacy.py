"""Shared utility functions for TG Sentinel UI.

This module contains pure utility functions with no side effects,
focusing on formatting, validation, and string manipulation.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, List


def format_timestamp(ts_str: str) -> str:
    """Format ISO timestamp to human-readable format.

    Args:
        ts_str: ISO format timestamp string

    Returns:
        Formatted timestamp as "YYYY-MM-DD HH:MM:SS" or original string on error
    """
    try:
        if not ts_str:
            return "Unknown"
        # Parse ISO format timestamp
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # Format as "YYYY-MM-DD HH:MM:SS"
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str


def truncate(text_value: str | None, limit: int = 96) -> str:
    """Truncate text to specified limit with ellipsis.

    Args:
        text_value: Text to truncate
        limit: Maximum length before truncation

    Returns:
        Truncated text with "..." if over limit, empty string if None
    """
    if not text_value:
        return ""
    return text_value if len(text_value) <= limit else f"{text_value[:limit]}..."


def mask_phone(phone: str | None) -> str:
    """Mask phone number for display, showing first 2 and last 4 digits.

    Args:
        phone: Phone number to mask (may include + prefix)

    Returns:
        Masked phone number like "+*******48" or "Not linked" if None
    """
    if not phone:
        return "Not linked"
    digits = phone.strip()
    if digits.startswith("+"):
        digits = digits[1:]
    digits = digits.replace(" ", "")
    if not digits:
        return "Not linked"
    if len(digits) <= 6:
        # Mask all but last 2 characters for short numbers
        return f"***{digits[-2:]}" if len(digits) >= 2 else "***"
    return f"+{digits[:0]}*****{digits[-2:]}"


def normalize_phone(raw: str) -> str:
    """Normalize phone into a stable key and E.164-like form for API calls.

    Normalization rules:
    - Trim whitespace
    - Convert leading '00' to '+'
    - Remove spaces, hyphens, parentheses

    Args:
        raw: Raw phone number string (may include spaces, dashes, etc.)

    Returns:
        Normalized phone number with + prefix
    """
    if not raw:
        return ""
    s = str(raw).strip()
    s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s.startswith("00"):
        s = "+" + s[2:]
    return s


def format_display_phone(phone: str | None) -> str | None:
    """Return a UI-friendly phone value with a single leading + when possible.

    Args:
        phone: Normalized phone number (E.164 format)

    Returns:
        Formatted phone number or None if invalid
    """
    if not phone:
        return None
    normalized = normalize_phone(str(phone))
    if not normalized:
        return None
    return normalized if normalized.startswith("+") else f"+{normalized}"


def normalize_tags(tags: Any) -> List[str]:
    """Normalize tags field from various formats to consistent list.

    Handles:
    - String with comma-separated tags
    - YAML array string (e.g., "[tag1, tag2]")
    - Python list
    - Single value

    Args:
        tags: Tags as string (comma-separated), list, or None

    Returns:
        Normalized list of tag strings
    """
    import yaml

    if not tags:
        return []
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    if isinstance(tags, str):
        raw = tags.strip()
        # Try to parse as YAML array first
        if raw.startswith("["):
            try:
                parsed = yaml.safe_load(raw)
                return normalize_tags(parsed)
            except Exception:
                pass
        # Fall back to comma-separated string
        return [piece.strip() for piece in raw.split(",") if piece.strip()]
    # Handle single value
    return [str(tags).strip()]


def fallback_username() -> str:
    """Get fallback username from environment or default.

    Returns:
        Username string (default: "Analyst")
    """
    return os.getenv("TG_SENTINEL_USER", "Analyst")


def fallback_avatar() -> str:
    """Get fallback avatar URL.

    Returns:
        Path to default avatar image
    """
    return "/static/images/logo.png"


__all__ = [
    "format_timestamp",
    "truncate",
    "mask_phone",
    "normalize_phone",
    "format_display_phone",
    "normalize_tags",
    "fallback_username",
    "fallback_avatar",
]
