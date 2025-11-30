"""Digest configuration service for UI.

Proxies digest config requests to Sentinel API.
"""

import logging
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class DigestService:
    """Service for managing digest configurations via Sentinel API."""

    def __init__(self, sentinel_api_base_url: str):
        """Initialize service with Sentinel API base URL.

        Args:
            sentinel_api_base_url: Base URL for Sentinel API (e.g., http://sentinel:8080/api)
        """
        self.sentinel_api_base_url = sentinel_api_base_url.rstrip("/")

    def _get_digest_config(
        self, endpoint_path: str, entity_type: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        """Private helper to GET digest config from Sentinel API.

        Args:
            endpoint_path: API endpoint path (e.g., "/config/profiles/{id}/digest")
            entity_type: Entity type for logging (e.g., "profile", "channel", "user")
            entity_id: Entity identifier for logging

        Returns:
            Digest config dict or None if not configured/error
        """
        try:
            url = f"{self.sentinel_api_base_url}{endpoint_path}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                return data.get("digest")

            logger.warning(
                f"[DIGEST-SERVICE] Failed to get digest config for {entity_type} {entity_id}: {data.get('message')}"
            )
            return None

        except requests.exceptions.RequestException as e:
            logger.error(
                f"[DIGEST-SERVICE] Error getting digest config for {entity_type} {entity_id}: {e}"
            )
            return None

    def _update_digest_config(
        self,
        endpoint_path: str,
        entity_type: str,
        entity_id: str,
        digest_config: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """Private helper to PUT digest config to Sentinel API.

        Args:
            endpoint_path: API endpoint path (e.g., "/config/profiles/{id}/digest")
            entity_type: Entity type for logging (e.g., "profile", "channel", "user")
            entity_id: Entity identifier for logging
            digest_config: Digest configuration to send

        Returns:
            Tuple of (success, message)
        """
        try:
            url = f"{self.sentinel_api_base_url}{endpoint_path}"
            response = requests.put(url, json=digest_config, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                logger.info(
                    f"[DIGEST-SERVICE] Updated digest config for {entity_type} {entity_id}"
                )
                return True, data.get("message", "Configuration updated")

            return False, data.get("message", "Unknown error")

        except requests.exceptions.RequestException as e:
            logger.error(
                f"[DIGEST-SERVICE] Error updating digest config for {entity_type} {entity_id}: {e}"
            )
            return False, str(e)

    def get_profile_digest_config(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Get digest config for a global profile.

        Args:
            profile_id: Profile ID (e.g., "security", "tech")

        Returns:
            Digest config dict or None if not configured
        """
        return self._get_digest_config(
            f"/config/profiles/{profile_id}/digest", "profile", profile_id
        )

    def update_profile_digest_config(
        self, profile_id: str, digest_config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Update digest config for a global profile.

        Args:
            profile_id: Profile ID
            digest_config: New digest configuration

        Returns:
            Tuple of (success, message)
        """
        return self._update_digest_config(
            f"/config/profiles/{profile_id}/digest",
            "profile",
            profile_id,
            digest_config,
        )

    def get_channel_digest_config(self, channel_id: int) -> Optional[Dict[str, Any]]:
        """Get digest config for a channel.

        Args:
            channel_id: Channel ID

        Returns:
            Digest config dict or None if not configured
        """
        return self._get_digest_config(
            f"/config/channels/{channel_id}/digest", "channel", str(channel_id)
        )

    def update_channel_digest_config(
        self, channel_id: int, digest_config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Update digest config for a channel.

        Args:
            channel_id: Channel ID
            digest_config: New digest configuration

        Returns:
            Tuple of (success, message)
        """
        return self._update_digest_config(
            f"/config/channels/{channel_id}/digest",
            "channel",
            str(channel_id),
            digest_config,
        )

    def get_channel_overrides_digest_config(
        self, channel_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get digest overrides for a channel.

        Args:
            channel_id: Channel ID

        Returns:
            Digest config dict or None if not configured
        """
        return self._get_digest_config(
            f"/config/channels/{channel_id}/overrides/digest",
            "channel overrides",
            str(channel_id),
        )

    def update_channel_overrides_digest_config(
        self, channel_id: int, digest_config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Update digest overrides for a channel.

        Args:
            channel_id: Channel ID
            digest_config: New digest configuration

        Returns:
            Tuple of (success, message)
        """
        return self._update_digest_config(
            f"/config/channels/{channel_id}/overrides/digest",
            "channel overrides",
            str(channel_id),
            digest_config,
        )

    def get_user_digest_config(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get digest config for a monitored user.

        Args:
            user_id: User ID

        Returns:
            Digest config dict or None if not configured
        """
        return self._get_digest_config(
            f"/config/users/{user_id}/digest", "user", str(user_id)
        )

    def update_user_digest_config(
        self, user_id: int, digest_config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Update digest config for a monitored user.

        Args:
            user_id: User ID
            digest_config: New digest configuration

        Returns:
            Tuple of (success, message)
        """
        return self._update_digest_config(
            f"/config/users/{user_id}/digest", "user", str(user_id), digest_config
        )

    def get_user_overrides_digest_config(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get digest overrides for a monitored user.

        Args:
            user_id: User ID

        Returns:
            Digest config dict or None if not configured
        """
        return self._get_digest_config(
            f"/config/users/{user_id}/overrides/digest",
            "user overrides",
            str(user_id),
        )

    def update_user_overrides_digest_config(
        self, user_id: int, digest_config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Update digest overrides for a monitored user.

        Args:
            user_id: User ID
            digest_config: New digest configuration

        Returns:
            Tuple of (success, message)
        """
        return self._update_digest_config(
            f"/config/users/{user_id}/overrides/digest",
            "user overrides",
            str(user_id),
            digest_config,
        )
