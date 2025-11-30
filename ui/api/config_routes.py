"""Configuration management routes for TG Sentinel UI."""

import logging
import os
import re
from typing import Any

import requests
from flask import Blueprint, jsonify, request, send_file

logger = logging.getLogger(__name__)

# Create blueprint
config_bp = Blueprint("config", __name__, url_prefix="/api/config")

# Dependencies (injected during registration)
redis_client = None
config_obj = None


def init_blueprint(redis_obj: Any, config_instance: Any) -> None:
    """Initialize blueprint with dependencies."""
    global redis_client, config_obj
    redis_client = redis_obj
    config_obj = config_instance


def get_sentinel_api_url() -> str:
    """Get the Sentinel API base URL."""
    return os.getenv("SENTINEL_API_BASE_URL", "http://sentinel:8080/api")


def _fetch_sentinel_config() -> tuple[dict, str | None]:
    """Fetch current config from Sentinel API.

    Returns:
        (config_data, error_message) tuple. If error_message is not None, config_data is {}.
    """
    try:
        response = requests.get(f"{get_sentinel_api_url()}/config", timeout=5)
        if not response.ok:
            logger.error(f"Sentinel API error: {response.status_code}")
            return (
                {},
                f"Could not fetch config from Sentinel (status {response.status_code})",
            )
        return response.json().get("data", {}), None
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API: {e}")
        return {}, "Could not reach Sentinel service"


def _update_sentinel_config(config_updates: dict) -> tuple[bool, str | None]:
    """Update config via Sentinel API (single source of truth).

    Args:
        config_updates: Dictionary of config sections/values to update.

    Returns:
        (success, error_message) tuple. If success is False, error_message explains why.
    """
    try:
        response = requests.post(
            f"{get_sentinel_api_url()}/config",
            json=config_updates,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if not response.ok:
            error_data = response.json() if response.content else {}
            error_msg = error_data.get(
                "message", f"Sentinel rejected update (status {response.status_code})"
            )
            logger.error(f"Sentinel config update failed: {error_msg}")
            return False, error_msg
        return True, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not connect to Sentinel API for config update: {e}")
        return False, "Could not reach Sentinel service"


def _normalize_chat_id(chat_id: int) -> set[int]:
    """Generate all possible ID variants for matching.

    Telegram IDs can appear in different formats:
    - Negative (e.g., -1001443765280)
    - Positive with 100 prefix (e.g., 1001443765280)
    - Positive without prefix (e.g., 1443765280)

    Returns a set of all variants for comparison.
    """
    variants = {chat_id}
    abs_id = abs(chat_id)
    variants.add(abs_id)

    # Handle 100 prefix variations
    abs_str = str(abs_id)
    if abs_str.startswith("100"):
        # Remove 100 prefix
        stripped = int(abs_str[3:])
        variants.add(stripped)
        variants.add(-stripped)
    else:
        # Add 100 prefix
        prefixed = int(f"100{abs_str}")
        variants.add(prefixed)
        variants.add(-prefixed)

    # Also add negative of abs_id
    variants.add(-abs_id)

    return variants


def _validate_config_data(config_data: dict) -> tuple[bool, str]:
    """Validate configuration data before saving.

    Returns:
        (is_valid, error_message) tuple
    """
    if not isinstance(config_data, dict):
        return False, "Configuration must be a dictionary"

    # Validate telegram section
    if "telegram" in config_data:
        telegram = config_data["telegram"]
        if not isinstance(telegram, dict):
            return False, "telegram section must be a dictionary"

        # Validate api_id
        if "api_id" in telegram:
            api_id = telegram["api_id"]
            if isinstance(api_id, str):
                if not api_id.strip().isdigit():
                    return False, "telegram.api_id must be a numeric string or integer"
            elif not isinstance(api_id, int):
                return False, "telegram.api_id must be an integer or numeric string"

        # Validate api_hash
        if "api_hash" in telegram:
            if not isinstance(telegram["api_hash"], str):
                return False, "telegram.api_hash must be a string"

        # Validate session path
        if "session" in telegram:
            if not isinstance(telegram["session"], str):
                return False, "telegram.session must be a string"

    # Validate alerts section
    if "alerts" in config_data:
        alerts = config_data["alerts"]
        if not isinstance(alerts, dict):
            return False, "alerts section must be a dictionary"

        # Validate mode (dm, digest, both - 'channel' deprecated)
        if "mode" in alerts:
            mode = alerts["mode"]
            if not isinstance(mode, str):
                return False, "alerts.mode must be a string"
            if mode not in ("dm", "digest", "both"):
                return (
                    False,
                    f"alerts.mode must be 'dm', 'digest', or 'both', got: {mode}",
                )

        # Validate target_channel
        if "target_channel" in alerts:
            if not isinstance(alerts["target_channel"], str):
                return False, "alerts.target_channel must be a string"

        # Validate digest section
        if "digest" in alerts:
            digest = alerts["digest"]
            if not isinstance(digest, dict):
                return False, "alerts.digest must be a dictionary"

            if "hourly" in digest and not isinstance(digest["hourly"], bool):
                return False, "alerts.digest.hourly must be a boolean"

            if "daily" in digest and not isinstance(digest["daily"], bool):
                return False, "alerts.digest.daily must be a boolean"

            if "top_n" in digest:
                top_n = digest["top_n"]
                if not isinstance(top_n, int) or top_n < 1 or top_n > 100:
                    return (
                        False,
                        "alerts.digest.top_n must be an integer between 1 and 100",
                    )

    # Validate channels section
    if "channels" in config_data:
        channels = config_data["channels"]
        if not isinstance(channels, list):
            return False, "channels must be a list"

        for idx, channel in enumerate(channels):
            if not isinstance(channel, dict):
                return False, f"channels[{idx}] must be a dictionary"

            # Validate required channel fields
            if "id" not in channel:
                return False, f"channels[{idx}] missing required field 'id'"

            channel_id = channel["id"]
            if isinstance(channel_id, str):
                # Allow string IDs but ensure they're numeric or start with -
                if not (channel_id.lstrip("-").isdigit()):
                    return (
                        False,
                        f"channels[{idx}].id must be a valid integer or numeric string",
                    )
            elif not isinstance(channel_id, int):
                return False, f"channels[{idx}].id must be an integer"

            # Validate optional numeric fields
            numeric_fields = [
                "reaction_threshold",
                "reply_threshold",
                "rate_limit_per_hour",
            ]
            for field in numeric_fields:
                if field in channel and not isinstance(channel[field], int):
                    return False, f"channels[{idx}].{field} must be an integer"

            # Validate boolean fields
            bool_fields = [
                "detect_codes",
                "detect_documents",
                "prioritize_pinned",
                "prioritize_admin",
                "detect_polls",
                "is_private",
                "enabled",
            ]
            for field in bool_fields:
                if field in channel and not isinstance(channel[field], bool):
                    return False, f"channels[{idx}].{field} must be a boolean"

            # Validate list fields
            list_fields = [
                "vip_senders",
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
            for field in list_fields:
                if field in channel and not isinstance(channel[field], list):
                    return False, f"channels[{idx}].{field} must be a list"

    # Validate monitored_users section
    if "monitored_users" in config_data:
        users = config_data["monitored_users"]
        if not isinstance(users, list):
            return False, "monitored_users must be a list"

        for idx, user in enumerate(users):
            if not isinstance(user, dict):
                return False, f"monitored_users[{idx}] must be a dictionary"

            if "id" not in user:
                return False, f"monitored_users[{idx}] missing required field 'id'"

            if not isinstance(user["id"], int):
                return False, f"monitored_users[{idx}].id must be an integer"

            if "enabled" in user and not isinstance(user["enabled"], bool):
                return False, f"monitored_users[{idx}].enabled must be a boolean"

    # Validate interests section
    if "interests" in config_data:
        interests = config_data["interests"]
        if not isinstance(interests, list):
            return False, "interests must be a list"

        for idx, interest in enumerate(interests):
            if not isinstance(interest, str):
                return False, f"interests[{idx}] must be a string"

    # Validate similarity_threshold
    if "similarity_threshold" in config_data:
        threshold = config_data["similarity_threshold"]
        if not isinstance(threshold, (int, float)):
            return False, "similarity_threshold must be a number"
        if not (0.0 <= threshold <= 1.0):
            return False, "similarity_threshold must be between 0.0 and 1.0"

    return True, ""


@config_bp.post("/save")
def api_config_save():
    """Save configuration via Sentinel API (single source of truth)."""
    try:
        config_data = request.get_json()
        if not config_data:
            return jsonify({"status": "error", "message": "No configuration data"}), 400

        # Validate configuration data
        is_valid, error_message = _validate_config_data(config_data)
        if not is_valid:
            logger.warning(f"Configuration validation failed: {error_message}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Invalid configuration: {error_message}",
                    }
                ),
                400,
            )

        # Forward to Sentinel API (single source of truth)

        sentinel_api_url = get_sentinel_api_url()

        try:
            response = requests.post(
                f"{sentinel_api_url}/config",
                json=config_data,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if not response.ok:
                error_data = (
                    response.json()
                    if response.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    else {}
                )
                error_msg = error_data.get(
                    "message", f"Sentinel returned {response.status_code}"
                )
                logger.error(f"Sentinel API rejected config: {error_msg}")
                return (
                    jsonify({"status": "error", "message": error_msg}),
                    response.status_code,
                )

            logger.info("Configuration saved successfully via Sentinel API")
            return jsonify({"status": "ok", "message": "Configuration saved"}), 200

        except requests.exceptions.RequestException as req_err:
            logger.error(f"Failed to connect to Sentinel API: {req_err}", exc_info=True)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Could not connect to Sentinel: {str(req_err)}",
                    }
                ),
                503,
            )

    except Exception as e:
        logger.error(f"Failed to save configuration: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.post("/clean-db")
def api_config_clean_db():
    """Clean up database by removing old entries - proxies to Sentinel."""
    try:
        import requests

        days = int(request.args.get("days", 30))

        sentinel_api_url = get_sentinel_api_url()

        # Forward cleanup request to Sentinel
        response = requests.post(
            f"{sentinel_api_url}/database/cleanup", params={"days": days}, timeout=60
        )

        if response.ok:
            data = response.json()
            if data.get("status") == "ok":
                result = data.get("data", {})
                deleted_count = result.get("total_deleted", 0)
                return (
                    jsonify(
                        {
                            "status": "ok",
                            "message": f"Deleted {deleted_count} messages older than {days} days",
                            "data": result,
                        }
                    ),
                    200,
                )
            else:
                error_msg = data.get("error", "Unknown error from Sentinel")
                logger.error(f"Sentinel cleanup failed: {error_msg}")
                return jsonify({"status": "error", "message": error_msg}), 500
        else:
            logger.error(
                f"Sentinel cleanup request failed: HTTP {response.status_code}"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Sentinel request failed: {response.status_code}",
                    }
                ),
                502,
            )

    except Exception as e:
        logger.error(f"Failed to clean database: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.get("/channels")
def api_config_channels():
    """Get list of configured channels.

    Proxies to Sentinel API (single source of truth).
    """
    try:
        sentinel_api_url = get_sentinel_api_url()
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)

        if not response.ok:
            logger.error(
                f"Failed to fetch config from Sentinel: {response.status_code}"
            )
            return jsonify({"channels": []}), 200

        sentinel_data = response.json()
        config_data = sentinel_data.get("data", {})
        channels = config_data.get("channels", [])

        return jsonify({"channels": channels}), 200
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to Sentinel failed: {e}", exc_info=True)
        return jsonify({"channels": []}), 200
    except Exception as e:
        logger.error(f"Failed to get channels: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.route("/current", methods=["GET"])
def api_config_current():
    """Get current configuration from Sentinel (single source of truth)."""
    try:
        # Proxy to Sentinel API to get complete config including env vars
        sentinel_api_url = get_sentinel_api_url()
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)

        if response.status_code == 200:
            sentinel_data = response.json()
            if sentinel_data.get("status") == "ok":
                return jsonify(sentinel_data.get("data", {})), 200
            else:
                logger.warning(f"Sentinel API returned error: {sentinel_data}")
                return (
                    jsonify({"status": "error", "message": "Sentinel API error"}),
                    500,
                )
        else:
            logger.error(f"Sentinel API returned status {response.status_code}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to fetch config from Sentinel",
                    }
                ),
                503,
            )

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Sentinel API: {e}", exc_info=True)
        # No fallback - Sentinel is the single source of truth
        return (
            jsonify({"status": "error", "message": "Sentinel service unavailable"}),
            503,
        )
    except Exception as e:
        logger.error(f"Failed to get configuration: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.get("/interests")
def api_config_interests():
    """Get interest tracking configuration.

    Proxies to Sentinel API (single source of truth).
    """
    try:
        sentinel_api_url = get_sentinel_api_url()
        response = requests.get(f"{sentinel_api_url}/config", timeout=5)

        if not response.ok:
            logger.error(
                f"Failed to fetch config from Sentinel: {response.status_code}"
            )
            return jsonify({"interests": []}), 200

        sentinel_data = response.json()
        config_data = sentinel_data.get("data", {})
        interests = config_data.get("interests", [])

        return jsonify({"interests": interests}), 200
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to Sentinel failed: {e}", exc_info=True)
        return jsonify({"interests": []}), 200
    except Exception as e:
        logger.error(f"Failed to get interests: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.get("/export")
def api_config_export():
    """Export current configuration as downloadable YAML file.

    Fetches config from Sentinel and returns it as a downloadable YAML file.
    """
    import io

    import yaml

    try:
        # Fetch config from Sentinel (single source of truth)
        config_data, error = _fetch_sentinel_config()
        if error:
            return jsonify({"status": "error", "message": error}), 503

        # Convert to YAML and return as downloadable file
        yaml_content = yaml.safe_dump(
            config_data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

        return send_file(
            io.BytesIO(yaml_content.encode("utf-8")),
            as_attachment=True,
            download_name="tgsentinel.yml",
            mimetype="application/x-yaml",
        )
    except Exception as e:
        logger.error(f"Failed to export configuration: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.post("/rules/test")
def api_config_rules_test():
    """Test scoring rules against sample messages."""
    try:
        data = request.get_json()
        rules = data.get("rules", [])
        messages = data.get("messages", [])

        # Validate and compile regex patterns before use (ReDoS protection)
        compiled_rules = []
        invalid_patterns = []

        # Dangerous regex patterns that can cause ReDoS
        redos_patterns = [
            r"\(.+\)\+",  # (.+)+ nested quantifiers
            r"\(.*\)\+",  # (.*)+
            r"\([^)]+\)\+\+",  # (a+)++ double quantifiers
            r"\.\*\.\*",  # .*.* multiple wildcards
            r"\.\+\.\+",  # .+.+ multiple wildcards
            r"\\[0-9]",  # \1, \2 backreferences (simple detection)
        ]

        for rule in rules:
            pattern = rule.get("pattern", "")
            if not pattern:
                compiled_rules.append({"rule": rule, "compiled": None})
                continue

            # Check for obviously dangerous constructs
            is_dangerous = False
            for dangerous in redos_patterns:
                if re.search(dangerous, pattern):
                    is_dangerous = True
                    logger.warning(
                        f"Rejected potentially dangerous regex pattern: {pattern[:100]}"
                    )
                    invalid_patterns.append(
                        {
                            "pattern": pattern,
                            "rule_name": rule.get("name", "Unnamed"),
                            "reason": "Contains potentially dangerous construct (nested quantifiers/backtracking)",
                        }
                    )
                    break

            if is_dangerous:
                continue

            # Attempt to compile pattern
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                compiled_rules.append({"rule": rule, "compiled": compiled})
            except re.error as regex_err:
                logger.warning(f"Invalid regex pattern '{pattern[:100]}': {regex_err}")
                invalid_patterns.append(
                    {
                        "pattern": pattern,
                        "rule_name": rule.get("name", "Unnamed"),
                        "reason": f"Invalid regex syntax: {str(regex_err)}",
                    }
                )

        # Return validation errors if any patterns are invalid
        if invalid_patterns:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Found {len(invalid_patterns)} invalid or unsafe regex pattern(s)",
                        "invalid_patterns": invalid_patterns,
                    }
                ),
                400,
            )

        # Test each message against compiled rules
        results = []
        for msg in messages:
            scores = []
            for item in compiled_rules:
                rule = item["rule"]
                compiled_pattern = item["compiled"]

                if compiled_pattern is None:
                    continue

                score = rule.get("score", 0)
                text = msg.get("text", "")

                # Apply simple timeout protection by limiting text length
                # (More robust: use signal.alarm on Unix or threading.Timer)
                if len(text) > 10000:
                    logger.warning(
                        "Skipping regex on text longer than 10000 chars to prevent ReDoS"
                    )
                    continue

                try:
                    if compiled_pattern.search(text):
                        scores.append(
                            {"rule": rule.get("name", "Unnamed"), "score": score}
                        )
                except Exception as match_err:
                    logger.error(
                        f"Regex matching failed for pattern '{rule.get('pattern', '')[:50]}': {match_err}"
                    )

            results.append(
                {
                    "message": msg,
                    "scores": scores,
                    "total": sum(s["score"] for s in scores),
                }
            )

        return jsonify({"status": "ok", "results": results}), 200
    except Exception as e:
        logger.error(f"Failed to test rules: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.post("/stats/reset")
def api_config_stats_reset():
    """Reset statistics counters."""
    try:
        if redis_client is None:
            return (
                jsonify({"status": "error", "message": "Redis not available"}),
                503,
            )

        # Reset message counters
        for key in redis_client.scan_iter("tgsentinel:stats:*"):
            redis_client.delete(key)

        return jsonify({"status": "ok", "message": "Statistics reset"}), 200
    except Exception as e:
        logger.error(f"Failed to reset statistics: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.post("/channels/add")
def api_config_channels_add():
    """Add new channels to monitoring configuration via Sentinel API."""
    try:
        data = request.get_json()

        # Support both single channel and array of channels
        channels_to_add = data.get("channels", [])

        # Legacy support: if "chat_id" is provided, treat as single channel
        if "chat_id" in data and not channels_to_add:
            chat_id = data.get("chat_id")
            name = data.get("name")

            if not chat_id:
                return jsonify({"status": "error", "message": "chat_id required"}), 400

            channels_to_add = [{"id": chat_id, "name": name or f"Channel {chat_id}"}]

        if not channels_to_add:
            return (
                jsonify({"status": "error", "message": "channels array required"}),
                400,
            )

        # Fetch current config from Sentinel (single source of truth)
        config_data, error = _fetch_sentinel_config()
        if error:
            return jsonify({"status": "error", "message": error}), 503

        channels = config_data.get("channels", [])
        existing_ids = {c.get("id") for c in channels}

        added_count = 0
        skipped_count = 0

        # Add new channels with default values (matching original logic)
        for channel_data in channels_to_add:
            channel_id = channel_data.get("id")
            if not channel_id:
                continue

            if channel_id in existing_ids:
                skipped_count += 1
                continue

            # Create channel entry with default values from original code
            channels.append(
                {
                    "id": channel_id,
                    "name": channel_data.get("name") or "Unknown Channel",
                    "vip_senders": [],
                    "keywords": [],
                    "reaction_threshold": 5,
                    "reply_threshold": 3,
                    "rate_limit_per_hour": 10,
                    "enabled": True,
                }
            )
            added_count += 1

        # Update config via Sentinel API
        success, error = _update_sentinel_config({"channels": channels})
        if not success:
            return jsonify({"status": "error", "message": error}), 502

        message = f"Added {added_count} channel(s)"
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
        logger.error(f"Failed to add channels: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.route("/channels/<chat_id>", methods=["DELETE"])
def api_config_channels_delete(chat_id):
    """Remove a channel from monitoring configuration via Sentinel API."""
    try:
        chat_id = int(chat_id)

        # Fetch current config from Sentinel (single source of truth)
        config_data, error = _fetch_sentinel_config()
        if error:
            return jsonify({"status": "error", "message": error}), 503

        channels = config_data.get("channels", [])
        original_count = len(channels)

        # Remove channel - use ID normalization for matching
        id_variants = _normalize_chat_id(chat_id)
        channels = [c for c in channels if c.get("id") not in id_variants]

        if len(channels) == original_count:
            return jsonify({"status": "error", "message": "Channel not found"}), 404

        # Update config via Sentinel API
        success, error = _update_sentinel_config({"channels": channels})
        if not success:
            return jsonify({"status": "error", "message": error}), 502

        return jsonify({"status": "ok", "message": "Channel removed"}), 200
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid chat_id"}), 400
    except Exception as e:
        logger.error(f"Failed to delete channel: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@config_bp.post("/users/add")
def api_config_users_add():
    """Add new users to monitoring configuration via Sentinel API."""
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
                {"id": user_id, "name": name or f"User {user_id}", "username": username}
            ]

        if not users_to_add:
            return jsonify({"status": "error", "message": "users array required"}), 400

        # Fetch current config from Sentinel (single source of truth)
        config_data, error = _fetch_sentinel_config()
        if error:
            return jsonify({"status": "error", "message": error}), 503

        # Use monitored_users key (matches original config schema)
        users = config_data.get("monitored_users", [])
        existing_ids = {u.get("id") for u in users}

        added_count = 0
        skipped_count = 0

        # Add new users that don't already exist
        for user_data in users_to_add:
            user_id = user_data.get("id")
            if not user_id:
                continue

            if user_id in existing_ids:
                skipped_count += 1
                continue

            # Add new user
            users.append(
                {
                    "id": user_id,
                    "name": user_data.get("name") or f"User {user_id}",
                    "username": user_data.get("username", ""),
                    "enabled": True,
                }
            )
            added_count += 1

        # Update config via Sentinel API
        success, error = _update_sentinel_config({"monitored_users": users})
        if not success:
            return jsonify({"status": "error", "message": error}), 502

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


@config_bp.route("/users/<user_id>", methods=["DELETE"])
def api_config_users_delete(user_id):
    """Remove a user from monitoring configuration via Sentinel API."""
    try:
        user_id = int(user_id)

        # Fetch current config from Sentinel (single source of truth)
        config_data, error = _fetch_sentinel_config()
        if error:
            return jsonify({"status": "error", "message": error}), 503

        # Use monitored_users key (matches original config schema)
        users = config_data.get("monitored_users", [])
        original_count = len(users)

        # Remove user
        users = [u for u in users if u.get("id") != user_id]

        if len(users) == original_count:
            return jsonify({"status": "error", "message": "User not found"}), 404

        # Update config via Sentinel API
        success, error = _update_sentinel_config({"monitored_users": users})
        if not success:
            return jsonify({"status": "error", "message": error}), 502

        return jsonify({"status": "ok", "message": "User removed"}), 200
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid user_id"}), 400
    except Exception as e:
        logger.error(f"Failed to delete user: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
