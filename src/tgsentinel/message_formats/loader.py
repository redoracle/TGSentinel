"""
Message format loader with YAML storage and caching.

Handles loading, saving, and caching of message format templates.
Thread-safe with RLock for concurrent access.
"""

import logging
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .defaults import DEFAULT_FORMATS

log = logging.getLogger(__name__)

# Thread-safe format cache
_format_cache: dict[str, Any] | None = None
_cache_lock = threading.RLock()
_cache_mtime: float = 0.0

# Default config path - can be overridden via environment
DEFAULT_FORMATS_PATH = "/app/config/message_formats.yml"


def get_formats_path() -> Path:
    """Get the path to the message formats YAML file."""
    path_str = os.environ.get("MESSAGE_FORMATS_PATH", DEFAULT_FORMATS_PATH)
    return Path(path_str)


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep merge two dictionaries.

    Override values take precedence over base values.
    Nested dicts are merged recursively.
    """
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_message_formats(force_reload: bool = False) -> dict[str, Any]:
    """
    Load message formats from YAML file with caching.

    Falls back to defaults if file doesn't exist or is invalid.
    Merged with defaults to ensure all required formats exist.

    Args:
        force_reload: If True, bypass cache and reload from disk

    Returns:
        Dictionary of message format templates
    """
    global _format_cache, _cache_mtime

    formats_path = get_formats_path()

    with _cache_lock:
        # Check if we can use cached version
        if not force_reload and _format_cache is not None:
            try:
                if formats_path.exists():
                    current_mtime = formats_path.stat().st_mtime
                    if current_mtime <= _cache_mtime:
                        return deepcopy(_format_cache)
            except OSError:
                # File access error, use cache if available
                return deepcopy(_format_cache)

        # Load from file or use defaults
        if formats_path.exists():
            try:
                with open(formats_path, encoding="utf-8") as f:
                    user_formats = yaml.safe_load(f) or {}

                # Merge with defaults to ensure all required formats exist
                _format_cache = _deep_merge(DEFAULT_FORMATS, user_formats)
                _cache_mtime = formats_path.stat().st_mtime

                log.info(
                    "[MESSAGE-FORMATS] Loaded custom formats",
                    extra={"path": str(formats_path)},
                )
            except yaml.YAMLError as e:
                log.error(
                    "[MESSAGE-FORMATS] YAML parse error, using defaults",
                    extra={"error": str(e), "path": str(formats_path)},
                )
                _format_cache = deepcopy(DEFAULT_FORMATS)
            except OSError as e:
                log.error(
                    "[MESSAGE-FORMATS] File read error, using defaults",
                    extra={"error": str(e), "path": str(formats_path)},
                )
                _format_cache = deepcopy(DEFAULT_FORMATS)
        else:
            log.info(
                "[MESSAGE-FORMATS] No custom formats file, using defaults",
                extra={"path": str(formats_path)},
            )
            _format_cache = deepcopy(DEFAULT_FORMATS)

        return deepcopy(_format_cache)


def save_message_formats(formats: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Save message formats to YAML file.

    Creates backup of existing file before overwriting.

    Args:
        formats: Dictionary of message format templates

    Returns:
        Tuple of (success, error_message)
    """
    global _format_cache, _cache_mtime

    formats_path = get_formats_path()
    backup_path = formats_path.with_suffix(".yml.bak")

    try:
        # Ensure config directory exists
        formats_path.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing file
        if formats_path.exists():
            try:
                import shutil

                shutil.copy2(formats_path, backup_path)
                log.info(
                    "[MESSAGE-FORMATS] Created backup",
                    extra={"backup_path": str(backup_path)},
                )
            except OSError as e:
                log.warning(
                    "[MESSAGE-FORMATS] Failed to create backup",
                    extra={"error": str(e)},
                )

        # Write new formats
        with open(formats_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                formats,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )

        # Update cache
        with _cache_lock:
            _format_cache = _deep_merge(DEFAULT_FORMATS, formats)
            _cache_mtime = formats_path.stat().st_mtime

        log.info(
            "[MESSAGE-FORMATS] Saved formats",
            extra={"path": str(formats_path)},
        )
        return True, None

    except yaml.YAMLError as e:
        error_msg = f"YAML serialization error: {e}"
        log.error(
            "[MESSAGE-FORMATS] Save failed",
            extra={"error": error_msg},
        )
        return False, error_msg
    except OSError as e:
        error_msg = f"File write error: {e}"
        log.error(
            "[MESSAGE-FORMATS] Save failed",
            extra={"error": error_msg},
        )
        return False, error_msg


def get_format(format_type: str, subtype: str | None = None) -> str:
    """
    Get a specific format template.

    Args:
        format_type: One of 'dm_alerts', 'saved_messages', 'digest', 'webhook_payload'
        subtype: For digest formats, one of 'header', 'entry', 'trigger_format'

    Returns:
        Template string, or empty string if not found
    """
    formats = load_message_formats()

    try:
        if subtype:
            section = formats.get(format_type, {})
            if isinstance(section, dict):
                subsection = section.get(subtype, {})
                if isinstance(subsection, dict):
                    return subsection.get("template", "")
        else:
            section = formats.get(format_type, {})
            if isinstance(section, dict):
                return section.get("template", "")
    except (KeyError, TypeError) as e:
        log.warning(
            "[MESSAGE-FORMATS] Format lookup failed",
            extra={
                "format_type": format_type,
                "subtype": subtype,
                "error": str(e),
            },
        )

    return ""


def reload_formats() -> dict[str, Any]:
    """
    Force reload formats from disk, bypassing cache.

    Returns:
        Fresh copy of message formats
    """
    return load_message_formats(force_reload=True)


def reset_to_defaults() -> tuple[bool, str | None]:
    """
    Reset message formats to defaults.

    Saves default formats to the YAML file.

    Returns:
        Tuple of (success, error_message)
    """
    return save_message_formats(DEFAULT_FORMATS)


def get_format_metadata(format_type: str, subtype: str | None = None) -> dict[str, Any]:
    """
    Get metadata for a format template (description, variables).

    Args:
        format_type: One of 'dm_alerts', 'saved_messages', 'digest', 'webhook_payload'
        subtype: For digest formats, one of 'header', 'entry', 'trigger_format'

    Returns:
        Dictionary with 'description' and 'variables' keys
    """
    formats = load_message_formats()

    try:
        if subtype:
            section = formats.get(format_type, {}).get(subtype, {})
        else:
            section = formats.get(format_type, {})

        return {
            "description": section.get("description", ""),
            "variables": section.get("variables", {}),
        }
    except (KeyError, TypeError):
        return {"description": "", "variables": {}}
