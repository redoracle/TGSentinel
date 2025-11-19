"""Input validation utilities for TG Sentinel UI.

This module provides validation functions for user inputs, configuration
payloads, and file uploads.
"""

from typing import Any, Dict, Tuple


def validate_config_payload(payload: Dict[str, Any]) -> None:
    """Validate configuration payload before applying changes.

    Args:
        payload: Configuration data to validate

    Raises:
        ValueError: If any field is invalid
    """
    # Validate numeric ranges
    if "redis_port" in payload:
        port = payload["redis_port"]
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError(f"Invalid redis_port: {port}. Must be 1-65535.")

    if "retention_days" in payload:
        days = payload["retention_days"]
        if not isinstance(days, int) or days < 0:
            raise ValueError(f"Invalid retention_days: {days}. Must be >= 0.")

    if "rate_limit_per_channel" in payload:
        rate = payload["rate_limit_per_channel"]
        if not isinstance(rate, int) or rate < 0:
            raise ValueError(f"Invalid rate_limit_per_channel: {rate}. Must be >= 0.")

    # Validate string fields are not empty when required
    required_strings = ["phone_number", "api_hash"]
    for field in required_strings:
        if field in payload and not isinstance(payload[field], str):
            raise ValueError(f"Invalid {field}: must be a string.")

    # Validate mode is in allowed values
    if "mode" in payload and payload["mode"] not in ["dm", "channel", "both"]:
        raise ValueError(
            f"Invalid mode: {payload['mode']}. Must be 'dm', 'channel', or 'both'."
        )

    # Validate channels structure
    if "channels" in payload:
        channels = payload["channels"]
        if not isinstance(channels, list):
            raise ValueError("Invalid channels: must be a list.")
        for idx, channel in enumerate(channels):
            if not isinstance(channel, dict):
                raise ValueError(f"Invalid channel at index {idx}: must be a dict.")


def validate_profile_structure(profile: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate interest profile structure.

    Args:
        profile: Profile data to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = ["name", "keywords"]

    for field in required_fields:
        if field not in profile:
            return False, f"Missing required field: {field}"

    if not isinstance(profile.get("keywords"), list):
        return False, "Keywords must be a list"

    if not isinstance(profile.get("name"), str) or not profile["name"].strip():
        return False, "Name must be a non-empty string"

    return True, ""


def validate_alert_rule(rule: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate alert rule structure.

    Args:
        rule: Alert rule to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if "condition" not in rule:
        return False, "Alert rule must have a condition"

    if "action" not in rule:
        return False, "Alert rule must have an action"

    valid_conditions = ["keyword_match", "score_threshold", "sender_match"]
    if rule.get("condition") not in valid_conditions:
        return False, f"Invalid condition. Must be one of: {valid_conditions}"

    return True, ""
