"""User management routes for TG Sentinel UI.

This module provides routes for managing monitored users via the Sentinel API.
All operations proxy to Sentinel as the single source of truth for configuration.
"""

import logging
import os
from typing import Optional, Callable

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

users_bp = Blueprint("users", __name__, url_prefix="/api/config/users")

# Optional config reload callback
_reload_config_fn: Optional[Callable[[], None]] = None


def set_reload_config_fn(fn: Callable[[], None]) -> None:
    """Set callback to reload config after user changes."""
    global _reload_config_fn
    _reload_config_fn = fn


@users_bp.route("", methods=["GET"])
def list_users():
    """Get list of all monitored users from Sentinel API."""
    import requests

    try:
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )

        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if not response.ok:
            logger.error(f"Sentinel API error: {response.status_code}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Could not fetch config from Sentinel",
                    }
                ),
                503,
            )

        config_data = response.json().get("data", {})
        users = config_data.get("monitored_users", [])

        return jsonify({"users": users}), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as e:
        logger.error(f"Failed to list users: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@users_bp.route("/add", methods=["POST"])
def add_users():
    """Add new users to monitoring configuration via Sentinel API."""
    import requests

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

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
                {
                    "id": user_id,
                    "name": name or f"User_{user_id}",
                    "username": username,
                    "enabled": True,
                }
            ]

        if not users_to_add:
            return jsonify({"status": "error", "message": "No users provided"}), 400

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
                    "enabled": user_data.get("enabled", True),
                }
            )
            added_count += 1

        # Update config via Sentinel API
        update_response = requests.post(
            f"{sentinel_api_url}/config",
            json={"monitored_users": users},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if not update_response.ok:
            logger.error(
                f"Sentinel rejected user addition: {update_response.status_code}"
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

        logger.info(f"Added {added_count} user(s) via Sentinel API")

        # Reload config in UI to reflect changes immediately
        if _reload_config_fn:
            _reload_config_fn()

        message = f"Added {added_count} user(s)"
        if skipped_count > 0:
            message += f", skipped {skipped_count} (already configured)"

        return jsonify({"status": "ok", "added": added_count, "message": message})

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as exc:
        logger.error(f"User add operation failed: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@users_bp.route("/<int:user_id>", methods=["GET"])
def get_user(user_id):
    """Get a single user's configuration."""
    import requests

    try:
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )

        response = requests.get(f"{sentinel_api_url}/config", timeout=5)
        if not response.ok:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Could not fetch config from Sentinel",
                    }
                ),
                503,
            )

        config_data = response.json().get("data", {})
        users = config_data.get("monitored_users", [])

        # Find the user
        for user in users:
            if user.get("id") == user_id:
                return jsonify({"status": "ok", "user": user})

        return jsonify({"status": "error", "message": "User not found"}), 404

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as exc:
        logger.error(f"Failed to get user: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@users_bp.route("/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    """Update a user's configuration via Sentinel API.

    Request body should contain fields to update:
    - name: User name
    - username: User username
    - enabled: Boolean
    - profiles: List of profile IDs to bind
    - overrides: Dict with keywords_extra, scoring_weights, etc.
    """
    import requests

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

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
        users = config.get("monitored_users", [])

        # Find the user to update
        user_index = None
        for i, u in enumerate(users):
            if u.get("id") == user_id:
                user_index = i
                break

        if user_index is None:
            return jsonify({"status": "error", "message": "User not found"}), 404

        # Update user fields from payload
        user = users[user_index]

        if "name" in data:
            user["name"] = data["name"]
        if "username" in data:
            user["username"] = data["username"]
        if "enabled" in data:
            user["enabled"] = data["enabled"]

        # Validate profile IDs if present
        if "profiles" in data:
            from ui.services.profiles_service import get_profile_service

            requested = data.get("profiles") or []
            if not isinstance(requested, list):
                return (
                    jsonify(
                        {"status": "error", "message": "'profiles' must be a list"}
                    ),
                    400,
                )
            svc = get_profile_service()
            available = {p["id"] for p in svc.list_global_profiles()}
            invalid = [p for p in requested if p not in available]
            if invalid:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Invalid profile ids: %s"
                            % ",".join(map(str, invalid)),
                        }
                    ),
                    400,
                )

        # Update profile bindings (two-layer architecture)
        if "profiles" in data:
            user["profiles"] = data["profiles"]
        if "overrides" in data:
            # Basic validation for overrides
            overrides = data["overrides"]
            if not isinstance(overrides, dict):
                return (
                    jsonify(
                        {"status": "error", "message": "'overrides' must be an object"}
                    ),
                    400,
                )
            # Validate min_score if provided
            min_score = overrides.get("min_score")
            if min_score is not None:
                try:
                    ms = float(min_score)
                    if not (0.0 <= ms <= 1.0):
                        raise ValueError("min_score must be between 0 and 1")
                except Exception as e:
                    return (
                        jsonify(
                            {"status": "error", "message": f"Invalid min_score: {e}"}
                        ),
                        400,
                    )
            user["overrides"] = overrides

        # Update config via Sentinel API
        update_response = requests.post(
            f"{sentinel_api_url}/config",
            json={"monitored_users": users},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if not update_response.ok:
            logger.error(
                f"Sentinel rejected user update: {update_response.status_code}"
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

        logger.info(f"Updated user {user_id} via Sentinel API")

        # Reload config in UI
        if _reload_config_fn:
            _reload_config_fn()

        return jsonify({"status": "ok", "message": "User updated", "user": user})

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as exc:
        logger.error(f"User update operation failed: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@users_bp.route("/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    """Remove a user from monitoring configuration via Sentinel API."""
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
        users = config.get("monitored_users", [])
        original_count = len(users)

        # Remove user
        users = [u for u in users if u.get("id") != user_id]

        if len(users) == original_count:
            return jsonify({"status": "error", "message": "User not found"}), 404

        # Update config via Sentinel API
        update_response = requests.post(
            f"{sentinel_api_url}/config",
            json={"monitored_users": users},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if not update_response.ok:
            logger.error(
                f"Sentinel rejected user deletion: {update_response.status_code}"
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

        logger.info(f"Removed user {user_id} via Sentinel API")

        # Reload config in UI
        if _reload_config_fn:
            _reload_config_fn()

        return jsonify({"status": "ok", "message": "User removed"}), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return (
            jsonify({"status": "error", "message": "Could not reach Sentinel service"}),
            503,
        )
    except Exception as e:
        logger.error(f"Failed to delete user: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
