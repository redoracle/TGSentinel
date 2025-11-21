"""Profile management service for TG Sentinel UI.

This module handles CRUD operations for all profile types (interest, alert, global)
by proxying to the Sentinel API endpoints. The Sentinel container owns the persistent
config volume where profiles are stored as YAML files.

All file operations are delegated to the Sentinel API to maintain single source of truth.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)


# Profile ID prefixes for different profile types
# Alert profiles: 1000-1999
# Global profiles: 2000-2999
# Interest profiles: 3000-3999
ALERT_PROFILE_ID_PREFIX = 1000
GLOBAL_PROFILE_ID_PREFIX = 2000
INTEREST_PROFILE_ID_PREFIX = 3000


class ProfileService:
    """Service for managing profiles via Sentinel API endpoints."""

    def __init__(self, sentinel_api_base_url: str = None):
        """Initialize profile service with Sentinel API base URL.

        Args:
            sentinel_api_base_url: Base URL for Sentinel API.
                                  Defaults to env var or http://sentinel:8080/api
        """
        if sentinel_api_base_url is None:
            sentinel_api_base_url = os.getenv(
                "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
            )
        self.sentinel_api_base_url = sentinel_api_base_url.rstrip("/")
        logger.info(
            f"ProfileService initialized with Sentinel API: {self.sentinel_api_base_url}"
        )

    def _get_profile_type(self, profile_id: int | str) -> str:
        """Determine profile type from ID.

        Args:
            profile_id: Profile identifier (numeric ID or name)

        Returns:
            Profile type: 'alert', 'global', or 'interest'
        """
        try:
            numeric_id = int(profile_id)
            if 1000 <= numeric_id < 2000:
                return "alert"
            elif 2000 <= numeric_id < 3000:
                return "global"
            elif 3000 <= numeric_id < 4000:
                return "interest"
        except (ValueError, TypeError):
            # Non-numeric IDs are interest profiles (named profiles)
            return "interest"

        # Default to interest for unknown ranges
        return "interest"

        # ==================== INTEREST PROFILES ====================

        logger.debug(
            "ProfileService initialized: alert=%s, global=%s, interest=%s",
            self.alert_profiles_file,
            self.global_profiles_file,
            self.interest_profiles_file,
        )

    def _migrate_to_unified_storage(self) -> None:
        """Migrate profiles from legacy storage to unified YAML structure.

        Migrates:
        - data/profiles.yml (or profiles.json) -> config/profiles_interest.yml
        - data/alert_profiles.json -> config/profiles_alert.yml
        - Preserves config/profiles.yml as config/profiles_global.yml
        """
        try:
            # Migrate Interest Profiles
            if not self.interest_profiles_file.exists():
                # Check for legacy interest profiles file
                legacy_interest = None
                if self.profiles_file_legacy.exists():
                    logger.info(
                        "Migrating interest profiles from %s", self.profiles_file_legacy
                    )
                    with open(self.profiles_file_legacy, "r", encoding="utf-8") as f:
                        legacy_interest = yaml.safe_load(f) or {}

                if legacy_interest:
                    with open(self.interest_profiles_file, "w", encoding="utf-8") as f:
                        yaml.safe_dump(
                            legacy_interest,
                            f,
                            default_flow_style=False,
                            sort_keys=False,
                        )
                    logger.info("Migrated %d interest profiles", len(legacy_interest))
                else:
                    # Create empty file
                    with open(self.interest_profiles_file, "w", encoding="utf-8") as f:
                        yaml.safe_dump({}, f)
                    logger.debug("Created empty interest profiles file")

            # Migrate Alert Profiles
            if not self.alert_profiles_file.exists():
                if self.alert_profiles_file_legacy.exists():
                    logger.info(
                        "Migrating alert profiles from %s",
                        self.alert_profiles_file_legacy,
                    )
                    with open(
                        self.alert_profiles_file_legacy, "r", encoding="utf-8"
                    ) as f:
                        legacy_alert = json.load(f)

                    with open(self.alert_profiles_file, "w", encoding="utf-8") as f:
                        yaml.safe_dump(
                            legacy_alert, f, default_flow_style=False, sort_keys=False
                        )
                    logger.info("Migrated %d alert profiles", len(legacy_alert))
                else:
                    with open(self.alert_profiles_file, "w", encoding="utf-8") as f:
                        yaml.safe_dump({}, f)
                    logger.debug("Created empty alert profiles file")

            # Create Global Profiles file if it doesn't exist
            if not self.global_profiles_file.exists():
                with open(self.global_profiles_file, "w", encoding="utf-8") as f:
                    yaml.safe_dump({}, f)
                logger.debug("Created empty global profiles file")

        except Exception as exc:
            logger.error("Failed to migrate profiles: %s", exc, exc_info=True)

    # ═══════════════════════════════════════════════════════════════════
    # Interest Profile Methods
    # ═══════════════════════════════════════════════════════════════════

    def _generate_next_id(self, prefix: int, existing_profiles: Dict[int, Any]) -> int:
        """Generate the next available ID with the given prefix.

        Args:
            prefix: Base prefix (1000, 2000, or 3000)
            existing_profiles: Dictionary of existing profiles keyed by integer ID

        Returns:
            Next available ID in the range [prefix, prefix+999]
        """
        if not existing_profiles:
            return prefix

        # Find all IDs in this prefix range
        range_ids = [
            pid
            for pid in existing_profiles.keys()
            if isinstance(pid, int) and prefix <= pid < prefix + 1000
        ]

        if not range_ids:
            return prefix

        # Return max + 1
        return max(range_ids) + 1

    # ==================== INTEREST PROFILES ====================

    def load_profiles(self) -> Dict[str, Any]:
        """Load interest profiles from Sentinel API.

        Returns:
            Dictionary mapping profile names to profile data.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/interest"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                profiles = data.get("data", {})
                logger.debug(
                    f"Loaded {len(profiles)} interest profiles from Sentinel API"
                )
                return profiles
            else:
                logger.error(f"Failed to load interest profiles: {data.get('message')}")
                return {}

        except requests.exceptions.RequestException as exc:
            logger.error(f"Error loading interest profiles from Sentinel API: {exc}")
            return {}
        except Exception as exc:
            logger.error(f"Unexpected error loading interest profiles: {exc}")
            return {}

    def save_profiles(self, profiles: Dict[str, Any]) -> bool:
        """Save interest profiles via Sentinel API.

        Args:
            profiles: Dictionary mapping profile names to profile data.

        Returns:
            True on success, False on error.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/interest"
            response = requests.post(url, json=profiles, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                logger.debug(
                    f"Saved {len(profiles)} interest profiles via Sentinel API"
                )
                return True
            else:
                logger.error(f"Failed to save interest profiles: {data.get('message')}")
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(f"Error saving interest profiles to Sentinel API: {exc}")
            return False
        except Exception as exc:
            logger.error(f"Unexpected error saving interest profiles: {exc}")
            return False

    def get_profile(self, name: str) -> Dict[str, Any] | None:
        """Get a single interest profile by name.

        Args:
            name: Profile name.

        Returns:
            Profile dictionary or None if not found.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/interest/{name}"
            response = requests.get(url, timeout=10)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                return data.get("data")
            else:
                logger.error(f"Failed to get profile {name}: {data.get('message')}")
                return None

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error getting interest profile {name} from Sentinel API: {exc}"
            )
            return None
        except Exception as exc:
            logger.error(f"Unexpected error getting interest profile {name}: {exc}")
            return None

    def upsert_profile(self, profile_dict: Dict[str, Any]) -> bool:
        """Insert or update an interest profile.

        Args:
            profile_dict: Profile data with 'id' and 'name' keys.

        Returns:
            True on success, False on error or missing required fields.
        """
        profile_id = profile_dict.get("id")
        name = profile_dict.get("name", "").strip()

        if not name:
            logger.warning("Cannot upsert profile without a name")
            return False

        if not profile_id:
            logger.warning("Cannot upsert profile without an ID")
            return False

        profiles = self.load_profiles()
        # Use string ID as key for consistency with Sentinel API
        profiles[str(profile_id)] = profile_dict
        return self.save_profiles(profiles)

    def delete_profile(self, name: str) -> bool:
        """Delete an interest profile by name.

        Args:
            name: Profile name to delete.

        Returns:
            True if deleted or didn't exist, False on error.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/interest/{name}"
            response = requests.delete(url, timeout=10)

            if response.status_code == 404:
                return True  # Not found is not an error for deletion

            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                logger.debug(f"Deleted interest profile: {name}")
                return True
            else:
                logger.error(f"Failed to delete profile {name}: {data.get('message')}")
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error deleting interest profile {name} from Sentinel API: {exc}"
            )
            return False
        except Exception as exc:
            logger.error(f"Unexpected error deleting interest profile {name}: {exc}")
            return False

    def toggle_interest_profile(self, profile_id: int) -> bool:
        """Toggle interest profile enabled/disabled state via Sentinel API.

        Args:
            profile_id: Interest profile ID to toggle (3000-3999)

        Returns:
            True on success, False on error.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/interest/{profile_id}/toggle"
            response = requests.post(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                enabled = data.get("data", {}).get("enabled", False)
                logger.debug(
                    f"Toggled interest profile {profile_id}: enabled={enabled}"
                )
                return True
            else:
                logger.error(
                    f"Failed to toggle interest profile {profile_id}: {data.get('message')}"
                )
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error toggling interest profile {profile_id} via Sentinel API: {exc}"
            )
            return False
        except Exception as exc:
            logger.error(
                f"Unexpected error toggling interest profile {profile_id}: {exc}"
            )
            return False

    # ==================== GLOBAL PROFILES ====================

    def toggle_global_profile(self, profile_id: int) -> bool:
        """Toggle global profile enabled/disabled state via Sentinel API.

        Args:
            profile_id: Global profile ID to toggle (2000-2999)

        Returns:
            True on success, False on error.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/global/{profile_id}/toggle"
            response = requests.post(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                enabled = data.get("data", {}).get("enabled", False)
                logger.debug(f"Toggled global profile {profile_id}: enabled={enabled}")
                return True
            else:
                logger.error(
                    f"Failed to toggle global profile {profile_id}: {data.get('message')}"
                )
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error toggling global profile {profile_id} via Sentinel API: {exc}"
            )
            return False
        except Exception as exc:
            logger.error(
                f"Unexpected error toggling global profile {profile_id}: {exc}"
            )
            return False

    def load_global_profiles(self) -> Dict[str, Any]:
        """Load global profiles from Sentinel API.

        Returns:
            Dictionary containing global profile definitions.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/global"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                profiles = data.get("data", {})
                logger.debug(
                    f"Loaded {len(profiles)} global profiles from Sentinel API"
                )
                return profiles
            else:
                logger.error(f"Failed to load global profiles: {data.get('message')}")
                return {}

        except requests.exceptions.RequestException as exc:
            logger.error(f"Error loading global profiles from Sentinel API: {exc}")
            return {}
        except Exception as exc:
            logger.error(f"Unexpected error loading global profiles: {exc}")
            return {}

    def save_global_profiles(self, profiles: Dict[str, Any]) -> bool:
        """Save global profiles via Sentinel API.

        Args:
            profiles: Dictionary containing profile definitions.

        Returns:
            True on success, False on error.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/global"
            response = requests.post(url, json=profiles, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                logger.debug(f"Saved {len(profiles)} global profiles via Sentinel API")
                return True
            else:
                logger.error(f"Failed to save global profiles: {data.get('message')}")
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(f"Error saving global profiles to Sentinel API: {exc}")
            return False
        except Exception as exc:
            logger.error(f"Unexpected error saving global profiles: {exc}")
            return False

    def list_global_profiles(self) -> List[Dict[str, Any]]:
        """List all global profiles with metadata.

        Returns:
            List of profile dictionaries with id and name keys.
        """
        profiles = self.load_global_profiles()
        return [
            {
                "id": int(profile_id),
                "name": profile_data.get("name", str(profile_id)),
                **profile_data,
            }
            for profile_id, profile_data in profiles.items()
        ]

    def get_global_profile(self, profile_id: int) -> Dict[str, Any] | None:
        """Get a single global profile by ID.

        Args:
            profile_id: Profile ID (integer in 2000-2999 range).

        Returns:
            Profile dictionary or None if not found.
        """
        try:
            profile_id = int(profile_id)  # Ensure integer
            url = f"{self.sentinel_api_base_url}/profiles/global/{profile_id}"
            response = requests.get(url, timeout=10)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                profile_data = data.get("data")
                if profile_data:
                    return {"id": profile_id, **profile_data}
            return None

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error getting global profile {profile_id} from Sentinel API: {exc}"
            )
            return None
        except Exception as exc:
            logger.error(f"Unexpected error getting global profile {profile_id}: {exc}")
            return None

    def create_global_profile(self, profile_data: Dict[str, Any]) -> int | None:
        """Create a new global profile with auto-generated ID.

        Args:
            profile_data: Profile configuration (keywords, weights, etc.).

        Returns:
            Generated profile ID on success, None on error.
        """
        profiles = self.load_global_profiles()

        # Convert string keys to integers for ID generation
        int_profiles = {int(k): v for k, v in profiles.items() if k.isdigit()}

        # Generate new ID
        profile_id = self._generate_next_id(GLOBAL_PROFILE_ID_PREFIX, int_profiles)

        # Store with string key (YAML requirement) but integer ID in data
        profile_data["id"] = profile_id
        profiles[str(profile_id)] = profile_data

        if self.save_global_profiles(profiles):
            logger.info(f"Created global profile with ID: {profile_id}")
            return profile_id
        return None

    def update_global_profile(
        self, profile_id: int, profile_data: Dict[str, Any]
    ) -> bool:
        """Update an existing global profile.

        Args:
            profile_id: Profile ID to update (integer in 2000-2999 range).
            profile_data: New profile configuration.

        Returns:
            True on success, False if profile doesn't exist or on error.
        """
        profile_id = int(profile_id)  # Ensure integer
        profiles = self.load_global_profiles()

        if str(profile_id) not in profiles:
            logger.warning(
                "Global profile '%s' not found, use create instead", profile_id
            )
            return False

        profile_data["id"] = profile_id
        profiles[str(profile_id)] = profile_data
        return self.save_global_profiles(profiles)

    def upsert_global_profile(
        self, profile_id: int, profile_data: Dict[str, Any]
    ) -> bool:
        """Insert or update a global profile.

        Args:
            profile_id: Profile ID (integer in 2000-2999 range).
            profile_data: Profile configuration.

        Returns:
            True on success, False on error.
        """
        profile_id = int(profile_id)  # Ensure integer
        profiles = self.load_global_profiles()
        profile_data["id"] = profile_id
        profiles[str(profile_id)] = profile_data
        return self.save_global_profiles(profiles)

    def delete_global_profile(self, profile_id: int) -> bool:
        """Delete a global profile by ID.

        Args:
            profile_id: Profile ID to delete (integer in 2000-2999 range).

        Returns:
            True if deleted or didn't exist, False on error.
        """
        try:
            profile_id = int(profile_id)  # Ensure integer
            url = f"{self.sentinel_api_base_url}/profiles/global/{profile_id}"
            response = requests.delete(url, timeout=10)

            if response.status_code == 404:
                return True  # Not found is not an error for deletion

            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                logger.debug(f"Deleted global profile: {profile_id}")
                return True
            else:
                logger.error(
                    f"Failed to delete global profile {profile_id}: {data.get('message')}"
                )
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error deleting global profile {profile_id} from Sentinel API: {exc}"
            )
            return False
        except Exception as exc:
            logger.error(
                f"Unexpected error deleting global profile {profile_id}: {exc}"
            )
            return False

    def validate_global_profile(self, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate global profile structure and return validation result.

        Args:
            profile_data: Profile configuration to validate.

        Returns:
            Dictionary with 'valid' boolean and 'errors' list.
        """
        errors = []

        # Check required fields
        if not profile_data.get("name"):
            errors.append("Profile name is required")

        # Validate keyword categories (should be lists)
        keyword_fields = [
            "keywords",
            "action_keywords",
            "decision_keywords",
            "urgency_keywords",
            "importance_keywords",
            "release_keywords",
            "security_keywords",
            "risk_keywords",
            "opportunity_keywords",
        ]
        for field in keyword_fields:
            if field in profile_data and not isinstance(profile_data[field], list):
                errors.append(f"'{field}' must be a list")

        # Validate scoring weights (should be dict with float values)
        if "scoring_weights" in profile_data:
            weights = profile_data["scoring_weights"]
            if not isinstance(weights, dict):
                errors.append("'scoring_weights' must be a dictionary")
            else:
                for key, value in weights.items():
                    if not isinstance(value, (int, float)):
                        errors.append(
                            f"scoring_weights.{key} must be a number, got {type(value).__name__}"
                        )

        # Validate boolean flags
        bool_fields = [
            "detect_codes",
            "detect_documents",
            "prioritize_pinned",
            "prioritize_admin",
            "detect_polls",
        ]
        for field in bool_fields:
            if field in profile_data and not isinstance(profile_data[field], bool):
                errors.append(f"'{field}' must be a boolean")

        return {"valid": len(errors) == 0, "errors": errors}

    def get_profile_usage(self, profile_id: str) -> Dict[str, Any]:
        """Get usage information for a global profile.

        Args:
            profile_id: Profile ID to check.

        Returns:
            Dictionary with 'channels' and 'users' lists showing where profile is used.
        """
        usage = {"channels": [], "users": []}

        try:
            if not self.tgsentinel_config.exists():
                logger.warning("Config file not found: %s", self.tgsentinel_config)
                return usage

            with open(self.tgsentinel_config, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            # Check channels
            for channel in config.get("channels", []):
                if profile_id in channel.get("profiles", []):
                    usage["channels"].append(
                        {
                            "id": channel.get("id"),
                            "name": channel.get("name", "Unnamed"),
                        }
                    )

            # Check monitored users
            for user in config.get("monitored_users", []):
                if profile_id in user.get("profiles", []):
                    usage["users"].append(
                        {
                            "id": user.get("id"),
                            "name": user.get("name", "Unnamed"),
                        }
                    )

        except Exception as exc:
            logger.error("Failed to get profile usage for '%s': %s", profile_id, exc)

        return usage

    # ==================== ALERT PROFILES (via Sentinel API) ====================

    def load_alert_profiles(self) -> Dict[str, Any]:
        """Load alert profiles from Sentinel API.

        Returns:
            Dictionary of alert profiles {profile_id: profile_data}, empty dict on error.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/alert"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                profiles = data.get("data", {})
                logger.debug(f"Loaded {len(profiles)} alert profiles from Sentinel API")
                return profiles
            else:
                logger.error(f"Failed to load alert profiles: {data.get('message')}")
                return {}

        except requests.exceptions.RequestException as exc:
            logger.error(f"Error loading alert profiles from Sentinel API: {exc}")
            return {}
        except Exception as exc:
            logger.error(f"Unexpected error loading alert profiles: {exc}")
            return {}

    def save_alert_profiles(self, profiles: Dict[str, Any]) -> bool:
        """Save alert profiles via Sentinel API.

        Args:
            profiles: Dictionary of alert profiles {profile_id: profile_data}.

        Returns:
            True on success, False on error.
        """
        try:
            url = f"{self.sentinel_api_base_url}/profiles/alert"
            response = requests.post(url, json=profiles, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                logger.debug(f"Saved {len(profiles)} alert profiles via Sentinel API")
                return True
            else:
                logger.error(f"Failed to save alert profiles: {data.get('message')}")
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(f"Error saving alert profiles to Sentinel API: {exc}")
            return False
        except Exception as exc:
            logger.error(f"Unexpected error saving alert profiles: {exc}")
            return False

    def get_alert_profile(self, profile_id: int) -> Dict[str, Any] | None:
        """Get a single alert profile by ID via Sentinel API.

        Args:
            profile_id: Profile ID (integer in 1000-1999 range).

        Returns:
            Alert profile dictionary or None if not found.
        """
        try:
            profile_id = int(profile_id)  # Ensure integer
            url = f"{self.sentinel_api_base_url}/profiles/alert/{profile_id}"
            response = requests.get(url, timeout=10)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                return data.get("data")
            else:
                logger.error(
                    f"Failed to get alert profile {profile_id}: {data.get('message')}"
                )
                return None

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error getting alert profile {profile_id} from Sentinel API: {exc}"
            )
            return None
        except Exception as exc:
            logger.error(f"Unexpected error getting alert profile {profile_id}: {exc}")
            return None

    def upsert_alert_profile(self, profile_dict: Dict[str, Any]) -> bool:
        """Insert or update an alert profile via Sentinel API.

        Args:
            profile_dict: Profile data with 'name' key and optional 'id'.

        Returns:
            True on success, False on error or missing name.
        """
        name = profile_dict.get("name", "").strip()
        if not name:
            logger.warning("Cannot upsert alert profile without a name")
            return False

        profiles = self.load_alert_profiles()

        # Get or generate integer ID
        profile_id = profile_dict.get("id")
        if profile_id is None:
            # New profile - generate ID
            profile_id = self._generate_next_id(ALERT_PROFILE_ID_PREFIX, profiles)
            profile_dict["id"] = profile_id
            logger.info(f"Generated alert profile ID: {profile_id}")
        else:
            # Ensure ID is integer
            profile_id = int(profile_id)
            profile_dict["id"] = profile_id

        # Key by integer ID for consistent lookups
        profiles[profile_id] = profile_dict
        return self.save_alert_profiles(profiles)

    def delete_alert_profile(self, profile_id: int) -> bool:
        """Delete an alert profile by ID via Sentinel API.

        Args:
            profile_id: Profile ID to delete (integer in 1000-1999 range).

        Returns:
            True if deleted or didn't exist, False on error.
        """
        try:
            profile_id = int(profile_id)  # Ensure integer
            url = f"{self.sentinel_api_base_url}/profiles/alert/{profile_id}"
            response = requests.delete(url, timeout=10)

            if response.status_code == 404:
                return True  # Not found is not an error for deletion

            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                logger.debug(f"Deleted alert profile: {profile_id}")
                return True
            else:
                logger.error(
                    f"Failed to delete alert profile {profile_id}: {data.get('message')}"
                )
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error deleting alert profile {profile_id} from Sentinel API: {exc}"
            )
            return False
        except Exception as exc:
            logger.error(f"Unexpected error deleting alert profile {profile_id}: {exc}")
            return False

    def toggle_alert_profile(self, profile_id: int) -> bool:
        """Toggle the enabled status of an alert profile via Sentinel API.

        Args:
            profile_id: Profile ID to toggle (integer in 1000-1999 range).

        Returns:
            True on success, False on error.
        """
        try:
            profile_id = int(profile_id)  # Ensure integer
            url = f"{self.sentinel_api_base_url}/profiles/alert/{profile_id}/toggle"
            response = requests.post(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("status") == "ok":
                new_status = data.get("data", {}).get("enabled", False)
                logger.info(f"Toggled alert profile {profile_id}: enabled={new_status}")
                return True
            else:
                logger.error(
                    f"Failed to toggle alert profile {profile_id}: {data.get('message')}"
                )
                return False

        except requests.exceptions.RequestException as exc:
            logger.error(
                f"Error toggling alert profile {profile_id} via Sentinel API: {exc}"
            )
            return False
        except Exception as exc:
            logger.error(f"Unexpected error toggling alert profile {profile_id}: {exc}")
            return False

    # =========================================================================
    # Alert Profile Synchronization
    # =========================================================================

    def sync_alert_profiles_to_config(self, config_path: Path) -> bool:
        """Sync alert profiles from JSON to tgsentinel.yml channels config.

        This ensures alert profile settings (keywords, thresholds, etc.) are
        reflected in the main configuration file for the sentinel worker.

        Args:
            config_path: Path to tgsentinel.yml configuration file.

        Returns:
            True on success, False on error.
        """
        try:
            alert_profiles = self.load_alert_profiles()

            if not config_path.exists():
                logger.warning("Config file not found, cannot sync alert profiles")
                return False

            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            # Update channels with alert profile data
            channels = config.get("channels", [])
            profile_map = {
                p.get("channel_id"): p
                for p in alert_profiles.values()
                if p.get("type") == "channel" and p.get("channel_id")
            }

            for channel in channels:
                channel_id = channel.get("id")
                if channel_id in profile_map:
                    profile = profile_map[channel_id]

                    # Sync keyword categories
                    for key in [
                        "action_keywords",
                        "decision_keywords",
                        "urgency_keywords",
                        "importance_keywords",
                        "release_keywords",
                        "security_keywords",
                        "risk_keywords",
                        "opportunity_keywords",
                    ]:
                        if profile.get(key):
                            channel[key] = profile[key]

                    # Sync other settings
                    channel["vip_senders"] = profile.get("vip_senders", [])
                    channel["reaction_threshold"] = profile.get("reaction_threshold", 5)
                    channel["reply_threshold"] = profile.get("reply_threshold", 3)
                    channel["detect_codes"] = profile.get("detect_codes", True)
                    channel["detect_documents"] = profile.get("detect_documents", True)
                    channel["prioritize_pinned"] = profile.get(
                        "prioritize_pinned", True
                    )
                    channel["prioritize_admin"] = profile.get("prioritize_admin", True)
                    channel["detect_polls"] = profile.get("detect_polls", True)
                    channel["rate_limit_per_hour"] = profile.get(
                        "rate_limit_per_hour", 10
                    )

                    # Sync digest configuration if present
                    if "digest_config" in profile:
                        channel["digest"] = profile["digest_config"]
                    elif "digest" not in channel:
                        # Clear digest if profile doesn't have one
                        channel.pop("digest", None)

            # Write back to config with file locking
            # Open target file and acquire exclusive lock for inter-process safety
            target_fd = os.open(str(config_path), os.O_CREAT | os.O_WRONLY, 0o644)

            try:
                # Acquire exclusive lock on target file
                fcntl.flock(target_fd, fcntl.LOCK_EX)

                try:
                    # Create temp file in same directory for atomic rename
                    temp_fd, temp_path = tempfile.mkstemp(
                        dir=config_path.parent, prefix=".config_", suffix=".tmp"
                    )

                    try:
                        # Write to temp file with flush + fsync
                        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                            yaml.safe_dump(
                                config,
                                temp_f,
                                default_flow_style=False,
                                sort_keys=False,
                            )
                            temp_f.flush()
                            os.fsync(temp_f.fileno())

                        # Atomic replace: os.replace is guaranteed atomic on POSIX
                        os.replace(temp_path, str(config_path))

                    except Exception:
                        # Clean up temp file on error
                        if os.path.exists(temp_path):
                            os.unlink(temp_path)
                        raise

                finally:
                    # Release lock on target file
                    fcntl.flock(target_fd, fcntl.LOCK_UN)

            finally:
                # Close target file descriptor
                os.close(target_fd)

            # Touch reload marker to signal config reload
            reload_marker = self.data_dir / ".reload_config"
            reload_marker.touch()

            logger.info("Alert profiles synced to config successfully")
            return True

        except Exception as exc:
            logger.error("Failed to sync alert profiles to config: %s", exc)
            return False


# Module-level singleton instance (initialized by app.py)
_profile_service: ProfileService | None = None


def get_profile_service() -> ProfileService:
    """Get or create the global ProfileService instance.

    Returns:
        ProfileService singleton.
    """
    global _profile_service
    if _profile_service is None:
        _profile_service = ProfileService()
    return _profile_service


def init_profile_service(sentinel_api_base_url: str = None) -> ProfileService:
    """Initialize the global ProfileService instance.

    Args:
        sentinel_api_base_url: Sentinel API base URL. If None, uses environment variable.

    Returns:
        ProfileService instance.
    """
    global _profile_service
    _profile_service = ProfileService(sentinel_api_base_url)
    return _profile_service
