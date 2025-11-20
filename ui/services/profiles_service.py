"""Profile management service for TG Sentinel UI.

This module handles CRUD operations for both interest profiles (YAML-based),
alert profiles (JSON-based), and global profiles (profiles.yml).
Provides thread-safe file operations with atomic writes and file locking.
"""

import fcntl
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)


class ProfileService:
    """Service for managing interest and alert profiles with thread-safe file I/O."""

    def __init__(self, data_dir: Path | None = None):
        """Initialize profile service with data directory.

        Args:
            data_dir: Directory for profile storage. Defaults to ../data from ui module.
        """
        if data_dir is None:
            # Default to <repo>/data directory
            data_dir = Path(__file__).parent.parent.parent / "data"

        self.data_dir = Path(data_dir)
        self.config_dir = self.data_dir.parent / "config"
        self.profiles_file = self.data_dir / "profiles.yml"
        self.profiles_file_legacy = (
            self.data_dir / "profiles.json"
        )  # Legacy filename for migration
        self.alert_profiles_file = self.data_dir / "alert_profiles.json"
        self.global_profiles_file = self.config_dir / "profiles.yml"
        self.tgsentinel_config = self.config_dir / "tgsentinel.yml"

        # Thread-safe locks for file operations
        self._profiles_lock = threading.Lock()
        self._alert_profiles_lock = threading.Lock()
        self._global_profiles_lock = threading.Lock()

        # Migrate from old profiles.json to profiles.yml if needed
        self._migrate_profiles_file()

        logger.debug(
            "ProfileService initialized: profiles=%s, alert_profiles=%s, global_profiles=%s",
            self.profiles_file,
            self.alert_profiles_file,
            self.global_profiles_file,
        )

    def _migrate_profiles_file(self) -> None:
        """Migrate profiles from old profiles.json to profiles.yml if needed.

        If profiles.yml doesn't exist but profiles.json does, load the JSON file
        and save it as YAML to preserve existing data.
        """
        try:
            # If new file exists, no migration needed
            if self.profiles_file.exists():
                logger.debug("Profiles file already exists: %s", self.profiles_file)
                return

            # Check if legacy file exists
            if not self.profiles_file_legacy.exists():
                logger.debug("No legacy profiles file to migrate")
                return

            # Load from legacy JSON file
            logger.info(
                "Migrating profiles from %s to %s",
                self.profiles_file_legacy,
                self.profiles_file,
            )

            with open(self.profiles_file_legacy, "r", encoding="utf-8") as f:
                # Legacy file was named .json but actually contained YAML
                profiles = yaml.safe_load(f) or {}

            # Save to new YAML file
            if profiles:
                self.profiles_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.profiles_file, "w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        profiles, f, default_flow_style=False, sort_keys=True
                    )
                logger.info(
                    "Successfully migrated %d profile(s) from %s to %s",
                    len(profiles),
                    self.profiles_file_legacy,
                    self.profiles_file,
                )

                # Optionally rename old file to .bak for safety
                backup_path = self.profiles_file_legacy.with_suffix(".json.bak")
                self.profiles_file_legacy.rename(backup_path)
                logger.info("Renamed legacy file to %s", backup_path)
            else:
                logger.info("Legacy profiles file was empty, no migration needed")

        except Exception as exc:
            logger.error(
                "Failed to migrate profiles from %s to %s: %s",
                self.profiles_file_legacy,
                self.profiles_file,
                exc,
                exc_info=True,
            )

    # =========================================================================
    # Interest Profiles (YAML-based)
    # =========================================================================

    def load_profiles(self) -> Dict[str, Any]:
        """Load all interest profiles from disk with file locking.

        Returns:
            Dictionary of profiles, empty dict if file doesn't exist or on error.
        """
        try:
            if not self.profiles_file.exists():
                logger.debug("Profiles file does not exist: %s", self.profiles_file)
                return {}

            # Open file and acquire shared lock for reading
            with open(self.profiles_file, "r", encoding="utf-8") as f:
                # Acquire shared (read) lock to prevent reading partial writes
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = yaml.safe_load(f) or {}
                    logger.debug(
                        "Loaded %d profile(s) from %s", len(data), self.profiles_file
                    )
                    return data
                finally:
                    # Release shared lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.error("Failed to load profiles from %s: %s", self.profiles_file, exc)
            return {}

    def save_profiles(self, profiles: Dict[str, Any]) -> bool:
        """Save all interest profiles to disk with atomic write and file locking.

        Args:
            profiles: Dictionary of profiles to save.

        Returns:
            True on success, False on error.
        """
        try:
            # Ensure data directory exists
            self.profiles_file.parent.mkdir(parents=True, exist_ok=True)

            # Open target file and acquire exclusive lock for inter-process safety
            # Create the file if it doesn't exist
            target_fd = os.open(
                str(self.profiles_file), os.O_CREAT | os.O_WRONLY, 0o644
            )

            try:
                # Acquire exclusive lock on target file
                fcntl.flock(target_fd, fcntl.LOCK_EX)

                try:
                    # Create temp file in same directory for atomic rename
                    temp_fd, temp_path = tempfile.mkstemp(
                        dir=self.profiles_file.parent,
                        prefix=".profiles_",
                        suffix=".tmp",
                    )

                    try:
                        # Write to temp file with flush + fsync
                        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                            yaml.safe_dump(
                                profiles,
                                temp_f,
                                default_flow_style=False,
                                sort_keys=True,
                            )
                            temp_f.flush()
                            os.fsync(temp_f.fileno())

                        # Atomic replace: os.replace is guaranteed atomic on POSIX
                        os.replace(temp_path, str(self.profiles_file))
                        logger.debug(
                            "Saved %d profile(s) to %s",
                            len(profiles),
                            self.profiles_file,
                        )
                        return True

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

        except Exception as exc:
            logger.error("Failed to save profiles to %s: %s", self.profiles_file, exc)
            return False

    def get_profile(self, name: str) -> Dict[str, Any] | None:
        """Get a single interest profile by name.

        Args:
            name: Profile name.

        Returns:
            Profile dictionary or None if not found.
        """
        with self._profiles_lock:
            profiles = self.load_profiles()
            return profiles.get(name)

    def upsert_profile(self, profile_dict: Dict[str, Any]) -> bool:
        """Insert or update an interest profile.

        Args:
            profile_dict: Profile data with 'name' key.

        Returns:
            True on success, False on error or missing name.
        """
        name = profile_dict.get("name", "").strip()
        if not name:
            logger.warning("Cannot upsert profile without a name")
            return False

        with self._profiles_lock:
            profiles = self.load_profiles()
            profiles[name] = profile_dict
            return self.save_profiles(profiles)

    def delete_profile(self, name: str) -> bool:
        """Delete an interest profile by name.

        Args:
            name: Profile name to delete.

        Returns:
            True if deleted or didn't exist, False on error.
        """
        with self._profiles_lock:
            profiles = self.load_profiles()
            if name in profiles:
                del profiles[name]
                return self.save_profiles(profiles)
            # Not found is not an error for deletion
            return True

    # =========================================================================
    # Global Profiles (from config/profiles.yml)
    # =========================================================================

    def load_global_profiles(self) -> Dict[str, Any]:
        """Load global profiles from config/profiles.yml with file locking.

        Returns:
            Dictionary containing 'profiles' key with profile definitions,
            empty dict if file doesn't exist or on error.
        """
        try:
            if not self.global_profiles_file.exists():
                logger.debug(
                    "Global profiles file does not exist: %s", self.global_profiles_file
                )
                return {"profiles": {}}

            # Open file and acquire shared lock for reading
            with open(self.global_profiles_file, "r", encoding="utf-8") as f:
                # Acquire shared (read) lock to prevent reading partial writes
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = yaml.safe_load(f) or {}
                    profiles = data.get("profiles", {})
                    logger.debug(
                        "Loaded %d global profile(s) from %s",
                        len(profiles),
                        self.global_profiles_file,
                    )
                    return data
                finally:
                    # Release shared lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.error(
                "Failed to load global profiles from %s: %s",
                self.global_profiles_file,
                exc,
            )
            return {"profiles": {}}

    def save_global_profiles(self, data: Dict[str, Any]) -> bool:
        """Save global profiles to config/profiles.yml with atomic write and file locking.

        Args:
            data: Dictionary containing 'profiles' key with profile definitions.

        Returns:
            True on success, False on error.
        """
        try:
            # Ensure config directory exists
            self.global_profiles_file.parent.mkdir(parents=True, exist_ok=True)

            # Open target file and acquire exclusive lock for inter-process safety
            # Create the file if it doesn't exist
            target_fd = os.open(
                str(self.global_profiles_file), os.O_CREAT | os.O_WRONLY, 0o644
            )

            try:
                # Acquire exclusive lock on target file
                fcntl.flock(target_fd, fcntl.LOCK_EX)

                try:
                    # Create temp file in same directory for atomic rename
                    temp_fd, temp_path = tempfile.mkstemp(
                        dir=self.global_profiles_file.parent,
                        prefix=".profiles_",
                        suffix=".tmp",
                    )

                    try:
                        # Write to temp file with flush + fsync
                        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                            # Add header comment
                            temp_f.write("# TG Sentinel - Global Profile Definitions\n")
                            temp_f.write(
                                "# Two-Layer Architecture: Define reusable profiles here, bind them to channels/users in tgsentinel.yml\n\n"
                            )
                            yaml.safe_dump(
                                data,
                                temp_f,
                                default_flow_style=False,
                                sort_keys=True,
                                allow_unicode=True,
                            )
                            temp_f.flush()
                            os.fsync(temp_f.fileno())

                        # Atomic replace: os.replace is guaranteed atomic on POSIX
                        os.replace(temp_path, str(self.global_profiles_file))
                        profiles_count = len(data.get("profiles", {}))
                        logger.debug(
                            "Saved %d global profile(s) to %s",
                            profiles_count,
                            self.global_profiles_file,
                        )
                        return True

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

        except Exception as exc:
            logger.error(
                "Failed to save global profiles to %s: %s",
                self.global_profiles_file,
                exc,
            )
            return False

    def list_global_profiles(self) -> List[Dict[str, Any]]:
        """List all global profiles with metadata.

        Returns:
            List of profile dictionaries with id and name keys.
        """
        with self._global_profiles_lock:
            data = self.load_global_profiles()
            profiles = data.get("profiles", {})
            return [
                {
                    "id": profile_id,
                    "name": profile_data.get("name", profile_id),
                    **profile_data,
                }
                for profile_id, profile_data in profiles.items()
            ]

    def get_global_profile(self, profile_id: str) -> Dict[str, Any] | None:
        """Get a single global profile by ID.

        Args:
            profile_id: Profile ID (key in profiles.yml).

        Returns:
            Profile dictionary or None if not found.
        """
        with self._global_profiles_lock:
            data = self.load_global_profiles()
            profiles = data.get("profiles", {})
            profile_data = profiles.get(profile_id)
            if profile_data:
                return {"id": profile_id, **profile_data}
            return None

    def create_global_profile(
        self, profile_id: str, profile_data: Dict[str, Any]
    ) -> bool:
        """Create a new global profile.

        Args:
            profile_id: Unique profile ID.
            profile_data: Profile configuration (keywords, weights, etc.).

        Returns:
            True on success, False if profile already exists or on error.
        """
        if not profile_id or not profile_id.strip():
            logger.warning("Cannot create global profile without an ID")
            return False

        with self._global_profiles_lock:
            data = self.load_global_profiles()
            profiles = data.get("profiles", {})

            if profile_id in profiles:
                logger.warning(
                    "Global profile '%s' already exists, use update instead", profile_id
                )
                return False

            profiles[profile_id] = profile_data
            data["profiles"] = profiles
            return self.save_global_profiles(data)

    def update_global_profile(
        self, profile_id: str, profile_data: Dict[str, Any]
    ) -> bool:
        """Update an existing global profile.

        Args:
            profile_id: Profile ID to update.
            profile_data: New profile configuration.

        Returns:
            True on success, False if profile doesn't exist or on error.
        """
        with self._global_profiles_lock:
            data = self.load_global_profiles()
            profiles = data.get("profiles", {})

            if profile_id not in profiles:
                logger.warning(
                    "Global profile '%s' not found, use create instead", profile_id
                )
                return False

            profiles[profile_id] = profile_data
            data["profiles"] = profiles
            return self.save_global_profiles(data)

    def upsert_global_profile(
        self, profile_id: str, profile_data: Dict[str, Any]
    ) -> bool:
        """Insert or update a global profile.

        Args:
            profile_id: Profile ID.
            profile_data: Profile configuration.

        Returns:
            True on success, False on error.
        """
        with self._global_profiles_lock:
            data = self.load_global_profiles()
            profiles = data.get("profiles", {})
            profiles[profile_id] = profile_data
            data["profiles"] = profiles
            return self.save_global_profiles(data)

    def delete_global_profile(self, profile_id: str) -> bool:
        """Delete a global profile by ID.

        Args:
            profile_id: Profile ID to delete.

        Returns:
            True if deleted or didn't exist, False on error.
        """
        with self._global_profiles_lock:
            data = self.load_global_profiles()
            profiles = data.get("profiles", {})
            if profile_id in profiles:
                del profiles[profile_id]
                data["profiles"] = profiles
                return self.save_global_profiles(data)
            # Not found is not an error for deletion
            return True

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

    # =========================================================================
    # Alert Profiles (JSON-based)
    # =========================================================================

    def load_alert_profiles(self) -> Dict[str, Any]:
        """Load alert profiles from JSON file with file locking.

        Returns:
            Dictionary of alert profiles, empty dict if file doesn't exist or on error.
        """
        try:
            if not self.alert_profiles_file.exists():
                logger.debug(
                    "Alert profiles file does not exist: %s", self.alert_profiles_file
                )
                return {}

            # Open file and acquire shared lock for reading
            with open(self.alert_profiles_file, "r", encoding="utf-8") as f:
                # Acquire shared (read) lock to prevent reading partial writes
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                    logger.debug("Loaded %d alert profile(s)", len(data))
                    return data
                finally:
                    # Release shared lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.error("Failed to load alert profiles: %s", exc)
            return {}

    def save_alert_profiles(self, profiles: Dict[str, Any]) -> bool:
        """Save alert profiles to JSON file with atomic write and file locking.

        Args:
            profiles: Dictionary of alert profiles to save.

        Returns:
            True on success, False on error.
        """
        try:
            # Ensure data directory exists
            self.alert_profiles_file.parent.mkdir(parents=True, exist_ok=True)

            # Open target file and acquire exclusive lock for inter-process safety
            # Create the file if it doesn't exist
            target_fd = os.open(
                str(self.alert_profiles_file), os.O_CREAT | os.O_WRONLY, 0o644
            )

            try:
                # Acquire exclusive lock on target file
                fcntl.flock(target_fd, fcntl.LOCK_EX)

                try:
                    # Create temp file in same directory for atomic rename
                    temp_fd, temp_path = tempfile.mkstemp(
                        dir=self.alert_profiles_file.parent,
                        prefix=".alert_profiles_",
                        suffix=".tmp",
                    )

                    try:
                        # Write to temp file with flush + fsync
                        with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_f:
                            json.dump(profiles, temp_f, indent=2, sort_keys=True)
                            temp_f.flush()
                            os.fsync(temp_f.fileno())

                        # Atomic replace: os.replace is guaranteed atomic on POSIX
                        os.replace(temp_path, str(self.alert_profiles_file))
                        logger.debug("Saved %d alert profile(s)", len(profiles))
                        return True

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

        except Exception as exc:
            logger.error("Failed to save alert profiles: %s", exc)
            return False

    def get_alert_profile(self, name: str) -> Dict[str, Any] | None:
        """Get a single alert profile by name.

        Args:
            name: Profile name.

        Returns:
            Alert profile dictionary or None if not found.
        """
        with self._alert_profiles_lock:
            profiles = self.load_alert_profiles()
            return profiles.get(name)

    def upsert_alert_profile(self, profile_dict: Dict[str, Any]) -> bool:
        """Insert or update an alert profile.

        Args:
            profile_dict: Profile data with 'name' key.

        Returns:
            True on success, False on error or missing name.
        """
        name = profile_dict.get("name", "").strip()
        if not name:
            logger.warning("Cannot upsert alert profile without a name")
            return False

        with self._alert_profiles_lock:
            profiles = self.load_alert_profiles()
            profiles[name] = profile_dict
            return self.save_alert_profiles(profiles)

    def delete_alert_profile(self, name: str) -> bool:
        """Delete an alert profile by name.

        Args:
            name: Profile name to delete.

        Returns:
            True if deleted or didn't exist, False on error.
        """
        with self._alert_profiles_lock:
            profiles = self.load_alert_profiles()
            if name in profiles:
                del profiles[name]
                return self.save_alert_profiles(profiles)
            return True

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


def init_profile_service(data_dir: Path | None = None) -> ProfileService:
    """Initialize the global ProfileService instance.

    Args:
        data_dir: Optional data directory override.

    Returns:
        ProfileService instance.
    """
    global _profile_service
    _profile_service = ProfileService(data_dir)
    return _profile_service
