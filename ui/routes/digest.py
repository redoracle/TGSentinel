"""Digest configuration API routes for UI.

Proxies digest config requests to Sentinel API.
"""

import logging
from typing import Callable, Tuple

from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

digest_bp = Blueprint("digest", __name__, url_prefix="/api/digest")


def init_digest_routes(digest_service):
    """Initialize digest routes with dependencies.

    Store the provided `digest_service` as a blueprint attribute so tests or
    late registration can provide the dependency. Production code should set
    the service on `app.config['DIGEST_SERVICE']` during app creation for
    application-level dependency injection.

    Args:
        digest_service: DigestService instance for proxying requests
    """
    # Attach to the blueprint as a fallback holder. Prefer app.config for DI.
    setattr(digest_bp, "_digest_service", digest_service)
    logger.info(
        "[DIGEST-ROUTES] Digest service attached to blueprint (or set app.config['DIGEST_SERVICE'] for app-level DI)"
    )


def _validate_digest_payload(data) -> Tuple[bool, str]:
    """Validate digest payload shape and basic types.

    This mirrors the minimal validation performed server-side to provide
    fast feedback to the client. It is intentionally conservative and
    returns a tuple (is_valid, error_message).
    """
    if not isinstance(data, dict):
        return False, "payload must be a JSON object"

    # Top-level optional fields and their expected types
    if "top_n" in data:
        if not isinstance(data["top_n"], int):
            return False, "top_n must be an integer"
        if data["top_n"] <= 0:
            return False, "top_n must be > 0"

    if "min_score" in data:
        try:
            val = float(data["min_score"])
        except (TypeError, ValueError):
            return False, "min_score must be a number"
        if not (0.0 <= val <= 10.0):
            return False, "min_score must be between 0.0 and 10.0"

    if "mode" in data:
        if data["mode"] not in {"dm", "channel", "both"}:
            return False, "mode must be one of: dm, channel, both"
        if data["mode"] in {"channel", "both"} and not data.get("target_channel"):
            return False, 'target_channel required when mode is "channel" or "both"'

    # Validate schedules list if present
    if "schedules" in data:
        schedules = data["schedules"]
        if not isinstance(schedules, list):
            return False, "schedules must be a list"

        allowed_schedules = {
            "hourly",
            "every_4h",
            "every_6h",
            "every_12h",
            "daily",
            "weekly",
            "none",
        }

        for idx, sched in enumerate(schedules):
            if not isinstance(sched, dict):
                return False, f"schedule at index {idx} must be an object"
            if "schedule" not in sched or sched["schedule"] not in allowed_schedules:
                return (
                    False,
                    f"schedule at index {idx} has invalid or missing 'schedule'",
                )

            if "enabled" in sched and not isinstance(sched["enabled"], bool):
                return False, f"schedule.enabled at index {idx} must be boolean"

            if "top_n" in sched:
                if not isinstance(sched["top_n"], int) or sched["top_n"] <= 0:
                    return False, f"schedule.top_n at index {idx} must be integer > 0"

            if "min_score" in sched:
                try:
                    smin = float(sched["min_score"])
                except (TypeError, ValueError):
                    return False, f"schedule.min_score at index {idx} must be a number"
                if not (0.0 <= smin <= 10.0):
                    return (
                        False,
                        f"schedule.min_score at index {idx} must be between 0.0 and 10.0",
                    )

            # timing fields
            if "daily_hour" in sched and not (
                isinstance(sched["daily_hour"], int) and 0 <= sched["daily_hour"] <= 23
            ):
                return False, f"schedule.daily_hour at index {idx} must be int 0-23"
            if "weekly_day" in sched and not (
                isinstance(sched["weekly_day"], int) and 0 <= sched["weekly_day"] <= 6
            ):
                return False, f"schedule.weekly_day at index {idx} must be int 0-6"
            if "weekly_hour" in sched and not (
                isinstance(sched["weekly_hour"], int)
                and 0 <= sched["weekly_hour"] <= 23
            ):
                return False, f"schedule.weekly_hour at index {idx} must be int 0-23"

    return True, ""


def _create_digest_route_handler(
    get_method_name: str,
    update_method_name: str,
    id_param_name: str,
) -> Callable:
    """Factory to create digest route handlers with centralized logic.

    Args:
        get_method_name: Name of service GET method (e.g., "get_profile_digest_config")
        update_method_name: Name of service PUT method (e.g., "update_profile_digest_config")
        id_param_name: Name of ID parameter for response (e.g., "profile_id", "channel_id")

    Returns:
        Route handler function
    """

    def handler(entity_id):
        """Generic digest config handler (GET/PUT).

        Args:
            entity_id: Entity identifier (profile_id, channel_id, user_id)

        Returns:
            JSON response with status and data
        """
        try:
            # Resolve digest service via app config (preferred) or blueprint fallback
            try:
                digest_service = current_app.config.get("DIGEST_SERVICE")
            except RuntimeError:
                # No app context available (e.g., at import-time); fall back to blueprint attribute
                digest_service = getattr(digest_bp, "_digest_service", None)

            if digest_service is None:
                return (
                    jsonify(
                        {"status": "error", "message": "Digest service not initialized"}
                    ),
                    503,
                )

            if request.method == "GET":
                # Call service GET method
                get_method = getattr(digest_service, get_method_name)
                config = get_method(entity_id)
                return jsonify(
                    {"status": "ok", id_param_name: entity_id, "digest": config}
                )

            elif request.method == "PUT":
                # Parse JSON body (None means invalid JSON)
                data = request.get_json(silent=True)
                if data is None:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Invalid or missing JSON body",
                            }
                        ),
                        400,
                    )

                # Validate payload structure and types
                is_valid, error_msg = _validate_digest_payload(data)
                if not is_valid:
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": f"Invalid payload: {error_msg}",
                            }
                        ),
                        400,
                    )

                # Call service UPDATE method
                update_method = getattr(digest_service, update_method_name)
                success, message = update_method(entity_id, data)

                if success:
                    return jsonify({"status": "ok", "message": message})
                else:
                    return jsonify({"status": "error", "message": message}), 400

        except AttributeError as e:
            logger.error(
                f"[DIGEST-ROUTES] Service method not found: {e}", exc_info=True
            )
            return (
                jsonify({"status": "error", "message": "Internal service error"}),
                500,
            )
        except Exception as e:
            logger.error(
                f"[DIGEST-ROUTES] Unexpected error in digest handler: {e}",
                exc_info=True,
            )
            return (
                jsonify({"status": "error", "message": "Internal server error"}),
                500,
            )

    return handler


@digest_bp.route("/profiles/<profile_id>/config", methods=["GET", "PUT"])
def profile_digest_config(profile_id: str):
    """Get or update digest config for a global profile.

    GET: Returns current digest configuration
    PUT: Updates digest configuration
    """
    handler = _create_digest_route_handler(
        get_method_name="get_profile_digest_config",
        update_method_name="update_profile_digest_config",
        id_param_name="profile_id",
    )
    return handler(profile_id)


@digest_bp.route("/channels/<int:channel_id>/config", methods=["GET", "PUT"])
def channel_digest_config(channel_id: int):
    """Get or update digest config for a channel.

    GET: Returns current digest configuration
    PUT: Updates digest configuration
    """
    handler = _create_digest_route_handler(
        get_method_name="get_channel_digest_config",
        update_method_name="update_channel_digest_config",
        id_param_name="channel_id",
    )
    return handler(channel_id)


@digest_bp.route("/channels/<int:channel_id>/overrides/config", methods=["GET", "PUT"])
def channel_overrides_digest_config(channel_id: int):
    """Get or update digest overrides for a channel.

    GET: Returns current digest override configuration
    PUT: Updates digest override configuration
    """
    handler = _create_digest_route_handler(
        get_method_name="get_channel_overrides_digest_config",
        update_method_name="update_channel_overrides_digest_config",
        id_param_name="channel_id",
    )
    return handler(channel_id)


@digest_bp.route("/users/<int:user_id>/config", methods=["GET", "PUT"])
def user_digest_config(user_id: int):
    """Get or update digest config for a monitored user.

    GET: Returns current digest configuration
    PUT: Updates digest configuration
    """
    handler = _create_digest_route_handler(
        get_method_name="get_user_digest_config",
        update_method_name="update_user_digest_config",
        id_param_name="user_id",
    )
    return handler(user_id)


@digest_bp.route("/users/<int:user_id>/overrides/config", methods=["GET", "PUT"])
def user_overrides_digest_config(user_id: int):
    """Get or update digest overrides for a monitored user.

    GET: Returns current digest override configuration
    PUT: Updates digest override configuration
    """
    handler = _create_digest_route_handler(
        get_method_name="get_user_overrides_digest_config",
        update_method_name="update_user_overrides_digest_config",
        id_param_name="user_id",
    )
    return handler(user_id)
