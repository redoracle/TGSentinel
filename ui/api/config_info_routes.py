"""Configuration information routes for TG Sentinel UI.

This module provides endpoints for retrieving and updating configuration
information including Telegram settings, alerts, digest, and channels.
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional, TypeVar

try:
    from pydantic import BaseModel as PydanticBaseModel  # type: ignore[assignment]
    from pydantic import Field as PydanticField  # type: ignore[assignment]
    from pydantic import field_validator as pydantic_field_validator  # type: ignore[assignment]
    from pydantic import model_validator as pydantic_model_validator  # type: ignore[assignment]
    from pydantic import ConfigDict as PydanticConfigDict  # type: ignore[assignment]

    PYDANTIC_AVAILABLE = True
    BaseModel = PydanticBaseModel  # type: ignore[misc,assignment]
    Field = PydanticField  # type: ignore[misc,assignment]
    field_validator = pydantic_field_validator  # type: ignore[misc,assignment]
    model_validator = pydantic_model_validator  # type: ignore[misc,assignment]
    ConfigDict = PydanticConfigDict  # type: ignore[misc,assignment]
except ImportError:
    PYDANTIC_AVAILABLE = False
    # Provide dummy implementations when Pydantic is not available
    BaseModel = object  # type: ignore[misc,assignment]

    _F = TypeVar("_F", bound=Callable[..., Any])

    def model_validator(*args: Any, **kwargs: Any) -> Callable[[_F], _F]:  # type: ignore[misc]
        """Fallback model_validator when Pydantic unavailable."""

        def decorator(func: _F) -> _F:
            return func

        return decorator

    def ConfigDict(**kwargs: Any) -> Any:  # type: ignore[misc]
        """Fallback ConfigDict when Pydantic unavailable."""
        return None

    def field_validator(*args: Any, **kwargs: Any) -> Callable[[_F], _F]:  # type: ignore[misc]
        """Fallback field_validator when Pydantic unavailable."""

        def decorator(func: _F) -> _F:
            return func

        return decorator

    def Field(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        """Fallback Field when Pydantic unavailable."""
        return None


from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

config_info_bp = Blueprint("config", __name__)


def _refresh_ui_config(context: str) -> None:
    """Reload TG Sentinel config so UI immediately reflects latest changes."""
    try:
        from core import get_deps
        from tgsentinel.config import load_config

        deps = get_deps()
        deps.config = load_config()
        logger.info("Reloaded configuration after %s", context)
    except Exception as exc:  # pragma: no cover - defensive path
        logger.warning("Failed to reload configuration after %s: %s", context, exc)


# Pydantic validation schemas
if PYDANTIC_AVAILABLE:
    from pydantic import BaseModel as PydanticBaseModel

    class ConfigPayload(PydanticBaseModel):
        """Validation schema for configuration updates."""

        # Telegram settings
        phone_number: Optional[str] = Field(None, pattern=r"^\+[1-9]\d{1,14}$")
        api_id: Optional[int] = Field(None, gt=0)
        api_hash: Optional[str] = Field(None, min_length=32)

        # Alerts settings
        mode: Optional[str] = Field(None, pattern=r"^(dm|channel|both)$")
        target_channel: Optional[str] = None
        digest: Optional[str] = Field(None, pattern=r"^(none|hourly|daily|both)$")
        digest_top: Optional[int] = Field(None, ge=1, le=100)
        dedupe_window: Optional[int] = Field(None, ge=0, le=1440)
        rate_limit_per_channel: Optional[int] = Field(None, ge=0, le=1000)
        template: Optional[str] = None

        # Semantic settings
        embedding_model: Optional[str] = None
        similarity_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
        decay_window: Optional[int] = Field(None, ge=1)
        interests: Optional[List[str]] = Field(None, max_length=50)
        feedback_learning: Optional[bool] = None

        # Heuristic weights
        weight_0: Optional[float] = Field(None, ge=0.0, le=1.0)
        weight_1: Optional[float] = Field(None, ge=0.0, le=1.0)
        weight_2: Optional[float] = Field(None, ge=0.0, le=1.0)
        weight_3: Optional[float] = Field(None, ge=0.0, le=1.0)
        weight_4: Optional[float] = Field(None, ge=0.0, le=1.0)
        weight_5: Optional[float] = Field(None, ge=0.0, le=1.0)

        # System settings
        redis_host: Optional[str] = None
        redis_port: Optional[int] = Field(None, ge=1, le=65535)
        database_uri: Optional[str] = None
        retention_days: Optional[int] = Field(None, ge=1, le=365)
        max_messages: Optional[int] = Field(None, ge=1, le=10000)
        cleanup_enabled: Optional[bool] = None
        cleanup_interval_hours: Optional[int] = Field(None, ge=1, le=168)
        vacuum_on_cleanup: Optional[bool] = None
        metrics_endpoint: Optional[str] = None
        logging_level: Optional[str] = Field(None, pattern=r"^(debug|info|warn|error)$")
        auto_restart: Optional[bool] = None

        model_config = ConfigDict(  # type: ignore[assignment]
            extra="allow"
        )  # Allow additional fields for flexibility

        @field_validator("interests")
        @classmethod
        def validate_interests_unique(
            cls, v: Optional[List[str]]
        ) -> Optional[List[str]]:
            if v is not None and len(v) != len(set(v)):
                raise ValueError("Interests must be unique")
            return v

        @field_validator("target_channel")
        @classmethod
        def validate_target_channel(cls, v: Optional[str], info) -> Optional[str]:
            mode = info.data.get("mode")
            if mode in ["channel", "both"] and not v:
                raise ValueError(
                    "target_channel required when mode is 'channel' or 'both'"
                )
            if v and not (v.startswith("@") or v.lstrip("-").isdigit()):
                raise ValueError(
                    "target_channel must start with @ or be a numeric chat_id"
                )
            return v

        @field_validator("cleanup_interval_hours")
        @classmethod
        def validate_cleanup_interval(cls, v: Optional[int], info) -> Optional[int]:
            """Ensure cleanup_interval_hours is set when cleanup_enabled is True."""
            cleanup_enabled = info.data.get("cleanup_enabled")
            if cleanup_enabled is True and v is None:
                raise ValueError(
                    "cleanup_interval_hours must be set when cleanup_enabled is True"
                )
            return v

        @model_validator(mode="after")
        def validate_vacuum_requires_cleanup(self) -> "ConfigPayload":
            """Ensure vacuum_on_cleanup requires cleanup_enabled to be True."""
            if self.vacuum_on_cleanup is True and self.cleanup_enabled is not True:
                raise ValueError(
                    "vacuum_on_cleanup requires cleanup_enabled to be True"
                )
            return self


def _format_display_phone(phone: str) -> str:
    """Format phone number for display (mask middle digits)."""
    if not phone or len(phone) < 4:
        return phone
    return phone[:2] + "*" * (len(phone) - 4) + phone[-2:]


@config_info_bp.post("/api/config/save")
def api_config_save():
    """Save configuration by forwarding to Sentinel (single source of truth)."""
    import requests
    from core import get_deps

    deps = get_deps()

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

    # Validate payload with pydantic if available
    if PYDANTIC_AVAILABLE:
        try:
            validated = ConfigPayload(**payload)
            payload = validated.dict(exclude_none=True)
        except Exception as validation_error:
            logger.warning("Config validation failed: %s", validation_error)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Validation failed: {str(validation_error)}",
                    }
                ),
                400,
            )

    logger.info("Received configuration update request: keys=%s", list(payload))

    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        # Forward config update to Sentinel
        response = requests.post(
            f"{sentinel_api_url}/config",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if not response.ok:
            logger.error(
                f"Sentinel rejected config update: {response.status_code} - {response.text}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Sentinel API error: {response.status_code}",
                    }
                ),
                response.status_code,
            )

        sentinel_response = response.json()
        logger.info("Config successfully updated via Sentinel")

        # Reload config in UI (for backwards compatibility, though UI should fetch from Sentinel)
        from tgsentinel.config import load_config

        try:
            deps.config = load_config()
            logger.info("Reloaded TG Sentinel configuration in UI")
        except Exception as exc:
            logger.warning("Failed to reload config in UI: %s", exc)

        return jsonify({"status": "ok", "data": sentinel_response.get("data", {})})

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as exc:
        logger.error("Unexpected error during config save: %s", exc)
        return jsonify({"status": "error", "message": "Internal error"}), 500


@config_info_bp.route("/api/config/current", methods=["GET"])
def api_config_current():
    """Get current configuration from Sentinel (single source of truth)."""
    import requests
    from core import get_deps

    sentinel_api_url = os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")

    try:
        # Fetch config from Sentinel
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)

        if not response.ok:
            logger.error(
                f"Failed to fetch config from Sentinel: {response.status_code}"
            )
            # Fallback to empty config
            return jsonify(
                {
                    "telegram": {
                        "api_id": os.getenv("TG_API_ID", ""),
                        "api_hash": os.getenv("TG_API_HASH", ""),
                        "phone_number": os.getenv("TG_PHONE", ""),
                        "session": "",
                    },
                    "alerts": {"mode": "dm", "target_channel": ""},
                    "digest": {"hourly": True, "daily": False, "top_n": 10},
                    "redis": {"host": "redis", "port": 6379},
                    "semantic": {"embeddings_model": "", "similarity_threshold": 0.42},
                    "database_uri": "",
                    "channels": [],
                    "monitored_users": [],
                }
            )

        sentinel_data = response.json()
        config_data = sentinel_data.get("data", {})

        # Enhance with UI-specific data (user info from Redis)
        from redis_cache import load_cached_user_info

        deps = get_deps()
        redis_client = deps.redis_client
        cached_user = load_cached_user_info(redis_client) if redis_client else None

        telegram_cfg = config_data.get("telegram", {})
        telegram_cfg["api_id"] = os.getenv("TG_API_ID", "")
        telegram_cfg["api_hash"] = os.getenv("TG_API_HASH", "")
        telegram_cfg["phone_number"] = _format_display_phone(os.getenv("TG_PHONE", ""))

        if cached_user:
            if cached_user.get("phone"):
                telegram_cfg["phone_number"] = cached_user["phone"]
            if cached_user.get("username"):
                telegram_cfg["username"] = cached_user["username"]
            if cached_user.get("avatar"):
                telegram_cfg["avatar"] = cached_user["avatar"]
            if cached_user.get("user_id"):
                telegram_cfg["user_id"] = cached_user["user_id"]

        logger.info(
            f"Fetched config from Sentinel: channels={len(config_data.get('channels', []))}, users={len(config_data.get('monitored_users', []))}"
        )

        # Return enhanced config (including system settings)
        return jsonify(
            {
                "telegram": telegram_cfg,
                "alerts": config_data.get("alerts", {}),
                "digest": config_data.get("digest", {}),
                "redis": config_data.get("redis", {}),
                "semantic": {
                    "embeddings_model": config_data.get("embeddings_model", ""),
                    "similarity_threshold": config_data.get(
                        "similarity_threshold", 0.42
                    ),
                },
                "database_uri": config_data.get("database_uri", ""),
                "channels": config_data.get("channels", []),
                "monitored_users": config_data.get("monitored_users", []),
                "system": config_data.get("system", {}),
            }
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Sentinel API: {e}")
        # Return minimal fallback config
        return jsonify(
            {
                "telegram": {
                    "api_id": os.getenv("TG_API_ID", ""),
                    "api_hash": os.getenv("TG_API_HASH", ""),
                    "phone_number": os.getenv("TG_PHONE", ""),
                    "session": "",
                },
                "alerts": {"mode": "dm", "target_channel": ""},
                "digest": {"hourly": True, "daily": False, "top_n": 10},
                "redis": {"host": "redis", "port": 6379},
                "semantic": {"embeddings_model": "", "similarity_threshold": 0.42},
                "database_uri": "",
                "channels": [],
                "monitored_users": [],
            }
        )


@config_info_bp.get("/api/config/interests")
def api_config_interests():
    """Get list of configured interests."""
    from core import get_deps

    deps = get_deps()
    config = deps.config

    interests_attr = getattr(config, "interests", None) if config else None
    interests = list(interests_attr) if interests_attr is not None else []
    return jsonify({"interests": interests})


# =================================================================
# User Management Endpoints (copied from config_routes.py)
# =================================================================


@config_info_bp.get("/api/config/users")
def api_config_users_list():
    """Get list of all monitored users."""
    import yaml
    from pathlib import Path

    try:
        config_file = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
        if not config_file.exists():
            return jsonify({"users": []}), 200

        with open(config_file, "r") as f:
            config = yaml.safe_load(f) or {}

        users = config.get("monitored_users", [])

        return jsonify({"users": users}), 200
    except Exception as e:
        logger.error(f"Failed to list users: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_info_bp.post("/api/config/users/add")
def api_config_users_add():
    """Add new users to monitoring configuration."""
    import tempfile
    import shutil
    import yaml
    from pathlib import Path

    try:
        data = request.get_json()

        # Support both single user and array of users
        users_to_add = data.get("users", [])

        # Legacy support: if "user_id" is provided, treat as single user
        if "user_id" in data and not users_to_add:
            user_id = data.get("user_id")
            name = data.get("name")
            username = data.get("username", "")

            if not user_id:
                return jsonify({"status": "error", "message": "user_id required"}), 400

            users_to_add = [
                {"id": user_id, "name": name or f"User_{user_id}", "username": username}
            ]

        if not users_to_add:
            return jsonify({"status": "error", "message": "No users provided"}), 400

        config_file = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
        config = {}
        if config_file.exists():
            with open(config_file, "r") as f:
                config = yaml.safe_load(f) or {}

        users = config.get("monitored_users", [])
        existing_ids = {u.get("id") for u in users}

        added_count = 0
        skipped_count = 0

        for user_data in users_to_add:
            user_id = user_data.get("id")
            if not user_id:
                continue

            if user_id in existing_ids:
                skipped_count += 1
                continue

            users.append(
                {
                    "id": user_id,
                    "name": user_data.get("name") or f"User_{user_id}",
                    "username": user_data.get("username", ""),
                    "enabled": True,
                }
            )
            added_count += 1

        config["monitored_users"] = users

        with open(config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        _refresh_ui_config("users_add")

        message = f"Added {added_count} user(s)"
        if skipped_count > 0:
            message += f", skipped {skipped_count} (already configured)"

        return (
            jsonify(
                {
                    "status": "ok",
                    "message": message,
                    "added": added_count,
                    "skipped": skipped_count,
                }
            ),
            200,
        )
    except Exception as e:
        logger.error(f"Failed to add users: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_info_bp.route("/api/config/users/<user_id>", methods=["DELETE"])
def api_config_users_delete(user_id):
    """Remove a user from monitoring configuration."""
    import yaml
    from pathlib import Path

    try:
        # Convert to int here instead of in the route
        try:
            user_id = int(user_id)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid user_id"}), 400

        config_file = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
        if not config_file.exists():
            return (
                jsonify({"status": "error", "message": "No configuration found"}),
                404,
            )

        with open(config_file, "r") as f:
            config = yaml.safe_load(f) or {}

        users = config.get("monitored_users", [])
        original_count = len(users)

        # Remove user
        users = [u for u in users if u.get("id") != user_id]

        if len(users) == original_count:
            return jsonify({"status": "error", "message": "User not found"}), 404

        config["monitored_users"] = users

        with open(config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        _refresh_ui_config("users_delete")

        return jsonify({"status": "ok", "message": "User removed"}), 200
    except Exception as e:
        logger.error(f"Failed to delete user: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# =================================================================
# Channel Management Endpoints (add route for channels.add)
# =================================================================


@config_info_bp.get("/api/config/channels")
def api_config_channels():
    """Get list of configured channels."""
    import yaml
    from pathlib import Path

    try:
        config_file = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
        if not config_file.exists():
            return jsonify({"channels": []}), 200

        with open(config_file, "r") as f:
            config = yaml.safe_load(f) or {}

        channels = config.get("channels", [])
        return jsonify({"channels": channels}), 200
    except Exception as e:
        logger.error(f"Failed to get channels: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_info_bp.route("/api/config/channels/<chat_id>", methods=["DELETE"])
def api_config_channels_delete(chat_id):
    """Remove a channel from monitoring configuration."""
    import yaml
    from pathlib import Path

    try:
        # Convert to int here instead of in the route
        try:
            chat_id = int(chat_id)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid chat_id"}), 400

        config_file = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
        if not config_file.exists():
            return (
                jsonify({"status": "error", "message": "No configuration found"}),
                404,
            )

        with open(config_file, "r") as f:
            config = yaml.safe_load(f) or {}

        channels = config.get("channels", [])
        original_count = len(channels)

        # Remove channel
        channels = [c for c in channels if c.get("id") != chat_id]

        if len(channels) == original_count:
            return jsonify({"status": "error", "message": "Channel not found"}), 404

        config["channels"] = channels

        with open(config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        _refresh_ui_config("channels_delete")

        return jsonify({"status": "ok", "message": "Channel removed"}), 200
    except Exception as e:
        logger.error(f"Failed to delete channel: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_info_bp.route("/api/config/channels/<chat_id>", methods=["PATCH"])
def api_config_channels_patch(chat_id):
    """Update channel properties (e.g., enabled state)."""
    import yaml
    from pathlib import Path

    try:
        # Convert chat_id to int
        try:
            chat_id = int(chat_id)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid chat_id"}), 400

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        config_file = Path(os.getenv("TG_SENTINEL_CONFIG", "config/tgsentinel.yml"))
        if not config_file.exists():
            return (
                jsonify({"status": "error", "message": "No configuration found"}),
                404,
            )

        with open(config_file, "r") as f:
            config = yaml.safe_load(f) or {}

        channels = config.get("channels", [])
        channel_found = False

        # Update channel properties
        for channel in channels:
            if channel.get("id") == chat_id:
                channel_found = True

                # Update enabled state if provided
                if "enabled" in data:
                    channel["enabled"] = bool(data["enabled"])

                # Update other properties if provided
                for key in [
                    "name",
                    "vip_senders",
                    "keywords",
                    "reaction_threshold",
                    "reply_threshold",
                    "rate_limit_per_hour",
                ]:
                    if key in data:
                        channel[key] = data[key]

                break

        if not channel_found:
            return jsonify({"status": "error", "message": "Channel not found"}), 404

        config["channels"] = channels

        with open(config_file, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

        # Refresh UI config cache to reflect changes immediately
        _refresh_ui_config("channel_update")

        return jsonify({"status": "ok", "message": "Channel updated"}), 200
    except Exception as e:
        logger.error(f"Failed to update channel: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
