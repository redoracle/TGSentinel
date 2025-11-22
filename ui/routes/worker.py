"""Worker status and progress monitoring routes for TG Sentinel UI.

This blueprint handles:
- Worker authorization status from Redis
- Login progress monitoring
- Logout progress monitoring
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify

# Import dependency container
try:
    from ui.core import get_deps
except ImportError:
    from core import get_deps  # type: ignore

logger = logging.getLogger(__name__)

# Create blueprint
worker_bp = Blueprint("worker", __name__)


@worker_bp.get("/status")
def worker_status():
    """Return worker authorization status as seen via Redis, if available."""
    deps = get_deps()
    try:
        status = None
        if deps.redis_client:
            try:
                raw = deps.redis_client.get("tgsentinel:worker_status")
                if raw:
                    raw = raw.decode() if isinstance(raw, bytes) else raw
                    status = json.loads(str(raw))
            except Exception:
                status = None

        if not status:
            # Fallback: Check if user_info exists (indicates active session)
            # This handles cases where worker_status key expired or Redis restarted
            try:
                if deps.redis_client:
                    user_info_raw = deps.redis_client.get("tgsentinel:user_info")
                    if user_info_raw:
                        logger.info(
                            "Worker status key missing but user_info exists - assuming ready"
                        )
                        return jsonify(
                            {"authorized": True, "status": "ready", "ts": None}
                        )
            except Exception as fallback_exc:
                logger.debug("Fallback check error: %s", fallback_exc)

            return jsonify({"authorized": None, "status": "unknown"})
        # Normalize fields and include rate limit info if present
        response = {
            "authorized": bool(status.get("authorized")),
            "status": status.get("status", "unknown"),
            "ts": status.get("ts"),
        }

        # Add rate limit information if present
        if status.get("status") == "rate_limited":
            response["rate_limit"] = {
                "action": status.get("rate_limit_action"),
                "wait_seconds": status.get("rate_limit_wait"),
                "wait_until": status.get("rate_limit_until"),
            }

        return jsonify(response)
    except Exception as exc:
        logger.debug("Worker status error: %s", exc)
        return jsonify({"authorized": None, "status": "error", "message": str(exc)})


@worker_bp.get("/logout-progress")
def worker_logout_progress():
    """Return real-time logout progress from sentinel."""
    deps = get_deps()
    try:
        progress = None
        if deps.redis_client:
            try:
                raw = deps.redis_client.get("tgsentinel:logout_progress")
                if raw:
                    raw = raw.decode() if isinstance(raw, bytes) else raw
                    progress = json.loads(str(raw))
            except Exception:
                progress = None

        if not progress:
            return jsonify(
                {
                    "stage": "unknown",
                    "percent": 0,
                    "message": "No progress data available",
                }
            )

        return jsonify(progress)
    except Exception as exc:
        logger.debug("Logout progress error: %s", exc)
        return jsonify({"stage": "error", "percent": 0, "message": str(exc)})


@worker_bp.get("/login-progress")
def worker_login_progress():
    """Get real-time login progress from sentinel.

    Returns:
        JSON with stage, percent, message, timestamp from Redis
    """
    deps = get_deps()
    try:
        progress = None
        if deps.redis_client:
            try:
                raw = deps.redis_client.get("tgsentinel:login_progress")
                if raw:
                    raw = raw.decode() if isinstance(raw, bytes) else raw
                    progress = json.loads(str(raw))
            except Exception:
                progress = None

        if not progress:
            return jsonify(
                {
                    "stage": "unknown",
                    "percent": 0,
                    "message": "No progress data available",
                }
            )

        return jsonify(progress)
    except Exception as exc:
        logger.debug("Login progress error: %s", exc)
        return jsonify({"stage": "error", "percent": 0, "message": str(exc)})
