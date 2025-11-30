"""Data serialization utilities for TG Sentinel UI.

This module provides functions to transform internal data structures
into API-friendly JSON representations.
"""

from typing import Any, Dict, List


def serialize_channels(config: Any) -> List[Dict[str, Any]]:
    """Serialize channel configuration for API responses.

    Args:
        config: AppCfg instance with channels configuration

    Returns:
        List of serialized channel dictionaries
    """
    if not config:
        return []

    channels = getattr(config, "channels", None)
    if not channels:
        return []

    serialized: List[Dict[str, Any]] = []
    for idx, channel in enumerate(channels, start=1):
        serialized.append(
            {
                "id": getattr(channel, "id", idx),
                "chat_id": getattr(channel, "id", idx),
                "name": getattr(channel, "name", f"Channel {idx}"),
                "vip_senders": list(getattr(channel, "vip_senders", [])),
                "keywords": list(getattr(channel, "keywords", [])),
                "reaction_threshold": getattr(channel, "reaction_threshold", 0),
                "reply_threshold": getattr(channel, "reply_threshold", 0),
                "rate_limit": getattr(channel, "rate_limit_per_hour", 0),
                "enabled": True,
            }
        )
    return serialized


def serialize_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a Telegram entity (user/chat) for API response.

    Args:
        entity: Raw entity data from Telegram

    Returns:
        Serialized entity dictionary
    """
    # Basic implementation - can be expanded based on actual usage
    return {
        "id": entity.get("id"),
        "name": entity.get("name", entity.get("title", "Unknown")),
        "type": entity.get("type", "unknown"),
        "username": entity.get("username"),
    }
