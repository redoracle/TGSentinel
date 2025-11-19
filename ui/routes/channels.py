"""Channel management API routes for TG Sentinel UI.

This blueprint handles all channel-related operations:
- List channels
- Add channels
- Delete channels
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

import yaml
from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

# Blueprint setup
channels_bp = Blueprint("channels", __name__, url_prefix="/api/config/channels")


def init_channels_routes(
    config=None, reload_config_fn: Callable[[], Any] | None = None
):
    """Initialize channels blueprint with dependencies.

    Args:
        config: Application config object
        reload_config_fn: Function to reload configuration
    """
    # Store dependencies in current_app.extensions to avoid module-level globals
    if not hasattr(current_app, "extensions"):
        current_app.extensions = {}

    if "channels" not in current_app.extensions:
        current_app.extensions["channels"] = {}

    current_app.extensions["channels"]["config"] = config
    current_app.extensions["channels"]["reload_config_fn"] = reload_config_fn
    logger.info("Channels routes initialized")


def _current_config():
    """Return the most up-to-date config object available."""
    try:
        from core import get_deps

        deps = get_deps()
        if getattr(deps, "config", None):
            return deps.config
    except Exception as exc:  # pragma: no cover - defensive path
        logger.debug("Could not obtain deps.config: %s", exc)

    # Fallback to config stored in app.extensions
    try:
        return current_app.extensions.get("channels", {}).get("config")
    except RuntimeError:  # pragma: no cover - outside app context
        return None


def _fetch_channels_from_sentinel() -> List[Dict[str, Any]] | None:
    """Fetch channels from Sentinel API (single source of truth)."""
    import requests

    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if response.ok:
            config_data = response.json().get("data", {})
            channels = config_data.get("channels", [])
            logger.debug(f"Fetched {len(channels)} channels from Sentinel API")
            return list(channels)
        else:
            logger.warning(
                f"Failed to fetch channels from Sentinel: {response.status_code}"
            )
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return None


def _normalize_channel_entry(channel: Any, fallback_id: int) -> Dict[str, Any]:
    """Convert raw channel objects/dicts into the API schema."""

    def _get(field: str, default: Any) -> Any:
        if isinstance(channel, dict):
            return channel.get(field, default)
        return getattr(channel, field, default)

    channel_id = _get("id", fallback_id)
    return {
        "id": channel_id,
        "name": _get("name", f"Channel {channel_id}"),
        "vip_senders": list(_get("vip_senders", [])),
        "keywords": list(_get("keywords", [])),
        "reaction_threshold": _get("reaction_threshold", 5),
        "reply_threshold": _get("reply_threshold", 3),
        "rate_limit_per_hour": _get("rate_limit_per_hour", 10),
        "enabled": _get("enabled", True),
    }


def _serialize_channels():
    """Serialize channels from Sentinel API to JSON-friendly format."""
    channels_from_sentinel = _fetch_channels_from_sentinel()
    if channels_from_sentinel is not None:
        source_channels = channels_from_sentinel
    else:
        # Fallback to deps.config if Sentinel unavailable
        config_obj = _current_config()
        source_channels = list(getattr(config_obj, "channels", []) or [])

    result: List[Dict[str, Any]] = []
    for idx, channel in enumerate(source_channels, start=1):
        result.append(_normalize_channel_entry(channel, idx))
    return result


def _ensure_config_file_exists(config_path: Path):
    """Ensure config file exists, create from template if needed."""
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # Create minimal config
        with open(config_path, "w") as f:
            yaml.dump({"channels": []}, f)
        logger.info(f"Created new config file at {config_path}")


# ═══════════════════════════════════════════════════════════════════
# Channel Routes
# ═══════════════════════════════════════════════════════════════════


@channels_bp.get("")
def list_channels():
    """Get list of all configured channels."""
    return jsonify({"channels": _serialize_channels()})


@channels_bp.post("/add")
def add_channels():
    """Add channels via Sentinel API (single source of truth)."""
    import requests

    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    channels_to_add = payload.get("channels", [])
    if not channels_to_add or not isinstance(channels_to_add, list):
        return (
            jsonify(
                {"status": "error", "message": "channels array is required in payload"}
            ),
            400,
        )

    try:
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )

        # Get current config from Sentinel
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if not response.ok:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Could not fetch current config from Sentinel",
                    }
                ),
                503,
            )

        current_config = response.json().get("data", {})
        existing_channels = current_config.get("channels", [])
        existing_ids = {ch.get("id") for ch in existing_channels}

        # Add new channels (skip duplicates)
        added_count = 0
        for new_channel in channels_to_add:
            channel_id = new_channel.get("id")
            if channel_id and channel_id not in existing_ids:
                # Create channel entry with default values
                channel_entry = {
                    "id": channel_id,
                    "name": new_channel.get("name", "Unknown Channel"),
                    "vip_senders": [],
                    "keywords": [],
                    "reaction_threshold": 5,
                    "reply_threshold": 3,
                    "rate_limit_per_hour": 10,
                }
                existing_channels.append(channel_entry)
                existing_ids.add(channel_id)
                added_count += 1

        # Update config via Sentinel API
        update_response = requests.post(
            f"{sentinel_api_url}/config",
            json={"channels": existing_channels},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if not update_response.ok:
            logger.error(
                f"Sentinel rejected channel update: {update_response.status_code}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to update config via Sentinel",
                    }
                ),
                502,
            )

        logger.info(f"Added {added_count} new channels via Sentinel API")

        # Reload config in UI to reflect changes immediately
        reload_fn = current_app.extensions.get("channels", {}).get("reload_config_fn")
        if reload_fn:
            reload_fn()

        return jsonify({"status": "ok", "added": added_count})

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as exc:
        logger.error(f"Channel add operation failed: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@channels_bp.route("/<int:chat_id>", methods=["DELETE"])
def delete_channel(chat_id):
    """Remove a channel from monitoring configuration via Sentinel API."""
    import requests

    try:
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )

        # Get current config from Sentinel
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if not response.ok:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Could not fetch current config from Sentinel",
                    }
                ),
                503,
            )

        config = response.json().get("data", {})
        channels = config.get("channels", [])
        original_count = len(channels)

        # Remove channel
        channels = [c for c in channels if c.get("id") != chat_id]

        if len(channels) == original_count:
            return jsonify({"status": "error", "message": "Channel not found"}), 404

        # Update config via Sentinel API
        update_response = requests.post(
            f"{sentinel_api_url}/config",
            json={"channels": channels},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if not update_response.ok:
            logger.error(
                f"Sentinel rejected channel deletion: {update_response.status_code}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to update config via Sentinel",
                    }
                ),
                502,
            )

        logger.info(f"Removed channel {chat_id} via Sentinel API")

        # Reload config in UI
        reload_fn = current_app.extensions.get("channels", {}).get("reload_config_fn")
        if reload_fn:
            reload_fn()

        return jsonify({"status": "ok", "message": "Channel removed"}), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as e:
        logger.error(f"Failed to delete channel: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
