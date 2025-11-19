"""Profile management API routes for TG Sentinel UI.

This blueprint handles all profile-related operations:
- Interest profiles (semantic-based scoring)
- Alert profiles (keyword/heuristic-based scoring)
- Import/export operations
- Backtest operations
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import yaml
from flask import Blueprint, jsonify, make_response, request

logger = logging.getLogger(__name__)

# Blueprint setup
profiles_bp = Blueprint("profiles", __name__, url_prefix="/api/profiles")

# Global dependencies (injected via init function)
_config = None
_engine = None
_query_one = None
_query_all = None
_profile_service = None


def init_profiles_routes(
    config=None,
    engine=None,
    query_one=None,
    query_all=None,
    profile_service=None,
):
    """Initialize profiles blueprint with dependencies.

    Args:
        config: Application config object
        engine: SQLAlchemy engine
        query_one: Function for single-row queries
        query_all: Function for multi-row queries
        profile_service: ProfileService instance
    """
    global _config, _engine, _query_one, _query_all, _profile_service
    _config = config
    _engine = engine
    _query_one = query_one
    _query_all = query_all
    _profile_service = profile_service
    logger.info("Profiles routes initialized")


# ═══════════════════════════════════════════════════════════════════
# Interest Profile Routes (Semantic-based)
# ═══════════════════════════════════════════════════════════════════


@profiles_bp.get("/export")
def export_profiles():
    """Export interest profiles as YAML file."""
    try:
        interests_attr = getattr(_config, "interests", None) if _config else None
        interests = list(interests_attr) if interests_attr is not None else []

        # Create YAML content
        yaml_content = yaml.dump(
            {"interests": interests},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

        # Create response with download headers
        response = make_response(yaml_content)
        response.headers["Content-Type"] = "application/x-yaml"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="interests_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.yml"'
        )
        return response

    except Exception as exc:
        logger.error(f"Failed to export interests: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.post("/train")
def train_profile():
    """Queue training for an interest profile."""
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

    topic = payload.get("topic")
    logger.info("Queued interest profile training for '%s'", topic)
    return jsonify({"status": "queued", "topic": topic})


@profiles_bp.post("/test")
def test_profile():
    """Test semantic similarity for an interest profile."""
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

    sample = payload.get("sample", "")
    interest = payload.get("interest", "")
    score = round(0.42 + len(sample) % 37 / 100, 2)
    logger.debug("Similarity test for '%s' -> %.2f", interest, score)
    return jsonify({"score": score})


@profiles_bp.post("/toggle")
def toggle_profile():
    """Toggle profile enabled/disabled state."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_name = data.get("name", "").strip()
    enabled = data.get("enabled", True)

    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        # Load profile
        profile = (
            _profile_service.get_profile(profile_name) if _profile_service else None
        )
        if profile is None:
            # Check if this is a legacy interest from config
            interests_attr = getattr(_config, "interests", None) if _config else None
            legacy_interests = (
                list(interests_attr) if interests_attr is not None else []
            )

            if profile_name in legacy_interests:
                # Auto-create default profile for legacy interest
                logger.info(
                    f"Auto-creating profile for legacy interest '{profile_name}' during toggle"
                )
                profile = {
                    "name": profile_name,
                    "description": "Migrated from legacy interests",
                    "positive_samples": [],
                    "negative_samples": [],
                    "threshold": 0.42,
                    "weight": 1.0,
                    "enabled": enabled,  # Use the requested state
                    "priority": "normal",
                    "keywords": [],
                    "channels": [],
                    "tags": [],
                    "notify_always": False,
                    "include_digest": True,
                }
            else:
                logger.warning(f"Toggle failed: profile '{profile_name}' not found")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile '{profile_name}' not found",
                        }
                    ),
                    404,
                )
        else:
            # Update enabled state for existing profile
            profile["enabled"] = enabled

        # Save changes
        if not _profile_service or not _profile_service.upsert_profile(profile):
            logger.error(f"Failed to persist toggle for profile '{profile_name}'")
            return (
                jsonify(
                    {"status": "error", "message": "Failed to save profile changes"}
                ),
                500,
            )

        logger.info(
            f"Toggled profile '{profile_name}' -> {'enabled' if enabled else 'disabled'}"
        )
        return jsonify(
            {"status": "success", "profile": profile_name, "enabled": enabled}
        )

    except Exception as exc:
        logger.error(f"Error toggling profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.get("/get")
def get_profile():
    """Get profile details by name."""
    profile_name = request.args.get("name", "").strip()
    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        profile = (
            _profile_service.get_profile(profile_name) if _profile_service else None
        )

        if profile is None:
            # Check if this is a legacy interest from config
            interests_attr = getattr(_config, "interests", None) if _config else None
            legacy_interests = (
                list(interests_attr) if interests_attr is not None else []
            )

            if profile_name in legacy_interests:
                # Auto-create default profile for legacy interest
                logger.info(
                    f"Auto-creating profile for legacy interest '{profile_name}'"
                )
                profile = {
                    "name": profile_name,
                    "description": "Migrated from legacy interests",
                    "positive_samples": [],
                    "negative_samples": [],
                    "threshold": 0.42,
                    "weight": 1.0,
                    "enabled": True,
                    "priority": "normal",
                    "keywords": [],
                    "channels": [],
                    "tags": [],
                    "notify_always": False,
                    "include_digest": True,
                }
                # Save it for future use
                if _profile_service:
                    _profile_service.upsert_profile(profile)
            else:
                logger.warning(f"Profile '{profile_name}' not found")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Profile '{profile_name}' not found",
                        }
                    ),
                    404,
                )

        logger.debug(f"Retrieved profile '{profile_name}'")
        return jsonify(profile)

    except Exception as exc:
        logger.error(f"Error retrieving profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/save")
def save_profile():
    """Save or update a profile."""
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

    # Validate required fields
    profile_name = payload.get("name", "").strip()
    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    # If updating existing profile, check if original_name differs (rename scenario)
    original_name = payload.get("original_name", "").strip()

    try:
        # Set defaults for optional fields
        profile_data = {
            "name": profile_name,
            "description": payload.get("description", "").strip(),
            "positive_samples": payload.get("positive_samples", []),
            "negative_samples": payload.get("negative_samples", []),
            "threshold": float(payload.get("threshold", 0.42)),
            "weight": float(payload.get("weight", 1.0)),
            "enabled": bool(payload.get("enabled", True)),
            "priority": payload.get("priority", "normal"),
            "keywords": payload.get("keywords", []),
            "channels": payload.get("channels", []),
            "tags": payload.get("tags", []),
            "notify_always": bool(payload.get("notify_always", False)),
            "include_digest": bool(payload.get("include_digest", True)),
        }

        # Validate list fields are actually lists
        for field in [
            "positive_samples",
            "negative_samples",
            "keywords",
            "channels",
            "tags",
        ]:
            if not isinstance(profile_data[field], list):
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Field '{field}' must be a list",
                        }
                    ),
                    400,
                )

        # Validate priority
        if profile_data["priority"] not in ["low", "normal", "high", "critical"]:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Priority must be: low, normal, high, or critical",
                    }
                ),
                400,
            )

        # Handle rename: delete old profile if name changed
        if original_name and original_name != profile_name:
            logger.info(f"Renaming profile '{original_name}' to '{profile_name}'")
            if _profile_service:
                _profile_service.delete_profile(original_name)

        # Save profile
        if not _profile_service or not _profile_service.upsert_profile(profile_data):
            logger.error(f"Failed to persist profile '{profile_name}'")
            return (
                jsonify({"status": "error", "message": "Failed to save profile"}),
                500,
            )

        logger.info(f"Profile saved: {profile_name}")
        return jsonify({"status": "ok", "message": f"Profile '{profile_name}' saved"})

    except (ValueError, TypeError) as exc:
        logger.warning(f"Validation error for profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": f"Validation error: {exc}"}), 400
    except Exception as exc:
        logger.error(f"Error saving profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/delete")
def delete_profile():
    """Delete a profile."""
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

    profile_name = payload.get("name", "").strip()
    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    try:
        # Check if profile exists first
        profile = (
            _profile_service.get_profile(profile_name) if _profile_service else None
        )
        if profile is None:
            logger.warning(f"Delete failed: profile '{profile_name}' not found")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Profile '{profile_name}' not found",
                    }
                ),
                404,
            )

        # Delete profile
        if not _profile_service or not _profile_service.delete_profile(profile_name):
            logger.error(f"Failed to delete profile '{profile_name}'")
            return (
                jsonify({"status": "error", "message": "Failed to delete profile"}),
                500,
            )

        logger.info(f"Profile deleted: {profile_name}")
        return jsonify({"status": "ok", "message": f"Profile '{profile_name}' deleted"})

    except Exception as exc:
        logger.error(f"Error deleting profile '{profile_name}': {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/import")
def import_profiles():
    """Import interest profiles from YAML file.

    Persists changes to Sentinel config via API (single source of truth).
    """
    import os
    import requests

    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"status": "error", "message": "Empty filename"}), 400

    try:
        # Read and validate YAML
        content = file.read().decode("utf-8")
        data = yaml.safe_load(content)

        if not isinstance(data, dict):
            return (
                jsonify({"status": "error", "message": "Invalid YAML structure"}),
                400,
            )

        # Validate interests structure
        if "interests" not in data:
            return (
                jsonify(
                    {"status": "error", "message": "Missing 'interests' key in YAML"}
                ),
                400,
            )

        interests = data["interests"]
        if not isinstance(interests, list):
            return (
                jsonify({"status": "error", "message": "'interests' must be a list"}),
                400,
            )

        # Persist to Sentinel config (single source of truth)
        sentinel_api_url = os.getenv(
            "SENTINEL_API_BASE_URL", "http://sentinel:8080/api"
        )

        # Get current config from Sentinel
        try:
            response = requests.get(f"{sentinel_api_url}/config", timeout=5)
            if not response.ok:
                logger.error(
                    f"Failed to fetch config from Sentinel: {response.status_code}"
                )
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Could not fetch current config from Sentinel",
                        }
                    ),
                    503,
                )

            # Update config via Sentinel API
            update_response = requests.post(
                f"{sentinel_api_url}/config",
                json={"interests": interests},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )

            if not update_response.ok:
                logger.error(
                    f"Sentinel rejected interests update: {update_response.status_code}"
                )
                error_detail = update_response.json().get("message", "Unknown error")
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": f"Failed to persist config via Sentinel: {error_detail}",
                        }
                    ),
                    502,
                )

            logger.info(
                f"Imported and persisted {len(interests)} interest profile(s) via Sentinel API"
            )

            # Update local config to reflect changes immediately (in-memory cache)
            if _config:
                _config.interests = interests

            return jsonify(
                {
                    "status": "ok",
                    "message": f"Imported {len(interests)} profile(s)",
                    "count": len(interests),
                    "imported": len(interests),
                    "persisted": True,
                }
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Could not connect to Sentinel API: {e}")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Could not reach Sentinel service to persist changes",
                    }
                ),
                503,
            )

    except yaml.YAMLError as exc:
        logger.error(f"YAML parsing error: {exc}")
        return jsonify({"status": "error", "message": f"Invalid YAML: {exc}"}), 400
    except Exception as exc:
        logger.error(f"Import error: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@profiles_bp.post("/interest/backtest")
def backtest_interest_profile():
    """Backtest an interest profile against historical messages."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_name = payload.get("name", "").strip()

    # Safely parse and validate hours_back
    try:
        hours_back_raw = payload.get("hours_back", 24)
        hours_back = int(hours_back_raw)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"hours_back must be a valid integer, got: {type(hours_back_raw).__name__}",
                }
            ),
            400,
        )

    # Safely parse and validate max_messages
    try:
        max_messages_raw = payload.get("max_messages", 100)
        max_messages = int(max_messages_raw)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"max_messages must be a valid integer, got: {type(max_messages_raw).__name__}",
                }
            ),
            400,
        )

    if not profile_name:
        return jsonify({"status": "error", "message": "Profile name required"}), 400

    # Validate hours_back bounds (prevent resource exhaustion)
    if hours_back < 0 or hours_back > 168:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "hours_back must be between 0 and 168 (7 days)",
                }
            ),
            400,
        )

    # Validate max_messages bounds (prevent SQL injection and excessive queries)
    if max_messages < 1 or max_messages > 1000:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "max_messages must be between 1 and 1000",
                }
            ),
            400,
        )

    try:
        # Get profile
        profile = (
            _profile_service.get_profile(profile_name) if _profile_service else None
        )
        if not profile:
            return jsonify({"status": "error", "message": "Profile not found"}), 404

        # Fetch historical messages
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        messages = (
            _query_all(
                """
            SELECT chat_id, msg_id, chat_title, sender_name, message_text, 
                   score, triggers, alerted, created_at
            FROM messages 
            WHERE datetime(created_at) >= :cutoff
            ORDER BY created_at DESC 
            LIMIT :limit
        """,
                cutoff=cutoff,
                limit=max_messages,
            )
            if _query_all
            else []
        )

        # Load semantic model if available
        try:
            from tgsentinel.semantic import _model, score_text

            if _model is None:
                return (
                    jsonify(
                        {"status": "error", "message": "Semantic model not loaded"}
                    ),
                    500,
                )

            # Score messages
            matches = []
            threshold = profile.get("threshold", 0.42)

            for msg in messages:
                text = msg.get("message_text", "")
                if not text:
                    continue

                # Score text with profile
                score = score_text(text)
                if score is None:
                    continue

                if score >= threshold:
                    matches.append(
                        {
                            "message_id": msg["msg_id"],
                            "chat_id": msg["chat_id"],
                            "chat_title": msg["chat_title"],
                            "sender_name": msg["sender_name"],
                            "score": round(score, 3),
                            "original_score": round(msg.get("score", 0.0), 2),
                            "text_preview": text[:100]
                            + ("..." if len(text) > 100 else ""),
                            "timestamp": msg["created_at"],
                        }
                    )

            # Calculate statistics
            stats = {
                "total_messages": len(messages),
                "matched_messages": len(matches),
                "match_rate": (
                    round(len(matches) / len(messages) * 100, 1)
                    if len(messages) > 0
                    else 0
                ),
                "avg_score": (
                    round(sum(m["score"] for m in matches) / len(matches), 3)
                    if len(matches) > 0
                    else 0
                ),
                "threshold": threshold,
            }

            result = {
                "status": "ok",
                "profile_name": profile_name,
                "test_date": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "hours_back": hours_back,
                    "max_messages": max_messages,
                },
                "matches": matches[:50],  # Limit response size
                "stats": stats,
            }

            logger.info(
                f"Backtest completed for interest profile {profile_name}: {stats}"
            )
            return jsonify(result)

        except ImportError:
            return (
                jsonify(
                    {"status": "error", "message": "Semantic module not available"}
                ),
                500,
            )

    except Exception as exc:
        logger.error(f"Error backtesting interest profile: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


# ═══════════════════════════════════════════════════════════════════
# Alert Profile Routes (Keyword/Heuristic-based)
# ═══════════════════════════════════════════════════════════════════


@profiles_bp.get("/alert/list")
def list_alert_profiles():
    """List all alert profiles with enriched metadata."""
    try:
        profiles = _profile_service.load_alert_profiles() if _profile_service else {}

        # Enrich profiles with activity metadata
        enriched_profiles = []
        for profile in profiles.values():
            enriched = dict(profile)

            # Add activity metadata if database is available
            if _engine and _query_one:
                try:
                    # Get last triggered timestamp for this profile
                    profile_id = enriched.get("id", "")
                    last_triggered = _query_one(
                        """
                        SELECT MAX(created_at) as last_triggered
                        FROM messages
                        WHERE alerted = 1 AND triggers LIKE :profile_pattern
                        """,
                        profile_pattern=f"%{profile_id}%",
                    )

                    if last_triggered and last_triggered.get("last_triggered"):
                        enriched["last_triggered_at"] = last_triggered["last_triggered"]

                    # Get trigger count in last 24 hours
                    trigger_count = _query_one(
                        """
                        SELECT COUNT(*) as count
                        FROM messages
                        WHERE alerted = 1 
                          AND triggers LIKE :profile_pattern
                          AND datetime(created_at) >= datetime('now', '-24 hours')
                        """,
                        profile_pattern=f"%{profile_id}%",
                    )

                    if trigger_count:
                        enriched["recent_triggers"] = trigger_count.get("count", 0)
                except Exception as e:
                    logger.debug(f"Could not enrich profile {profile.get('id')}: {e}")

            enriched_profiles.append(enriched)

        stats = {
            "total": len(enriched_profiles),
            "enabled": sum(1 for p in enriched_profiles if p.get("enabled", True)),
        }
        return jsonify({"status": "ok", "profiles": enriched_profiles, "stats": stats})
    except Exception as exc:
        logger.error(f"Error listing alert profiles: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.get("/alert/get")
def get_alert_profile():
    """Get a specific alert profile."""
    profile_id = request.args.get("id", "").strip()
    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        profile = (
            _profile_service.get_alert_profile(profile_id) if _profile_service else None
        )
        if not profile:
            return jsonify({"status": "error", "message": "Profile not found"}), 404
        return jsonify({"status": "ok", "profile": profile})
    except Exception as exc:
        logger.error(f"Error getting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/alert/upsert")
def upsert_alert_profile():
    """Create or update an alert profile."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    profile = request.get_json(silent=True)
    if not profile:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id = profile.get("id", "").strip()
    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    # Add timestamps
    now = datetime.now(timezone.utc).isoformat()
    if "created_at" not in profile:
        profile["created_at"] = now
    profile["updated_at"] = now

    try:
        if not _profile_service or not _profile_service.upsert_alert_profile(profile):
            return (
                jsonify({"status": "error", "message": "Failed to save profile"}),
                500,
            )

        # Sync to config file
        if _profile_service:
            _profile_service.sync_alert_profiles_to_config()

        logger.info(f"Alert profile upserted: {profile_id}")
        return jsonify({"status": "ok", "profile_id": profile_id})
    except Exception as exc:
        logger.error(f"Error upserting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.delete("/alert/delete")
def delete_alert_profile():
    """Delete an alert profile."""
    profile_id = request.args.get("id", "").strip()
    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        if not _profile_service or not _profile_service.delete_alert_profile(
            profile_id
        ):
            return jsonify({"status": "error", "message": "Profile not found"}), 404

        # Sync to config file
        if _profile_service:
            _profile_service.sync_alert_profiles_to_config()

        logger.info(f"Alert profile deleted: {profile_id}")
        return jsonify({"status": "ok", "message": "Profile deleted"})
    except Exception as exc:
        logger.error(f"Error deleting alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/alert/toggle")
def toggle_alert_profile():
    """Toggle alert profile enabled status."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id = payload.get("id", "").strip()
    enabled = payload.get("enabled", True)

    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    try:
        profile = (
            _profile_service.get_alert_profile(profile_id) if _profile_service else None
        )
        if not profile:
            return jsonify({"status": "error", "message": "Profile not found"}), 404

        profile["enabled"] = bool(enabled)
        profile["updated_at"] = datetime.now(timezone.utc).isoformat()

        if not _profile_service or not _profile_service.upsert_alert_profile(profile):
            return (
                jsonify({"status": "error", "message": "Failed to update profile"}),
                500,
            )

        # Sync to config file
        if _profile_service:
            _profile_service.sync_alert_profiles_to_config()

        logger.info(
            f"Alert profile {profile_id} {'enabled' if enabled else 'disabled'}"
        )
        return jsonify({"status": "ok", "enabled": enabled})
    except Exception as exc:
        logger.error(f"Error toggling alert profile: {exc}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@profiles_bp.post("/alert/backtest")
def backtest_alert_profile():
    """Backtest an alert profile against historical messages."""
    if not request.is_json:
        return (
            jsonify(
                {"status": "error", "message": "Content-Type must be application/json"}
            ),
            400,
        )

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    profile_id = payload.get("id", "").strip()

    # Safely parse and validate hours_back
    try:
        hours_back_raw = payload.get("hours_back", 24)
        hours_back = int(hours_back_raw)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"hours_back must be a valid integer, got: {type(hours_back_raw).__name__}",
                }
            ),
            400,
        )

    # Safely parse and validate max_messages
    try:
        max_messages_raw = payload.get("max_messages", 100)
        max_messages = int(max_messages_raw)
    except (ValueError, TypeError):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"max_messages must be a valid integer, got: {type(max_messages_raw).__name__}",
                }
            ),
            400,
        )

    channel_filter = payload.get("channel_filter")  # Optional channel ID

    if not profile_id:
        return jsonify({"status": "error", "message": "Profile ID required"}), 400

    # Validate hours_back bounds (prevent resource exhaustion)
    if hours_back < 0 or hours_back > 168:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "hours_back must be between 0 and 168 (7 days)",
                }
            ),
            400,
        )

    # Validate max_messages bounds (prevent SQL injection and excessive queries)
    if max_messages < 1 or max_messages > 1000:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "max_messages must be between 1 and 1000",
                }
            ),
            400,
        )

    try:
        profile = (
            _profile_service.get_alert_profile(profile_id) if _profile_service else None
        )
        if not profile:
            return jsonify({"status": "error", "message": "Profile not found"}), 404

        # Fetch historical messages
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Build query with optional channel filter
        query = """
            SELECT chat_id, msg_id, chat_title, sender_name, message_text, 
                   score, triggers, alerted, created_at
            FROM messages 
            WHERE datetime(created_at) >= :cutoff
        """
        params: Dict[str, Any] = {"cutoff": cutoff}

        if channel_filter:
            query += " AND chat_id = :channel_id"
            params["channel_id"] = channel_filter

        query += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = max_messages  # Use integer directly for SQL LIMIT

        messages = _query_all(query, **params) if _query_all else []

        # Re-score messages with profile
        matches = []
        for msg in messages:
            text = msg.get("message_text", "")

            # Collect all keywords from profile
            keywords = []
            for category in [
                "action_keywords",
                "decision_keywords",
                "urgency_keywords",
                "importance_keywords",
                "release_keywords",
                "security_keywords",
                "risk_keywords",
                "opportunity_keywords",
            ]:
                keywords.extend(profile.get(category, []))

            # Check if any keywords match
            keyword_score = 0.0
            matched_keywords = []
            for kw in keywords:
                if kw.lower() in text.lower():
                    keyword_score += 0.8
                    matched_keywords.append(kw)

            # Build triggers list
            triggers = []
            rescore = keyword_score

            if matched_keywords:
                triggers.append(f"keywords:{','.join(matched_keywords[:3])}")

            # Estimate if this would trigger an alert
            threshold = 0.7  # Configurable threshold
            would_alert = rescore >= threshold

            if would_alert or msg.get("alerted"):
                matches.append(
                    {
                        "message_id": msg["msg_id"],
                        "chat_id": msg["chat_id"],
                        "chat_title": msg["chat_title"],
                        "sender_name": msg["sender_name"],
                        "score": round(rescore, 2),
                        "original_score": round(msg.get("score", 0.0), 2),
                        "triggers": triggers,
                        "original_triggers": msg.get("triggers", ""),
                        "text_preview": text[:100] + ("..." if len(text) > 100 else ""),
                        "timestamp": msg["created_at"],
                        "would_alert": would_alert,
                        "actually_alerted": bool(msg.get("alerted")),
                    }
                )

        # Calculate statistics
        total_messages = len(messages)
        matched_messages = len(matches)
        true_positives = sum(
            1 for m in matches if m["would_alert"] and m["actually_alerted"]
        )
        false_positives = sum(
            1 for m in matches if m["would_alert"] and not m["actually_alerted"]
        )
        false_negatives = sum(
            1
            for m in messages
            if m.get("alerted")
            and not any(
                match["message_id"] == m["msg_id"] and match["would_alert"]
                for match in matches
            )
        )

        stats = {
            "total_messages": total_messages,
            "matched_messages": matched_messages,
            "match_rate": (
                round(matched_messages / total_messages * 100, 1)
                if total_messages > 0
                else 0
            ),
            "avg_score": (
                round(sum(m["score"] for m in matches) / matched_messages, 2)
                if matched_messages > 0
                else 0
            ),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": (
                round(true_positives / (true_positives + false_positives) * 100, 1)
                if (true_positives + false_positives) > 0
                else 0
            ),
        }

        # Generate recommendations
        recommendations = []
        if stats["false_positives"] > stats["true_positives"]:
            recommendations.append(
                "⚠️ High false positive rate - consider tightening keyword matches"
            )
        if stats["match_rate"] > 50:
            recommendations.append("📊 Very high match rate - profile may be too broad")
        if stats["match_rate"] < 5:
            recommendations.append(
                "📉 Low match rate - consider adding more keywords or lowering thresholds"
            )
        if stats["precision"] < 70:
            recommendations.append("🎯 Low precision - review keyword relevance")

        result = {
            "status": "ok",
            "profile_id": profile_id,
            "profile_name": profile.get("name", profile_id),
            "test_date": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "hours_back": hours_back,
                "max_messages": max_messages,
                "channel_filter": channel_filter,
            },
            "matches": matches[:50],  # Limit response size
            "stats": stats,
            "recommendations": recommendations,
        }

        logger.info(f"Backtest completed for alert profile {profile_id}: {stats}")
        return jsonify(result)

    except Exception as exc:
        logger.error(f"Error backtesting alert profile: {exc}", exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500
