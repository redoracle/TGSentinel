"""UI lock routes for TG Sentinel.

This module provides endpoints for locking/unlocking the UI
without affecting the underlying Telegram session.
"""

import logging
import os

from flask import Blueprint, jsonify, request, session

logger = logging.getLogger(__name__)

ui_lock_bp = Blueprint("ui_lock", __name__)

# UI lock configuration
UI_LOCK_PASSWORD = os.getenv("UI_LOCK_PASSWORD", "")

# Parse UI_LOCK_TIMEOUT with validation
_timeout_env = os.getenv("UI_LOCK_TIMEOUT", "300")
try:
    _timeout_value = int(_timeout_env)
    # Enforce non-negative minimum
    if _timeout_value < 0:
        logger.warning(
            f"UI_LOCK_TIMEOUT must be non-negative, got '{_timeout_env}'. Using default 300."
        )
        UI_LOCK_TIMEOUT = 300
    else:
        UI_LOCK_TIMEOUT = _timeout_value
except (ValueError, TypeError):
    logger.warning(
        f"Invalid UI_LOCK_TIMEOUT value '{_timeout_env}' (must be a valid integer). Using default 300."
    )
    UI_LOCK_TIMEOUT = 300


@ui_lock_bp.post("/api/ui/lock")
def api_ui_lock():
    """Lock or unlock the UI without logging out the Telegram session.

    JSON body:
      {"action": "lock"} → sets session['ui_locked']=True
      {"action": "unlock", "password": "..."} → unlocks if password matches UI_LOCK_PASSWORD
    """
    try:
        if not request.is_json:
            return jsonify({"status": "error", "message": "JSON body required"}), 400
        payload = request.get_json(silent=True) or {}
        action = str(payload.get("action", "")).strip().lower()
        if action == "lock":
            session.permanent = True  # Ensure lock persists across browser restarts
            session["ui_locked"] = True
            session.pop(
                "ui_has_been_unlocked", None
            )  # Clear unlocked flag so it stays locked
            session.modified = True
            return jsonify({"status": "ok", "locked": True})
        if action == "unlock":
            pwd = str(payload.get("password", ""))
            if UI_LOCK_PASSWORD and pwd != UI_LOCK_PASSWORD:
                return jsonify({"status": "error", "message": "Invalid password"}), 403
            session.pop("ui_locked", None)
            session.permanent = True  # Keep unlocked state across browser restarts
            session["ui_has_been_unlocked"] = (
                True  # Track that user has unlocked in this session
            )
            session.modified = True
            return jsonify({"status": "ok", "locked": False})
        return jsonify({"status": "error", "message": "Unknown action"}), 400
    except Exception as exc:
        logger.error("UI lock error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


@ui_lock_bp.get("/api/ui/lock/status")
def api_ui_lock_status():
    """Return the current UI lock status and configuration hints."""
    try:
        # Check if UI lock is enabled
        ui_lock_enabled = bool(UI_LOCK_PASSWORD or os.getenv("UI_LOCK_TIMEOUT"))

        # Determine lock state:
        # - If explicitly locked: locked=True
        # - If never unlocked in this session AND UI_LOCK enabled: locked=True (default locked)
        # - If unlocked before in this session: locked=False
        explicitly_locked = bool(session.get("ui_locked"))
        has_been_unlocked = bool(session.get("ui_has_been_unlocked"))

        # Default to locked if UI_LOCK is enabled and user hasn't unlocked yet
        locked = explicitly_locked or (ui_lock_enabled and not has_been_unlocked)

        return jsonify(
            {
                "locked": locked,
                "timeout": UI_LOCK_TIMEOUT,
                "enabled": ui_lock_enabled,
            }
        )
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500
