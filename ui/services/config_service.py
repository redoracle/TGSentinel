"""Configuration service for TG Sentinel UI.

This service handles all configuration file operations including:
- Reading and writing config.yml
- Validating configuration changes
- Managing config backups
- Coordinating with Sentinel service via HTTP API
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)


class ConfigService:
    """Service for configuration file management.

    This service is responsible for all operations involving the
    tgsentinel.yml configuration file. It ensures that configuration
    changes are properly validated and coordinated with the Sentinel
    service when necessary.
    """

    def __init__(self, config_path: Path):
        """Initialize ConfigService.

        Args:
            config_path: Path to the tgsentinel.yml configuration file
        """
        self.config_path = config_path
        self.backup_dir = config_path.parent / "backups"
        self.backup_dir.mkdir(exist_ok=True)

    def read_config(self) -> Dict[str, Any]:
        """Read configuration from file.

        Returns:
            Configuration dictionary

        Raises:
            FileNotFoundError: If config file does not exist
            yaml.YAMLError: If config file is not valid YAML
        """
        with open(self.config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            # Only return empty dict if loaded is None (empty file or only comments)
            # Preserve legitimate falsy values like false, 0, [], ""
            if loaded is None:
                return {}
            return loaded

    def write_config(self, config_data: Dict[str, Any], backup: bool = True) -> None:
        """Write configuration to file.

        Args:
            config_data: Configuration dictionary to write
            backup: Whether to create a backup before writing

        Raises:
            yaml.YAMLError: If config_data cannot be serialized to YAML
        """
        if backup and self.config_path.exists():
            self._create_backup()

        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                config_data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        logger.info(f"Configuration written to {self.config_path}")

    def _create_backup(self) -> Path:
        """Create a timestamped backup of the current config file.

        Returns:
            Path to the backup file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = self.backup_dir / f"tgsentinel_{timestamp}.yml"
        shutil.copy2(self.config_path, backup_path)
        logger.info(f"Created config backup: {backup_path}")
        return backup_path

    def get_config_hash(self) -> str:
        """Calculate hash of current configuration file.

        Returns:
            SHA256 hash of config file contents
        """
        with open(self.config_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def validate_config(self, config_data: Dict[str, Any]) -> tuple[bool, str]:
        """Validate configuration data.

        Args:
            config_data: Configuration dictionary to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        required_sections = ["telegram", "scoring", "channels"]

        for section in required_sections:
            if section not in config_data:
                return False, f"Missing required section: {section}"

        # Validate telegram section
        telegram = config_data.get("telegram", {})
        if not isinstance(telegram.get("api_id"), int):
            return False, "telegram.api_id must be an integer"
        if not isinstance(telegram.get("api_hash"), str):
            return False, "telegram.api_hash must be a string"

        # Validate scoring section
        scoring = config_data.get("scoring", {})
        if not isinstance(scoring.get("threshold_high", 0), (int, float)):
            return False, "scoring.threshold_high must be a number"
        if not isinstance(scoring.get("threshold_medium", 0), (int, float)):
            return False, "scoring.threshold_medium must be a number"

        # Validate channels section
        channels = config_data.get("channels", [])
        if not isinstance(channels, list):
            return False, "channels must be a list"

        return True, ""

    def merge_config(
        self, base_config: Dict[str, Any], updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge configuration updates into base config.

        Args:
            base_config: Base configuration dictionary
            updates: Updates to merge

        Returns:
            Merged configuration dictionary
        """
        result = base_config.copy()

        for key, value in updates.items():
            if (
                isinstance(value, dict)
                and key in result
                and isinstance(result[key], dict)
            ):
                result[key] = self.merge_config(result[key], value)
            else:
                result[key] = value

        return result

    def list_backups(self) -> list[Path]:
        """List all available config backups.

        Returns:
            List of backup file paths, sorted by timestamp (newest first)
        """
        backups = sorted(
            self.backup_dir.glob("tgsentinel_*.yml"),
            reverse=True,
        )
        return backups

    def restore_backup(self, backup_path: Path) -> None:
        """Restore configuration from a backup file.

        Args:
            backup_path: Path to backup file

        Raises:
            FileNotFoundError: If backup file does not exist
        """
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        # Create backup of current config before restoring
        self._create_backup()

        # Restore from backup
        shutil.copy2(backup_path, self.config_path)
        logger.info(f"Restored configuration from backup: {backup_path}")
